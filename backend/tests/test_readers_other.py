"""PDF, MOBI/AZW3, audio, the files beside a book, covers, and opening a file safely."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.services.readers import ReadError, files, images, limits, read_file

from .bookmaker import cover_image, make_flac, make_m4b, make_mobi, make_mp3, make_opus, make_pdf
from .library_helpers import can_link_files, make_link


def read(folder: Path, name: str, fmt: str) -> dict[str, Any]:
    return read_file(str(folder), name, fmt)


# ---- PDF -----------------------------------------------------------------------------------------------------------


def test_a_pdf_gives_title_author_and_pages(tmp_path: Path) -> None:
    make_pdf(tmp_path / "a.pdf", title="Field Notes", author="Ada Feldkamp", pages=7)
    probe = read(tmp_path, "a.pdf", "pdf")
    assert (probe["title"], probe["authors"], probe["pages"]) == ("Field Notes", ["Ada Feldkamp"], 7)
    assert probe["cover"] is None


def test_a_pdf_whose_first_page_is_a_picture_has_a_cover(tmp_path: Path) -> None:
    make_pdf(tmp_path / "c.pdf", cover=True, pages=2)
    probe = read(tmp_path, "c.pdf", "pdf")
    assert probe["cover"] is not None and probe["pages"] == 2


def test_a_pdf_without_information_has_none(tmp_path: Path) -> None:
    make_pdf(tmp_path / "n.pdf", title=None, author=None)
    probe = read(tmp_path, "n.pdf", "pdf")
    assert probe["title"] == "" and probe["authors"] == [] and probe["pages"] == 3


def test_what_is_no_pdf_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / "x.pdf").write_bytes(b"GIF89a" + bytes(200))
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "x.pdf", "pdf")
    assert caught.value.code == "unreadable"
    (tmp_path / "y.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 99 0 R >>\nendobj\n" + b"junk" * 50)
    with pytest.raises(ReadError):
        read(tmp_path, "y.pdf", "pdf")


def test_a_pdf_too_large_to_be_parsed_is_listed_by_name_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_pdf(tmp_path / "big.pdf")
    monkeypatch.setattr("app.services.readers.pdf.PDF_MAX_BYTES", 10)
    probe = read(tmp_path, "big.pdf", "pdf")
    assert probe["pages"] == 0 and probe["title"] == ""


# ---- MOBI / AZW3 ---------------------------------------------------------------------------------------------------


def test_a_mobi_gives_its_exth_metadata_and_cover(tmp_path: Path) -> None:
    make_mobi(tmp_path / "a.mobi", cover=cover_image())
    probe = read(tmp_path, "a.mobi", "mobi")
    assert probe["title"] == "Kite Season" and probe["authors"] == ["Jonas Brandauer"]
    assert probe["isbn"] == "9783999900100" and probe["publisher"] == "Example Press"
    assert probe["language"] == "de" and probe["published"] == "2020-05-01"
    assert probe["pages"] == round(44_000 / limits.BYTES_PER_PAGE)
    assert probe["cover"] is not None and probe["drm"] is False


def test_a_mobi_with_drm_gives_its_metadata_and_says_so(tmp_path: Path) -> None:
    make_mobi(tmp_path / "d.azw3", encryption=2)
    probe = read(tmp_path, "d.azw3", "azw3")
    assert probe["drm"] is True and probe["title"] == "Kite Season"


@pytest.mark.parametrize("lie", ["record", "count", "offset"])
def test_a_mobi_that_lies_about_a_length_is_read_as_far_as_it_is_true_or_refused(tmp_path: Path, lie: str) -> None:
    make_mobi(tmp_path / "lie.mobi", break_exth=lie)
    try:
        probe = read(tmp_path, "lie.mobi", "mobi")
    except ReadError as exc:
        assert exc.code == "unreadable"
    else:
        assert probe["title"] == "Kite Season"  # what came before the lie


def test_a_mobi_cut_off_or_empty_is_unreadable(tmp_path: Path) -> None:
    make_mobi(tmp_path / "cut.mobi")
    data = (tmp_path / "cut.mobi").read_bytes()
    (tmp_path / "cut.mobi").write_bytes(data[:200])
    with pytest.raises(ReadError):
        read(tmp_path, "cut.mobi", "mobi")
    (tmp_path / "zero.mobi").write_bytes(b"")
    with pytest.raises(ReadError):
        read(tmp_path, "zero.mobi", "mobi")
    (tmp_path / "junk.mobi").write_bytes(os.urandom(4000))
    with pytest.raises(ReadError):
        read(tmp_path, "junk.mobi", "mobi")


def test_a_mobi_with_a_record_count_that_is_absurd_is_refused(tmp_path: Path) -> None:
    make_mobi(tmp_path / "records.mobi")
    data = bytearray((tmp_path / "records.mobi").read_bytes())
    data[76:78] = struct.pack(">H", 65535)
    (tmp_path / "records.mobi").write_bytes(bytes(data))
    with pytest.raises(ReadError):
        read(tmp_path, "records.mobi", "mobi")


# ---- audio ---------------------------------------------------------------------------------------------------------


def test_an_mp3_gives_tags_chapters_and_the_running_time(tmp_path: Path) -> None:
    make_mp3(tmp_path / "a.mp3", 5.0, title="Chapter 1", album="The Book", artist="Ada Feldkamp",
             composer="Ben Hollister", track=1, cover=cover_image(), chapters=[("One", 0), ("Two", 2.5)],
             series="Saga", series_pos=2)
    probe = read(tmp_path, "a.mp3", "mp3")
    assert probe["album"] == "The Book" and probe["title"] == "Chapter 1"
    assert probe["authors"] == ["Ada Feldkamp"]
    # The composer is the narrator when nothing says otherwise (see the top of audio.py).
    assert probe["narrators"] == ["Ben Hollister"]
    assert abs(probe["duration"] - 5.0) < 0.2
    assert [(c["title"], c["start"]) for c in probe["chapters"]] == [("One", 0.0), ("Two", 2.5)]
    assert probe["track"] == 1 and (probe["series"], probe["series_pos"]) == ("Saga", 2.0)
    assert probe["cover"] is not None


def _frames(*kinds: tuple[int, int]) -> bytes:
    """MPEG 1 layer 3 frames of silence at 44.1 kHz: ``(bit rate in kbit/s, how many)``, each frame without padding."""
    codes = {32: (0x10, 104), 64: (0x50, 208), 128: (0x90, 417)}
    out = b""
    for rate, count in kinds:
        code, size = codes[rate]
        out += (b"\xff\xfb" + bytes([code, 0x00]) + bytes(size - 4)) * count
    return out


def test_an_mp3_without_a_length_header_is_measured_by_counting_its_frames(tmp_path: Path) -> None:
    """mutagen guesses the length of such a file from its size and the first bit rate: half a minute for a file that
    changes its bit rate becomes a minute. The frames are counted instead."""
    from mutagen.mp3 import MP3

    # 500 frames at 32 kbit/s, then 500 at 128: 1000 frames of 1152 samples at 44.1 kHz.
    path = tmp_path / "vbr.mp3"
    path.write_bytes(_frames((32, 500), (128, 500)))
    truth = 1000 * 1152 / 44100
    guessed = MP3(path).info.length
    assert abs(guessed - truth) > 10, "the guess must be far off for this file to prove anything"
    assert abs(read(tmp_path, "vbr.mp3", "mp3")["duration"] - truth) < 0.01


def test_counting_frames_skips_the_tags_at_both_ends_and_gives_up_in_time(tmp_path: Path) -> None:
    from app.services.readers import audio

    body = _frames((64, 400), (128, 400))
    truth = 800 * 1152 / 44100
    # A tag at the front (ID3v2 of 300 bytes) and one at the back (ID3v1 of 128 bytes), and a few bytes of junk inside.
    front = b"ID3\x03\x00\x00" + bytes([0, 0, 2, 0x2C]) + bytes(300)
    path = tmp_path / "tagged.mp3"
    path.write_bytes(front + body[: 400 * 208] + b"\x00" * 7 + body[400 * 208 :] + b"TAG" + bytes(125))
    with path.open("rb") as handle:
        assert abs((audio._counted_length(handle) or 0) - truth) < 0.01
    # Out of time before the end: no answer (the guess of mutagen stands), never a short length.
    long = tmp_path / "long.mp3"
    long.write_bytes(_frames((128, 9000)))
    ticks = iter(range(10**6))
    with long.open("rb") as handle:
        assert audio._counted_length(handle, limit=0.5, clock=lambda: next(ticks)) is None
    with long.open("rb") as handle:
        assert abs((audio._counted_length(handle) or 0) - 9000 * 1152 / 44100) < 0.01
    # Noise is no MP3 to count.
    (tmp_path / "noise.mp3").write_bytes(bytes(range(256)) * 400)
    with (tmp_path / "noise.mp3").open("rb") as handle:
        assert audio._counted_length(handle) is None


def test_an_mp3_that_says_its_length_is_not_counted(tmp_path: Path) -> None:
    """The first frame of such a file (Xing) carries the length; that is the one used, and the frames are not walked."""
    code, size = 0x90, 417
    xing = b"\xff\xfb" + bytes([code, 0x00]) + bytes(32) + b"Xing" + struct.pack(">II", 1, 100)
    path = tmp_path / "xing.mp3"
    path.write_bytes(xing + bytes(size - len(xing)) + (b"\xff\xfb" + bytes([code, 0x00]) + bytes(size - 4)) * 299)
    # The header says 100 frames, the file holds 300: the answer is the header's.
    assert abs(read(tmp_path, "xing.mp3", "mp3")["duration"] - 100 * 1152 / 44100) < 0.05


def test_a_narrator_tag_beats_the_composer(tmp_path: Path) -> None:
    make_mp3(tmp_path / "n.mp3", 2.0, album="B", artist="Ada Feldkamp", composer="Someone Else", narrator="Ben Hollister")
    assert read(tmp_path, "n.mp3", "mp3")["narrators"] == ["Ben Hollister"]


def test_an_m4b_gives_the_time_to_the_second_and_its_chapters(tmp_path: Path) -> None:
    make_m4b(tmp_path / "a.m4b", 3725.4, chapters=[("Intro", 0), ("Main", 30.5), ("End", 3600)],
             title="The Book", album="The Book", artist="Ada Feldkamp", narrator="Ben Hollister", cover=cover_image(),
             series="Saga", series_pos=3)
    probe = read(tmp_path, "a.m4b", "m4b")
    assert round(probe["duration"]) == 3725
    assert [(c["title"], c["start"]) for c in probe["chapters"]] == [("Intro", 0.0), ("Main", 30.5), ("End", 3600.0)]
    assert probe["narrators"] == ["Ben Hollister"] and probe["authors"] == ["Ada Feldkamp"]
    assert (probe["series"], probe["series_pos"]) == ("Saga", 3.0)
    assert probe["cover"] is not None


def test_flac_and_opus_are_read(tmp_path: Path) -> None:
    make_flac(tmp_path / "a.flac", 12.0, title="T", album="A", artist="X Y", composer="Nora Nilsen",
              chapters=[("c1", 0), ("c2", 5.5)])
    probe = read(tmp_path, "a.flac", "flac")
    assert round(probe["duration"]) == 12 and probe["narrators"] == ["Nora Nilsen"]
    assert [(c["title"], c["start"]) for c in probe["chapters"]] == [("c1", 0.0), ("c2", 5.5)]
    make_opus(tmp_path / "a.opus", 4.0, title="T", album="A", artist="X Y")
    probe = read(tmp_path, "a.opus", "opus")
    assert round(probe["duration"]) == 4 and probe["album"] == "A"


def test_audio_that_is_none_is_unreadable(tmp_path: Path) -> None:
    for name, fmt in (("a.mp3", "mp3"), ("a.m4b", "m4b"), ("a.flac", "flac"), ("a.ogg", "ogg")):
        (tmp_path / name).write_bytes(os.urandom(3000))
        with pytest.raises(ReadError):
            read(tmp_path, name, fmt)
        (tmp_path / name).write_bytes(b"")
        with pytest.raises(ReadError):
            read(tmp_path, name, fmt)


def test_absurd_tags_are_cut_and_not_trusted(tmp_path: Path) -> None:
    make_mp3(tmp_path / "t.mp3", 2.0, title="T" * 5000, album="A" * 5000, artist="Z" * 5000)
    probe = read(tmp_path, "t.mp3", "mp3")
    assert len(probe["title"]) <= 300 and len(probe["album"]) <= 300 and len(probe["authors"][0]) <= 200
    chapters = [(f"c{n}", n) for n in range(300)]
    make_mp3(tmp_path / "many.mp3", 2.0, album="A", chapters=chapters)
    assert len(read(tmp_path, "many.mp3", "mp3")["chapters"]) <= limits.AUDIO_MAX_CHAPTERS


# ---- the files beside a book ----------------------------------------------------------------------------------------


def test_audiobookshelfs_metadata_json_is_read(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text(json.dumps({
        "title": "Aschenfeld", "subtitle": None, "authors": ["Rune Falkner"], "narrators": ["Ben Hollister"],
        "series": ["Anselm Quist #4.0"], "genres": ["Thriller"], "tags": ["x"], "publishedYear": "2013",
        "publisher": "Example", "description": "<p>Text</p>", "isbn": "3999900504", "asin": "B0EXAMPL12",
        "language": "de-DE", "abridged": False,
        "chapters": [{"id": 0, "start": 0, "end": 5, "title": "One"}, {"id": 1, "start": 5.5, "end": 9, "title": "Two"}],
    }), encoding="utf-8")
    probe = read(tmp_path, "metadata.json", "json")
    assert probe["title"] == "Aschenfeld" and probe["authors"] == ["Rune Falkner"] and probe["narrators"] == ["Ben Hollister"]
    assert (probe["series"], probe["series_pos"]) == ("Anselm Quist", 4.0)
    assert probe["subjects"] == ["Thriller", "x"] and probe["year"] == 2013
    assert probe["isbn"] == "9783999900506" or probe["isbn"].startswith("978")
    assert probe["language"] == "de" and probe["abridged"] is False
    assert [(c["title"], c["start"]) for c in probe["chapters"]] == [("One", 0.0), ("Two", 5.5)]
    assert probe["description"] == "Text"


@pytest.mark.parametrize("content", [
    pytest.param(b"[]", id="list"), pytest.param(b"5", id="number"), pytest.param(b'"text"', id="text"),
    pytest.param(b"{not json", id="broken"), pytest.param(b"\xff\xfe\x00", id="bytes"),
    pytest.param(b"[" * 100_000 + b"]" * 100_000, id="deep"), pytest.param(b"null", id="null"),
])
def test_a_metadata_json_that_is_not_an_object_is_unreadable(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "metadata.json").write_bytes(content)
    with pytest.raises(ReadError):
        read(tmp_path, "metadata.json", "json")


def test_a_metadata_json_with_wrong_types_is_read_as_far_as_it_makes_sense(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text(json.dumps({
        "title": 5, "authors": {"a": 1}, "narrators": [1, 2, "Real Narrator"], "series": 5, "genres": "Krimi",
        "publishedYear": [2000], "description": 7, "isbn": 5, "asin": [], "language": 3, "abridged": "yes",
        "chapters": [{"start": "x", "title": 1}, {"start": -5}, {"start": 1e30}, "junk", {"start": 2, "title": "ok"}],
    }), encoding="utf-8")
    probe = read(tmp_path, "metadata.json", "json")
    assert probe["title"] == "" and probe["authors"] == [] and probe["narrators"] == ["Real Narrator"]
    assert probe["subjects"] == ["Krimi"] and probe["year"] is None and probe["abridged"] is None
    assert probe["chapters"] == [{"title": "ok", "start": 2.0}]


def test_a_huge_metadata_json_is_refused(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text('{"title": "' + "x" * (limits.SIDECAR_MAX_BYTES + 10) + '"}', encoding="utf-8")
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "metadata.json", "json")
    assert caught.value.code == "too_large"


def test_calibres_metadata_opf_is_read(tmp_path: Path) -> None:
    (tmp_path / "metadata.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">'
        '<dc:title>Nebelchor</dc:title><dc:creator opf:role="aut">Falkner, Rune</dc:creator><dc:language>deu</dc:language>'
        '<meta name="calibre:series" content="Anselm Quist"/><meta name="calibre:series_index" content="1.0"/>'
        "</metadata></package>", encoding="utf-8")
    probe = read(tmp_path, "metadata.opf", "opf")
    assert probe["title"] == "Nebelchor" and probe["authors"] == ["Rune Falkner"] and probe["language"] == "de"
    assert (probe["series"], probe["series_pos"]) == ("Anselm Quist", 1.0)


def test_a_cover_file_is_made_small(tmp_path: Path) -> None:
    (tmp_path / "cover.jpg").write_bytes(cover_image((40, 90, 160), (1400, 2100), "JPEG"))
    probe = read(tmp_path, "cover.jpg", "cover")
    webp, color = probe["cover"]
    with Image.open(__import__("io").BytesIO(webp)) as picture:
        assert picture.format == "WEBP" and max(picture.size) <= 1080
    assert color.startswith("#") and len(color) == 7


# ---- covers and colours ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("color", "hue"), [((190, 40, 50), "red"), ((40, 160, 70), "green"), ((50, 70, 200), "blue")])
def test_the_main_colour_of_a_cover_is_its_dominant_colour(color: tuple[int, int, int], hue: str) -> None:
    image = Image.new("RGB", (200, 300), color)
    out = __import__("io").BytesIO()
    image.save(out, "PNG")
    result = images.reduce_cover(out.getvalue())
    assert result is not None
    red, green, blue = (int(result[1][i:i + 2], 16) for i in (1, 3, 5))
    dominant = {"red": red, "green": green, "blue": blue}[hue]
    assert dominant == max(red, green, blue) and dominant - min(red, green, blue) > 40


def test_a_grey_cover_gets_a_grey_colour_and_a_white_one_is_not_white() -> None:
    for fill in ((128, 128, 128), (255, 255, 255), (0, 0, 0)):
        out = __import__("io").BytesIO()
        Image.new("RGB", (100, 150), fill).save(out, "PNG")
        result = images.reduce_cover(out.getvalue())
        assert result is not None
        red, green, blue = (int(result[1][i:i + 2], 16) for i in (1, 3, 5))
        assert max(red, green, blue) - min(red, green, blue) < 30
        assert 0.15 * 255 <= max(red, green, blue) <= 0.92 * 255


def test_what_is_no_picture_or_too_big_a_picture_is_no_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    assert images.reduce_cover(b"") is None
    assert images.reduce_cover(b"not a picture" * 20) is None
    assert images.reduce_cover(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>" * 3) is None
    assert images.reduce_cover(b"\xff\xd8\xff" + os.urandom(500)) is None
    monkeypatch.setattr(images, "COVER_MAX_PIXELS", 100)
    assert images.reduce_cover(cover_image(size=(50, 50))) is None


def test_a_photo_of_a_person_is_made_small_and_a_huge_one_is_none() -> None:
    io = __import__("io")
    small = io.BytesIO()
    Image.new("RGB", (400, 500), (90, 120, 150)).save(small, "PNG")
    made = images.reduce_portrait(small.getvalue())
    assert made is not None and Image.open(io.BytesIO(made)).width <= images.PORTRAIT_SIZE[0]
    # 36 million pixels is below the limit of Pillow itself but above the one of nexbooks: not even decoded.
    huge = io.BytesIO()
    Image.new("L", (6000, 6000), 128).save(huge, "PNG")
    assert limits.COVER_MAX_PIXELS < 6000 * 6000 < 89_000_000
    assert len(huge.getvalue()) < limits.COVER_MAX_BYTES
    assert images.reduce_portrait(huge.getvalue()) is None
    assert images.reduce_portrait(b"") is None


def test_a_transparent_cover_is_put_on_white() -> None:
    out = __import__("io").BytesIO()
    Image.new("RGBA", (100, 150), (0, 0, 0, 0)).save(out, "PNG")
    assert images.reduce_cover(out.getvalue()) is not None


# ---- opening a file safely ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../../b", "a//b", "", ".", "a/./b", "a\\b", "x\x00y", "C:/x"])
def test_a_path_that_leaves_the_folder_is_refused(tmp_path: Path, bad: str) -> None:
    (tmp_path / "x").write_text("inside", encoding="utf-8")
    with pytest.raises(ReadError), files.open_below(str(tmp_path), bad):
        pass


def test_a_link_is_not_followed_in_the_file_or_in_a_folder_on_the_way(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.epub").write_bytes(b"secret")
    inside = tmp_path / "inside"
    inside.mkdir()
    make_link(inside / "linked", outside)
    with pytest.raises(ReadError) as caught, files.open_below(str(inside), "linked/secret.epub"):
        pass
    assert caught.value.code == "link"
    if can_link_files():
        os.symlink(outside / "secret.epub", inside / "file.epub")
        with pytest.raises(ReadError) as caught, files.open_below(str(inside), "file.epub"):
            pass
        assert caught.value.code == "link"


def test_a_file_that_changes_while_it_is_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "moving.bin"
    path.write_bytes(b"a" * 100)
    with pytest.raises(ReadError) as caught, files.open_below(str(tmp_path), "moving.bin") as handle:
        handle.read(10)
        path.write_bytes(b"b" * 200)
    assert caught.value.code == "changed"


def test_a_folder_is_no_file(tmp_path: Path) -> None:
    (tmp_path / "folder.epub").mkdir()
    with pytest.raises(ReadError):
        read(tmp_path, "folder.epub", "epub")


def test_an_unsupported_format_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "x.txt", "txt")
    assert caught.value.code == "unsupported"
