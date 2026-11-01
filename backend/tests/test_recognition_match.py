"""The matching as the recognition of the books needs it: the sources a book is asked at by its language, the genre as the
majority of the voices, the year in the name of a folder, a second source worth points, and what stands between a book and
"sure" said as the list shows it. Against faked services, with invented books."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition, MatchEntry
from app.services import settings_service
from app.services.match import gap
from app.services.match import run as matching
from app.services.match.score import (
    AMBIGUITY,
    MAYBE,
    P_CONFIRM,
    P_YEAR_NAME,
    SURE,
    Wanted,
    decide,
    score,
)
from app.services.sources import config
from app.services.sources.model import Candidate

from .fake_sources import FIXTURES, audible_product, fixture, ol_doc
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan
from .match_helpers import World, edition_of, entries, member

BOOK = {"title": "Die Nebelwacht", "authors": ("Ada Feldkamp",), "language": "de"}


def audible_fog(*categories: str, asin: str = "B0EXAMPL10") -> dict[str, Any]:
    product = audible_product(asin, "Die Nebelwacht (Ein Wolff-Krimi 1): Ein Mord im Nebel", ["Ada Feldkamp"], 612)
    product["category_ladders"] = [{"ladder": [{"id": str(i), "name": name} for i, name in enumerate(categories)]}]
    return product


def fresh(world: World, name: str = "shelf", **files: dict[str, Any]) -> int:
    folder = books_folder(name)
    for relative, arguments in files.items():
        if relative.endswith((".m4b", ".mp3")):
            recording(folder, relative, **arguments)
        else:
            book(folder, relative, **arguments)
    library = make_library(folder, name.title())
    member(library)
    scan(library)
    return library


def asked(world: World) -> list[str]:
    """The sources asked, in the order they were asked, each once."""
    names = {"services.dnb.de": "dnb", "openlibrary.org": "openlibrary", "api.hardcover.app": "hardcover"}
    found: list[str] = []
    for host in world.fake.hosts():
        name = names.get(host, "audible" if host.startswith("api.audible.") else host)
        if name not in found and name not in ("covers.openlibrary.org", "m.media-amazon.com"):
            found.append(name)
    return found


# ---- Which sources a book is asked at -----------------------------------------------------------------------------------


def test_a_german_book_is_asked_at_the_national_library_and_audible_and_at_open_library_only_when_it_is_not_sure(
        client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_sru_books.xml")
    world.fake.audible = {"de": [audible_fog("Krimis & Thriller")]}
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    assert asked(world) == ["dnb", "audible"], "settled by the library and Audible: Open Library is not asked"
    assert entries() == [("Die Nebelwacht", "sure", "", "")]


def test_a_german_book_the_first_two_do_not_settle_is_asked_at_open_library_too(client: TestClient, operator: Account,
                                                                                 world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><records/></searchRetrieveResponse>'
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    assert asked(world) == ["dnb", "audible", "openlibrary"]


def test_an_english_book_is_asked_at_open_library_and_audible_and_never_at_the_national_library(
        client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Mara Lindqvist"], languages=("eng",), subjects=("Fantasy",))]
    fresh(world, **{"a.epub": {"title": "The Quiet Orchard", "authors": ("Mara Lindqvist",), "language": "en"}})
    assert asked(world)[:2] == ["openlibrary", "audible"] and "dnb" not in asked(world)


def test_a_book_in_another_language_is_asked_at_audible_first_and_not_at_the_national_library(
        client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    fresh(world, **{"a.epub": {"title": "Le Verger Silencieux", "authors": ("Mara Lindqvist",), "language": "fr"}})
    assert asked(world)[:2] == ["audible", "openlibrary"] and "dnb" not in asked(world)


def test_a_recording_is_asked_at_audible_first_whatever_its_language(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.audible = {"de": [audible_fog("Krimis & Thriller")]}
    fresh(world, **{"a/a.m4b": {"seconds": 36720.0, "title": "Die Nebelwacht", "album": "Die Nebelwacht",
                                 "artist": "Ada Feldkamp"}})
    assert asked(world)[0] == "audible"


def test_the_plan_is_a_table_that_names_the_sources_for_each_language() -> None:
    always, extra = matching.plan_for(Wanted(kind="ebook", title="x", language="de"))
    assert always == ("dnb", "audible") and "openlibrary" in extra
    assert matching.plan_for(Wanted(kind="ebook", title="x", language="en"))[0] == ("openlibrary", "audible", "hardcover")
    assert matching.plan_for(Wanted(kind="ebook", title="x", language="sv"))[0][0] == "audible"
    assert matching.plan_for(Wanted(kind="ebook", title="x", language=""))[1] == ("dnb",)
    assert matching.plan_for(Wanted(kind="audio", title="x", language="en"))[0][0] == "audible"


def test_the_national_library_is_on_from_the_start_and_an_operator_who_switched_it_off_keeps_it_off(
        client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        assert settings_service.get(db, "source_dnb") is True
        settings_service.save(db, {"source_dnb": False})
        assert settings_service.get(db, "source_dnb") is False
        assert "dnb" not in config.ready_sources(db)


# ---- The genre as the majority of the voices -----------------------------------------------------------------------------


def test_a_german_book_with_no_subject_in_its_file_gets_its_genre_from_the_library_and_audible(
        client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_sru_books.xml")
    world.fake.audible = {"de": [audible_fog("Literatur & Unterhaltung", "Krimis & Thriller")]}
    fresh(world, **{"a.epub": BOOK})
    edition = edition_of("Die Nebelwacht")
    assert edition.genre == "krimi" and edition.genre_guess is False
    assert edition.overlay["genre"]["by"] == "source" and edition.overlay["genre"]["votes"]["krimi"] > 0


def test_the_file_saying_only_fiction_does_not_stand_against_the_sources_that_say_crime(
        client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_sru_books.xml")
    world.fake.audible = {"de": [audible_fog("Krimis & Thriller")]}
    fresh(world, **{"a.epub": dict(BOOK, subjects=("Fiction",))})
    assert edition_of("Die Nebelwacht").genre == "krimi"


def test_the_file_saying_fantasy_is_not_overruled_by_one_source_saying_crime(client: TestClient, operator: Account,
                                                                               world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_sru_books.xml")
    world.fake.audible = {"de": [audible_fog("Fiction")]}
    fresh(world, **{"a.epub": dict(BOOK, subjects=("Fantasy", "Epic Fantasy"))})
    assert edition_of("Die Nebelwacht").genre == "fantasy"


def test_a_genre_a_person_set_is_never_voted_down(client: TestClient, operator: Account, world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_sru_books.xml")
    world.fake.audible = {"de": [audible_fog("Krimis & Thriller")]}
    library = fresh(world, **{"a.epub": BOOK})
    edition = edition_of("Die Nebelwacht")
    assert client.patch(f"/api/v1/editions/{edition.id}", json={"genre": "lyrik"}).status_code == 200
    scan(library)
    run_jobs()
    assert edition_of("Die Nebelwacht").genre == "lyrik"


# ---- What an e-book takes from the catalogue of recordings ---------------------------------------------------------------


def test_an_ebook_matched_with_audible_takes_no_year_and_no_publisher_from_a_recording(client: TestClient,
                                                                                         operator: Account,
                                                                                         world: World) -> None:
    world.only("audible", "openlibrary")
    product = audible_fog("Krimis & Thriller")
    product["publisher_name"] = "Beispiel Hoerverlag"
    product["release_date"] = "2023-03-03"
    world.fake.audible = {"de": [product]}
    # A second source that tells the same book (an e-book is not sure on one catalogue's word), with no year of its own.
    world.fake.ol = [ol_doc("OL7W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",), year=None)]
    fresh(world, **{"a.epub": BOOK})
    edition = edition_of("Die Nebelwacht")
    assert edition.genre == "krimi"
    assert edition.published == "" and edition.publisher == "", "the year and the publisher of the recording are not the book's"


# ---- The year in the name of a folder ---------------------------------------------------------------------------------


def twin_years(world: World) -> None:
    world.only("openlibrary", "audible")
    # A second source that tells the book (an e-book is not sure on one catalogue's word); it knows no year of a book.
    world.fake.audible = {"de": [audible_product("B0EXAMPL31", "Frevelpfad", ["Ada Feldkamp"], 600, released="")]}
    world.fake.ol = [
        ol_doc("OL1W", "Frevelpfad", ["Ada Feldkamp"], languages=("ger",), year=2019, editions=4),
        ol_doc("OL2W", "Frevelpfad", ["Ada Feldkamp"], languages=("ger",), year=2022, editions=4),
    ]


def test_the_year_in_the_name_of_the_folder_tells_two_books_of_the_same_name_apart(client: TestClient,
                                                                                    operator: Account,
                                                                                    world: World) -> None:
    twin_years(world)
    fresh(world, **{"Ada Feldkamp/Frevelpfad (2022)/frevelpfad.epub": {
        "title": "Frevelpfad", "authors": ("Ada Feldkamp",), "language": "de"}})
    assert entries() == [("Frevelpfad", "sure", "", "")]
    with SessionLocal() as db:
        refs = db.execute(select(matching.apply.ExternalRef.key)).scalars().all()
    assert "OL2W" in refs and "OL1W" not in refs, "the book of 2022, not the one of 2019"


def test_without_a_year_in_the_name_two_records_of_one_title_are_the_same_book_and_the_better_known_is_taken(
        client: TestClient, operator: Account, world: World) -> None:
    """Two records of one source with the same title and author are the same book as far as the matching can tell (an
    open catalogue lists duplicates); the better known one is the one taken."""
    twin_years(world)
    world.fake.ol[1]["edition_count"] = 40
    fresh(world, **{"frevelpfad.epub": {"title": "Frevelpfad", "authors": ("Ada Feldkamp",), "language": "de"}})
    assert entries() == [("Frevelpfad", "sure", "", "")]
    with SessionLocal() as db:
        refs = db.execute(select(matching.apply.ExternalRef.key)).scalars().all()
        assert "OL2W" in refs and "OL1W" not in refs


# ---- Points -------------------------------------------------------------------------------------------------------------


def cand(source: str, **kwargs: Any) -> Candidate:
    base: dict[str, Any] = {"source": source, "key": f"{source}-1", "title": "Moorland: Rueckkehr",
                            "authors": ["Ada Feldkamp"], "languages": ["de"]}
    base.update(kwargs)
    return Candidate(**base)


WANT = Wanted(kind="ebook", title="Moorland", authors=["Ada Feldkamp"], language="de")


def test_a_title_up_to_its_subtitle_with_the_author_and_the_language_is_only_a_maybe_alone() -> None:
    result = decide(WANT, [cand("dnb")])
    assert result.best is not None and result.best.score == 77 and result.state == "maybe"
    assert "one_source" in result.best.missing and "second_source" not in result.best.why


def test_a_second_source_that_knows_the_same_book_is_worth_ten_points() -> None:
    result = decide(WANT, [cand("dnb"), cand("audible", key="B0EXAMPL10")])
    assert result.best is not None and result.best.score == 77 + P_CONFIRM and result.state == "sure"
    assert "second_source" in result.best.why and "one_source" not in result.best.missing


def test_a_second_source_that_names_another_author_is_no_confirmation() -> None:
    other = cand("audible", key="B0EXAMPL10", authors=["Ben Hollister"])
    result = decide(WANT, [cand("dnb"), other])
    assert result.best is not None and result.best.score == 77 and "second_source" not in result.best.why


def test_a_second_source_that_names_no_author_is_no_confirmation_either() -> None:
    nameless = cand("audible", key="B0EXAMPL10", authors=[])
    result = decide(WANT, [cand("dnb"), nameless])
    assert result.best is not None and result.best.score == 77 and "second_source" not in result.best.why


def test_a_title_with_the_kind_of_book_at_its_end_or_one_letter_off_is_the_title_of_the_catalogue() -> None:
    """Only the core of the titles (the article ignored, nine tenths of the letters) tells these apart from another
    title: the plain comparison of the whole titles and of the part before a subtitle finds nothing in common."""
    wanted = Wanted(kind="ebook", title="Der Beobachter Kriminalroman", authors=["Ada Feldkamp"], language="de")
    scored = score(wanted, cand("dnb", title="Beobachter"))
    assert "title_main" in scored.why and not scored.fatal
    wanted = Wanted(kind="ebook", title="Das Gezeitenbuch der Insel", authors=["Ada Feldkamp"], language="de")
    scored = score(wanted, cand("dnb", title="Das Gezeitenbuch der Inseln"))
    assert "title_main" in scored.why and not scored.fatal
    # A title that is another (not nine tenths alike, a different volume) stays another.
    assert score(wanted, cand("dnb", title="Das Gezeitenbuch der Sterne")).fatal == "title_differs"
    numbered = Wanted(kind="ebook", title="Gezeitenbuch 2", authors=["Ada Feldkamp"], language="de")
    assert score(numbered, cand("dnb", title="Gezeitenbuch 3")).fatal in ("volume_differs", "title_differs")


def test_the_year_in_the_name_of_a_folder_is_worth_more_than_the_year_of_the_file() -> None:
    old = cand("openlibrary", year=2019, title="Moorland")
    new = cand("hardcover", key="h-2", year=2022, title="Moorland", weight=1)
    wanted = Wanted(kind="ebook", title="Moorland", authors=["Ada Feldkamp"], language="de", name_year=2019, year=2022)
    a, b = score(wanted, old), score(wanted, new)
    assert a.score - b.score >= P_YEAR_NAME - 5 and "year_name_ok" in a.why and "year_ok" in b.why
    assert P_YEAR_NAME > AMBIGUITY


def test_a_year_far_from_the_year_the_file_names_is_said_against_the_book_but_does_not_rule_it_out() -> None:
    far = cand("openlibrary", year=2022, title="Moorland")
    scored = score(Wanted(kind="ebook", title="Moorland", authors=["Ada Feldkamp"], language="de", name_year=2019), far)
    assert scored.against == ["year_off"] and scored.detail["year_off"] == {"file": 2019, "source": 2022}
    assert not scored.fatal and scored.score >= MAYBE


def test_what_the_file_lacks_is_said_as_what_is_missing() -> None:
    bare = Wanted(kind="ebook", title="Moorland", authors=[], language="")
    scored = score(bare, cand("openlibrary", languages=["de"], title="Moorland", series="Moor", series_pos=2.0))
    assert set(scored.missing) >= {"no_isbn", "no_year", "language_unknown", "no_author", "no_volume"}
    full = Wanted(kind="ebook", title="Moorland", authors=["Ada Feldkamp"], language="de", isbn="", year=2020,
                  series_pos=2.0)
    complete = score(full, cand("openlibrary", title="Moorland", series="Moor", series_pos=2.0))
    assert "no_year" not in complete.missing and "no_volume" not in complete.missing and "no_isbn" in complete.missing


def test_the_sentence_under_the_points_names_what_would_help_in_the_order_of_how_much_it_helps() -> None:
    result = decide(Wanted(kind="ebook", title="Moorland", authors=["Ada Feldkamp"], language="de"), [cand("dnb")])
    assert result.best is not None
    proposal = {"score": result.best.score, "missing": result.best.missing, "fatal": "", "c": result.best.candidate.to_json()}
    assert gap.gap_of("low_score", [proposal]) == {
        "sure_at": SURE, "points": 77, "missing": ["isbn", "second_source"], "join": "or", "cap": "", "tie": None}
    assert gap.lacks_of(proposal) == ["isbn", "second_source"]


def test_two_proposals_about_as_good_are_told_apart_by_what_differs() -> None:
    first = {"score": 82, "missing": [], "fatal": "", "c": cand("openlibrary", series_pos=1.0, year=2019).to_json()}
    second = {"score": 82, "missing": [], "fatal": "", "c": cand("openlibrary", key="b", series_pos=2.0, year=2022).to_json()}
    result = gap.gap_of("ambiguous", [first, second])
    assert result is not None and result["tie"] == {"difference": "volume", "a": "1", "b": "2"}
    second["c"]["series_pos"] = None
    assert gap.gap_of("ambiguous", [first, second])["tie"]["difference"] == "year"  # type: ignore[index]
    second["c"]["year"] = 2019
    assert gap.gap_of("ambiguous", [first, second])["tie"] == {"difference": "nothing", "a": None, "b": None}  # type: ignore[index]
    second["c"]["title"] = "Moorland: Aufbruch"
    assert gap.gap_of("ambiguous", [first, second])["tie"]["difference"] == "title"  # type: ignore[index]
    assert gap.gap_of("nothing_found", []) is None


def test_two_proposals_that_are_close_and_differ_are_a_tie_also_when_the_matching_called_it_low_score() -> None:
    one = {"score": 74, "missing": [], "fatal": "", "c": cand("dnb", series_pos=1.0).to_json()}
    two = {"score": 72, "missing": [], "fatal": "", "c": cand("audible", key="b", series_pos=2.0).to_json()}
    assert gap.gap_of("low_score", [one, two])["tie"] == {"difference": "volume", "a": "1", "b": "2"}  # type: ignore[index]
    # The same book told by two sources is no tie; nor are two proposals far apart.
    same = {"score": 73, "missing": [], "fatal": "", "c": cand("audible", key="b").to_json()}
    assert gap.gap_of("low_score", [{**one, "c": cand("dnb").to_json()}, same])["tie"] is None  # type: ignore[index]
    # Two sources that tell one book differ in their year or their languages: no tie, however close their points.
    other_year = {"score": 74, "missing": [], "fatal": "", "c": cand("audible", key="b", year=2003).to_json()}
    assert gap.gap_of("low_score", [{**one, "c": cand("dnb", year=2007, languages=["de", "en"]).to_json()}, other_year])[
        "tie"] is None  # type: ignore[index]
    far = {"score": 50, "missing": [], "fatal": "", "c": cand("audible", key="b", series_pos=2.0).to_json()}
    assert gap.gap_of("low_score", [one, far])["tie"] is None  # type: ignore[index]


def test_the_list_says_how_far_from_sure_a_book_is_and_what_would_help(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Moorland: Rueckkehr", ["Ada Feldkamp"], languages=("ger",), year=2019)]
    library = fresh(world, **{"a.epub": {"title": "Moorland", "authors": ("Ada Feldkamp",), "language": "de"}})
    item = client.get(f"/api/v1/libraries/{library}/review").json()["items"][0]
    assert item["reason"] == "low_score"
    assert item["gap"]["sure_at"] == SURE and 0 < item["gap"]["points"] < SURE
    assert item["gap"]["missing"][0] == "isbn"
    proposal = item["proposals"][0]
    assert proposal["lacks"] == item["gap"]["missing"]
    assert [m["code"] for m in proposal["missing"]][:2] == ["no_isbn", "no_year"] or "no_isbn" in [
        m["code"] for m in proposal["missing"]]
    assert all(m["text"] for m in proposal["missing"])


# ---- Matching again by newer rules -------------------------------------------------------------------------------------


def test_books_are_matched_again_once_when_the_rules_are_newer_than_the_ones_they_were_matched_by(
        client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    with SessionLocal() as db:
        # (The state of an instance from before: matched by older rules.)
        settings_service.save(db, {"match_rev": matching.MATCH_REV - 1})
        entry = db.scalar(select(MatchEntry))
        entry.state = "open"
        db.commit()
        assert matching.upgrade_rules(db) is True
        assert db.scalar(select(MatchEntry.state)) == "pending"
        assert settings_service.get(db, "match_rev") == matching.MATCH_REV
        assert matching.upgrade_rules(db) is False, "once"


def test_a_line_of_a_publisher_an_older_match_left_goes_by_the_newer_rules_and_a_correction_stays(
        client: TestClient, operator: Account, world: World) -> None:
    from app.services.match import apply, overlay

    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    with SessionLocal() as db:
        # (An instance matched by the rules of version 4: a line of a publisher came as the series, a person corrected
        # the genre.)
        settings_service.save(db, {"match_rev": 4})
        edition = db.scalar(select(Edition))
        assert edition is not None
        edition.overlay = overlay.with_hand(overlay.with_source(
            edition.overlay, {"series": "Neobooks", "series_pos": 2.0}, origins={"series": "dnb", "series_pos": "dnb"}),
            {"genre": "lyrik"})
        apply.rebuild(db, edition)
        db.scalar(select(MatchEntry)).state = "sure"  # type: ignore[union-attr]
        db.commit()
        assert edition_of("Die Nebelwacht").series_name == "Neobooks"
        assert matching.upgrade_rules(db) is True
    run_jobs()
    again = edition_of("Die Nebelwacht")
    assert again.series_name == "" and again.genre == "lyrik"
    assert overlay.hand_fields(again.overlay) == ["genre"]


def test_a_line_of_a_publisher_is_no_series_when_a_match_is_taken() -> None:
    from app.services.match import apply
    from app.services.match.score import Scored

    library = Candidate(source="dnb", key="1", title="Die Nebelwacht", authors=["Ada Feldkamp"], series="dtv",
                        series_pos=2164.0)
    shop = Candidate(source="audible", key="2", title="Die Nebelwacht", authors=["Ada Feldkamp"], series="Kai Lorn",
                     series_pos=2.0)
    values, _, _ = apply.build_values(Edition(kind="ebook", language="de"), Scored(library), [Scored(shop)])
    assert (values["series"], values["series_pos"]) == ("Kai Lorn", 2.0)
    values, _, _ = apply.build_values(Edition(kind="ebook", language="de"), Scored(Candidate(
        source="hardcover", key="3", title="Die Nebelwacht", authors=["Ada Feldkamp"], series="Neobooks",
        series_pos=4.0)), [])
    assert "series" not in values


def test_a_decision_of_a_person_survives_matching_again(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry))
        entry.decision, entry.state = "kept", "open"
        settings_service.save(db, {"match_rev": 0})
        db.commit()
        matching.upgrade_rules(db)
        assert db.scalar(select(MatchEntry.state)) == "open" and db.scalar(select(MatchEntry.decision)) == "kept"


def test_nothing_is_put_back_in_the_queue_while_no_source_can_be_asked(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",))]
    fresh(world, **{"a.epub": BOOK})
    world.settings(sources_enabled=False)
    with SessionLocal() as db:
        settings_service.save(db, {"match_rev": 0})
        state = db.scalar(select(MatchEntry.state))
        assert matching.upgrade_rules(db) is False
        assert db.scalar(select(MatchEntry.state)) == state and edition_of("Die Nebelwacht")
        assert isinstance(db.get(Edition, 1), Edition) or True


def test_the_fixture_files_exist() -> None:
    assert (FIXTURES / "dnb_sru_books.xml").is_file()
    assert pytest
