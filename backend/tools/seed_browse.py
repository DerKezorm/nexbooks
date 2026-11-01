"""Invented shelves for Browse, written straight into a data folder: for looking at the page, for the pictures,
for the tests in a browser. A development tool; it is not part of the image. Nothing asks a source: the books
and the shelves come from a file.

    python tools/seed_browse.py --spec browse-seed.json --data-dir <folder> [--market de] [--on] [--history-only]

The file (everything in it is invented):

    {"books": [{"id": "kri001", "title": "...", "author": "...", "genre": "krimi", "color": "#1f304b", "language": "de",
                "released": "2026-05-02" or "", "year": 2026, "pages": 300 or null, "blurb": "...",
                "source": "apple" or "openlibrary", "award": "Edgar Award" or "", "topics": ["ermittler"],
                "audio_minutes": 1260 (optional)}],
     "shelves": {"krimi": {"new": ["kri001", ...], "popular": [...], "award": [...], "classic": [...]}},
     "history": [{"title": "Die Salzwiese", "days": 40, "finished_on": "2026-10-10"}]}

``history`` is written for the operator (the account the tests sign in with) when the libraries have been
scanned and hold a book of that title. With ``--on`` the sources are switched on (nothing is asked: pair it
with a server that has no background loop). With ``--history-only`` the shelves are left as they are.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--market", default="de")
    parser.add_argument(
        "--on", action="store_true", help="switch the sources on (a server without background loop asks nothing)"
    )
    parser.add_argument(
        "--history-only", action="store_true", help="write the reading history only (the shelves are there already)"
    )
    args = parser.parse_args()
    os.environ["NEXBOOKS_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from sqlalchemy import delete, select

    from app import clock
    from app.db import SessionLocal, init_db
    from app.models import Account, Edition, ForeignBook, HistoryEntry, ShelfEntry, ShelfFetch
    from app.services import genres, settings_service
    from app.services.browse import fetch, shelves
    from app.services.readers.names import title_keys

    init_db()
    spec: dict[str, Any] = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    books: dict[str, dict[str, Any]] = {book["id"]: book for book in spec["books"]}
    now = clock.now()

    with SessionLocal() as db:
        if not args.history_only:
            if args.on:
                settings_service.save(db, {"sources_enabled": True, "source_hardcover": False, "source_audible": False})
            db.execute(delete(ShelfEntry).where(ShelfEntry.market == args.market))
            rows: dict[str, ForeignBook] = {}
            for book_id, book in books.items():
                ref = f"{book['source']}:{book_id}"[:80]
                released = book.get("released") or ""
                if released.startswith("+") and released.endswith("d"):
                    # A day after the day of the run ("+90d"): a book to come stays one to come.
                    released = (now + timedelta(days=int(released[1:-1]))).date().isoformat()
                forms = [
                    {
                        "kind": "ebook",
                        "variant": "",
                        "language": "",
                        "minutes": None,
                        "narrators": [],
                        "pages": book.get("pages"),
                        "source": book["source"],
                    }
                ]
                if book.get("audio_minutes"):
                    forms.append(
                        {
                            "kind": "audio",
                            "variant": "unabridged",
                            "language": book.get("language", "de"),
                            "minutes": book["audio_minutes"],
                            "narrators": [],
                            "pages": None,
                            "source": "audible",
                        }
                    )
                found = db.scalar(select(ForeignBook).where(ForeignBook.ref == ref))
                if found is None:
                    found = ForeignBook(
                        token=secrets.token_urlsafe(14)[:18],
                        ref=ref,
                        title=book["title"][:300],
                        title_key=title_keys(book["title"])[0][:300],
                        authors=[book["author"]],
                        released=released,
                        language=book.get("language", "de") if book["source"] == "openlibrary" else "",
                        genre=book.get("genre", "") or genres.genre_of([]),
                        series="",
                        series_pos=None,
                        cover_url="",
                        cover="",
                        color=book.get("color", "") or genres.palette_color(book.get("genre", ""), book_id),
                        data={
                            "description": book.get("blurb", ""),
                            "text_at": now.isoformat() if book.get("blurb") else None,
                            "isbns": [],
                            "asin": "",
                            "year": book.get("year"),
                            "subjects": [],
                            "publisher": "",
                            "forms": forms,
                            "markets": [args.market],
                            "pages": book.get("pages"),
                        },
                        enriched_at=now,
                        seen_at=now,
                        created_at=now,
                    )
                    db.add(found)
                    db.flush()
                rows[book_id] = found
            for shelf_key, sections in spec["shelves"].items():
                if shelf_key not in shelves.BY_KEY:
                    continue
                ranks: dict[tuple[str, str], int] = {}
                for section, ids in sections.items():
                    for book_id in ids:
                        book = books[book_id]
                        source = book["source"]
                        ranks[(source, section)] = ranks.get((source, section), 0) + 1
                        db.add(
                            ShelfEntry(
                                source=source,
                                market=args.market,
                                shelf=shelf_key,
                                section=section,
                                rank=ranks[(source, section)],
                                foreign_id=rows[book_id].id,
                                award=book.get("award", "") if section == "award" else "",
                                topics=book.get("topics", []),
                                fetched_at=now,
                            )
                        )
            for source, shelf in fetch.units(args.market):
                log = db.get(ShelfFetch, (source, args.market, shelf)) or ShelfFetch(
                    source=source, market=args.market, shelf=shelf
                )
                log.state, log.count, log.error, log.fetched_at, log.wanted_at = "ok", 1, "", now, now
                db.merge(log)
            db.commit()

        operator = db.scalar(select(Account).where(Account.name == "tester"))
        made = 0
        if operator is not None:
            for entry in spec.get("history", []):
                edition = db.scalars(select(Edition).where(Edition.title == entry["title"])).first()
                if edition is None:
                    continue
                already = db.scalar(
                    select(HistoryEntry.id).where(
                        HistoryEntry.user_id == operator.id,
                        HistoryEntry.edition_id == edition.id,
                        HistoryEntry.finished_on == entry["finished_on"],
                    )
                )
                if already is None:
                    db.add(
                        HistoryEntry(
                            user_id=operator.id,
                            edition_id=edition.id,
                            kind="read",
                            finished_on=entry["finished_on"],
                            source="auto",
                            days=entry.get("days"),
                        )
                    )
                    made += 1
            db.commit()
        count = len(db.scalars(select(ForeignBook.id)).all())
    print(f"books {count}, shelves {len(spec['shelves'])}, history {made if operator is not None else 'none'}")


if __name__ == "__main__":
    main()
