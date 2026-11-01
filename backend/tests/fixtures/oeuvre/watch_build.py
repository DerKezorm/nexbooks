"""Writes the answers of the sources about the invented author **Ivar Quellbrandt** (English, translated into German):
one work for each question "does watching wish the wrong thing?", in the formats of the real services (the shapes of
``build.py`` next to this file). Every author, title, name and number here is invented. The tests live on 12 October
2026 (``oeuvre_fakes.TODAY``).

    python tests/fixtures/oeuvre/watch_build.py      (from the backend folder) writes the files next to this one.

Each case says what watching an author does with it (``want``), for a person who may ask for books, watching e-books
and audio books, novels and stories only, every source switched on, with two books of the author on the shelf ("Der
alte Leuchtturm" in German, "The Blue Archive" in English):

* ``wish``: a wish is made; ``nothing``: neither a wish nor the wish list; ``list:<why>``: the wish list with this reason,
  and no wish; ``limit``: a book the records cannot tell from a new one (said in the README), not held to anything.
* ``kinds`` (with ``wish``): the kinds wished, when one kind is wished and the other listed (``list`` then says why).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

AUTHOR = "Ivar Quellbrandt"
SLUG = "quellbrandt"
GND = "0000000019"
GND_OTHER = "0000000027"
OL_KEY = "OL700001A"
OL_OTHER = "OL700002A"
HC_ID = 77001
F = ["Fiction", "Mystery"]


def marc(ident: str, title: str, author: str, *, year: int, group: str = "B", language: str = "ger",
         translated: str = "", original: str = "", isbn: str = "", audio: bool = False, series: str = "",
         position: str = "", pages: int | None = None, subtitle: str = "", gnd: str = GND, responsibility: str = "",
         headings: tuple[str, ...] = (), part_number: str = "", part_name: str = "") -> str:
    """A record of the German library (MARC 21) with the fields a record of one part has (245 ``$n``, ``$p``), who made
    it (245 ``$c``) and the person's number (100 ``$0``)."""
    fields = [f'<controlfield tag="001">{ident}</controlfield>',
              f'<controlfield tag="007">{"sd" if audio else "tu"}</controlfield>']
    lang = f'<subfield code="a">{language}</subfield>' + (f'<subfield code="h">{translated}</subfield>'
                                                           if translated else "")
    fields.append(f'<datafield tag="041" ind1="1" ind2=" ">{lang}</datafield>')
    if isbn:
        fields.append(f'<datafield tag="020" ind1=" " ind2=" "><subfield code="a">{isbn}</subfield></datafield>')
    if group:
        fields.append(f'<datafield tag="082" ind1="0" ind2="4"><subfield code="a">{group}</subfield></datafield>')
    fields.append(f'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">{escape(author)}</subfield>'
                  f'<subfield code="0">(DE-588){gnd}</subfield><subfield code="4">aut</subfield></datafield>')
    if original:
        fields.append(f'<datafield tag="240" ind1="1" ind2="0"><subfield code="a">{escape(original)}</subfield>'
                      '</datafield>')
    extra = "".join(f'<subfield code="{code}">{escape(value)}</subfield>' for code, value in (
        ("b", subtitle), ("n", part_number), ("p", part_name), ("c", responsibility)) if value)
    fields.append(f'<datafield tag="245" ind1="1" ind2="0"><subfield code="a">{escape(title)}</subfield>{extra}'
                  '</datafield>')
    fields.append(f'<datafield tag="264" ind1=" " ind2="1"><subfield code="b">Beispielverlag</subfield>'
                  f'<subfield code="c">{year}</subfield></datafield>')
    extent = f"{pages} Seiten" if pages else ("1 CD (512 Min.)" if audio else "")
    if extent:
        fields.append(f'<datafield tag="300" ind1=" " ind2=" "><subfield code="a">{extent}</subfield></datafield>')
    if series:
        fields.append(f'<datafield tag="490" ind1="0" ind2=" "><subfield code="a">{escape(series)}</subfield>'
                      f'<subfield code="v">{position}</subfield></datafield>')
    for heading in headings:
        fields.append(f'<datafield tag="650" ind1=" " ind2="7"><subfield code="a">{escape(heading)}</subfield>'
                      '</datafield>')
    leader = "00000nim a2200000 c 4500" if audio else "00000nam a2200000 c 4500"
    return (f'<record><recordSchema>MARC21-xml</recordSchema><recordPacking>xml</recordPacking><recordData>'
            f'<record xmlns="http://www.loc.gov/MARC21/slim" type="Bibliographic"><leader>{leader}</leader>'
            f'{"".join(fields)}</record></recordData></record>')


# Each case: id, English title, German title, what watching does, a note, and the records.
#   ol: (first year, subjects, pages, German edition title or None[, author key])
#   hc: dict(en_day, en_year, de=[(title, isbn, day)], tags, compilation, series, pos, pages, lit)
#   aud_de / aud_us: [(title, minutes, day, subtitle, format)]
#   dnb: [dict(title, year, group, audio, original, pages, series, position, subtitle, responsibility, headings,
#        part_number, gnd)]
CASES: list[dict] = []


def case(ident: str, en: str, de: str, want: str, note: str, **records: object) -> None:
    CASES.append(dict(id=ident, en=en, de=de, want=want, note=note, **records))


# ---- New books ---------------------------------------------------------------------------------------------------
case("c01_new_both", "The Lantern Keeper", "Der Laternenwaerter", "wish", "new novel, English 2026-11-20, German 2027-03-04",
     ol=(2026, F, 380, "Der Laternenwaerter"),
     hc={"en_day": "2026-11-20", "en_year": 2026, "de": [("Der Laternenwaerter", "9783100000011", "2027-03-04")], "tags": F, "pages": 380},
     aud_de=[("Der Laternenwaerter", 640, "2027-03-04", "", "unabridged")],
     aud_us=[("The Lantern Keeper", 610, "2026-11-20", "", "unabridged")],
     dnb=[{"title": "Der Laternenwaerter", "year": 2027, "original": "The Lantern Keeper", "pages": 380, "isbn": "9783100000011"}])
case("c02_tomorrow", "Harbour Ledger", "Das Hafenbuch", "wish", "German edition tomorrow",
     ol=(2026, F, 300, "Das Hafenbuch"),
     hc={"en_day": "2026-10-13", "en_year": 2026, "de": [("Das Hafenbuch", "9783100000028", "2026-10-13")], "tags": F, "pages": 300},
     dnb=[{"title": "Das Hafenbuch", "year": 2026, "original": "Harbour Ledger", "pages": 300, "isbn": "9783100000028"}])
case("c03_today", "The Quiet Mill", "Die stille Muehle", "nothing", "German edition out today (not announced)",
     ol=(2026, F, 300, "Die stille Muehle"),
     hc={"en_day": "2026-10-12", "en_year": 2026, "de": [("Die stille Muehle", "9783100000035", "2026-10-12")], "tags": F, "pages": 300},
     dnb=[{"title": "Die stille Muehle", "year": 2026, "original": "The Quiet Mill", "pages": 300, "isbn": "9783100000035"}])
case("c04_far", "The Far Shoal", "Die ferne Sandbank", "wish", "German 2029, far ahead",
     ol=(2029, F, 400, "Die ferne Sandbank"),
     hc={"en_day": "2029-01-15", "en_year": 2029, "de": [("Die ferne Sandbank", None, "2029-01-15")], "tags": F, "pages": 400},
     dnb=[{"title": "Die ferne Sandbank", "year": 2029, "original": "The Far Shoal", "pages": 400}])
case("c05_series4", "The Brine Road Four", "Die Lakenstrasse 4", "wish", "volume 4 of a series, announced",
     ol=(2027, F, 350, "Die Lakenstrasse 4"),
     hc={"en_day": "2027-02-02", "en_year": 2027, "de": [("Die Lakenstrasse 4", None, "2027-04-06")], "tags": F, "pages": 350,
             "series": "The Brine Road", "pos": 4},
     dnb=[{"title": "Die Lakenstrasse", "year": 2027, "original": "The Brine Road Four", "pages": 350, "series": "Die Lakenstrasse",
               "position": "4"}])
case("c06_stories", "Frost Tales", "Frostlicht: Erzaehlungen", "wish", "story collection, announced",
     ol=(2027, ["Short stories", "Fiction"], 240, "Frostlicht: Erzaehlungen"),
     hc={"en_day": "2027-01-10", "en_year": 2027, "de": [("Frostlicht: Erzaehlungen", None, "2027-02-10")],
             "tags": ["Short Stories", "Fiction"], "pages": 240},
     dnb=[{"title": "Frostlicht : Erzaehlungen", "year": 2027, "original": "Frost Tales", "pages": 240}])
case("c52_translated_within_a_year", "The Stone Lamp", "Die Steinlampe", "wish",
     "German 2027-05-11, English 2026-06-02: a translation within a year",
     ol=(2026, F, 300, "Die Steinlampe"),
     hc={"en_day": "2026-06-02", "en_year": 2026, "de": [("Die Steinlampe", None, "2027-05-11")], "tags": F, "pages": 300},
     dnb=[{"title": "Die Steinlampe", "year": 2027, "original": "The Stone Lamp", "pages": 300}])
case("c47_audio_only_new", "The Echo Cellar", "Der Echokeller", "wish", "a new work Audible announces in German",
     aud_de=[("Der Echokeller", 640, "2027-03-02", "", "unabridged")],
     aud_us=[("The Echo Cellar", 610, "2026-12-01", "", "unabridged")], ol=(2027, F, 380, "Der Echokeller"))
case("c48_ebook_only", "The Ink Well", "Das Tintenfass", "wish", "e-book only (nothing when watching audio books)",
     ol=(2027, F, 300, "Das Tintenfass"),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Das Tintenfass", None, "2027-03-02")], "tags": F, "pages": 300},
     dnb=[{"title": "Das Tintenfass", "year": 2027, "original": "The Ink Well", "pages": 300}])

# ---- New editions of books that are out -------------------------------------------------------------------------
case("c07_new_edition", "The Fen House", "Das Fennhaus", "wish",
     "first 2005 in English, the first German edition the records know comes 2027: new in German (a new German edition "
     "whose first one no record knows looks the same: README)",
     ol=(2005, F, 320, "Das Fennhaus"),
     hc={"en_day": "2027-02-10", "en_year": 2027, "de": [("Das Fennhaus", "9783100000073", "2027-02-10")], "tags": F, "pages": 320},
     dnb=[{"title": "Das Fennhaus", "year": 2027, "original": "The Fen House", "pages": 320, "isbn": "9783100000073"}])
case("c08_paperback_old", "The Tin Orchard", "Der Zinngarten", "wish",
     "English 2024, the only German edition the records know comes 2027: new in German (were it a paperback of an "
     "earlier German edition, no record says so)",
     ol=(2024, F, 300, "Der Zinngarten"),
     hc={"en_day": "2024-03-05", "en_year": 2024, "de": [("Der Zinngarten", "9783100000080", "2027-01-12")], "tags": F, "pages": 300},
     dnb=[{"title": "Der Zinngarten", "year": 2027, "original": "The Tin Orchard", "pages": 300, "isbn": "9783100000080"}])
case("c09_paperback_year", "The Glass Season", "Die Glassaison", "nothing",
     "the paperback of a book out this year, both German editions known",
     ol=(2026, F, 300, "Die Glassaison"),
     hc={"en_day": "2026-03-05", "en_year": 2026, "de": [("Die Glassaison", "9783100000097", "2026-03-05"),
                                                     ("Die Glassaison", "9783100000098", "2027-02-09")], "tags": F, "pages": 300},
     dnb=[{"title": "Die Glassaison", "year": 2026, "original": "The Glass Season", "pages": 300, "isbn": "9783100000097"},
          {"title": "Die Glassaison", "year": 2027, "original": "The Glass Season", "pages": 300, "isbn": "9783100000098"}])
case("c10_paperback_alone", "The Brass Winter", "Der Messingwinter", "wish",
     "English 2026-02-02, the only German edition the records know 2027-02-09 (a year and a week later): a translation, "
     "new in German; a paperback whose first German edition no record knows cannot be told from it",
     ol=(2026, F, 300, "Der Messingwinter"),
     hc={"en_day": "2026-02-02", "en_year": 2026, "de": [("Der Messingwinter", "9783100000103", "2027-02-09")], "tags": F, "pages": 300},
     dnb=[{"title": "Der Messingwinter", "year": 2027, "original": "The Brass Winter", "pages": 300, "isbn": "9783100000103"}])
case("c54_two_years_by_year", "The Copper Pond", "Der Kupferteich", "wish",
     "first out in English two years before (a year, no day), the first German edition comes out the day after tomorrow",
     ol=(2024, F, 300, "Der Kupferteich"),
     hc={"en_day": None, "en_year": 2024, "de": [("Der Kupferteich", None, "2026-10-14")], "tags": F, "pages": 300},
     dnb=[{"title": "Der Kupferteich", "year": 2026, "original": "The Copper Pond", "pages": 300}])
case("c11_new_translation", "The Night Barge", "Die Nachtbarke", "list:translation",
     "a new German translation of a novel of 1998 by the library's subtitle, the old translation unknown",
     ol=(1998, F, 330, "Die Nachtbarke"),
     hc={"en_day": "1998-06-01", "en_year": 1998, "de": [("Die Nachtbarke", None, "2027-04-13")], "tags": F, "pages": 330},
     dnb=[{"title": "Die Nachtbarke", "year": 2027, "original": "The Night Barge", "pages": 330,
               "subtitle": "Neu uebersetzt von Anna Beispiel"}])
case("c12_new_translation_old_known", "The Paper Bridge", "Die Papierbruecke", "list:translation",
     "a second German translation of a novel of 1999: the first (2001) known, the library names other translators",
     ol=(1999, F, 330, "Die Papierbruecke"),
     hc={"en_day": "1999-06-01", "en_year": 1999, "de": [("Die Papierbruecke", None, "2001-05-01"),
                                                     ("Die Papierbruecke", None, "2027-04-13")], "tags": F, "pages": 330},
     dnb=[{"title": "Die Papierbruecke", "year": 2001, "original": "The Paper Bridge", "pages": 330,
               "responsibility": "Ivar Quellbrandt ; aus dem Englischen von Jonas Beispiel"},
          {"title": "Die Papierbruecke", "year": 2027, "original": "The Paper Bridge", "pages": 330,
               "responsibility": "Ivar Quellbrandt ; aus dem Englischen von Anna Beispiel"}])
case("c56_paperback_of_german", "The Moor Light", "Das Moorlicht", "nothing",
     "the German paperback (2027) of the German edition of 2024, by the same translator; the shop knows the paperback only",
     ol=(2023, F, 310, "Das Moorlicht"),
     hc={"en_day": "2023-03-07", "en_year": 2023, "de": [("Das Moorlicht", "9783100000561", "2027-01-19")], "tags": F,
             "pages": 310},
     dnb=[{"title": "Das Moorlicht", "year": 2024, "original": "The Moor Light", "pages": 310,
               "responsibility": "Ivar Quellbrandt ; aus dem Englischen von Anna Beispiel"},
          {"title": "Das Moorlicht", "year": 2027, "original": "The Moor Light", "pages": 310, "isbn": "9783100000561",
               "responsibility": "Ivar Quellbrandt ; aus dem Englischen von Anna Beispiel"}])
case("c57_translated_two_years_after", "The Peat Bell", "Die Torfglocke", "wish",
     "the first German edition 2026-11-17, two years and twelve days after the English original: new in German",
     ol=(2024, F, 290, "Die Torfglocke"),
     hc={"en_day": "2024-11-05", "en_year": 2024, "de": [("Die Torfglocke", None, "2026-11-17")], "tags": F, "pages": 290},
     dnb=[{"title": "Die Torfglocke", "year": 2026, "original": "The Peat Bell", "pages": 290}])
case("c14_audiobook_of_old", "The Ember Clock", "Die Glutuhr", "wish",
     "a novel of 2003 whose first German edition, a book and a recording, the records know in 2027: new in German",
     aud_de=[("Die Glutuhr", 540, "2027-02-17", "", "unabridged")], ol=(2003, F, 360, None),
     dnb=[{"title": "Die Glutuhr", "year": 2027, "original": "The Ember Clock", "pages": 360}])
case("c27_jubilee", "The Old Clockmaker", "Der alte Uhrmacher", "list:reissue", "a jubilee edition of a novel of 1979",
     ol=(1979, F, 300, "Der alte Uhrmacher"),
     dnb=[{"title": "Der alte Uhrmacher", "year": 2027, "original": "The Old Clockmaker", "pages": 300,
               "subtitle": "Jubilaeumsausgabe"}],
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Der alte Uhrmacher", None, "2027-03-01")], "tags": F, "pages": 300})
case("c28_new_edition_in_title", "The Amber Mile", "Die Bernsteinmeile Neuausgabe", "list:reissue",
     "Neuausgabe in the title",
     ol=(2010, F, 300, "Die Bernsteinmeile Neuausgabe"),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Die Bernsteinmeile Neuausgabe", None, "2027-03-02")], "tags": F,
             "pages": 300})
case("c50_new_edition_subtitle", "The Rust Garden", "Der Rostgarten", "list:reissue",
     "a new edition the library's subtitle names; no source knows a first year (every record says 2027)",
     ol=(2027, F, 300, "Der Rostgarten"),
     hc={"en_day": "2027-02-02", "en_year": 2027, "de": [("Der Rostgarten", None, "2027-04-06")], "tags": F, "pages": 300},
     dnb=[{"title": "Der Rostgarten", "year": 2027, "original": "The Rust Garden", "pages": 300, "subtitle": "Neuausgabe"}])

# ---- Parts, boxes, plays ---------------------------------------------------------------------------------------
case("c17_part_1_of_2", "The Slow Fall", "Der langsame Sturz (Teil 1 von 2)", "list:part",
     "Teil 1 von 2 in Audible's brackets and the library's part number",
     aud_de=[("Der langsame Sturz (Teil 1 von 2)", 400, "2027-05-04", "", "unabridged")],
     ol=(2027, F, 600, None),
     dnb=[{"title": "Der langsame Sturz", "year": 2027, "original": "The Slow Fall", "pages": 300, "part_number": "1 von 2"}])
case("c18_part_slash", "The Deep Yard", "Der tiefe Hof 1/2", "list:part", "the part as 1/2",
     hc={"en_day": "2027-05-01", "en_year": 2027, "de": [("Der tiefe Hof 1/2", None, "2027-05-04")], "tags": F, "pages": 300},
     ol=(2027, F, 600, "Der tiefe Hof 1/2"))
case("c55_part_by_the_library", "The Long Thaw", "Das lange Tauen", "list:part",
     "only the library's record says it is the second of two parts",
     ol=(2027, F, 300, "Das lange Tauen"),
     hc={"en_day": "2027-02-01", "en_year": 2027, "de": [("Das lange Tauen", None, "2027-04-01")], "tags": F, "pages": 300},
     dnb=[{"title": "Das lange Tauen", "year": 2027, "original": "The Long Thaw", "pages": 300, "part_number": "2 von 2"}])
case("c19_package", "The Fen Pack", "Das Fenn-Paket", "list:box", "a package without a box word",
     hc={"en_day": "2027-06-01", "en_year": 2027, "de": [("Das Fenn-Paket", None, "2027-06-08")], "tags": F, "pages": 900},
     ol=(2027, F, 900, "Das Fenn-Paket"))
case("c20_two_novels", "Dusk and Dawn", "Daemmerung und Morgenrot", "list:box",
     "two earlier novels in one volume, said only in the library's subtitle",
     hc={"en_day": "2027-06-01", "en_year": 2027, "de": [("Daemmerung und Morgenrot", None, "2027-06-08")], "tags": F, "pages": 900},
     ol=(2027, F, 900, "Daemmerung und Morgenrot"),
     dnb=[{"title": "Daemmerung und Morgenrot", "year": 2027, "pages": 880, "subtitle": "Zwei Romane in einem Band"}])
case("c21_box_flag", "The Brine Road Complete", "Die Lakenstrasse. Gesamtausgabe", "nothing",
     "a box by Hardcover's flag and its title",
     hc={"en_day": "2027-06-01", "en_year": 2027, "de": [("Die Lakenstrasse. Gesamtausgabe", None, "2027-06-08")], "tags": F,
             "pages": 1500, "compilation": True})
case("c39_play", "The Mist Radio", "Der Nebelfunk - Das Hoerspiel", "list:play", "a play announced",
     aud_de=[("Der Nebelfunk - Das Hoerspiel", 150, "2027-03-02", "", "unabridged")])
case("c53_play_beside_novel", "The Salt Lantern", "Die Salzlaterne", "wish", "a new novel and, beside it, a play of it",
     kinds=["ebook"], listed="play",
     ol=(2027, F, 320, "Die Salzlaterne"),
     hc={"en_day": "2027-01-12", "en_year": 2027, "de": [("Die Salzlaterne", None, "2027-03-09")], "tags": F, "pages": 320},
     aud_de=[("Die Salzlaterne - Das Hoerspiel", 140, "2027-03-09", "", "unabridged")],
     dnb=[{"title": "Die Salzlaterne", "year": 2027, "original": "The Salt Lantern", "pages": 320}])
case("c40_play_without_the_word", "The Fire Watch", "Die Feuerwache", "limit",
     "a recording with a cast that no record names a play",
     aud_de=[("Die Feuerwache", 150, "2027-03-02", "Mit Sprechern der Beispielbuehne", "unabridged")],
     ol=(2027, F, 300, "Die Feuerwache"))

# ---- Books about the book, adaptations --------------------------------------------------------------------------
case("c29_study_guide", "The Mist Choir", "Der Nebelchor. Lektuerehilfe", "list:companion", "a study guide",
     dnb=[{"title": "Der Nebelchor", "year": 2027, "pages": 120, "subtitle": "Lektuerehilfe", "group": "4"}],
     ol=(2027, F, 120, "Der Nebelchor. Lektuerehilfe"))
case("c31_graphic_novel", "The Lamp Ghost", "Der Lampengeist - Die Graphic Novel", "nothing", "a graphic novel",
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Der Lampengeist - Die Graphic Novel", None, "2027-03-02")],
             "tags": ["Graphic Novels", "Comics"], "pages": 120})
case("c33_film_book", "The Crow Bell", "Die Kraehenglocke. Das Buch zum Film", "list:edition", "the book of the film",
     dnb=[{"title": "Die Kraehenglocke", "year": 2027, "pages": 300, "subtitle": "Das Buch zum Film"}],
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Die Kraehenglocke. Das Buch zum Film", None, "2027-03-02")], "tags": F,
             "pages": 300})
case("c51_adapted_by", "The Ice Harp", "Die Eisharfe", "list:adaptation",
     "another hand's version: only the library's credits say 'bearbeitet von'",
     ol=(2027, F, 140, "Die Eisharfe"),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Die Eisharfe", None, "2027-03-02")], "tags": F, "pages": 140},
     dnb=[{"title": "Die Eisharfe", "year": 2027, "original": "The Ice Harp", "pages": 140,
               "responsibility": "Ivar Quellbrandt ; bearbeitet von Lena Beispiel"}])

# ---- Languages, the person's own, namesakes, odd records ---------------------------------------------------------
case("c24_english_only", "The Ninth Lamp", "Die neunte Lampe", "list:language:en", "announced in English only",
     ol=(2027, F, 340, None),
     hc={"en_day": "2027-05-05", "en_year": 2027, "de": [], "tags": F, "pages": 340},
     aud_us=[("The Ninth Lamp", 600, "2027-05-05", "", "unabridged")])
case("c44_owned_de", "The Old Lighthouse", "Der alte Leuchtturm", "nothing",
     "the person has the German e-book; a new paperback is announced",
     ol=(2025, F, 300, "Der alte Leuchtturm"),
     hc={"en_day": "2025-03-01", "en_year": 2025, "de": [("Der alte Leuchtturm", "9783100000446", "2027-01-12")], "tags": F,
             "pages": 300},
     dnb=[{"title": "Der alte Leuchtturm", "year": 2027, "original": "The Old Lighthouse", "pages": 300, "isbn": "9783100000446"}])
case("c45_owned_en", "The Blue Archive", "Das blaue Archiv", "list:own:en",
     "the person has the English e-book; the German translation is announced",
     ol=(2026, F, 300, "Das blaue Archiv"),
     hc={"en_day": "2026-02-01", "en_year": 2026, "de": [("Das blaue Archiv", None, "2027-01-02")], "tags": F, "pages": 300},
     dnb=[{"title": "Das blaue Archiv", "year": 2027, "original": "The Blue Archive", "pages": 300}])
case("c23_namesake", "Cold Harbour Nights", "Kalte Hafennaechte", "nothing",
     "a namesake's crime novel: the other person's numbers at Open Library and the library",
     ol=(2027, F, 330, "Kalte Hafennaechte", OL_OTHER),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Kalte Hafennaechte", None, "2027-03-08")], "tags": F, "pages": 330},
     aud_de=[("Kalte Hafennaechte", 600, "2027-03-08", "", "unabridged")],
     dnb=[{"title": "Kalte Hafennaechte", "year": 2027, "original": "Cold Harbour Nights", "pages": 330, "gnd": GND_OTHER}])
case("c25_year_only", "The Hidden Latch", "Der verborgene Riegel", "nothing", "a year without a day",
     ol=(2027, F, 300, "Der verborgene Riegel"),
     dnb=[{"title": "Der verborgene Riegel", "year": 2027, "original": "The Hidden Latch", "pages": 300}])
case("c46_bad_date", "The Odd Date", "Das schiefe Datum", "nothing", "a day that is no day",
     hc={"en_day": "2027-13-45", "en_year": 2027, "de": [("Das schiefe Datum", None, "2027-13-45")], "tags": F, "pages": 300})
case("c35_nonfiction", "On Silence Kept", "Vom bewahrten Schweigen", "nothing", "non-fiction (watched: novels only)",
     ol=(2027, ["Philosophy"], 220, "Vom bewahrten Schweigen"),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Vom bewahrten Schweigen", None, "2027-03-02")],
             "tags": ["Philosophy"], "pages": 220, "lit": 2},
     dnb=[{"title": "Vom bewahrten Schweigen", "year": 2027, "pages": 220, "group": "150", "original": "On Silence Kept"}])
case("c43_old_book_new_title", "The Coal Years", "Die Kohlenjahre", "limit",
     "an old book under a new title, which only a library's note says",
     ol=(2027, F, 300, "Die Kohlenjahre"),
     hc={"en_day": "2027-03-01", "en_year": 2027, "de": [("Die Kohlenjahre", None, "2027-03-02")], "tags": F, "pages": 300},
     dnb=[{"title": "Die Kohlenjahre", "year": 2027, "pages": 300, "original": "The Coal Years",
               "subtitle": "Fruehere Ausgabe unter dem Titel Die dunkle Zeit"}])

#: Cases whose records are complete as written (no record of another source is added).
NOFILL = {"c23_namesake", "c14_audiobook_of_old", "c47_audio_only_new", "c24_english_only", "c25_year_only",
          "c10_paperback_alone", "c54_two_years_by_year"}


def finalize() -> None:
    """Every case has a record at Open Library, Hardcover and the German library unless it says otherwise (most books
    are known to the three)."""
    for item in CASES:
        if item["id"] in NOFILL:
            continue
        hc = item.get("hc")
        first = item["ol"][0] if item.get("ol") else (hc["en_year"] if hc else 2027)
        day = hc["de"][-1][2] if hc and hc.get("de") else None
        if not item.get("ol"):
            item["ol"] = (first, F, 300, item["de"])
        if not item.get("hc"):
            item["hc"] = {"en_day": f"{first}-03-01", "en_year": first, "de": [(item["de"], None, day or "2027-03-02")],
                              "tags": F, "pages": 300}
        if not item.get("dnb"):
            item["dnb"] = [{"title": item["de"], "year": int((day or "2027")[:4]), "original": item["en"], "pages": 300}]


def ol_doc(item: dict, language: str) -> dict | None:
    ol = item.get("ol")
    if not ol:
        return None
    first, subjects, pages, de_title = ol[0], ol[1], ol[2], ol[3]
    if language == "de" and not de_title:
        return None
    number = CASES.index(item)
    edition = {"key": f"/books/OL7{number:03d}M", "title": item["en"] if language == "en" else de_title,
               "language": ["eng" if language == "en" else "ger"], "isbn": []}
    doc = build.ol_doc(f"OL70{number:03d}W", item["en"], AUTHOR, first, ["eng", "ger"] if de_title else ["eng"],
                       edition, subjects=subjects, pages=pages)
    doc["author_key"] = [ol[4] if len(ol) > 4 else OL_KEY]
    return doc


def write(name: str, data: object) -> None:
    path = HERE / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def files() -> None:
    finalize()
    for language in ("en", "de"):
        docs = [doc for item in CASES if (doc := ol_doc(item, language))]
        write(f"ol_{SLUG}_{language}.json", {"numFound": len(docs), "start": 0, "docs": docs})
    write(f"ol_authors_{SLUG}.json", {"numFound": 2, "start": 0, "docs": [
        {"key": OL_KEY, "name": AUTHOR, "alternate_names": [], "birth_date": "14 Mar 1951", "work_count": 40,
         "top_work": "The Lantern Keeper", "type": "author"},
        {"key": OL_OTHER, "name": AUTHOR, "birth_date": "2 Nov 1962", "work_count": 4, "top_work": "Fjord Sailing",
         "type": "author"}]})
    write(f"hc_author_search_{SLUG}.json", {"data": {"search": {"results": {"hits": [
        {"document": {"id": str(HC_ID), "name": AUTHOR, "books_count": 44, "alternate_names": []}},
        {"document": {"id": str(HC_ID + 1), "name": AUTHOR, "books_count": 4, "alternate_names": []}}]}}}})
    rows = []
    for index, item in enumerate(CASES):
        hc = item.get("hc")
        if not hc:
            continue
        editions = [{"title": title, "isbn_13": isbn, "release_date": day, "language": {"language": "German"}}
                    for (title, isbn, day) in hc["de"]]
        book = {"id": 90000 + index, "title": item["en"], "release_date": hc["en_day"], "release_year": hc["en_year"],
                "compilation": bool(hc.get("compilation")), "pages": hc.get("pages"),
                "literary_type_id": hc.get("lit", 1), "book_category_id": 1, "users_count": 200 - index,
                "cached_tags": {"Genre": [{"tag": tag} for tag in hc["tags"]]},
                "image": {"url": f"https://assets.hardcover.app/books/{90000 + index}/cover.jpg"},
                "canonical_id": None, "editions": editions,
                "book_series": [{"position": hc["pos"], "series": {"name": hc["series"]}}] if hc.get("series") else []}
        rows.append({"contribution": None, "book": book})
    write(f"hc_contributions_{HC_ID}.json", {"data": {"contributions": rows}})
    for market, field, language in (("de", "aud_de", "german"), ("us", "aud_us", "english")):
        products = []
        for index, item in enumerate(CASES):
            for number, (title, minutes, day, subtitle, form) in enumerate(item.get(field, [])):
                product = build.audible_product(f"B0QB{index:02d}{number}0{market.upper()}", title, AUTHOR, minutes,
                                                language, day, subtitle=subtitle)
                product["format_type"] = form
                products.append(product)
        write(f"audible_{market}_{SLUG}.json", {"products": products, "total_results": len(products)})
    records = []
    for index, item in enumerate(CASES):
        for number, record in enumerate(item.get("dnb", [])):
            records.append(marc(
                f"30{index:02d}{number:03d}", record["title"], "Quellbrandt, Ivar", year=record["year"],
                group=record.get("group", "B"), translated="eng" if record.get("original") else "",
                original=record.get("original", ""), isbn=record.get("isbn", ""), pages=record.get("pages"),
                subtitle=record.get("subtitle", ""), gnd=record.get("gnd", GND),
                responsibility=record.get("responsibility", ""), headings=record.get("headings", ()),
                part_number=record.get("part_number", ""), series=record.get("series", ""),
                position=record.get("position", "")))
    write(f"dnb_{SLUG}.xml", build.sru(records))
    write("watch_cases.json", [{key: item.get(key) for key in ("id", "en", "de", "want", "note", "kinds", "listed")}
                               for item in CASES])


if __name__ == "__main__":
    files()
    print(len(CASES), "cases written")
