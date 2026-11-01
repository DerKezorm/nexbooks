"""Invented catalogues of authors' works, written straight into a data folder: for the tests in a browser and
for looking at the page. A development tool; it is not part of the image. Nothing asks a source.

    python tools/seed_catalog.py --spec catalog.json --data-dir <folder> [--out tokens.json]

The file (everything in it is invented):

    {"authors": [{"name": "...", "orig": "en", "aliases": ["..."],
                  "born": "1951",
                  "works": [{"key": "salt", "kind": "roman", "why": "", "pen": "", "series": "", "pos": null,
                             "color": "#334455", "pattern": 0..4,
                             "titles": {"en": {"title": "...", "year": 1974, "released": "", "ebook": true,
                                               "audio": false},
                                        "de": {...}},
                             "sources": ["openlibrary", "dnb"]}]}]}

An author whose name is a person of the libraries is linked to that person (scan the libraries first). The catalogue
counts as built now, so that the server does not build it again for a day. ``--out`` writes the tokens of the authors
and of their works (``{"Name": {"token": ..., "works": {"salt": token}}}``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def token_for(pattern: int | None) -> str:
    """A token whose drawn cover has the pattern asked for (``lib/books.ts``, ``patternOfToken``)."""
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
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    os.environ["NEXBOOKS_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from sqlalchemy import delete

    from app import clock
    from app.db import SessionLocal, init_db
    from app.models import CatalogWork, ForeignBook
    from app.services.discover import foreign
    from app.services.oeuvre import store
    from app.services.readers.names import title_keys

    init_db()
    spec: dict[str, Any] = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    now = clock.now()
    with SessionLocal() as db:
        for index, author in enumerate(spec["authors"]):
            row = store.for_name(db, author["name"])
            assert row is not None, author["name"]
            db.execute(delete(CatalogWork).where(CatalogWork.catalog_person_id == row.id))
            by_source: dict[str, dict[str, int]] = {}
            tokens: dict[str, str] = {}
            for number, item in enumerate(author["works"]):
                titles = item["titles"]
                orig = item.get("orig") or author["orig"]
                first = titles.get(orig) or next(iter(titles.values()))
                ref = f"openlibrary:OLE2E{index:02d}{number:03d}W"
                book = db.query(ForeignBook).filter_by(ref=ref).one_or_none()
                if book is None:
                    book = ForeignBook(token=token_for(item.get("pattern")), ref=ref, seen_at=now, created_at=now)
                    db.add(book)
                forms = []
                for language, edition in titles.items():
                    if edition.get("ebook", True):
                        forms.append({"kind": "ebook", "variant": "", "language": language, "minutes": None,
                                      "narrators": [], "pages": item.get("pages"), "source": "openlibrary"})
                    if edition.get("audio"):
                        forms.append({"kind": "audio", "variant": "unabridged", "language": language,
                                      "minutes": item.get("minutes"), "narrators": [], "pages": None,
                                      "source": "openlibrary"})
                book.title = first["title"]
                book.title_key = title_keys(first["title"])[0][:300]
                book.authors = [author["name"]]
                book.language = orig
                book.released = first.get("released") or ""
                book.genre = item.get("genre", author.get("genre", ""))
                book.series = item.get("series") or ""
                book.series_pos = item.get("pos")
                book.color = item.get("color", "#3b4a5c")
                book.data = {"titles": [{"text": e["title"], "language": code} for code, e in titles.items()],
                             "forms": foreign.merge_forms([], forms), "year": first.get("year")}
                db.flush()
                sources = item.get("sources") or ["openlibrary", "dnb"]
                for source in sources:
                    by_source.setdefault(source, {"works": 0})["works"] += 1
                db.add(CatalogWork(
                    catalog_person_id=row.id, key=item["key"], foreign_id=book.id, kind=item.get("kind", "roman"),
                    why=item.get("why", ""), pen=item.get("pen", ""), series=item.get("series") or "",
                    series_pos=item.get("pos"), year=first.get("year"), orig_language=orig,
                    titles={code: {"title": e["title"], "year": e.get("year"), "released": e.get("released", ""),
                                   "ebook": e.get("ebook", True), "audio": e.get("audio", False), "sources": sources}
                            for code, e in titles.items()},
                    sources=sources, isbns=[], refs=[ref], pages=item.get("pages"), minutes=item.get("minutes")))
                tokens[item["key"]] = book.token
            row.aliases = author.get("aliases", [])
            row.orig_language = author["orig"]
            row.born = author.get("born", "")
            row.languages = sorted({code for item in author["works"] for code in item["titles"]})
            row.sources = {"by": by_source, "away": [], "answered": {code: sorted(by_source) for code in row.languages}}
            row.state = "ready"
            row.built_at = now
            out[author["name"]] = {"token": row.token, "works": tokens}
        db.commit()
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
