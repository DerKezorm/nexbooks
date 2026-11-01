"""A made-up library on disk, for looking at the shelf with many books: e-books and recordings that are no real books.

A development tool; it is not part of the image. The books come from a list (a JSON file) of works, each with a title,
an author, a genre, a colour, pages, perhaps a series, and its editions:

    {"title": "...", "author": "...", "genre": "krimi", "color": "#5e2027", "pages": 320, "year": 2011,
     "series": "..." or null, "seriesNo": 1 or null, "blurb": "...",
     "fassungen": [{"kind": "ebook", "lang": "Deutsch", "format": "EPUB", "pages": 320},
                   {"kind": "audio", "variant": "ungekürzt", "lang": "Deutsch", "minutes": 540, "narrator": "..."}]}

For every work a folder ``NNNN Title`` (the order of the list is the order the scan meets them in) holds the e-books
(EPUB, PDF or AZW3, made with the test makers: text of the length of ``pages``, a table of contents, a cover in the
colour of the book) and, in a folder of its own for each recording, a bare M4B whose length is the running time in its
header (no sound: a made-up library of 2,000 books weighs a few megabytes).

    python tools/make_library.py --spec works.json --out <folder>

``--spec`` takes a JSON list of that shape. Everything here is invented; nothing real goes in.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from tests import bookmaker

#: A subject the genre of the shelf is read from (``app/services/genres.py``).
SUBJECT = {"roman": "Roman", "krimi": "Krimi", "fantasy": "Fantasy", "scifi": "Science Fiction",
           "klassiker": "Klassiker", "sachbuch": "Sachbuch", "kinder": "Kinderbuch", "lyrik": "Lyrik"}
LANGUAGE = {"Deutsch": "de", "Englisch": "en"}
#: Words in the name of the folder of a recording that say which kind it is (``catalog._detect_variant``).
VARIANT_FOLDER = {"ungekürzt": "Hörbuch ungekürzt", "gekürzt": "Hörbuch gekürzt", "Hörspiel": "Hörspiel"}
BYTES_PER_PAGE = 2200
CHAPTERS = 12


def safe(name: str, limit: int = 60) -> str:
    return re.sub(r"[^\w .,'()&+-]", "", name, flags=re.UNICODE).strip()[:limit] or "book"


#: Set by ``--drawn-covers``: covers with the author and the title on them (for pictures), not a bare colour.
DRAWN_COVERS = False


def _font(size: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an old Pillow has one size only
        return ImageFont.load_default()


def cover_png(color: str, work: dict[str, Any] | None = None) -> bytes:
    """A cover in one colour, as small as a cover can be; with ``--drawn-covers``, 300 by 450 with the author above and
    the title in the middle in the colour that reads on it."""
    value = color.lstrip("#")
    rgb = (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    out = io.BytesIO()
    if not (DRAWN_COVERS and work):
        Image.new("RGB", (200, 300), rgb).save(out, "PNG")
        return out.getvalue()
    import textwrap

    from PIL import ImageDraw

    image = Image.new("RGB", (300, 450), rgb)
    draw = ImageDraw.Draw(image)
    light = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 > 0.45
    ink = (42, 36, 32) if light else (239, 230, 210)
    faint = tuple(int(c * 0.82 + (0 if light else 255) * 0.18) for c in rgb)
    draw.rectangle((14, 14, 285, 435), outline=faint, width=2)
    draw.text((150, 52), str(work["author"]).upper(), font=_font(15), fill=ink, anchor="mm")
    lines = textwrap.wrap(str(work["title"]), 14) or [""]
    top = 225 - len(lines) * 17
    for number, line in enumerate(lines[:5]):
        draw.text((150, top + number * 34), line, font=_font(30), fill=ink, anchor="mm")
    if work.get("series"):
        label = f"{work['series']} {work.get('seriesNo') or ''}".strip()[:28]
        draw.text((150, 400), label, font=_font(13), fill=ink, anchor="mm")
    image.save(out, "PNG")
    return out.getvalue()


def invented_isbn(counter: int) -> str:
    """A valid ISBN-13 of the range the tests use for invented books, one for every number: books made here must not be
    taken for one book because a maker gave all of them the same number."""
    base = f"9783999{counter:05d}"
    check = (10 - sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(base)) % 10) % 10
    return base + str(check)


def make_ebook(folder: Path, work: dict[str, Any], edition: dict[str, Any], first: bool, isbn: str) -> Path:
    language = LANGUAGE.get(edition.get("lang", "Deutsch"), "de")
    title = work["title"]
    stem = safe(title) + ("" if first else f" ({language})")
    cover = cover_png(work["color"], work)
    kind = edition.get("format", "EPUB")
    if kind != "EPUB":
        # A PDF or an AZW3 made here carries no subject: the file beside it (Audiobookshelf's) says the genre.
        series = [{"name": work["series"], "sequence": str(work.get("seriesNo"))}] if work.get("series") else []
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "metadata.json").write_text(json.dumps({
            "title": work["title"], "authors": [work["author"]], "genres": [SUBJECT.get(work["genre"], "")],
            "description": work.get("blurb", ""), "language": language, "series": series,
            "publishedYear": str(work.get("year") or "")}), encoding="utf-8")
    if kind == "PDF":
        return bookmaker.make_pdf(folder / f"{stem}.pdf", title=title, author=work["author"],
                                  pages=max(1, int(edition["pages"])))
    if kind == "AZW3":
        return bookmaker.make_mobi(folder / f"{stem}.azw3", title=title, author=work["author"], cover=cover,
                                   isbn=isbn, text_length=int(edition["pages"]) * BYTES_PER_PAGE)
    return bookmaker.make_epub(
        folder / f"{stem}.epub", title=title, authors=(work["author"],), language=language,
        series=work.get("series"), series_pos=work.get("seriesNo"), cover=cover,
        chapters=tuple(f"Kapitel {n}" for n in range(1, CHAPTERS + 1)),
        text_bytes=max(int(edition["pages"]) * BYTES_PER_PAGE, 400), description=work.get("blurb"),
        subjects=(SUBJECT.get(work["genre"], ""),) if SUBJECT.get(work["genre"]) else (),
        date=str(work.get("year") or ""))


def make_recording(folder: Path, work: dict[str, Any], edition: dict[str, Any]) -> Path:
    from mutagen.mp4 import MP4

    variant = edition.get("variant", "ungekürzt")
    place = folder / VARIANT_FOLDER.get(variant, "Hörbuch")
    path = bookmaker.make_m4b(
        place / f"{safe(work['title'])}.m4b", float(edition["minutes"]) * 60, title=work["title"],
        album=work["title"], artist=work["author"], albumartist=work["author"], narrator=edition.get("narrator"),
        series=work.get("series"), series_pos=work.get("seriesNo"), cover=cover_png(work["color"], work))
    # The genre of the shelf is read from the tag of the file (the test maker has none for it).
    audio = MP4(path)
    if audio.tags is not None and SUBJECT.get(work["genre"]):
        audio.tags["©gen"] = [SUBJECT[work["genre"]]]
        audio.save()
    return path


def make_library(works: list[dict[str, Any]], out: Path) -> tuple[int, int]:
    """The files of every work under ``out``, an hour old (a scan leaves a file that was changed a moment ago for the
    next run). Returns the number of e-books and recordings made."""
    out.mkdir(parents=True, exist_ok=True)
    ebooks = recordings = 0
    serial = 0
    for number, work in enumerate(works):
        folder = out / f"{number:04d} {safe(work['title'])}"
        first = True
        for edition in work["fassungen"]:
            if edition["kind"] == "ebook":
                serial += 1
                make_ebook(folder, work, edition, first, invented_isbn(serial))
                first = False
                ebooks += 1
            else:
                make_recording(folder, work, edition)
                recordings += 1
    old = time.time() - 3600
    for path in out.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    return ebooks, recordings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", required=True, type=Path, help="JSON list of works (see above)")
    parser.add_argument("--out", required=True, type=Path, help="the folder to make (inside a library root)")
    parser.add_argument("--drawn-covers", action="store_true",
                        help="draw the author and the title on the covers (for pictures)")
    args = parser.parse_args()
    global DRAWN_COVERS
    DRAWN_COVERS = args.drawn_covers
    works = json.loads(args.spec.read_text(encoding="utf-8"))
    ebooks, recordings = make_library(works, args.out)
    print(f"{len(works)} works: {ebooks} e-books, {recordings} recordings in {args.out}")


if __name__ == "__main__":
    main()
