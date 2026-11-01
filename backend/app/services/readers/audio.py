"""Audiobooks: the tags, the running time to the second, the chapters and the cover of an audio file, with ``mutagen``.

**Who is author and who is narrator.** The tags were made for music, and every producer bends them a little. The rule
here is the most common one, in this order:

* author: a tag that says so (``TXXX:AUTHOR``, ``AUTHOR``, ``WRITER``), else the *artist* (``TPE1``, ``©ART``,
  ``ARTIST``), else the *album artist*. Only the first is an author field (``authors_field``): the artist names a
  studio, a publisher or a narrator as often as an author, and such a name is never taken for proof of a pen name;
* narrator: a tag that says so (``©nrt``, ``TXXX:NARRATOR``, ``NARRATOR``), else the *composer* (``TCOM``, ``©wrt``,
  ``COMPOSER``; Audible and the tools that copy its files put the narrator there), else the *album artist* when it
  differs from the artist. The assembly turns the two around when the album artist is the author the folders name and
  the artist is not (``catalog._roles_by_the_folder``): two witnesses outweigh one;
* the title of the book is the *album*; the title tag of a file is the name of its chapter or part.

**Running time** is the length the stream says, rounded to the second at the end (the parts of one book are added up
before rounding, so the sum is not off by half a second for every part). An MP3 without the header that says its length
(Xing, VBRI) has none to read: mutagen then guesses from the size and the first bit rate, which is far off for one
with a changing bit rate and a few seconds short or long for a constant one. Its frames are counted instead
(``_counted_length``, within a time limit; when it cannot finish, the guess stands).
**Chapters** come from the file (M4B/M4A ``chpl``, MP3 ``CHAP`` frames, Vorbis ``CHAPTER001=00:00:00.000``); a file
without any is one chapter, which the catalogue names. Everything read is cut to a length and a count, and a tag of
the wrong type is a tag that is not there.
"""

from __future__ import annotations

import re
import time
from typing import IO, Any

from . import images
from .limits import AUDIO_MAX_CHAPTERS, AUDIO_MAX_DURATION, COVER_MAX_BYTES, TAG_TEXT_MAX, TITLE_MAX, ReadError
from .names import AUTHOR_ROLES, authors_compatible, number_of, person_key, read_people, series_from_field
from .text import clean, fold, html_to_text, isbn13, language_of, year_of

_NARRATOR_KEYS = ("narrator", "narrated by", "reader", "read by", "sprecher")
#: What a tag of the people holds for a cast it does not name (``div.``, ``Various``), folded.
_UNNAMED_CAST = frozenset({"div", "diverse", "diverse sprecher", "div sprecher", "various", "various artists",
                           "verschiedene", "u a", "und andere", "full cast", "cast"})


def unnamed_cast(*fields: str) -> bool:
    """Whether one of the tags of the people says a cast read it, without naming it (``div.``)."""
    return any(fold(field) in _UNNAMED_CAST for field in fields if field)
_SERIES_KEYS = ("series", "seriesname", "series name", "mvnm", "movement name", "movementname")
_SERIES_POS_KEYS = ("series_part", "seriespart", "series-part", "part", "mvin", "movement", "series_index",
                    "seriesindex", "series index", "series_position")


def _first(values: Any) -> str:
    if isinstance(values, list | tuple):
        values = values[0] if values else ""
    if isinstance(values, bytes):
        values = values.decode("utf-8", "replace")
    return values if isinstance(values, str) else (str(values) if isinstance(values, int | float) else "")


def _number(text: str) -> int | None:
    match = re.match(r"\s*(\d{1,5})", text or "")
    return int(match.group(1)) if match else None


def _clock(text: str) -> float | None:
    """``00:12:34.567`` (a Vorbis chapter time) in seconds."""
    match = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\s*", text or "")
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int((fraction or "0").ljust(3, "0")) / 1000
    return value if value < AUDIO_MAX_DURATION else None


# ---- The length of an MP3 that does not say it ----------------------------------------------------------------------

#: Bit rates (kbit/s) by (MPEG version 1 or 2, layer 1 to 3); version 2 and 2.5 share them.
_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
#: Sample rates by the version bits (3: MPEG 1, 2: MPEG 2, 0: MPEG 2.5).
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
#: How long counting the frames of one file may take, and how far a header may be searched for after a bad place.
COUNT_SECONDS = 3.0
_RESYNC_BYTES = 8192


def _frame(header: bytes) -> tuple[int, float] | None:
    """The size in bytes and the length in seconds of the MPEG audio frame a header starts, or None if it is none."""
    if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return None
    version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
    bitrate_index, rate_index, padding = header[2] >> 4, (header[2] >> 2) & 3, (header[2] >> 1) & 1
    if version == 1 or layer == 0 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    kind, number = (1 if version == 3 else 2), 4 - layer
    bitrate = _BITRATES[(kind, number)][bitrate_index] * 1000
    rate = _SAMPLE_RATES[version][rate_index]
    if number == 1:
        size, samples = (12 * bitrate // rate + padding) * 4, 384
    else:
        size = (144 if kind == 1 or number == 2 else 72) * bitrate // rate + padding
        samples = 1152 if kind == 1 or number == 2 else 576
    return (size, samples / rate) if size > 4 else None


def _counted_length(handle: IO[bytes], limit: float = COUNT_SECONDS, clock: Any = time.monotonic) -> float | None:
    """The running time of an MP3 by counting its frames, or None when that did not work out (no frames, not the whole
    file within the time limit) or is not needed (the file says its length). The tag at the front is skipped; something
    that is no frame (the tag at the end, a bad place) is searched past, a little way."""
    handle.seek(0, 2)
    end = handle.tell()
    handle.seek(0)
    head = handle.read(10)
    position = 0
    if head[:3] == b"ID3" and len(head) == 10:
        size = sum((byte & 0x7F) << (7 * (3 - index)) for index, byte in enumerate(head[6:10]))
        position = 10 + size + (10 if head[5] & 0x10 else 0)
    deadline = clock() + limit
    seconds, frames, skipped = 0.0, 0, 0
    while position + 4 <= end:
        handle.seek(position)
        found = _frame(handle.read(4))
        if found is None or position + found[0] > end:
            # Not a frame: the tag at the end of the file, or a place that is damaged. Searched past, a little way.
            handle.seek(position + 1)
            chunk = handle.read(_RESYNC_BYTES)
            index = -1
            for candidate in range(len(chunk) - 3):
                if chunk[candidate] == 0xFF and _frame(chunk[candidate : candidate + 4]) is not None:
                    index = candidate
                    break
            if index < 0:
                break
            position += 1 + index
            skipped += 1 + index
            continue
        if frames == 0:
            # The first frame of a file that says its length (Xing and Info of LAME, VBRI of Fraunhofer) carries it:
            # that length is exact, and mutagen has it.
            handle.seek(position + 4)
            if any(mark in handle.read(60) for mark in (b"Xing", b"Info", b"VBRI")):
                return None
        position += found[0]
        seconds += found[1]
        frames += 1
        if frames % 4096 == 0 and clock() > deadline:
            return None
    # Only what is left of the file is no frame (a tag, a few bytes): counted whole. A file that is mostly no frames is
    # not an MP3 this can measure.
    if frames == 0 or end - position > max(1024, end // 50) or skipped > max(4096, end // 50):
        return None
    return seconds


def _narrators(field: str, titles: list[str], authors: list[str]) -> list[str]:
    """The narrators a tag of narrators names: the readers when it says who reads whom (``Ben Hollister liest Mara
    Feldkamp`` in the album artist), else everyone in it; a tag that only names the book and its author (``Der
    Leuchtturm - Mara Feldkamp``) names no narrator. (An author who reads her own book is her narrator.)"""
    read = read_people(field, titles=titles)
    readers = [name for name, role in read.people if role == "narrator"]
    if readers or not read.marked:
        return readers or [name for name, _role in read.people]
    return [name for name, _role in read.people if not any(
        person_key(name) == person_key(author) or authors_compatible(name, author) for author in authors)]


def _stream_rate(audio: Any, handle: IO[bytes], fmt: str, length: float) -> int:
    """The bit rate of the sound alone in kbit/s, 0 when it is not known. Of an MP3 its size without the tag at the
    front over its running time, which holds for a changing rate as well (the header of a frame names only that
    frame's rate); a cover in every part is no sound. Of the other formats what the file says."""
    if fmt == "mp3":
        handle.seek(0, 2)
        sound = handle.tell() - int(getattr(audio.tags, "size", 0) or 0)
        return int(sound * 8 / length / 1000) if sound > 0 and length > 0 else 0
    return int(getattr(audio.info, "bitrate", 0) or 0) // 1000


def _finish(fmt: str, length: float, tags: dict[str, list[str]], chapters: list[dict[str, Any]],
            cover: bytes | None, bitrate: int = 0) -> dict[str, Any]:
    """The probe from a format's tags, as lower-case keys with lists of text."""

    def one(*keys: str, limit: int = 300) -> str:
        for key in keys:
            if tags.get(key):
                text = clean(_first(tags[key]), limit)
                if text:
                    return text
        return ""

    artist, albumartist, composer = one("artist"), one("albumartist"), one("composer")
    author_tag = one("author", "writer")
    named = author_tag or artist or albumartist
    # ``Reader liest Author`` in the artist tag: the reader is the narrator; ``A schreibt als B``: one person; ``Titel
    # (ungekürzt) - Autor``: the author, when the title is the album's or the track's.
    titles = [one("album", limit=TITLE_MAX), one("title", limit=TITLE_MAX)]
    read = read_people(named, titles=titles)
    authors = [name for name, role in read.people if role in AUTHOR_ROLES]
    readers = [name for name, role in read.people if role == "narrator"]
    pen_names = [list(pair) for pair in read.pens]
    narrator = one(*_NARRATOR_KEYS)
    if not narrator and composer and composer.casefold() not in {name.casefold() for name in authors}:
        narrator = composer
    if not narrator and albumartist and artist and albumartist.casefold() != artist.casefold():
        narrator = albumartist
    series, series_pos = "", None
    raw_series = one(*_SERIES_KEYS, limit=200)
    if raw_series:
        series, series_pos = series_from_field(raw_series)
        position = one(*_SERIES_POS_KEYS, limit=20)
        if position:
            series_pos = number_of(position) or series_pos
    track = _number(one("tracknumber", "track"))
    disc = _number(one("discnumber", "disc"))
    asin = one("asin", limit=20).upper()
    out: dict[str, Any] = {
        "format": fmt,
        "duration": round(length, 3),
        "bitrate": bitrate,
        "title": one("title", limit=TITLE_MAX),
        "album": one("album", limit=TITLE_MAX),
        "authors": authors,
        "authors_field": bool(author_tag),
        # The two tags as they are, for the folders to weigh (``catalog._roles_by_the_folder``).
        "artist": artist,
        "albumartist": albumartist,
        "narrators": _narrators(narrator, titles, authors) or readers,
        "cast": unnamed_cast(narrator, artist, albumartist, composer),
        "pen_names": pen_names,
        "track": track,
        "disc": disc,
        "year": year_of(one("date", "year", "originaldate", limit=40)),
        "genre": one("genre", limit=80),
        "description": html_to_text(_first(tags.get("description") or tags.get("comment") or [""])[:TAG_TEXT_MAX]),
        "series": series,
        "series_pos": series_pos,
        "asin": asin if len(asin) == 10 and asin.isalnum() else "",
        "isbn": isbn13(one("isbn", limit=20)),
        "language": language_of(one("language")),
        "publisher": one("publisher", "organization", limit=200),
        "chapters": chapters[:AUDIO_MAX_CHAPTERS],
        "cover": images.reduce_cover(cover) if cover else None,
    }
    return out


def _id3(audio: Any) -> tuple[dict[str, list[str]], list[dict[str, Any]], bytes | None]:
    tags: dict[str, list[str]] = {}
    chapters: list[dict[str, Any]] = []
    cover: bytes | None = None
    if audio.tags is None:
        return tags, chapters, cover
    wanted = {"TIT2": "title", "TALB": "album", "TPE1": "artist", "TPE2": "albumartist", "TCOM": "composer",
              "TRCK": "tracknumber", "TPOS": "discnumber", "TCON": "genre", "TLAN": "language", "TPUB": "publisher",
              "TDRC": "date", "TYER": "date", "MVNM": "mvnm", "MVIN": "mvin", "TSOA": "tsoa"}
    for key in audio.tags:
        frame = audio.tags[key]
        name = key.split(":", 1)[0]
        if name in wanted and hasattr(frame, "text"):
            tags.setdefault(wanted[name], []).extend(str(item) for item in frame.text[:5])
        elif name == "TXXX" and hasattr(frame, "desc"):
            tags.setdefault(str(frame.desc).casefold()[:60], []).extend(str(item) for item in frame.text[:5])
        elif name == "COMM" and hasattr(frame, "text") and "description" not in tags:
            tags["description"] = [str(item) for item in frame.text[:1]]
        elif name == "USLT" and hasattr(frame, "text") and "description" not in tags:
            tags["description"] = [str(frame.text)[:TAG_TEXT_MAX]]
        elif name == "APIC" and hasattr(frame, "data"):
            data = bytes(frame.data)
            if len(data) <= COVER_MAX_BYTES and (cover is None or getattr(frame, "type", 0) == 3):
                cover = data
        elif name == "CHAP" and hasattr(frame, "start_time") and len(chapters) < AUDIO_MAX_CHAPTERS:
            title = ""
            for sub in getattr(frame, "sub_frames", {}).values():
                if hasattr(sub, "text") and sub.FrameID == "TIT2":
                    title = clean(_first([str(item) for item in sub.text]), TITLE_MAX)
            start = frame.start_time / 1000
            if 0 <= start < AUDIO_MAX_DURATION:
                chapters.append({"title": title, "start": start})
    chapters.sort(key=lambda item: item["start"])
    return tags, chapters, cover


def _mp4(audio: Any) -> tuple[dict[str, list[str]], list[dict[str, Any]], bytes | None]:
    tags: dict[str, list[str]] = {}
    cover: bytes | None = None
    chapters: list[dict[str, Any]] = []
    if audio.tags is not None:
        names = {"\xa9nam": "title", "\xa9alb": "album", "\xa9ART": "artist", "aART": "albumartist",
                 "\xa9wrt": "composer", "\xa9nrt": "narrator", "\xa9day": "date", "\xa9gen": "genre",
                 "desc": "description", "ldes": "description", "\xa9cmt": "comment", "\xa9mvn": "mvnm",
                 "\xa9grp": "grouping", "tvsh": "series"}
        for key, value in audio.tags.items():
            if key in names and isinstance(value, list):
                tags.setdefault(names[key], []).extend(_first([item]) for item in value[:5])
            elif key in ("trkn", "disk") and isinstance(value, list) and value and isinstance(value[0], tuple):
                tags["tracknumber" if key == "trkn" else "discnumber"] = [str(value[0][0])]
            elif key == "\xa9mvi" and isinstance(value, list) and value:
                tags["mvin"] = [str(value[0])]
            elif key == "covr" and isinstance(value, list) and value:
                data = bytes(value[0])
                if len(data) <= COVER_MAX_BYTES:
                    cover = data
            elif key.startswith("----:") and isinstance(value, list):
                label = key.rsplit(":", 1)[-1].casefold()[:60]
                tags.setdefault(label, []).extend(_first([item]) for item in value[:5])
    for chapter in list(audio.chapters or [])[:AUDIO_MAX_CHAPTERS]:
        start = float(chapter.start)
        if 0 <= start < AUDIO_MAX_DURATION:
            chapters.append({"title": clean(chapter.title, TITLE_MAX), "start": start})
    return tags, chapters, cover


def _vorbis(audio: Any) -> tuple[dict[str, list[str]], list[dict[str, Any]], bytes | None]:
    tags: dict[str, list[str]] = {}
    chapters: list[dict[str, Any]] = []
    cover: bytes | None = None
    if audio.tags is not None:
        for key, value in audio.tags.items():
            if isinstance(value, list):
                tags.setdefault(key.casefold()[:60], []).extend(_first([item]) for item in value[:5])
        starts: dict[str, float] = {}
        names: dict[str, str] = {}
        for key, values in tags.items():
            match = re.fullmatch(r"chapter(\d{1,4})(name)?", key)
            if match and values:
                if match.group(2):
                    names[match.group(1)] = clean(values[0], TITLE_MAX)
                else:
                    moment = _clock(values[0])
                    if moment is not None:
                        starts[match.group(1)] = moment
        for number in sorted(starts, key=int)[:AUDIO_MAX_CHAPTERS]:
            chapters.append({"title": names.get(number, ""), "start": starts[number]})
    for picture in getattr(audio, "pictures", [])[:3]:
        if len(picture.data) <= COVER_MAX_BYTES:
            cover = bytes(picture.data)
            if picture.type == 3:
                break
    return tags, chapters, cover


def read(handle: IO[bytes], fmt: str, relative: str = "") -> dict[str, Any]:
    """The tags and the running time of an audio file. ``ReadError("unreadable")`` when it is none."""
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    try:
        if fmt == "mp3":
            audio: Any = MP3(handle)
            extract = _id3
        elif fmt in ("m4a", "m4b"):
            audio = MP4(handle)
            extract = _mp4
        elif fmt == "flac":
            audio = FLAC(handle)
            extract = _vorbis
        else:
            audio = mutagen.File(handle)
            extract = _vorbis
            if audio is None:
                raise ReadError("unreadable")
        length = float(audio.info.length)
        if fmt == "mp3":
            counted = _counted_length(handle)
            if counted is not None and counted > 0:
                length = counted
        tags, chapters, cover = extract(audio)
        bitrate = _stream_rate(audio, handle, fmt, length)
    except ReadError:
        raise
    except Exception as exc:
        raise ReadError("unreadable") from exc
    if not 0 < length < AUDIO_MAX_DURATION:
        raise ReadError("unreadable")
    return _finish(fmt, length, tags, chapters, cover, bitrate)
