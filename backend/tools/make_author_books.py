"""Invented libraries for the end to end tests of the authors and of tidying. A development tool; it is not
part of the image. Nothing here is real: the names, the books and the portraits are made up.

    python tools/make_author_books.py --out <folder>

makes, below the folder,

* ``authors/``: a library laid out by author, as many people keep one. Ada Feldkamp has a photo called ``author.jpg``
  and three volumes of a series (one of them with a recording too), Ben Hollister a photo called ``folder.jpg`` and a
  book, Cy Dorn no photo and a book (and a book with no author folder at all, to see the circle of initials);
* ``tidy/``: a few loose files in no order, for tidying: an e-book in two formats, a book with a cover beside it, and a
  recording of two parts with its cover.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from tests import bookmaker


def portrait(color: tuple[int, int, int]) -> bytes:
    """A made-up face: a field of ``color`` with a round head and shoulders. Nobody."""
    image = Image.new("RGB", (480, 600), color)
    draw = ImageDraw.Draw(image)
    shade = tuple(max(0, part - 70) for part in color)
    draw.ellipse([150, 120, 330, 330], fill=(224, 180, 150))
    draw.ellipse([60, 360, 420, 760], fill=shade)
    draw.ellipse([205, 205, 228, 228], fill=(40, 30, 20))
    draw.ellipse([252, 205, 275, 228], fill=(40, 30, 20))
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def age(folder: Path, seconds: float = 3600) -> None:
    """Every file looks an hour old: the scan leaves a file that changed in the last minute for the next run."""
    for path in folder.rglob("*"):
        if path.is_file():
            stamp = path.stat().st_mtime - seconds
            os.utime(path, (stamp, stamp))


def make(out: Path) -> None:
    authors = out / "authors"
    ada = authors / "Ada Feldkamp"
    for number, (title, pages) in enumerate((("Alpha", 90_000), ("Beta", 60_000), ("Gamma", 30_000)), start=1):
        bookmaker.make_epub(ada / f"Harbour Days {number} - {title}" / f"{title.lower()}.epub", title=title,
                            authors=("Ada Feldkamp",), language="en", series="Harbour Days", series_pos=number,
                            text_bytes=pages, date=f"{2010 + number}-04-01")
    bookmaker.make_m4b(ada / "Harbour Days 1 - Alpha" / "audio" / "alpha.m4b", 5400.0, title="Alpha", album="Alpha",
                       artist="Ada Feldkamp", narrator="Rune Falkner")
    (ada / "author.jpg").parent.mkdir(parents=True, exist_ok=True)
    (ada / "author.jpg").write_bytes(portrait((70, 110, 150)))
    ben = authors / "Ben Hollister"
    bookmaker.make_epub(ben / "Delta Station" / "delta.epub", title="Delta Station", authors=("Ben Hollister",),
                        language="en", text_bytes=45_000, date="2016")
    (ben / "folder.jpg").write_bytes(portrait((150, 100, 70)))
    bookmaker.make_epub(authors / "Cy Dorn" / "Epsilon" / "epsilon.epub", title="Epsilon", authors=("Cy Dorn",),
                        language="de", text_bytes=25_000, date="2019")
    bookmaker.make_epub(authors / "Loose" / "zeta.epub", title="Zeta", authors=("Dee Kranich",), language="en",
                        text_bytes=20_000)

    tidy = out / "tidy"
    bookmaker.make_epub(tidy / "Mara Lindqvist - The Quiet Orchard.epub", title="The Quiet Orchard",
                        authors=("Mara Lindqvist",), language="en", series="Orchard Days", series_pos=1,
                        date="2011-03-04")
    bookmaker.make_pdf(tidy / "Mara Lindqvist - The Quiet Orchard.pdf", pages=3)
    bookmaker.make_epub(tidy / "Second Orchard" / "second.epub", title="The Loud Orchard", authors=("Mara Lindqvist",),
                        language="en", series="Orchard Days", series_pos=2.5, date="2013")
    (tidy / "Second Orchard" / "cover.jpg").write_bytes(bookmaker.cover_image((90, 60, 40), (300, 450), "JPEG"))
    bookmaker.make_epub(tidy / "single" / "kite.epub", title="Kite Season", authors=("Jonas Brandauer",), language="de")
    for part in (1, 2):
        bookmaker.make_mp3(tidy / "Audio" / "Seven Bridges" / f"{part:02d}.mp3", 12.0, album="Seven Bridges",
                           artist="Ben Hollister", title=f"Part {part}", track=part)
    cover = tidy / "Audio" / "Seven Bridges" / "cover.jpg"
    cover.write_bytes(bookmaker.cover_image((10, 40, 90), (300, 300), "JPEG"))
    age(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path, help="the folder to make (inside a library root)")
    args = parser.parse_args()
    make(args.out)
    print(f"made {args.out}")


if __name__ == "__main__":
    main()
