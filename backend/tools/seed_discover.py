"""Invented lists for Discover, written straight into a data folder: for looking at the page, for the pictures, for the tests
in a browser. A development tool; it is not part of the image. Nothing asks a source: the books, the lists, the gaps in a
series and the books of an author all come from a file.

    python tools/seed_discover.py --spec seed.json --data-dir <folder> [--market de] [--on]

The file (everything in it is invented):

    {"books": [{"id": "b1", "title": "...", "author": "...", "genre": "krimi", "color": "#1f304b", "language": "de",
                "released": "2026-05-02", "series": "..." or null, "seriesNo": 2 or null, "pages": 300,
                "pattern": 0..4 (the drawn cover; optional), "blurb": "...",
                "forms": [{"kind": "ebook", "language": "de", "pages": 300},
                          {"kind": "audio", "variant": "unabridged", "language": "de", "minutes": 540, "narrator": "..."}]}],
     "lists": {"best": [{"book": "b1", "rank": 1}], "new": [...], "soon": [...], "trending": [...]},
     "series": [{"name": "...", "volumes": [{"book": "b7", "pos": 3}]}],
     "authors": [{"name": "...", "books": ["b2", "b3"]}]}

The libraries must be scanned already when ``series`` or ``authors`` are given (the series and the people are looked up by
name). A book with an e-book form is a row of Apple's chart (``apple:``), one with a recording a row of Audible's
(``audible:``), one in ``trending`` a row of Hardcover's; the page puts the rows of one book together, as it does for the real
ones. With ``--on`` the sources are switched on (nothing is asked: pair it with a server that has no background loop).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def token_for(pattern: int | None, ref: str) -> str:
    """A token (as the server makes them) whose drawn cover has the pattern asked for: the interface takes the pattern of a
    book that is not on a shelf from its token (``lib/books.ts``, ``patternOfToken``), and the same
    pattern keeps the pictures comparable."""
    import secrets

    def pattern_of(token: str) -> int:
        value = 0
        for char in token:
            value = (value * 31 + ord(char)) & 0xFFFFFFFF
        return value % 5

    while True:
        token = secrets.token_urlsafe(14)[:18]
        if pattern is None or pattern_of(token) == pattern:
            return token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--market", default="de")
    parser.add_argument("--today", default="")
    parser.add_argument("--on", action="store_true", help="switch the sources on (a server without background loop asks nothing)")
    args = parser.parse_args()
    os.environ["NEXBOOKS_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from sqlalchemy import delete, select

    from app import clock
    from app.db import SessionLocal, init_db
    from app.models import ForeignBook, ListEntry, ListFetch, Person, Series
    from app.services import genres, settings_service
    from app.services.discover import foryou
    from app.services.readers.names import title_keys
    from app.services.sources import cache

    init_db()
    spec: dict[str, Any] = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    books: dict[str, dict[str, Any]] = {book["id"]: book for book in spec["books"]}
    now = clock.now()
    today = args.today or now.date().isoformat()
    rows: dict[tuple[str, str], int] = {}

    def row_for(db: Any, book_id: str, source: str) -> ForeignBook:
        """The row of a book at a source (made once)."""
        book = books[book_id]
        ref = f"{source}:{book_id.replace('-', '')}"[:80]
        found = db.scalar(select(ForeignBook).where(ForeignBook.ref == ref))
        if found is not None:
            return found
        forms = []
        for form in book.get("forms", []):
            if (source == "audible") != (form["kind"] == "audio") and source != "hardcover":
                continue
            language = form.get("language", book.get("language", "de"))
            if form["kind"] == "audio":
                variant = {"ungekürzt": "unabridged", "gekürzt": "abridged", "Hörspiel": "dramatized"}.get(
                    form.get("variant", ""), form.get("variant", "unabridged"))
                forms.append({"kind": "audio", "variant": variant, "language": language, "minutes": form.get("minutes"),
                              "narrators": [form["narrator"]] if form.get("narrator") else [], "pages": None, "source": source})
            else:
                forms.append({"kind": "ebook", "variant": "", "language": language, "minutes": None, "narrators": [],
                              "pages": form.get("pages"), "source": source})
        released = book.get("released") or ""
        found = ForeignBook(
            token=token_for(book.get("pattern"), ref), ref=ref, title=book["title"][:300],
            title_key=title_keys(book["title"])[0][:300], authors=[book["author"]] if book.get("author") else [],
            released=released, language=book.get("language", "de"), genre=book.get("genre", ""),
            series=book.get("series") or "", series_pos=book.get("seriesNo"), cover_url="", cover="",
            color=book.get("color", "") or genres.palette_color(book.get("genre", ""), book_id),
            data={"description": book.get("blurb", ""), "isbns": [], "asin": "", "year": int(released[:4]) if released[:4].isdigit() else None,
                  "subjects": [], "publisher": "", "forms": forms, "markets": [args.market], "pages": book.get("pages")},
            enriched_at=now, seen_at=now, created_at=now)
        db.add(found)
        db.flush()
        rows[(book_id, source)] = found.id
        return found

    with SessionLocal() as db:
        if args.on:
            settings_service.save(db, {"sources_enabled": True, "source_hardcover": False})
        db.execute(delete(ListEntry))
        for list_name, entries in spec.get("lists", {}).items():
            unit = {"best": "best", "new": "best", "soon": "release", "trending": "trending"}[list_name]
            for entry in entries:
                book = books[entry["book"]]
                kinds = {form["kind"] for form in book.get("forms", [])} or {"ebook"}
                rows_of = ["hardcover"] if list_name == "trending" else [
                    s for s, k in (("apple", "ebook"), ("audible", "audio")) if k in kinds]
                for source in rows_of:
                    found = row_for(db, entry["book"], source)
                    # New and coming books are Audible's list (the page asks nobody else for them), whatever the book is.
                    listed = "audible" if list_name in ("new", "soon") else source
                    db.add(ListEntry(
                        source=listed, market="" if listed == "hardcover" else args.market, list=list_name, rank=entry["rank"],
                        ref=found.ref, foreign_id=found.id, title=found.title[:300], author=", ".join(found.authors or [])[:300],
                        cover_url="", released=found.released, kind="audio" if listed == "audible" else "ebook",
                        language=found.language, genre=found.genre, fetched_at=now))
                    if list_name in ("new", "soon"):
                        break
        for source, unit in (("apple", "best"), ("audible", "best"), ("audible", "release")):
            log = db.get(ListFetch, (source, args.market, unit)) or ListFetch(source=source, market=args.market, unit=unit)
            log.state, log.count, log.error, log.fetched_at, log.wanted_at = "ok", 1, "", now, now
            db.merge(log)
        db.commit()

        for entry in spec.get("series", []):
            series = db.scalar(select(Series).where(Series.name == entry["name"]))
            if series is None:
                print("no series", entry["name"])
                continue
            volumes = []
            for volume in entry["volumes"]:
                found = row_for(db, volume["book"], "hardcover")
                volumes.append({"fid": found.id, "pos": volume["pos"], "released": volume.get("released") or found.released})
            db.commit()
            cache.put(foryou.CACHE, foryou.series_key(series.id), {"source": "hardcover", "volumes": volumes, "fids": []},
                      hit=True, days=365)
        for entry in spec.get("authors", []):
            person = db.scalar(select(Person).where(Person.name == entry["name"]))
            if person is None:
                print("no person", entry["name"])
                continue
            fids = [row_for(db, book_id, "hardcover").id for book_id in entry["books"]]
            db.commit()
            cache.put(foryou.CACHE, foryou.author_key(person.id), {"source": "hardcover", "volumes": [], "fids": fids},
                      hit=True, days=365)
        db.commit()
        # Every other series and person is known to have nothing: the page does not wait for a source that is never asked.
        for series in db.scalars(select(Series)):
            if cache.get(foryou.CACHE, foryou.series_key(series.id)) is cache.MISSING:
                cache.put(foryou.CACHE, foryou.series_key(series.id), {"source": "hardcover", "volumes": [], "fids": []}, hit=True, days=365)
        for person in db.scalars(select(Person)):
            if cache.get(foryou.CACHE, foryou.author_key(person.id)) is cache.MISSING:
                cache.put(foryou.CACHE, foryou.author_key(person.id), {"source": "hardcover", "volumes": [], "fids": []}, hit=True, days=365)
        count = len(db.scalars(select(ForeignBook.id)).all())
    print(f"books {count}, lists {sum(len(v) for v in spec.get('lists', {}).values())}, today {today}")


if __name__ == "__main__":
    main()
