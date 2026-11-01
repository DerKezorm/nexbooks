"""Test books, made in the test: nothing here is a real book, a real cover or a real recording.

EPUBs are built with ``zipfile`` (EPUB 2 with an NCX or EPUB 3 with a navigation document, with or without cover and
series), PDFs with Pillow and pypdf, MOBI files byte by byte, MP3 from silent frames with tags from mutagen, M4B/M4A as a
bare MP4 container with chapters, FLAC as a stream header, Ogg Opus page by page. No ffmpeg, no tool outside the
requirements. The names and titles are invented.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
    "</container>"
)


def cover_image(color: tuple[int, int, int] = (150, 40, 50), size: tuple[int, int] = (300, 450), kind: str = "PNG") -> bytes:
    """A cover: a field of ``color`` with a lighter band and a dark one, so that it is not one colour only."""
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, size[1] // 5, size[0], size[1] // 5 + 12], fill=(235, 225, 200))
    draw.rectangle([0, size[1] - 60, size[0], size[1]], fill=tuple(max(0, c - 60) for c in color))
    out = io.BytesIO()
    image.save(out, kind)
    return out.getvalue()


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


#: The text of a chapter when a test gives none: a sentence in the language of the package (a book's text is in a
#: language, and the check after a download reads it), Latin filler for another or none.
LOREM = "Lorem ipsum dolor sit amet. "
FILLER = {
    "de": "Der Wind kam über die Felder, und sie ging mit dem Hund nach Hause, weil es schon spät war. ",
    "en": "The wind came over the fields and she walked home with the dog, because it was late. ",
    "fr": "Le vent est venu sur les champs et elle est rentrée à la maison avec le chien, car il était tard. ",
    "es": "El viento llegó por los campos y ella volvió a casa con el perro, porque ya era tarde. ",
    "it": "Il vento era forte e lui non sapeva che la casa della nonna fosse così lontana. ",
    "nl": "Het was al laat toen hij met de hond naar huis liep, want de wind kwam uit het oosten. ",
}


def make_epub(
    path: Path,
    *,
    title: str = "The Quiet Orchard",
    authors: tuple[str, ...] = ("Mara Lindqvist",),
    language: str | None = "en",
    isbn: str | None = None,
    asin: str | None = None,
    series: str | None = None,
    series_pos: float | None = None,
    epub3: bool = True,
    calibre_series: bool = False,
    cover: bytes | None = None,
    cover_style: str = "meta",
    toc: str = "nav",
    chapters: tuple[str, ...] = ("One", "Two", "Three"),
    text_bytes: int = 6000,
    description: str | None = None,
    subjects: tuple[str, ...] = (),
    publisher: str | None = None,
    date: str | None = None,
    original_title: str | None = None,
    extra_opf: str = "",
    doctype: str = "",
    extra_entries: dict[str, bytes] | None = None,
    prose: str | None = None,
    scrambled: tuple[int, ...] = (),
) -> Path:
    """An EPUB. ``scrambled``: the numbers of chapters written as bytes that are no markup (an encrypted chapter as a
    copy protection leaves it, with or without a file that names the protection). ``prose``: the text of every chapter (repeated to ``text_bytes``), else a filler of Latin words. ``epub3`` writes a navigation document and ``belongs-to-collection``, otherwise EPUB 2 with an NCX and
    (if ``calibre_series``) Calibre's meta elements. ``cover_style``: ``meta`` (EPUB 2 ``<meta name="cover">``),
    ``property`` (EPUB 3 ``cover-image``), ``name`` (nothing declared, an image called cover.png)."""
    ident = "urn:uuid:11111111-2222-3333-4444-555555555555"
    parts = [f'<dc:identifier id="bookid">{ident}</dc:identifier>']
    if isbn:
        parts.append(f'<dc:identifier opf:scheme="ISBN">{isbn}</dc:identifier>')
    if asin:
        parts.append(f'<dc:identifier opf:scheme="AMAZON">{asin}</dc:identifier>')
    parts.append(f"<dc:title>{_xml(title)}</dc:title>")
    for index, author in enumerate(authors):
        if epub3:
            parts.append(f'<dc:creator id="creator{index}">{_xml(author)}</dc:creator>'
                         f'<meta refines="#creator{index}" property="role" scheme="marc:relators">aut</meta>')
        else:
            parts.append(f'<dc:creator opf:role="aut">{_xml(author)}</dc:creator>')
    if language:
        parts.append(f"<dc:language>{language}</dc:language>")
    if publisher:
        parts.append(f"<dc:publisher>{_xml(publisher)}</dc:publisher>")
    if date:
        parts.append(f"<dc:date>{date}</dc:date>")
    if description:
        parts.append(f"<dc:description>{_xml(description)}</dc:description>")
    for subject in subjects:
        parts.append(f"<dc:subject>{_xml(subject)}</dc:subject>")
    if series and epub3 and not calibre_series:
        parts.append(f'<meta property="belongs-to-collection" id="series1">{_xml(series)}</meta>'
                     '<meta refines="#series1" property="collection-type">series</meta>'
                     + (f'<meta refines="#series1" property="group-position">{series_pos:g}</meta>'
                        if series_pos is not None else ""))
    if series and calibre_series:
        parts.append(f'<meta name="calibre:series" content="{_xml(series)}"/>')
        if series_pos is not None:
            parts.append(f'<meta name="calibre:series_index" content="{series_pos:g}"/>')
    if original_title:
        value = '{"#value#": "' + original_title.replace('"', "") + '", "label": "orgtitle"}'
        parts.append(f'<meta name="calibre:user_metadata:#orgtitle" content="{_xml(value).replace(chr(34), "&quot;")}"/>')
    manifest = []
    spine = []
    for number in range(len(chapters)):
        manifest.append(f'<item id="c{number}" href="text/c{number}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
    if toc == "nav" or epub3:
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    if toc == "ncx":
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    if cover is not None:
        props = ' properties="cover-image"' if cover_style == "property" else ""
        manifest.append(f'<item id="cover-img" href="images/cover.png" media-type="image/png"{props}/>')
        if cover_style == "meta":
            parts.append('<meta name="cover" content="cover-img"/>')
    version = "3.0" if epub3 else "2.0"
    spine_attr = ' toc="ncx"' if toc == "ncx" else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        f'xmlns:opf="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="{version}">'
        f'<metadata>{"".join(parts)}{extra_opf}</metadata><manifest>{"".join(manifest)}</manifest>'
        f"<spine{spine_attr}>{''.join(spine)}</spine></package>"
    )
    if doctype:
        opf = opf.replace("?>", "?>" + doctype, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        per_chapter = max(text_bytes // max(len(chapters), 1), 40)
        for number, name in enumerate(chapters):
            filler = (prose.strip() + " ") if prose else FILLER.get((language or "")[:2], LOREM)
            body = ("<p>" + filler * (per_chapter // len(filler) + 1) + "</p>")[:per_chapter]
            if number in scrambled:
                noise = b"".join(hashlib.sha256(f"{name}{block}".encode()).digest() for block in range(per_chapter // 32 + 1))
                archive.writestr(f"OEBPS/text/c{number}.xhtml", noise[:per_chapter])
                continue
            archive.writestr(
                f"OEBPS/text/c{number}.xhtml",
                f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{_xml(name)}</title></head>'
                f'<body><h1 id="top">{_xml(name)}</h1>{body}</body></html>',
            )
        if toc == "nav" or epub3:
            items = "".join(f'<li><a href="text/c{n}.xhtml">{_xml(name)}</a></li>' for n, name in enumerate(chapters))
            archive.writestr(
                "OEBPS/nav.xhtml",
                '<?xml version="1.0"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>' + items + "</ol></nav>"
                '<nav epub:type="landmarks"><ol><li><a href="text/c0.xhtml">Start</a></li></ol></nav></body></html>',
            )
        if toc == "ncx":
            points = "".join(
                f'<navPoint id="n{n}" playOrder="{n + 1}"><navLabel><text>{_xml(name)}</text></navLabel>'
                f'<content src="text/c{n}.xhtml#top"/></navPoint>' for n, name in enumerate(chapters))
            archive.writestr(
                "OEBPS/toc.ncx",
                '<?xml version="1.0"?><!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" '
                '"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd"><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                f'version="2005-1"><navMap>{points}</navMap></ncx>',
            )
        if cover is not None:
            archive.writestr("OEBPS/images/cover.png", cover)
        for name, data in (extra_entries or {}).items():
            archive.writestr(name, data)
    return path


def make_pdf(path: Path, *, title: str | None = "Field Notes", author: str | None = "Ada Feldkamp", pages: int = 3,
             cover: bool = False) -> Path:
    """A PDF of ``pages`` pages; with ``cover`` the first page is one picture."""
    from pypdf import PdfWriter

    path.parent.mkdir(parents=True, exist_ok=True)
    if cover:
        picture = Image.open(io.BytesIO(cover_image((30, 90, 120), (400, 600))))
        raw = io.BytesIO()
        picture.save(raw, "PDF")
        from pypdf import PdfReader

        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(raw.getvalue())))
        for _ in range(pages - 1):
            writer.add_blank_page(width=400, height=600)
    else:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=400, height=600)
    meta: dict[str, str] = {}
    if title:
        meta["/Title"] = title
    if author:
        meta["/Author"] = author
    if meta:
        writer.add_metadata(meta)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_mobi(path: Path, *, title: str = "Kite Season", author: str = "Jonas Brandauer", language: int = 0x07,
              isbn: str = "9783999900100", text_length: int = 44_000, cover: bytes | None = None,
              publisher: str = "Example Press", encryption: int = 0, break_exth: str = "") -> Path:
    """A MOBI file: the Palm database, the first record with the MOBI header and the EXTH block, a text record and, with
    ``cover``, a picture record. ``break_exth`` makes a lie in a length: ``record`` (an EXTH record longer than the
    block), ``count`` (more records than there are), ``offset`` (a record offset past the end of the file)."""
    name = title.encode("utf-8")
    exth_records = [(100, author.encode()), (101, publisher.encode()), (104, isbn.encode()), (503, name),
                    (524, b"de"), (106, b"2020-05-01T00:00:00+00:00")]
    if cover is not None:
        exth_records.append((201, struct.pack(">I", 0)))
    body = b"".join(struct.pack(">II", kind, 8 + len(data)) + data for kind, data in exth_records)
    count = len(exth_records)
    if break_exth == "record":
        body += struct.pack(">II", 100, 99999) + b"xx"
        count += 1
    if break_exth == "count":
        count = 9999
    exth = b"EXTH" + struct.pack(">II", 12 + len(body), count) + body
    exth += b"\x00" * ((4 - len(exth) % 4) % 4)
    header_length = 232
    mobi = bytearray(header_length)
    mobi[0:4] = b"MOBI"
    mobi[4:8] = struct.pack(">I", header_length)
    mobi[8:12] = struct.pack(">I", 2)
    mobi[12:16] = struct.pack(">I", 65001)
    mobi[20:24] = struct.pack(">I", 6)
    full_name_offset = 16 + header_length + len(exth)
    mobi[68:72] = struct.pack(">I", full_name_offset)
    mobi[72:76] = struct.pack(">I", len(name))
    mobi[76:80] = struct.pack(">I", language)
    mobi[92:96] = struct.pack(">I", 2 if cover is not None else 0xFFFFFFFF)
    mobi[112:116] = struct.pack(">I", 0x40)
    palmdoc = struct.pack(">HHIHHHH", 2, 0, text_length, 1, 4096, encryption, 0)
    record0 = palmdoc + bytes(mobi) + exth + name + b"\x00\x00"
    text = b"Lorem ipsum. " * 8
    records = [record0, text] + ([cover] if cover is not None else [])
    offset = 78 + 8 * len(records) + 2
    table = b""
    position = offset
    for index, record in enumerate(records):
        table += struct.pack(">II", position if break_exth != "offset" or index != len(records) - 1 else 1 << 30, 0)
        position += len(record)
    header = b"Test Book".ljust(32, b"\x00") + b"\x00" * 28 + b"BOOKMOBI" + b"\x00" * 8 + struct.pack(">H", len(records))
    header = header[:76] + struct.pack(">H", len(records))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + table + b"\x00\x00" + b"".join(records))
    return path


# ---- Audio -------------------------------------------------------------------------------------------------------


def make_mp3(path: Path, seconds: float = 3.0, **tags: Any) -> Path:
    """A silent MP3 (MPEG 1 layer 3, 128 kbit/s, 44.1 kHz: a frame is 417 bytes and 26.12 ms) of about ``seconds``.
    ``tags``: title, album, artist, albumartist, composer, genre, track, disc, narrator, series, series_pos, asin, author (an
    author tag), comment (``COMM``), chapters (a list of ``(title, start seconds)``), cover (image bytes)."""
    from mutagen.id3 import APIC, CHAP, COMM, ID3, TALB, TCOM, TCON, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX

    frames = max(int(seconds / 0.026122), 1)
    frame = b"\xff\xfb\x90\x00" + bytes(413)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(frame * frames)
    tag = ID3()
    mapping = {"title": TIT2, "album": TALB, "artist": TPE1, "albumartist": TPE2, "composer": TCOM, "genre": TCON}
    for key, frame_class in mapping.items():
        if tags.get(key):
            tag.add(frame_class(encoding=3, text=tags[key]))
    if tags.get("track"):
        tag.add(TRCK(encoding=3, text=str(tags["track"])))
    if tags.get("disc"):
        tag.add(TPOS(encoding=3, text=str(tags["disc"])))
    for key, label in (("narrator", "NARRATOR"), ("series", "SERIES"), ("asin", "ASIN"),
                       ("series_pos", "SERIES_PART"), ("author", "AUTHOR")):
        if tags.get(key) is not None:
            tag.add(TXXX(encoding=3, desc=label, text=str(tags[key])))
    if tags.get("cover"):
        tag.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=tags["cover"]))
    if tags.get("comment"):
        tag.add(COMM(encoding=3, lang="eng", desc="", text=tags["comment"]))
    for number, (name, start) in enumerate(tags.get("chapters", [])):
        tag.add(CHAP(element_id=f"ch{number}", start_time=int(start * 1000), end_time=int(start * 1000) + 1000,
                     start_offset=0xFFFFFFFF, end_offset=0xFFFFFFFF, sub_frames=[TIT2(encoding=3, text=name)]))
    tag.save(path, v2_version=3)
    return path


def _atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def make_m4b(path: Path, seconds: float = 120.0, *, chapters: list[tuple[str, float]] | None = None,
             brand: bytes = b"M4B ", **tags: Any) -> Path:
    """A bare MP4 container with an audio track of ``seconds`` (no samples: the length is what the header says), the
    chapters of the Nero kind (``chpl``) and the tags by mutagen. ``tags``: title, album, artist, albumartist,
    composer, narrator, genre, series, track, disc, cover."""
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

    timescale = 1000
    mvhd = _atom(b"mvhd", struct.pack(">BBBBIIIIIhh", 0, 0, 0, 0, 0, 0, timescale, int(seconds * timescale), 0x10000, 0x100, 0)
                 .ljust(100, b"\x00"))
    mdhd = _atom(b"mdhd", struct.pack(">BBBBIIII", 0, 0, 0, 0, 0, 0, timescale, int(seconds * timescale)) + b"\x55\xc4\x00\x00")
    hdlr = _atom(b"hdlr", struct.pack(">BBBBI", 0, 0, 0, 0, 0) + b"soun" + bytes(12) + b"SoundHandler\x00")
    trak = _atom(b"trak", _atom(b"mdia", mdhd + hdlr))
    blob = b""
    if chapters:
        entries = b"".join(struct.pack(">QB", int(start * 10_000_000), len(name.encode())) + name.encode()
                           for name, start in chapters)
        blob = _atom(b"udta", _atom(b"chpl", bytes(8) + struct.pack(">B", len(chapters)) + entries))
    moov = _atom(b"moov", mvhd + trak + blob)
    ftyp = _atom(b"ftyp", brand + bytes(4) + brand + b"mp42isom")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + moov + _atom(b"mdat", bytes(32)))
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    names = {"title": "\xa9nam", "album": "\xa9alb", "artist": "\xa9ART", "albumartist": "aART", "composer": "\xa9wrt",
             "narrator": "\xa9nrt", "genre": "\xa9gen"}
    for key, atom in names.items():
        if tags.get(key):
            audio.tags[atom] = [tags[key]]
    if tags.get("series"):
        audio.tags["----:com.apple.iTunes:SERIES"] = [MP4FreeForm(tags["series"].encode())]
    if tags.get("series_pos") is not None:
        audio.tags["----:com.apple.iTunes:SERIES_PART"] = [MP4FreeForm(str(tags["series_pos"]).encode())]
    if tags.get("track"):
        audio.tags["trkn"] = [(int(tags["track"]), 0)]
    if tags.get("cover"):
        audio.tags["covr"] = [MP4Cover(tags["cover"], imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()
    return path


def make_flac(path: Path, seconds: float = 5.0, **tags: Any) -> Path:
    """A FLAC stream header with no frames (the length is what the header says) and the Vorbis comments by mutagen.
    ``tags``: title, album, artist, composer, narrator, tracknumber, chapters (a list of ``(title, start seconds)``)."""
    from mutagen.flac import FLAC

    rate = 44100
    info = struct.pack(">HHIIQ", 4096, 4096, 0, 0, 0)
    # min/max frame size are 24 bits each, then 20 bits of rate, 3 of channels - 1, 5 of bits - 1, 36 of samples.
    packed = (rate << 44) | (1 << 41) | (15 << 36) | int(seconds * rate)
    streaminfo = struct.pack(">HH", 4096, 4096) + bytes(6) + packed.to_bytes(8, "big") + bytes(16)
    del info
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big") + streaminfo)
    audio = FLAC(path)
    audio.add_tags()
    for key, value in tags.items():
        if key == "chapters":
            for number, (name, start) in enumerate(value, start=1):
                moment = f"{int(start // 3600):02d}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}.{round(start % 1 * 1000):03d}"
                audio[f"CHAPTER{number:03d}"] = moment
                audio[f"CHAPTER{number:03d}NAME"] = name
        elif value is not None:
            audio[key] = str(value)
    audio.save()
    return path


def _ogg_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _ogg_page(payload: bytes, *, granule: int, sequence: int, header_type: int) -> bytes:
    segments = [255] * (len(payload) // 255) + [len(payload) % 255]
    head = (b"OggS" + bytes([0, header_type]) + struct.pack("<qIII", granule, 0x1234, sequence, 0)
            + bytes([len(segments)]) + bytes(segments))
    page = head + payload
    crc = _ogg_crc(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def make_opus(path: Path, seconds: float = 4.0, **tags: str) -> Path:
    """An Ogg Opus stream of three pages (head, tags, one with the end position): the length is what the pages say."""
    head = b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    vendor = b"test"
    comments = [f"{key.upper()}={value}".encode() for key, value in tags.items()]
    tag = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for comment in comments:
        tag += struct.pack("<I", len(comment)) + comment
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_ogg_page(head, granule=0, sequence=0, header_type=2) + _ogg_page(tag, granule=0, sequence=1,
                                                                                       header_type=0)
                     + _ogg_page(bytes(20), granule=int(seconds * 48000) + 312, sequence=2, header_type=4))
    return path
