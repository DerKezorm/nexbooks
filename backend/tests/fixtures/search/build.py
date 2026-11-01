"""Writes the answers of the sources to the searches of the catalogue search (``answers.json`` next to this file), in
the shapes of the real services: Open Library's ``search.json`` with the edition in the language asked for, the records
of the German National Library (rendered to MARC XML by the test's stand-in), Audible's products, Hardcover's search.

The shapes and what made trouble were recorded from the services; every author, title and number here is
invented. What each search holds, and what it shows:

* **Ada Sorrel**, ``The Lantern Saga`` (English, translated into German): 1 ``The Lantern Road`` / ``Die
  Laternenstraße``, 2 ``The Salt Gate`` / ``Das Salztor``, 3 ``The Last Ember`` / ``Die letzte Glut``. Around them a
  box, a play of volume 2 with a full cast, a study guide, a calendar, a recording of volume 3 in two parts, a German
  record of volume 2 that names the original title of volume 1 (a library's uniform title of the series), a
  recording read by a narrator whose name stands in front of the title, the volume written behind the title by a
  shop, an Open Library edition that carries only the name of the series, a graphic novel by another author with her
  as the second name, and the music of a film of the same title by somebody else.
* **Ilse Marbach**, ``Der stille Garten`` (German, 1961), translated into four languages.
* **Jonas Brehm**, ``Das Moorhaus``: the novel, the recording of it, and a play of it.
* ``Die Brücke`` by **Klara Wendt** (well known) and by **Paul Ostrander** (one record).
* **Hanne Voss**, ``Pim und der Leuchtturm`` (for children).
* **J. K. Ash** (``Joanna K. Ash`` at Audible), ``The Copper Bell`` / ``Die Kupferglocke``.
* **Wren Calloway**, ``Salt & Ashes`` (``Salt and Ashes`` at the shop).
* **Odile Ferrand**, ``La Maison du Phare``: French only.

    python tests/fixtures/search/build.py      (from the backend folder) writes ``answers.json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


def author_key(name: str) -> str:
    """The number of a person at Open Library: one for each name (two people never share one)."""
    return f"OL{int(hashlib.sha1(name.encode()).hexdigest()[:6], 16)}A"


def ol_doc(key: str, title: str, authors: list[str], year: int, languages: list[str], *, editions: int = 10,
           edition: tuple[str, str, str] | None = None, isbns: tuple[str, ...] = (), subjects: tuple[str, ...] = (),
           cover: int | None = None, pages: int | None = None) -> dict[str, Any]:
    """A work of Open Library; ``edition``: ``(edition key, title, language)`` of the edition in the language asked
    for."""
    doc: dict[str, Any] = {"key": f"/works/{key}", "title": title, "author_name": authors,
                           "author_key": [author_key(name) for name in authors],
                           "first_publish_year": year, "language": languages, "edition_count": editions,
                           "subject": list(subjects or ("Fiction",))}
    if isbns:
        doc["isbn"] = list(isbns)
    if cover:
        doc["cover_i"] = cover
    if pages:
        doc["number_of_pages_median"] = pages
    if edition is not None:
        ident, text, language = edition
        doc["editions"] = {"numFound": 1, "start": 0, "numFoundExact": True,
                           "docs": [{"key": f"/books/{ident}", "title": text, "language": [language],
                                     "isbn": list(isbns[:1]), "publish_date": str(year)}]}
    return doc


def product(asin: str, title: str, authors: list[str], minutes: int | None, *, language: str = "german",
            series: str = "", sequence: str = "", subtitle: str = "", kind: str = "unabridged",
            narrators: tuple[str, ...] = ("Ben Hollister",), released: str = "2019-03-01") -> dict[str, Any]:
    """A product of Audible."""
    item: dict[str, Any] = {"asin": asin, "title": title, "authors": [{"name": name} for name in authors],
                            "narrators": [{"name": name} for name in narrators], "format_type": kind,
                            "language": language, "release_date": released}
    if minutes:
        item["runtime_length_min"] = minutes
    if subtitle:
        item["subtitle"] = subtitle
    if series:
        item["series"] = [{"title": series, "sequence": sequence}]
    return item


def record(ident: str, title: str, author: str, year: int, *, subtitle: str = "", language: str = "ger",
           translated: str = "", original: str = "", isbn: str = "", series: str = "", position: str = "",
           audio: bool = False, group: str = "B", pages: int | None = None, part_number: str = "",
           part_name: str = "", made_by: str = "", gnd: str = "", whole: str = "") -> dict[str, Any]:
    """A record of the German National Library (``author`` as the library writes it: ``Last, First``). The rest as
    the library writes it: the number and the name of a part (245 ``$n``, ``$p``) of a book or a series whose record
    is the whole (773), who made it (245 ``$c``), the authority number of the author (``$0``), the series as the title
    of the whole (800 ``$t``)."""
    return {"id": ident, "title": title, "subtitle": subtitle, "author": author, "year": year, "language": language,
            "translated": translated, "original": original, "isbn": isbn, "series": series, "position": position,
            "audio": audio, "group": group, "pages": pages, "part_number": part_number, "part_name": part_name,
            "made_by": made_by, "gnd": gnd, "whole": whole}


SORREL = "Ada Sorrel"
SAGA = "The Lantern Saga"
SAGA_DE = "Die Laternen-Saga"
LANTERN_OL = [
    ol_doc("OL7001W", "The Lantern Road", [SORREL], 2012, ["eng", "ger"], editions=210,
           edition=("OL7001M", "Die Laternenstraße", "ger"), isbns=("9780000070011", "9783000070011", "9783000070028"),
           cover=7001, pages=420),
    ol_doc("OL7002W", "The Salt Gate", [SORREL], 2014, ["eng", "ger"], editions=150,
           edition=("OL7002M", "Das Salztor", "ger"), isbns=("9780000070035", "9783000070042"), cover=7002, pages=460),
    ol_doc("OL7003W", "The Last Ember", [SORREL], 2016, ["eng", "ger"], editions=120,
           edition=("OL7003M", "Die Laternen-Saga", "ger"), isbns=("9780000070059",), cover=7003, pages=510),
    ol_doc("OL7004W", "The Lantern Saga 1-3 Boxed Set", [SORREL], 2017, ["eng"], editions=4),
    ol_doc("OL7005W", "The Lantern Road", ["Finn Hale", SORREL], 2020, ["eng"], editions=2,
           subjects=("Comics & graphic novels",)),
    ol_doc("OL7006W", "The Salt Gate", ["Theo Brand"], 2015, ["eng"], editions=3,
           subjects=("Motion picture music",)),
]
LANTERN_DNB = [
    record("70101", "Die Laternenstraße", "Sorrel, Ada", 2013, translated="eng", original="The lantern road",
           isbn="9783000070011", series=SAGA_DE, position="1", pages=430),
    record("70102", "Die Laternenstraße", "Sorrel, Ada", 2015, translated="eng", original="The lantern road",
           isbn="9783000070028", series=SAGA_DE, position="1", pages=430),
    record("70103", "Die Laternen-Saga 2. Das Salztor", "Sorrel, Ada", 2015, translated="eng",
           original="The lantern road", isbn="9783000070042", series=SAGA_DE, position="2", pages=470),
    record("70104", "Das Salztor", "Sorrel, Ada", 2016, subtitle="Hörspiel", translated="eng", series=SAGA_DE,
           position="2", audio=True),
    record("70105", "Ada Sorrels Das Salztor", "Brück, Lena", 2018, subtitle="Lektürehilfe", group="830"),
    record("70106", "Das Salztor (Wandkalender 2027 DIN A3 hoch)", "Sorrel, Ada", 2026),
    record("70107", "Die letzte Glut", "Sorrel, Ada", 2017, translated="eng", original="The last ember",
           series=SAGA_DE, position="3", pages=520),
]
LANTERN_AUDIBLE_DE = [
    product("B070000001", "Die Laternenstraße", [SORREL], 840, series=SAGA_DE, sequence="1", subtitle="Laternen-Saga 1"),
    product("B070000002", "Mara Quell liest Das Salztor", [SORREL], 900, series=SAGA_DE, sequence="2",
            narrators=("Mara Quell",)),
    product("B070000003", "Das Salztor", [SORREL], 120, series="Die Laternen-Saga (Hörspiele mit voller Besetzung)",
            sequence="2", narrators=("Ensemble",)),
    product("B070000004", "Die letzte Glut (1/2)", [SORREL], 480, series=SAGA_DE, sequence="3"),
    product("B070000005", "Die letzte Glut (2/2)", [SORREL], 470, series=SAGA_DE, sequence="3"),
    product("B070000006", "Le portail de sel", [SORREL], 880, language="french", series="La Saga des lanternes",
            sequence="2"),
]
LANTERN_AUDIBLE_US = [
    product("B070000011", "The Salt Gate, Book 2", [SORREL], 890, language="english", series=SAGA, sequence="2"),
    product("B070000012", "The Lantern Road", [SORREL], 830, language="english", series=SAGA, sequence="1"),
]

MARBACH = "Ilse Marbach"
GARDEN_OL = [ol_doc("OL7101W", "Der stille Garten", [MARBACH], 1961, ["ger", "eng", "fre", "spa", "ita"],
                    editions=320, edition=("OL7101M", "Der stille Garten", "ger"), cover=7101,
                    isbns=("9783000071018", "9780000071019"))]
GARDEN_OL_EN = [ol_doc("OL7101W", "Der stille Garten", [MARBACH], 1961, ["ger", "eng", "fre", "spa", "ita"],
                       editions=320, edition=("OL7102M", "The Quiet Garden", "eng"), cover=7101,
                       isbns=("9783000071018", "9780000071019"))]
GARDEN_DNB = [record("71101", "Der stille Garten", "Marbach, Ilse", 1961, isbn="9783000071018"),
              record("71102", "Der stille Garten", "Marbach, Ilse", 2001, subtitle="Roman", isbn="9783000071025")]
GARDEN_AUDIBLE_DE = [
    product("B071000001", "Der stille Garten", [MARBACH], 410),
    product("B071000002", "Le jardin silencieux", [MARBACH], 400, language="french"),
    product("B071000003", "El jardín silencioso", [MARBACH], 395, language="spanish"),
    product("B071000004", "Il giardino silenzioso", [MARBACH], 405, language="italian"),
]

BREHM = "Jonas Brehm"
MOOR_OL = [ol_doc("OL7201W", "Das Moorhaus", [BREHM], 2019, ["ger"], editions=40, cover=7201,
                  edition=("OL7201M", "Das Moorhaus", "ger"), isbns=("9783000072015",))]
MOOR_DNB = [record("72101", "Das Moorhaus", "Brehm, Jonas", 2019, subtitle="Kriminalroman", isbn="9783000072015",
                   series="Ein Fall für Kommissar Lund", position="1"),
            record("72102", "Das Moorhaus", "Brehm, Jonas", 2019, subtitle="ungekürzte Lesung", audio=True,
                   series="Ein Fall für Kommissar Lund", position="1")]
MOOR_AUDIBLE_DE = [
    product("B072000001", "Das Moorhaus", [BREHM], 612, series="Kommissar Lund", sequence="1"),
    product("B072000002", "Das Moorhaus - Das Hörspiel", [BREHM], 95, series="Kommissar Lund Hörspiele",
            sequence="1", narrators=("Ensemble",)),
]

BRIDGE_OL = [ol_doc("OL7301W", "Die Brücke", ["Klara Wendt"], 2008, ["ger"], editions=60, cover=7301,
                    edition=("OL7301M", "Die Brücke", "ger"), isbns=("9783000073012",)),
             ol_doc("OL7302W", "Die Brücke", ["Paul Ostrander"], 1999, ["ger"], editions=1)]
BRIDGE_DNB = [record("73101", "Die Brücke", "Wendt, Klara", 2008, subtitle="Roman", isbn="9783000073012")]
BRIDGE_AUDIBLE_DE = [product("B073000001", "Die Brücke", ["Klara Wendt"], 520)]

PIM_OL = [ol_doc("OL7401W", "Pim und der Leuchtturm", ["Hanne Voss"], 2011, ["ger"], editions=12,
                 edition=("OL7401M", "Pim und der Leuchtturm", "ger"), subjects=("Juvenile fiction",), pages=48)]
PIM_DNB = [record("74101", "Pim und der Leuchtturm", "Voss, Hanne", 2011, group="K", pages=48)]

ASH_OL = [ol_doc("OL7501W", "The Copper Bell", ["J. K. Ash"], 2018, ["eng", "ger"], editions=55,
                 edition=("OL7501M", "Die Kupferglocke", "ger"), isbns=("9783000075016",))]
ASH_DNB = [record("75101", "Die Kupferglocke", "Ash, J. K.", 2019, translated="eng", original="The copper bell",
                  isbn="9783000075016")]
ASH_AUDIBLE_DE = [product("B075000001", "Die Kupferglocke", ["Joanna K. Ash"], 700)]

SALT_OL = [ol_doc("OL7601W", "Salt & Ashes", ["Wren Calloway"], 2021, ["eng"], editions=30)]
SALT_AUDIBLE_US = [product("B076000001", "Salt and Ashes", ["Wren Calloway"], 640, language="english")]

PHARE_OL = [ol_doc("OL7701W", "La Maison du Phare", ["Odile Ferrand"], 1998, ["fre"], editions=25,
                   subjects=("Roman",))]
PHARE_AUDIBLE_DE = [product("B077000001", "La Maison du Phare", ["Odile Ferrand"], 520, language="french")]

#: Each search: what it is typed as, and what each source answers to it (Open Library by the language asked for).
SEARCHES: list[dict[str, Any]] = [
    {"q": "Laternenstraße Sorrel", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": LANTERN_AUDIBLE_US}},
    {"q": "lantern saga 2", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": LANTERN_AUDIBLE_US}},
    {"q": "Das Salztor", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": LANTERN_AUDIBLE_US}},
    {"q": "The Salt Gate", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": LANTERN_AUDIBLE_US}},
    {"q": "Lantern Saga Box", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": LANTERN_AUDIBLE_US}},
    {"q": "Salztor Lektürehilfe", "ol": {}, "dnb": LANTERN_DNB, "audible": {"de": LANTERN_AUDIBLE_DE}},
    {"q": "Die letzte Glut", "ol": {"de": LANTERN_OL, "en": LANTERN_OL}, "dnb": LANTERN_DNB,
     "audible": {"de": LANTERN_AUDIBLE_DE, "us": []}},
    {"q": "Der stille Garten", "ol": {"de": GARDEN_OL, "en": GARDEN_OL_EN}, "dnb": GARDEN_DNB,
     "audible": {"de": GARDEN_AUDIBLE_DE, "us": []}},
    {"q": "Das Moorhaus", "ol": {"de": MOOR_OL, "en": MOOR_OL}, "dnb": MOOR_DNB,
     "audible": {"de": MOOR_AUDIBLE_DE, "us": []}},
    {"q": "Die Brücke", "ol": {"de": BRIDGE_OL, "en": BRIDGE_OL}, "dnb": BRIDGE_DNB,
     "audible": {"de": BRIDGE_AUDIBLE_DE, "us": []}},
    {"q": "Pim und der Leuchtturm", "ol": {"de": PIM_OL, "en": PIM_OL}, "dnb": PIM_DNB,
     "audible": {"de": [], "us": []}},
    {"q": "J. K. Ash Kupferglocke", "ol": {"de": ASH_OL, "en": ASH_OL}, "dnb": ASH_DNB,
     "audible": {"de": ASH_AUDIBLE_DE, "us": []}},
    {"q": "Salt and Ashes", "ol": {"de": SALT_OL, "en": SALT_OL}, "dnb": [],
     "audible": {"de": [], "us": SALT_AUDIBLE_US}},
    {"q": "La Maison du Phare", "ol": {"de": PHARE_OL, "en": PHARE_OL}, "dnb": [],
     "audible": {"de": PHARE_AUDIBLE_DE, "us": []}},
    {"q": "The Copper Bell", "ol": {"de": ASH_OL, "en": ASH_OL}, "dnb": ASH_DNB,
     "audible": {"de": ASH_AUDIBLE_DE, "us": []}},
]


def main() -> None:
    """Every answer once (``sets``, by its name here), and each search naming the answers it gets."""
    sets: dict[str, Any] = {}
    names = {id(value): name for name, value in globals().items() if name.isupper() and isinstance(value, list)}

    def ref(value: list[Any]) -> str:
        name = names.get(id(value))
        if name is None:
            raise ValueError("every answer is a named list")
        sets[name] = value
        return name

    searches = []
    for search in SEARCHES:
        searches.append({"q": search["q"], "ol": {code: ref(docs) for code, docs in search["ol"].items()},
                         "dnb": ref(search["dnb"]) if search["dnb"] else "",
                         "audible": {code: ref(items) for code, items in search["audible"].items() if items}})
    (HERE / "answers.json").write_text(json.dumps({"sets": sets, "searches": searches}, ensure_ascii=False,
                                                  indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
