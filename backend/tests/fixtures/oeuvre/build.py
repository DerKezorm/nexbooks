"""Writes the answers of the sources about the works of invented authors, in the formats of the real services
(Open Library, Hardcover, Audible, the German, French, Swedish, Dutch and Norwegian national libraries; the shapes follow
the services, every author, title and number here is invented).

    python tests/fixtures/oeuvre/build.py      (from the backend folder) writes the files next to this one.

The world:

* **Edmund Halloran** writes in English and is translated into German; four early books came out as **Richard Vane**.
  A series of three, a box of it, stories, a book for children, non-fiction, a comic of the series (unsure), a book that
  is still to come (in English in November 2026, in German in March 2027), and a calendar only one source knows.
* **Marit Lindqvist** writes in Swedish (LIBRIS), translated into German.
* **Femke Aalders** writes in Dutch (the Dutch library alone knows her Dutch books).
* **Sigrun Holte** writes in Norwegian; **Odile Marchetti** in French.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent

# (key, kind, english title, german title, year, extra)
HALLORAN = [
    ("salt", "roman", "The Salt Daughters", "Die Salztöchter", 1974, {"pages": 238, "audio_de": 512, "isbn_en": "9780000000101", "isbn_de": "9783000000101"}),
    ("soot", "roman", "Soot", "Ruß", 1983, {"pages": 512, "isbn_en": "9780000000118", "isbn_de": "9783000000118"}),
    ("roses", "erz", "Soot and Roses", "Ruß und Rosen", 2024, {"pages": 384, "isbn_en": "9780000000125", "isbn_de": "9783000000125", "stories": True}),
    ("shore", "roman", "The Ninth Shore", "Das neunte Ufer", 1986, {"pages": 1104, "audio_de": 2730, "audio_en": 2690, "isbn_en": "9780000000132", "isbn_de": "9783000000132"}),
    ("ash", "roman", "The Ash Road", "Die Aschenstraße", 1982, {"pages": 260, "series": 1, "isbn_en": "9780000000149", "isbn_de": "9783000000149"}),
    ("doors", "roman", "Three Doors", "Drei Türen", 1987, {"pages": 432, "series": 2, "isbn_en": "9780000000156", "isbn_de": "9783000000156"}),
    ("plains", "roman", "The Salt Plains", "Die Salzebene", 1991, {"pages": 512, "series": 3, "isbn_en": "9780000000163", "isbn_de": "9783000000163"}),
    ("box", "box", "The Wrenfield Chronicles: Complete Edition", "Die Wrenfield-Chroniken. Gesamtausgabe", 2017, {"pages": 1210, "compilation": True}),
    ("afraid", "sach", "On Being Afraid", "Vom Fürchten", 1981, {"pages": 412, "nonfiction": True}),
    ("pim", "kinder", "Pim and the Lighthouse", "Pim und der Leuchtturm", 2011, {"pages": 48, "kids": True}),
    ("keeper", "roman", "The Keeper of Lost Rooms", "Der Hüter der verlorenen Zimmer", 2026, {"pages": 560, "released_en": "2026-11-15", "audio_de": 1210, "released_de": "2027-03-03"}),
    ("comic", "unsure", "Wrenfield: The Graphic Novel", "Wrenfield. Die Graphic Novel", 2010, {"pages": 184, "comic": True}),
]
# Written as Richard Vane: (key, english, german, year)
VANE = [("rain", "Rain Fury", "Regenwut", 1977), ("road", "Roadside", "Am Straßenrand", 1980)]


def write(name: str, data: object) -> None:
    path = HERE / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


# ---- Open Library --------------------------------------------------------------------------------------------------


def ol_doc(key: str, title: str, author: str, year: int, languages: list[str], edition: dict | None, *,
           subjects: list[str] | None = None, pages: int | None = None, editions: int = 4) -> dict:
    doc = {"author_key": ["OL900001A"], "author_name": [author], "edition_count": editions,
           "first_publish_year": year, "key": f"/works/{key}", "language": languages, "title": title,
           "subject": subjects or ["Fiction"]}
    if pages:
        doc["number_of_pages_median"] = pages
    if edition is not None:
        doc["editions"] = {"numFound": 2, "start": 0, "numFoundExact": True, "docs": [edition]}
    return doc


def ol_files() -> None:
    for language, marc in (("en", "eng"), ("de", "ger")):
        docs = []
        for index, (key, _kind, en, de, year, extra) in enumerate(HALLORAN):
            if key == "keeper" and language == "de":
                continue
            title = en if not extra.get("series") else f"The Wrenfield Chronicles: {en}"
            subjects = ["Fiction", "Horror"]
            if extra.get("stories"):
                subjects = ["Short stories", "Fiction"]
            if extra.get("kids"):
                subjects = ["Juvenile fiction", "Lighthouses"]
            if extra.get("comic"):
                subjects = ["Comics & graphic novels"]
            if extra.get("nonfiction"):
                subjects = ["Fear", "Psychology"]
            shown = title if language == "en" else de
            edition = {"key": f"/books/OL9{index:04d}M", "title": shown, "language": [marc],
                       "isbn": [extra.get(f"isbn_{language}")] if extra.get(f"isbn_{language}") else []}
            docs.append(ol_doc(f"OL90{index:03d}W", title, "Edmund Halloran", year, ["eng", "ger"], edition,
                               subjects=subjects, pages=extra.get("pages")))
        if language == "en":
            # A calendar only Open Library knows: one source, and Hardcover answered for English too.
            docs.append(ol_doc("OL90999W", "Halloran Calendar 2025", "Edmund Halloran", 2024, ["eng"],
                               {"key": "/books/OL99999M", "title": "Halloran Calendar 2025", "language": ["eng"]}))
            # A namesake: another first name, never one of his.
            docs.append(ol_doc("OL90998W", "Harbour Lights", "Ellen Halloran", 2001, ["eng"],
                               {"key": "/books/OL99998M", "title": "Harbour Lights", "language": ["eng"]}))
        write(f"ol_halloran_{language}.json", {"numFound": len(docs), "start": 0, "docs": docs})
    vane = [ol_doc(f"OL91{index:03d}W", en, "Richard Vane", year, ["eng"],
                   {"key": f"/books/OL91{index:03d}M", "title": en, "language": ["eng"]}, pages=280)
            for index, (_key, en, _de, year) in enumerate(VANE)]
    write("ol_vane_en.json", {"numFound": len(vane), "start": 0, "docs": vane})
    write("ol_authors.json", {"numFound": 2, "start": 0, "docs": [
        {"key": "OL900001A", "name": "Edmund Halloran", "alternate_names": ["Richard Vane", "E. Halloran"],
         "birth_date": "14 Mar 1951", "work_count": 58, "top_work": "The Ninth Shore", "type": "author"},
        {"key": "OL900002A", "name": "Ellen Halloran", "work_count": 3, "top_work": "Harbour Lights",
         "type": "author"},
    ]})


# ---- Hardcover -----------------------------------------------------------------------------------------------------


def hc_files() -> None:
    write("hc_author_search.json", {"data": {"search": {"results": {"hits": [
        {"document": {"id": "70001", "name": "Edmund Halloran", "books_count": 52,
                      "alternate_names": ["Richard Vane", "E. Halloran", "Halloran, Edmund"]}},
        {"document": {"id": "70002", "name": "Ellen Halloran", "books_count": 3, "alternate_names": []}},
    ]}}}})
    rows = []
    for index, (key, _kind, en, de, year, extra) in enumerate(HALLORAN):
        tags = {"Genre": [{"tag": "Horror"}, {"tag": "Fiction"}]}
        if extra.get("comic"):
            tags = {"Genre": [{"tag": "Graphic Novels"}, {"tag": "Comics"}]}
        if extra.get("stories"):
            tags = {"Genre": [{"tag": "Short Stories"}, {"tag": "Horror"}]}
        if extra.get("kids"):
            tags = {"Genre": [{"tag": "Children"}, {"tag": "Juvenile Fiction"}]}
        editions = [{"title": de, "isbn_13": extra.get("isbn_de"), "release_date": extra.get("released_de"),
                     "language": {"language": "German"}}]
        if key == "keeper":
            editions = []
        book = {"id": 80000 + index, "title": en, "release_date": extra.get("released_en", f"{year}-05-01"),
                "release_year": year, "compilation": bool(extra.get("compilation")), "pages": extra.get("pages"),
                "literary_type_id": 2 if extra.get("nonfiction") else 1,
                "book_category_id": 1, "users_count": 500 - index * 10, "cached_tags": tags,
                "image": {"url": f"https://assets.hardcover.app/books/{80000 + index}/cover.jpg"},
                "canonical_id": None, "editions": editions,
                "book_series": [{"position": extra["series"], "series": {"name": "The Wrenfield Chronicles"}}]
                if extra.get("series") else []}
        rows.append({"contribution": None, "book": book})
    # A translation Hardcover lists as a book of its own: a copy of another (canonical_id), left out.
    rows.append({"contribution": None, "book": {"id": 89999, "title": "Die Salztöchter", "canonical_id": 80000,
                                                 "editions": [], "book_series": []}})
    # Hardcover lists the books under the pen name with the author already.
    for index, (_key, en, de, year) in enumerate(VANE):
        rows.append({"contribution": None, "book": {
            "id": 81000 + index, "title": en, "release_date": f"{year}-03-01", "release_year": year,
            "compilation": False, "pages": 280, "literary_type_id": 1, "book_category_id": 1, "users_count": 40,
            "cached_tags": {"Genre": [{"tag": "Thriller"}]}, "image": None, "canonical_id": None,
            "editions": [{"title": de, "isbn_13": None, "release_date": None, "language": {"language": "German"}}],
            "book_series": []}})
    write("hc_contributions_70001.json", {"data": {"contributions": rows}})


# ---- Audible -------------------------------------------------------------------------------------------------------


def audible_product(asin: str, title: str, author: str, minutes: int, language: str, released: str, *,
                    subtitle: str = "", series: str = "", sequence: str = "") -> dict:
    product = {"asin": asin, "title": title, "subtitle": subtitle, "authors": [{"name": author}],
               "narrators": [{"name": "Ida Leser"}], "runtime_length_min": minutes, "format_type": "unabridged",
               "language": language, "release_date": released, "publisher_name": "Beispiel Audio",
               "category_ladders": [{"ladder": [{"name": "Krimis & Thriller"}]}],
               "product_images": {"500": f"https://m.media-amazon.com/images/I/{asin}._SL500_.jpg"}}
    if series:
        product["series"] = [{"title": series, "sequence": sequence}]
    return product


def audible_files() -> None:
    de = [audible_product("B0EXAMPL01", "Die Salztöchter", "Edmund Halloran", 512, "german", "2012-04-01",
                          subtitle="Ein Roman aus Port Aldous"),
          audible_product("B0EXAMPL02", "Das neunte Ufer", "Edmund Halloran", 2730, "german", "2015-09-01"),
          audible_product("B0EXAMPL03", "Der Hüter der verlorenen Zimmer", "Edmund Halloran", 1210, "german",
                          "2027-03-03"),
          # The German market lists recordings in other languages too: a language nobody asked for.
          audible_product("B0EXAMPL04", "Las hijas de la sal", "Edmund Halloran", 500, "spanish", "2016-02-01")]
    us = [audible_product("B0EXAMPL11", "The Ninth Shore", "Edmund Halloran", 2690, "english", "2010-01-01"),
          audible_product("B0EXAMPL12", "The Keeper of Lost Rooms", "Edmund Halloran", 1180, "english", "2026-11-15")]
    write("audible_de_halloran.json", {"products": de, "total_results": len(de)})
    write("audible_us_halloran.json", {"products": us, "total_results": len(us)})


# ---- The German National Library (MARC 21) ---------------------------------------------------------------------------


def marc(ident: str, title: str, author: str, *, year: int, group: str = "B", language: str = "ger",
         translated: str = "", original: str = "", isbn: str = "", audio: bool = False, series: str = "",
         position: str = "", pages: int | None = None, subtitle: str = "") -> str:
    fields = [f'<controlfield tag="001">{ident}</controlfield>',
              f'<controlfield tag="007">{"sd" if audio else "tu"}</controlfield>']
    lang = f'<subfield code="a">{language}</subfield>' + (f'<subfield code="h">{translated}</subfield>' if translated else "")
    fields.append(f'<datafield tag="041" ind1="1" ind2=" ">{lang}</datafield>')
    if isbn:
        fields.append(f'<datafield tag="020" ind1=" " ind2=" "><subfield code="a">{isbn}</subfield></datafield>')
    if group:
        fields.append(f'<datafield tag="082" ind1="0" ind2="4"><subfield code="a">{group}</subfield></datafield>')
    fields.append(f'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">{escape(author)}</subfield>'
                  '<subfield code="4">aut</subfield></datafield>')
    if original:
        fields.append(f'<datafield tag="240" ind1="1" ind2="0"><subfield code="a">{escape(original)}</subfield></datafield>')
    sub = f'<subfield code="b">{escape(subtitle)}</subfield>' if subtitle else ""
    fields.append(f'<datafield tag="245" ind1="1" ind2="0"><subfield code="a">{escape(title)}</subfield>{sub}</datafield>')
    fields.append(f'<datafield tag="264" ind1=" " ind2="1"><subfield code="b">Beispielverlag</subfield>'
                  f'<subfield code="c">{year}</subfield></datafield>')
    extent = f"{pages} Seiten" if pages else ("1 CD (512 Min.)" if audio else "")
    if extent:
        fields.append(f'<datafield tag="300" ind1=" " ind2=" "><subfield code="a">{extent}</subfield></datafield>')
    if series:
        fields.append(f'<datafield tag="490" ind1="0" ind2=" "><subfield code="a">{escape(series)}</subfield>'
                      f'<subfield code="v">{position}</subfield></datafield>')
    leader = "00000nim a2200000 c 4500" if audio else "00000nam a2200000 c 4500"
    return (f'<record><recordSchema>MARC21-xml</recordSchema><recordPacking>xml</recordPacking><recordData>'
            f'<record xmlns="http://www.loc.gov/MARC21/slim" type="Bibliographic"><leader>{leader}</leader>'
            f'{"".join(fields)}</record></recordData></record>')


def sru(records: list[str]) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f'<version>1.1</version><numberOfRecords>{len(records)}</numberOfRecords><records>{"".join(records)}'
            '</records></searchRetrieveResponse>').encode()


def dnb_files() -> None:
    records = []
    for index, (key, _kind, en, de, year, extra) in enumerate(HALLORAN):
        if key == "comic":
            continue
        group = "K" if extra.get("kids") else ("150" if extra.get("nonfiction") else "B")
        de_year = int(extra["released_de"][:4]) if extra.get("released_de") else year + 2
        title = de if not extra.get("stories") else f"{de} : Erzählungen"
        records.append(marc(f"10{index:05d}", title, "Halloran, Edmund", year=de_year, group=group,
                            translated="eng", original=en, isbn=extra.get("isbn_de", ""), pages=extra.get("pages"),
                            series="Die Wrenfield-Chroniken" if extra.get("series") else "",
                            position=str(extra.get("series", ""))))
        if extra.get("audio_de"):
            records.append(marc(f"11{index:05d}", de, "Halloran, Edmund", year=de_year + 1, group="B",
                                translated="eng", original=en, audio=True))
        if key == "salt":
            # A later paperback of the same book: the earliest year counts.
            records.append(marc(f"12{index:05d}", f"{de} : Roman", "Halloran, Edmund", year=de_year + 20, group="B",
                                translated="eng", original=en, pages=240))
    # A namesake of the same last name.
    records.append(marc("1999999", "Leuchtfeuer", "Halloran, Ellen", year=2003))
    write("dnb_halloran.xml", sru(records))
    vane = [marc(f"13{index:05d}", de, "Vane, Richard", year=year + 3, translated="eng", original=en, pages=300)
            for index, (_key, en, de, year) in enumerate(VANE)]
    write("dnb_vane.xml", sru(vane))
    lind = [marc(f"14{index:05d}", de, "Lindqvist, Marit", year=year, translated="swe", original=sv, pages=300)
            for index, (sv, de, year) in enumerate(LINDQVIST)]
    write("dnb_lindqvist.xml", sru(lind))


# ---- LIBRIS, the Dutch, Norwegian and French libraries -------------------------------------------------------------

LINDQVIST = [("Den tysta hamnen", "Der stille Hafen", 1971), ("Mannen vid kanalen", "Der Mann am Kanal", 1973),
             ("Isvägen", "Die Eisstraße", 1975)]


def libris_files() -> None:
    items = []
    for index, (sv, _de, year) in enumerate(LINDQVIST):
        items.append({"identifier": f"http://libris.kb.se/bib/9{index:06d}", "title": f"{sv} : kriminalroman",
                      "creator": "Lindqvist, Marit, 1938-2019", "isbn": f"978910000{index:04d}", "type": "book",
                      "publisher": "Stockholm : Exempelförlaget", "date": str(year), "language": "swe"})
        # Translations into other languages are listed too: not Swedish, left out.
        items.append({"identifier": f"http://libris.kb.se/bib/8{index:06d}", "title": f"{sv} (fr)",
                      "creator": "Lindqvist, Marit", "type": "text", "date": str(year + 10), "language": "fre"})
    write("libris_lindqvist.json", {"xsearch": {"from": 1, "to": len(items), "records": len(items), "list": items}})


def kb_files() -> None:
    def record(ident: str, title: str, year: int, nur: str, pages: int) -> str:
        return ('<srw:record><srw:recordData><srw_dc:dc xmlns:srw_dc="info:srw/schema/1/dc-v1.1" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:dcx="http://krait.kb.nl/coop/tel/handbook/telterms.html" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:type xml:lang="nl">boek</dc:type><dc:identifier xsi:type="dcterms:ISBN">9789000000'
                f'{ident[-3:]}</dc:identifier><dc:title xml:lang="nl" xsi:type="dcx:maintitle">{escape(title)}</dc:title>'
                f'<dcx:recordIdentifier xsi:type="dcterms:URI">http://example.com/DB=2.4/PPN?PPN=40{ident}'
                f'</dcx:recordIdentifier><dc:language xsi:type="dcterms:ISO639-2">dut</dc:language>'
                f'<dc:date>{year}</dc:date><dcterms:extent>{pages} pagina\'s</dcterms:extent>'
                f'<dc:subject xsi:type="dcx:NUR">{nur}</dc:subject><dc:creator dcx:role="aut">Aalders, Femke</dc:creator>'
                '</srw_dc:dc></srw:recordData></srw:record>')

    body = (record("0001", "Het zomerhuis aan de dijk", 2009, "301", 320) +
            record("0002", "De tweede zomer", 2012, "301", 288) +
            record("0003", "Pieter en de vlieger", 2014, "282", 40))
    write("kb_aalders.xml", ('<?xml version="1.0" encoding="UTF-8"?><srw:searchRetrieveResponse '
                             'xmlns:srw="http://www.loc.gov/zing/srw/"><srw:version>1.2</srw:version>'
                             f'<srw:numberOfRecords>3</srw:numberOfRecords><srw:records>{body}</srw:records>'
                             '</srw:searchRetrieveResponse>').encode())


def nb_files() -> None:
    def item(ident: str, title: str, year: int, pages: int) -> dict:
        return {"id": ident, "_links": {"self": {"href": f"https://api.nb.no/catalog/v1/items/{ident}"}},
                "metadata": {"creators": ["Holte, Sigrun"], "identifiers": {"isbn13": [f"97882000{ident[-5:]}"]},
                             "languages": [{"code": "nob"}], "mediaTypes": ["bøker"],
                             "originInfo": {"publisher": "Eksempelforlaget", "issued": str(year)},
                             "pageCount": pages, "title": title}}

    items = [item("holteitem0001", "Fjordlys : roman", 2015, 412),
             item("holteitem0002", "Vinterbro", 2018, 380)]
    write("nb_holte.json", {"_embedded": {"items": items}, "page": {"size": 100, "totalElements": 2, "number": 0}})


def bnf_files() -> None:
    def record(ident: str, title: str, year: int, audience: str, litcode: str, pages: int) -> str:
        general = f"20240101d{year}    m  y0frey50      ba"
        general = general[:17] + audience + general[18:]
        coded = "||||z   00" + "|" + litcode + "|"
        return (f'<srw:record><srw:recordData><mxc:record xmlns:mxc="info:lc/xmlns/marcxchange-v2" format="UNIMARC" '
                f'type="Bibliographic"><mxc:leader>     nam  22        450 </mxc:leader>'
                f'<mxc:controlfield tag="001">FRBNF{ident}</mxc:controlfield>'
                f'<mxc:datafield tag="010" ind1=" " ind2=" "><mxc:subfield code="a">978-2-00-000{ident[-3:]}-0</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="100" ind1=" " ind2=" "><mxc:subfield code="a">{general}</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="101" ind1="0" ind2=" "><mxc:subfield code="a">fre</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="105" ind1=" " ind2=" "><mxc:subfield code="a">{coded}</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="200" ind1="1" ind2=" "><mxc:subfield code="a">{escape(title)}</mxc:subfield>'
                f'<mxc:subfield code="b">Texte imprimé</mxc:subfield><mxc:subfield code="f">Odile Marchetti</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="215" ind1=" " ind2=" "><mxc:subfield code="a">{pages} p.</mxc:subfield></mxc:datafield>'
                f'<mxc:datafield tag="700" ind1=" " ind2="|"><mxc:subfield code="a">Marchetti</mxc:subfield>'
                f'<mxc:subfield code="b">Odile</mxc:subfield><mxc:subfield code="4">070</mxc:subfield></mxc:datafield>'
                '</mxc:record></srw:recordData></srw:record>')

    body = (record("000000010001", "La maison des marées", 2004, "m", "a", 310) +
            record("000000010002", "Les lettres de Brest", 2008, "m", "i", 180) +
            record("000000010003", "Le petit phare", 2011, "b", "a", 32))
    write("bnf_marchetti.xml", ('<?xml version="1.0" encoding="UTF-8"?><srw:searchRetrieveResponse '
                                'xmlns:srw="http://www.loc.gov/zing/srw/"><srw:version>1.2</srw:version>'
                                f'<srw:numberOfRecords>3</srw:numberOfRecords><srw:records>{body}</srw:records>'
                                '</srw:searchRetrieveResponse>').encode())


# ---- Two people of one name ---------------------------------------------------------------------------------------

#: Tobias Wren writes thrillers in English (born 1970); another Tobias Wren (born 1902) wrote about tides. The sources
#: know both under the name: Open Library and Audible by two numbers, Hardcover files everything under one author, and
#: Audible put a memoir of somebody else under the thriller writer's number.
WREN = "OL800001A"
WREN_TWICE = "OL800003A"
WREN_OTHER = "OL800002A"


def wren_files() -> None:
    def doc(key: str, title: str, year: int, author_key: str, pages: int = 320) -> dict:
        found = ol_doc(key, title, "Tobias Wren", year, ["eng"],
                       {"key": f"/books/{key[:-1]}M", "title": title, "language": ["eng"]}, pages=pages)
        found["author_key"] = [author_key]
        return found

    docs = [doc("OL80001W", "The Glass Harbour", 2004, WREN), doc("OL80002W", "Night Ferry", 2009, WREN),
            # Open Library filed the writer twice: the same book under another key.
            doc("OL80003W", "The Glass Harbour", 2004, WREN_TWICE),
            # The other Tobias Wren.
            doc("OL80004W", "Ledger of Tides", 1951, WREN_OTHER, pages=210)]
    write("ol_wren_en.json", {"numFound": len(docs), "start": 0, "docs": docs})
    write("ol_authors_wren.json", {"numFound": 3, "start": 0, "docs": [
        {"key": WREN, "name": "Tobias Wren", "birth_date": "1970", "work_count": 12, "type": "author"},
        {"key": WREN_OTHER, "name": "Tobias Wren", "birth_date": "1902", "work_count": 3, "type": "author"},
        {"key": WREN_TWICE, "name": "Tobias Wren", "work_count": 1, "type": "author"},
    ]})
    write("hc_author_search_wren.json", {"data": {"search": {"results": {"hits": [
        {"document": {"id": "71001", "name": "Tobias Wren", "books_count": 15, "alternate_names": []}},
        {"document": {"id": "71002", "name": "Tobias Wren", "books_count": 2, "alternate_names": []}},
    ]}}}})
    rows = []
    for index, (title, year) in enumerate((("The Glass Harbour", 2004), ("Night Ferry", 2009),
                                           ("Ledger of Tides", 1951), ("The Lantern Teacher", 2012))):
        rows.append({"contribution": None, "book": {
            "id": 82000 + index, "title": title, "release_date": f"{year}-04-01", "release_year": year,
            "compilation": False, "pages": 300, "literary_type_id": 1, "book_category_id": 1, "users_count": 90 - index,
            "cached_tags": {"Genre": [{"tag": "Thriller"}]}, "image": None, "canonical_id": None, "editions": [],
            "book_series": []}})
    write("hc_contributions_71001.json", {"data": {"contributions": rows}})

    def product(asin: str, title: str, author_asin: str, released: str) -> dict:
        found = audible_product(asin, title, "Tobias Wren", 640, "english", released)
        found["authors"] = [{"asin": author_asin, "name": "Tobias Wren"}]
        return found

    write("audible_us_wren.json", {"products": [
        product("B0WRENPR01", "The Glass Harbour", "B0WRENAU01", "2005-01-01"),
        # Somebody else's memoir under the writer's number (as the real catalogue does now and then).
        product("B0WRENPR02", "The Lantern Teacher", "B0WRENAU01", "2012-06-01"),
        product("B0WRENPR03", "Ledger of Tides", "B0WRENAU02", "2015-01-01")], "total_results": 3})


if __name__ == "__main__":
    wren_files()
    ol_files()
    hc_files()
    audible_files()
    dnb_files()
    libris_files()
    kb_files()
    nb_files()
    bnf_files()
    print("written")
