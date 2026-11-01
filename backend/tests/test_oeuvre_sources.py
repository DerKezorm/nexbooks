"""Which source the catalogue of an author asks for which language, how the answers are read, how often a source is
asked (throttled, kept for a day) and what happens when one is away or switched off. Every answer is
recorded and invented (``tests/fixtures/oeuvre``)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, CatalogWork
from app.services.oeuvre import gather, kinds, store
from app.services.sources import bnf, kb, libris, nb, throttle
from app.services.sources.model import Entry

from .oeuvre_fakes import Sources, load


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Sources:
    return Sources(monkeypatch)


def reader(language: str) -> None:
    with SessionLocal() as db:
        db.add(Account(name=f"reader-{language}", display_name="R", role="member", sign_in="password",
                       password_hash="x", language=language))
        db.commit()


def works_of(name: str) -> dict[str, CatalogWork]:
    with SessionLocal() as db:
        row = store.for_name(db, name)
        assert row is not None
        db.commit()
        store.build(db, row)
        found = {}
        for work in db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id)):
            for item in (work.titles or {}).values():
                found[item["title"]] = work
        db.expunge_all()
        return found


# ---- Reading the answers ---------------------------------------------------------------------------------------


def test_the_french_library_gives_titles_readers_and_form() -> None:
    entries, page, total = bnf.parse(load("bnf_marchetti.xml"), "Odile Marchetti")
    assert (page, total) == (3, 3)
    by = {e.title: e for e in entries}
    assert by["La maison des marées"].year == 2004 and by["La maison des marées"].pages == 310
    assert by["La maison des marées"].signals["audience"] == "m" and by["La maison des marées"].signals["litcode"] == "a"
    assert by["Le petit phare"].signals["audience"] == "b"
    assert by["Les lettres de Brest"].signals["litcode"] == "i"
    assert all(e.language == "fr" for e in entries)
    # Another author of the same last name is no record of hers.
    assert bnf.parse(load("bnf_marchetti.xml"), "Paul Marchetti")[0] == []


def test_libris_keeps_the_swedish_records_only() -> None:
    import json

    items = json.loads(load("libris_lindqvist.json"))["xsearch"]["list"]
    found = [e for item in items if (e := libris.entry_of(item, "Marit Lindqvist", "sv")) is not None]
    assert [e.title for e in found] == ["Den tysta hamnen", "Mannen vid kanalen", "Isvägen"]
    assert found[0].year == 1971 and found[0].authors == ["Lindqvist, Marit"]


def test_the_dutch_library_reads_the_trade_code() -> None:
    entries, page, _total = kb.parse(load("kb_aalders.xml"), "Femke Aalders")
    assert page == 3
    by = {e.title: e for e in entries}
    assert by["Pieter en de vlieger"].signals["nur"] == ["282"] and by["Het zomerhuis aan de dijk"].pages == 320


def test_the_norwegian_library_cuts_the_subtitle() -> None:
    import json

    items = json.loads(load("nb_holte.json"))["_embedded"]["items"]
    found = [nb.entry_of(item, "Sigrun Holte") for item in items]
    assert [e.title for e in found if e] == ["Fjordlys", "Vinterbro"]
    assert nb.entry_of(items[0], "Arne Holte") is None


def test_a_record_of_the_german_library_in_a_language_without_a_code_here_is_no_german_one() -> None:
    """Measured: translations into languages nexbooks has no code for (041 $a ``slv``) counted as German and joined
    other works. Such a record is in an unknown language; a record that names no language at all is German."""
    from app.services.sources import dnb

    record = (b'<?xml version="1.0"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>1'
              b'</numberOfRecords><records><record><recordData><record xmlns="http://www.loc.gov/MARC21/slim">'
              b'<leader>00000nam a2200000 c 4500</leader><controlfield tag="001">77</controlfield>'
              b'<datafield tag="041" ind1=" " ind2=" "><subfield code="a">slv</subfield></datafield>'
              b'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">Falkner, Ida</subfield></datafield>'
              b'<datafield tag="245" ind1="1" ind2="0"><subfield code="a">Megleni zbor</subfield></datafield>'
              b"</record></recordData></record></records></searchRetrieveResponse>")
    found = dnb.parse_page(record)[0]
    assert [dnb.entry_of(r).language for r in found] == ["und"]  # type: ignore[union-attr]
    plain = record.replace(b'<datafield tag="041" ind1=" " ind2=" "><subfield code="a">slv</subfield></datafield>', b"")
    assert [dnb.entry_of(r).language for r in dnb.parse_page(plain)[0]] == ["de"]  # type: ignore[union-attr]


def test_a_hostile_answer_is_refused() -> None:
    hostile = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    for parser in (bnf.parse, kb.parse):
        with pytest.raises(ValueError):
            parser(hostile, "Odile Marchetti")


# ---- The source for the language ------------------------------------------------------------------------------


def test_a_swedish_author_is_asked_at_libris_after_the_translations_say_so(sources: Sources) -> None:
    reader("de")
    works = works_of("Marit Lindqvist")
    assert sources.fake.count("libris.kb.se") >= 1
    harbour = works["Der stille Hafen"]
    assert harbour.orig_language == "sv"
    assert {code: item["title"] for code, item in harbour.titles.items()} == {
        "de": "Der stille Hafen", "sv": "Den tysta hamnen"}


def test_one_source_is_enough_for_a_language_only_it_knows(sources: Sources) -> None:
    reader("nl")
    works = works_of("Femke Aalders")
    assert set(works) == {"Het zomerhuis aan de dijk", "De tweede zomer", "Pieter en de vlieger"}
    assert works["Pieter en de vlieger"].kind == "kinder" and works["De tweede zomer"].kind == "roman"


def test_a_norwegian_and_a_french_author_are_asked_at_their_libraries(sources: Sources) -> None:
    reader("no")
    reader("fr")
    holte = works_of("Sigrun Holte")
    assert "Fjordlys" in holte and sources.fake.count("api.nb.no") >= 1
    marchetti = works_of("Odile Marchetti")
    assert marchetti["Le petit phare"].kind == "kinder"
    assert marchetti["Les lettres de Brest"].kind in ("unsure", "lyrik")
    assert marchetti["La maison des marées"].kind == "roman"


def test_a_source_that_is_switched_off_is_not_asked(sources: Sources) -> None:
    reader("de")
    sources.settings(source_audible=False, source_hardcover=False)
    works_of("Edmund Halloran")
    assert sources.fake.count("audible") == 0 and sources.fake.count("hardcover") == 0
    assert sources.fake.count("dnb") >= 1


def test_a_source_that_is_away_leaves_the_others_to_build(sources: Sources) -> None:
    reader("de")
    sources.fake.status["services.dnb.de"] = 503
    works = works_of("Edmund Halloran")
    assert "The Ninth Shore" in works
    with SessionLocal() as db:
        row = store.for_name(db, "Edmund Halloran")
        assert row is not None and "dnb" in row.sources["away"]


def test_nothing_is_built_when_every_source_is_away(sources: Sources) -> None:
    for host in ("openlibrary.org", "api.hardcover.app", "services.dnb.de", "api.audible.com", "api.audible.de"):
        sources.fake.status[host] = 503
    with SessionLocal() as db:
        row = store.for_name(db, "Edmund Halloran")
        assert row is not None
        db.commit()
        assert store.build(db, row) is False
        assert row.state == "failed" and row.built_at is None


def test_the_answers_are_kept_for_a_day_and_asked_again_after(sources: Sources) -> None:
    reader("de")
    works_of("Edmund Halloran")
    asked = len(sources.fake.log)
    works_of("Edmund Halloran")
    assert len(sources.fake.log) == asked
    sources.days(1.01)
    works_of("Edmund Halloran")
    assert len(sources.fake.log) == 2 * asked


def test_the_sources_are_asked_at_their_pace(sources: Sources) -> None:
    reader("de")
    works_of("Edmund Halloran")
    # Every request takes a token of its source; past the burst the background waits (the clock jumps).
    assert throttle.state_of("openlibrary").day_count == sources.fake.count("openlibrary.org")
    assert sources.offset > 0


def test_pen_names_are_names_of_others_not_spellings() -> None:
    assert gather.pen_names("Edmund Halloran", ["Richard Vane", "E. Halloran", "Halloran, Edmund",
                                                "Vane Richard"]) == ["Richard Vane"]


def test_the_original_language_comes_from_the_translations_first() -> None:
    entries = [Entry(source="dnb", key="1", title="A", language="de", original_language="sv"),
               Entry(source="dnb", key="2", title="B", language="de", original_language="sv"),
               Entry(source="dnb", key="3", title="C", language="de")]
    assert gather.original_language(entries, ["de"]) == "sv"
    assert gather.original_language(entries[2:], ["fr", "fr"]) == "fr"
    assert gather.original_language(entries[2:], []) == "de"


# ---- The kinds -------------------------------------------------------------------------------------------------


def e(source: str, **values: object) -> Entry:
    return Entry(source=source, key=str(id(values)), title=str(values.pop("title", "Der Titel")), **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(("entries", "expected"), [
    ([e("dnb", signals={"group": ["B"]}), e("hardcover", signals={"lit": 1, "cat": 1})], ("roman", "")),
    ([e("dnb", signals={"group": ["K"]}), e("openlibrary", signals={"subjects": ["fiction"]})], ("kinder", "")),
    ([e("hardcover", signals={"lit": 2, "genres": ["biography"]}), e("openlibrary", signals={"subjects": ["biography"]})],
     ("bio", "")),
    ([e("hardcover", signals={"lit": 2}), e("dnb", signals={"group": ["150"]})], ("sach", "")),
    ([e("openlibrary", title="Stories of the Coast"), e("dnb", title="Stories of the Coast")], ("erz", "")),
    ([e("openlibrary", title="Die Reihe. Band 1 bis 3"), e("dnb", title="Die Reihe. Band 1 bis 3")], ("box", "")),
    ([e("hardcover", compilation=True), e("openlibrary")], ("box", "")),
    ([e("openlibrary", signals={"subjects": ["comics & graphic novels"]}), e("hardcover", signals={"genres": ["comics"]})],
     ("unsure", "comic")),
    # Two library records say "fiction for adults": one record of a comic does not tip the work.
    ([e("dnb", signals={"group": ["B"]}), e("dnb", signals={"group": ["B"]}), e("hardcover", signals={"lit": 1}),
      e("openlibrary", signals={"subjects": ["comics & graphic novels"]})], ("roman", "")),
    # "children" is a subject, not readers.
    ([e("openlibrary", signals={"subjects": ["children", "family"]}), e("hardcover", signals={"lit": 1})], ("roman", "")),
    ([e("openlibrary", pages=60), e("hardcover", pages=64)], ("unsure", "short")),
    # A short book with a long unabridged recording is a novel.
    ([e("openlibrary", pages=110), e("audible", runtime_min=420, form="audio")], ("roman", "")),
    ([e("openlibrary", title="Der Titel (Leseprobe)"), e("dnb", title="Der Titel (Leseprobe)")], ("unsure", "sample")),
    ([e("openlibrary", authors_count=6), e("audible", authors_count=5, form="audio")], ("unsure", "anthology")),
    ([e("bnf", signals={"audience": "m", "litcode": "c"}), e("openlibrary", signals={"subjects": ["poetry"]})],
     ("lyrik", "")),
])
def test_the_kind_of_a_work(entries: list[Entry], expected: tuple[str, str]) -> None:
    assert kinds.kind_of(entries) == expected


def test_a_book_about_the_author_is_unsure_and_a_work_known_only_in_translation_stands_in_its_language() -> None:
    from app.services.oeuvre import works

    entries = [Entry(source="dnb", key="1", title="Ida Falkner, Der Nebelchor", language="de"),
               Entry(source="openlibrary", key="2", title="Ida Falkner, Der Nebelchor", language="de"),
               # Translations that say they come from English, with nobody knowing the English title.
               Entry(source="dnb", key="3", title="Die Salzwiese", language="de", original_language="en"),
               Entry(source="openlibrary", key="4", title="Die Salzwiese", language="de")]
    built = {w.title_in(w.orig_language): w for w in works.build(entries, answered={"de": {"dnb", "openlibrary"}},
                                                                  author_language="en", author="Ida Falkner")}
    assert (built["Ida Falkner, Der Nebelchor"].kind, built["Ida Falkner, Der Nebelchor"].why) == ("unsure", "about")
    assert built["Die Salzwiese"].orig_language == "de" and built["Die Salzwiese"].kind == "roman"


def test_an_isbn_never_joins_two_works_and_a_number_two_works_share_recognises_no_book() -> None:
    """Measured against the real German library: records that list the ISBNs of other books joined a prolific author's
    novels into one work. Titles join works, numbers do not; and a number two works carry proves neither."""
    from types import SimpleNamespace

    from app.services.oeuvre import view, works

    shared = "9780000009999"
    entries = [Entry(source="dnb", key="1", title="Der Nebelchor", language="de", isbns=["9780000000011", shared]),
               Entry(source="openlibrary", key="2", title="Der Nebelchor", language="de"),
               Entry(source="dnb", key="3", title="Die Salzwiese", language="de", isbns=[shared]),
               Entry(source="openlibrary", key="4", title="Die Salzwiese", language="de"),
               # A set that lists the numbers of both books: it joins nothing and gives its numbers to nobody.
               Entry(source="dnb", key="5", title="Zwei Romane", language="de",
                     isbns=["9780000000011", "9780000000028", shared])]
    built = works.build(entries, answered={"de": {"dnb", "openlibrary"}}, author_language="de")
    titles = sorted(w.title_in("de") for w in built)
    assert titles == ["Der Nebelchor", "Die Salzwiese"]
    rows = [SimpleNamespace(id=index, isbns=w.isbns, refs=[], titles={k: e.to_json() for k, e in w.titles.items()},
                            series_pos=None) for index, w in enumerate(built)]
    nebel = next(r for r in rows if r.titles["de"]["title"] == "Der Nebelchor")
    own = view.Own(view=None, titles=["Ein ganz anderer Titel"], numbers={shared})  # type: ignore[arg-type]
    assert view.match(rows, [own], "Ida Falkner") == {}  # type: ignore[arg-type]
    own.numbers = {"9780000000011"}
    assert list(view.match(rows, [own], "Ida Falkner")) == [nebel.id]  # type: ignore[arg-type]


def test_records_in_a_language_the_catalogue_is_not_built_for_are_left_out(sources: Sources) -> None:
    reader("de")
    works = works_of("Edmund Halloran")
    # Audible's German market lists a Spanish recording too: nobody asked for Spanish, it stands nowhere.
    assert "Las hijas de la sal" not in works
    assert all(code in ("de", "en") for work in works.values() for code in (work.titles or {}))


def test_a_day_to_come_beside_an_earlier_year_is_a_new_edition_not_a_book_to_come() -> None:
    from app.routers.catalog import upcoming

    today = "2026-10-12"
    assert upcoming({"released": "2027-03-03", "year": 2027}, today)
    assert upcoming({"released": "", "year": 2027}, today)
    assert not upcoming({"released": "2027-03-03", "year": 1980}, today)
    assert not upcoming({"released": "2025-01-01", "year": 2025}, today)
    assert not upcoming({"released": "", "year": None}, today)


def test_hardcover_gives_a_book_its_own_title_as_english_only_for_an_author_who_writes_in_english() -> None:
    from app.services.sources import hardcover

    rows = [{"contribution": None, "book": {"id": 5, "title": "Der Nebelchor", "release_year": 2001, "editions": [],
                                             "book_series": [], "canonical_id": None}}]
    assert [(e.language, e.title, e.year) for e in hardcover.contribution_entries(rows, ["de", "en"], "en")] == [
        ("en", "Der Nebelchor", 2001)]
    assert hardcover.contribution_entries(rows, ["de", "en"], "de") == []
    # A German edition: its title in German, never the same title once more as English.
    rows[0]["book"]["editions"] = [{"title": "Der Nebelchor", "language": {"language": "German"}}]
    assert [(e.language, e.title) for e in hardcover.contribution_entries(rows, ["de", "en"], "")] == [
        ("de", "Der Nebelchor")]
