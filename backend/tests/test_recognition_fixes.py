"""Corrections of the recognition: names (initials, roles, organisations, what is no person),
the author a PDF's path names, the year of a folder and of a date, the title of two books that only look alike, the genre
as the majority of votes that are exact, what the sentence "sure from 80 points" names, the keys of the cache, the order
of the first run, the lanes of the queue and a record of the National Library that XML does not allow. All invented."""

from __future__ import annotations

import asyncio
import itertools
import random
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import (
    AUTHOR,
    Account,
    Credit,
    Edition,
    Job,
    Library,
    LibraryMember,
    MatchEntry,
    Person,
    SourceCache,
)
from app.routers import review as review_router
from app.services import genres, jobs, scanner
from app.services.match import gap
from app.services.match import run as matching
from app.services.match.score import P_CONFIRM, SURE
from app.services.readers import names
from app.services.readers.names import (
    authors_in_path,
    collapse_repeats,
    core_title,
    dedupe_words,
    split_authors,
    split_people,
    titles_alike,
    year_in_path,
)
from app.services.readers.text import date_of, year_of
from app.services.sources import audible, cache, dnb, openlibrary
from app.services.sources.model import Query

from .bookmaker import make_pdf
from .conftest import BOOKS_DIR, make_account, new_client
from .fake_sources import fixture
from .library_helpers import age, book, books_folder, make_library, run_jobs, scan
from .match_helpers import World, entries, member

# ---- Names: initials, roles, organisations, what is no person ----------------------------------------------------------


@pytest.mark.parametrize(("name", "kept"), [
    ("J. R. R. Falkner", "J. R. R. Falkner"),
    ("Mara R. R. Lindqvist", "Mara R. R. Lindqvist"),
    ("Joanne K. K. Hellwig", "Joanne K. K. Hellwig"),
    ("Jussi Jussi Adler-Olsen", "Jussi Adler-Olsen"),
    ("Bora Bora", "Bora Bora"),
    ("Mara Bora Bora", "Mara Bora Bora"),
    ("Ulf Ulf Svensson", "Ulf Svensson"),
])
def test_only_a_word_of_three_letters_is_a_repetition_and_initials_are_repeated_by_right(name: str, kept: str) -> None:
    assert dedupe_words(name) == kept
    assert split_authors(name) == [kept]


def test_the_rule_that_wrote_every_repeat_once_is_known_to_find_the_people_it_split() -> None:
    assert collapse_repeats("J. R. R. Falkner") == "J. R. Falkner"
    assert collapse_repeats("Ada Feldkamp") == "Ada Feldkamp"


def test_people_made_twice_by_the_old_rule_are_one_person_at_the_next_run(client: TestClient, operator: Account) -> None:
    folder = books_folder("initials")
    book(folder, "a.epub", title="Der erste Band", authors=("J. R. R. Falkner",), language="de")
    book(folder, "b.epub", title="Der zweite Band", authors=("J. R. R. Falkner",), language="de")
    library_id = make_library(folder, "Initials", scan=True)
    member(library_id)
    with SessionLocal() as db:
        # (How a version that wrote every repeat once left it: a person of the short form with the second book.)
        old = Person(name="J. R. Falkner", sort_name="Falkner, J. R.", key="j r falkner")
        db.add(old)
        db.flush()
        second = db.scalar(select(Edition).where(Edition.title == "Der zweite Band"))
        assert second is not None
        db.execute(Credit.__table__.update().where(Credit.edition_id == second.id, Credit.role == AUTHOR)
                   .values(person_id=old.id))
        db.commit()
        assert db.scalar(select(Person.id).where(Person.key == "j r falkner")) is not None
        assert len(db.scalars(select(Person.id).where(Person.key.like("j r%"))).all()) == 2
    scan(library_id)
    with SessionLocal() as db:
        people = db.scalars(select(Person).where(Person.key.like("j r%"))).all()
        assert [p.name for p in people] == ["J. R. R. Falkner"]
        credited = db.scalars(select(Credit.edition_id).where(Credit.person_id == people[0].id, Credit.role == AUTHOR)).all()
        assert len({edition for edition in credited if edition is not None}) == 2, "both books are by the one person"
        works = db.scalars(select(Credit.work_id).where(Credit.person_id == people[0].id, Credit.work_id.is_not(None))).all()
        assert len(set(works)) == 2


@pytest.mark.parametrize(("field", "expected"), [
    ("Hans Müller (Hrsg.)", [("Hans Müller", "editor")]),
    ("Müller, Hans (Hrsg.)", [("Hans Müller", "editor")]),
    ("Müller, Hans, Hrsg.", [("Hans Müller", "editor")]),
    ("Hans Müller Hrsg.", [("Hans Müller", "editor")]),
    ("Hrsg. Hans Müller", [("Hans Müller", "editor")]),
    ("Hg.: Hans Müller", [("Hans Müller", "editor")]),
    ("Hans Müller (Herausgeber)", [("Hans Müller", "editor")]),
    ("Jane Doe, ed.", [("Jane Doe", "editor")]),
    ("Ed. Jane Doe", [("Jane Doe", "editor")]),
    ("Jane Doe (Ed.)", [("Jane Doe", "editor")]),
    ("edited by Jane Doe", [("Jane Doe", "editor")]),
    ("Anna Schmidt (Übersetzer)", [("Anna Schmidt", "translator")]),
    ("Anna Schmidt (Translator)", [("Anna Schmidt", "translator")]),
    # A name that only looks like a mark is a name.
    ("Ed Brandt", [("Ed Brandt", "")]),
    ("Real Narrator", [("Real Narrator", "")]),
    ("Anne Illner", [("Anne Illner", "")]),
])
def test_the_part_a_person_played_is_a_role_and_not_a_piece_of_the_name(field: str, expected: list[tuple[str, str]]) -> None:
    assert split_people(field) == expected


def test_an_editor_heads_a_collection_but_a_translator_or_narrator_marked_in_the_authors_is_no_author() -> None:
    assert split_authors("Hans Müller (Hrsg.); Eva Meier") == ["Hans Müller", "Eva Meier"]
    assert split_authors("Eva Meier; Anna Schmidt (Übersetzer)") == ["Eva Meier"]
    # In the field of that very part the mark is no reason to leave the person out.
    assert split_authors("Anna Schmidt (Übersetzer)", any_role=True) == ["Anna Schmidt"]


@pytest.mark.parametrize("field", ["Orchard Press, Inc.", "Müller & Söhne GmbH", "Kurt Mai & Co.", "Beispiel Verlag",
                                   "Example Publishing Ltd.", "Fischer & Sons"])
def test_an_organisation_is_neither_turned_around_nor_split(field: str) -> None:
    assert split_authors(field) == [field]
    assert names.display_name(field) == field
    assert names.sort_name(field) == field


def test_an_organisation_beside_a_person_is_still_two_names() -> None:
    assert split_authors("Orchard Press, Inc.; Eva Meier") == ["Orchard Press, Inc.", "Eva Meier"]
    assert split_authors("Hans Müller & Eva Meier") == ["Hans Müller", "Eva Meier"]


@pytest.mark.parametrize("field", ["Diverse Autoren", "Anonym", "Unknown", "N.N.", "Various Authors", "Anonymous",
                                   "Unbekannt", "Verschiedene Autoren"])
def test_a_name_that_is_no_person_is_dropped(field: str) -> None:
    assert split_authors(field) == []
    assert split_authors(["Eva Meier", field]) == ["Eva Meier"]


# ---- The author of a PDF and the path --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "authors"), [
    ("Inspector Wolff/A Study in Fog.pdf", []),
    ("Orchard Tales/Der erste Band.pdf", []),
    ("Moor Chronicles/Band 1.pdf", []),
    ("Neue Bücher/Der Duftmacher.pdf", []),
    ("Neue Bücher/Der Duftmacher - Ada Feldkamp.pdf", ["Ada Feldkamp"]),
    ("Feldkamp, Ada/Der Duftmacher.pdf", ["Ada Feldkamp"]),
    ("Sachbücher, Neue/Der Duftmacher.pdf", ["Neue Sachbücher"]),
])
def test_a_folder_names_the_author_of_a_pdf_only_when_it_is_written_last_name_first(path: str, authors: list[str]) -> None:
    assert authors_in_path(path) == authors


def test_a_pdf_in_a_folder_of_a_series_or_a_shelf_keeps_the_author_its_information_names(client: TestClient,
                                                                                       operator: Account) -> None:
    folder = books_folder("pdf-folders")
    for relative, title, author in (
            ("Inspector Wolff/Eine Studie.pdf", "Eine Studie", "Arthur Beispiel"),
            ("Orchard Tales/Der Stein.pdf", "Der Stein", "Joanne Beispiel"),
            ("Neue Bücher/Der Duftmacher.pdf", "Der Duftmacher", "Patrick Beispiel"),
            ("Feldkamp, Ada/Der Weg.pdf", "Der Weg", "Wer Anders")):
        path = make_pdf(folder / relative, title=title, author=author)
        age(path)
    library_id = make_library(folder, "Pdf", scan=True)
    member(library_id)
    with SessionLocal() as db:
        found = {e.title: list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
            Credit.edition_id == e.id, Credit.role == AUTHOR))) for e in db.scalars(select(Edition))}
    assert found == {"Eine Studie": ["Arthur Beispiel"], "Der Stein": ["Joanne Beispiel"],
                     "Der Duftmacher": ["Patrick Beispiel"], "Der Weg": ["Ada Feldkamp"]}


# ---- Years ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "year"), [
    ("Neu (2024)/Sub/Auris.epub", None),
    ("Reihe (2010)/Band 1 (2012)/x.epub", 2012),
    ("Autor/Reihe (2010)/Band 1/x.epub", None),
    ("Auris (2019)/x.epub", 2019),
    ("Auris/x (2020).epub", 2020),
    ("Autor/Auris (2019)/CD 1/x.mp3", 2019),
    ("Autor (1999)/Auris/CD 1/x.mp3", None),
])
def test_the_year_comes_from_the_folder_of_the_book_or_the_file_name_and_not_from_any_parent(path: str,
                                                                                       year: int | None) -> None:
    assert year_in_path(path) == year


@pytest.mark.parametrize("date", ["2015-13-45", "2015-02-30", "2015-00-10", "2015-04-31", "0101-01-01", "0000-00-00"])
def test_a_date_that_does_not_exist_is_dropped(date: str) -> None:
    assert date_of(date) == "" and year_of(date) is None


def test_a_date_that_exists_is_kept_as_the_file_wrote_it() -> None:
    assert date_of("2016-02-29") == "2016-02-29" and year_of("2016-02-29") == 2016
    assert date_of("2015-05") == "2015-05" and date_of("2015") == "2015"
    assert date_of("20150101") == "2015-01-01" and year_of("20150101") == 2015
    assert date_of("2003-02-24T23:00:00+00:00") == "2003-02-24"


def test_a_book_with_an_impossible_date_in_its_file_has_no_published_date(client: TestClient, operator: Account) -> None:
    folder = books_folder("dates")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",), language="de", date="2015-13-45")
    book(folder, "b.epub", title="Beta", authors=("Ada Feldkamp",), language="de", date="2015-06-15")
    scan(make_library(folder, "Dates"))
    with SessionLocal() as db:
        published = {e.title: e.published for e in db.scalars(select(Edition))}
    assert published == {"Alpha": "", "Beta": "2015-06-15"}


# ---- The core of a title and two titles that look alike ----------------------------------------------------------------


@pytest.mark.parametrize(("title", "core"), [
    ("Eine unbeliebte Dame - Ein Selbstmord, ein Mord", "Eine unbeliebte Dame"),
    ("Der Beobachter – Thriller", "Der Beobachter"),
    ("The Quiet Orchard - A Novel", "The Quiet Orchard"),
    # A dash inside a word, or without a space on each side, is part of the title.
    ("Wolff-Krimi", "Wolff-Krimi"),
    ("Ost-West Passage", "Ost-West Passage"),
])
def test_a_dash_between_spaces_ends_the_core_of_a_title(title: str, core: str) -> None:
    assert core_title(title) == core


@pytest.mark.parametrize(("a", "b", "alike"), [
    ("Im Westen Nebel", "Im Osten Nebel", False),
    ("Der Kräuterdoktor", "Die Kräuterdoktorin", False),
    ("Das Haus am See", "Das Haus am Meer", False),
    ("The Quiet Orchard", "The Quiet Orchards", True),
    ("Die Nebelwacht", "Die Nebelwacht: Kriminalroman", True),
    ("Colour of Honey", "Color of Honey", True),
    ("Der Duftmacher", "Der Duftmacher", True),
    ("Moorland 2", "Moorland 3", False),
])
def test_two_titles_that_differ_in_a_word_are_not_one_title_however_close_their_letters(a: str, b: str, alike: bool) -> None:
    assert titles_alike(a, b) is alike


# ---- The genre ------------------------------------------------------------------------------------------------------------


def test_a_novel_whose_subjects_say_history_is_not_non_fiction() -> None:
    """The subject headings of a novel of the 18th century ("Geschichte 1802-1830", "die Geschichte einer Fälscherin") and the
    subject group of fiction: a broad word alone is no vote for non-fiction."""
    votes = genres.add_votes(genres.tally(["Geschichte 1802-1830", "die Geschichte einer Fälscherin", "Belletristik"], 2),
                             genres.tally(["Krimis & Thriller"], 1), genres.tally(["Fiction"], 3))
    assert genres.winner(votes) != "sachbuch"
    assert genres.winner(genres.tally(["Geschichte 1802-1830", "die Geschichte einer Fälscherin", "Belletristik"], 2)) == "roman"


@pytest.mark.parametrize("subjects", [["Die Zukunft der Arbeit"], ["Natur"], ["Sport", "Roman"], ["Reise"], ["Geschichte"]])
def test_a_broad_word_alone_casts_no_vote_for_its_genre(subjects: list[str]) -> None:
    assert genres.tally(subjects, 2).get("sachbuch", 0) == 0 and genres.tally(subjects, 2).get("scifi", 0) == 0


def test_a_broad_word_counts_with_a_second_sign() -> None:
    assert genres.tally(["Geschichte", "Sachbuch"], 2).get("sachbuch", 0) > 0
    assert genres.tally(["Geschichte", "Politik"], 2).get("sachbuch", 0) > 0, "two different broad words"
    assert genres.tally(["Geschichte", "Geschichte des Hauses"], 2).get("sachbuch", 0) == 0, "the same word twice is one"
    assert genres.tally(["Zukunft", "Dystopie"], 2).get("scifi", 0) > 0


def test_a_telling_genre_needs_a_fair_share_of_all_that_was_said() -> None:
    assert genres.winner({"roman": Fraction(222, 100), "sachbuch": Fraction(111, 100), "krimi": Fraction(2, 3)}) == "roman"
    assert genres.winner({"roman": Fraction(3), "krimi": Fraction(2)}) == "krimi"
    # The share, not the weight alone: a whole voice against a novel three voices strong does not win.
    assert genres.winner({"roman": Fraction(7), "krimi": Fraction(2)}) == "roman"
    # Nobody says "novel": the telling genre wins, however thin.
    assert genres.winner({"krimi": Fraction(1, 10)}) == "krimi"


def test_a_tie_is_decided_by_the_order_of_the_genres_whatever_the_order_the_votes_came_in() -> None:
    parts = [{"krimi": Fraction(1)}, {"fantasy": Fraction(1)}, {"roman": Fraction(1)}]
    results = {genres.winner(genres.add_votes(*order)) for order in itertools.permutations(parts)}
    assert results == {"fantasy"}, "fantasy stands before crime in the order of telling words"
    assert genres.winner({"krimi": Fraction(1), "fantasy": Fraction(1)}) == "fantasy"
    assert genres.winner({"fantasy": Fraction(1), "krimi": Fraction(1)}) == "fantasy"


def test_the_votes_add_up_exactly_so_the_order_of_the_voices_never_changes_the_winner() -> None:
    generator = random.Random(7)
    for _round in range(300):
        voices = [genres.tally(generator.sample(["Thriller", "Fantasy", "Roman", "Science Fiction", "Sachbuch", "Krimi"],
                                                generator.randint(1, 4)), generator.choice([1, 2, 3]) / generator.choice([1, 3, 7]))
                  for _ in range(4)]
        winners = {genres.winner(genres.add_votes(*order)) for order in itertools.permutations(voices)}
        assert len(winners) == 1, voices


def test_votes_kept_as_numbers_come_back_as_the_same_exact_fractions() -> None:
    assert genres.to_fraction(0.667) == Fraction(667, 1000) and genres.to_fraction(2) == Fraction(2)
    assert genres.to_fraction(Fraction(1, 3)) == Fraction(1, 3)


# ---- What the sentence under the points names ------------------------------------------------------------------------------


def proposal(score: int, missing: list[str], why: list[str] | None = None, **candidate: Any) -> dict[str, Any]:
    base = {"source": "dnb", "key": "1", "title": "Moorland", "authors": ["Ada Feldkamp"], "languages": ["de"]}
    return {"id": "dnb:1:", "score": score, "missing": missing, "why": why or [], "fatal": "", "c": {**base, **candidate}}


def test_only_what_would_really_make_the_book_sure_is_named() -> None:
    # 70 points: 10 are missing. A year is worth 5, so it is not named; a second source is worth 10.
    info = gap.lack_info(proposal(70, ["one_source", "no_year"], year=2019))
    assert (info.words, info.join, info.cap) == (["second_source"], "or", "")
    # An ISBN settles the book, whatever the points.
    assert gap.lack_info(proposal(40, ["no_isbn", "no_year"], year=2019)).words == ["isbn"]
    # Everything that is enough alone is named, in the order of how much it helps.
    assert gap.lack_info(proposal(72, ["no_year", "one_source", "no_isbn"], year=2019)).words == ["isbn", "second_source"]


def test_what_is_enough_only_together_is_named_with_and() -> None:
    info = gap.lack_info(proposal(62, ["one_source", "language_unknown", "no_year"], year=2019))
    assert (info.words, info.join) == (["second_source", "language"], "and")
    assert 62 + P_CONFIRM + 10 >= SURE


def test_when_nothing_the_file_could_give_is_enough_nothing_is_named() -> None:
    assert gap.lack_info(proposal(40, ["no_year"], year=2019)).words == []
    assert gap.lack_info(proposal(55, ["no_year", "language_unknown"], year=2019, languages=["de"])).words == []


def test_what_a_source_does_not_have_cannot_be_earned() -> None:
    # The file lacks a year, but the source has none either: no point to win.
    assert gap.lack_info(proposal(76, ["no_year"])).words == []
    assert gap.lack_info(proposal(76, ["no_year"], year=2019)).words == ["year"]


@pytest.mark.parametrize(("score", "missing", "why", "cap", "words"), [
    (58, ["no_isbn", "one_source", "no_year"], ["author_weak"], "author_weak", ["isbn"]),
    (58, ["one_source", "no_year"], ["author_weak"], "author_weak", []),
    (50, ["no_author", "no_isbn", "one_source"], ["author_unknown"], "author_unknown", ["isbn", "author"]),
    (90, ["one_source"], ["language_missing"], "language_missing", []),
    (90, ["no_isbn"], ["language_missing"], "language_missing", ["isbn"]),
])
def test_a_book_held_back_whatever_its_points_names_what_holds_it_back(score: int, missing: list[str], why: list[str],
                                                                       cap: str, words: list[str]) -> None:
    info = gap.lack_info(proposal(score, missing, why, year=2019))
    assert (info.cap, info.words) == (cap, words)


def test_the_gap_of_a_book_says_the_join_and_the_cap() -> None:
    result = gap.gap_of("author_weak", [proposal(58, ["no_isbn"], ["author_weak"])])
    assert result is not None and result["cap"] == "author_weak" and result["missing"] == ["isbn"]
    result = gap.gap_of("low_score", [proposal(62, ["one_source", "language_unknown"], year=2019)])
    assert result is not None and result["join"] == "and" and result["missing"] == ["second_source", "language"]


def test_the_list_gives_every_proposal_the_words_the_join_and_the_cap(client: TestClient, operator: Account) -> None:
    entry = MatchEntry(id=5, library_id=1, edition_id=1, state="maybe")
    item = proposal(62, ["one_source", "language_unknown"], year=2019)
    item["c"]["source"] = "dnb"
    out = review_router._proposal(entry, item)
    assert out is not None and (out.lacks, out.lacks_join, out.cap) == (["second_source", "language"], "and", "")
    held = review_router._proposal(entry, proposal(70, ["no_isbn"], ["author_weak"]))
    assert held is not None and (held.lacks, held.cap) == (["isbn"], "author_weak")


# ---- The keys of the cache ------------------------------------------------------------------------------------------------


def test_an_answer_kept_by_an_older_version_is_not_used_by_a_search_that_wants_the_new_fields(
        client: TestClient, operator: Account, world: World) -> None:
    """Audible and Open Library gave fewer fields before (no categories, no subjects): what they answered then is kept for
    a month, and must not stand in for an answer with the new fields."""
    world.fake.audible = {"de": [{"asin": "B0EXAMPL10", "title": "Die Nebelwacht", "authors": [{"name": "Ada Feldkamp"}],
                                  "runtime_length_min": 600, "language": "german"}]}
    query = Query(kind="ebook", title="Die Nebelwacht", authors=["Ada Feldkamp"], language="de")
    old = {"audible": cache.make_key("search", "de", "", "die nebelwacht", "ada feldkamp"),
           "openlibrary": cache.make_key("search", "", "die nebelwacht", "ada feldkamp", str(query.limit))}
    for source, key in old.items():
        cache.put(source, key, [], hit=True)
    with SessionLocal() as db:
        assert audible.search(db, query, "de") != [], "asked again, the old nothing was not believed"
        openlibrary.search(db, query)
        keys = {row.key.split("|")[0] for row in db.scalars(select(SourceCache))}
    assert world.fake.count("api.audible") == 1 and world.fake.count("openlibrary.org", "search.json") >= 1
    assert "search2" in keys


# ---- The first run after an update -------------------------------------------------------------------------------------------


def make_entries(world: World, titles: dict[str, str]) -> int:
    """Books that wait for the sources (they are off), then the states given (title -> state), as after a run."""
    world.settings(sources_enabled=False)
    folder = books_folder("order")
    for title in titles:
        book(folder, f"{title}.epub", title=title, authors=("Ada Feldkamp",), language="en")
    library_id = make_library(folder, "Order")
    member(library_id)
    scan(library_id)
    with SessionLocal() as db:
        for title, state in titles.items():
            edition = db.scalar(select(Edition).where(Edition.title == title))
            assert edition is not None
            db.execute(MatchEntry.__table__.update().where(MatchEntry.edition_id == edition.id).values(
                state=state, reason="" if state != "open" else "nothing_found"))
        db.commit()
    return library_id


def titles_in_order(library_id: int) -> list[str]:
    with SessionLocal() as db:
        ids = matching._pending(db, library_id)
        rows = {r.id: r.edition_id for r in db.scalars(select(MatchEntry))}
        names_ = {e.id: e.title for e in db.scalars(select(Edition))}
    return [names_[rows[ident]] for ident in ids]


def test_matching_everything_again_asks_about_the_unsure_first_and_the_sure_books_last(
        client: TestClient, operator: Account, world: World) -> None:
    library_id = make_entries(world, {"Alpha": "sure", "Beta": "maybe", "Gamma": "open", "Delta": "sure", "Epsilon": "pending"})
    world.settings(sources_enabled=True)
    with SessionLocal() as db:
        assert matching.rematch_all(db) == 5
    # The books that were not sure come first, in the order they were made; the ones that were sure wait behind.
    assert titles_in_order(library_id) == ["Beta", "Epsilon", "Gamma", "Alpha", "Delta"]
    states = {row[0]: (row[1], row[2]) for row in entries()}
    assert states["Alpha"] == ("pending", matching.RECHECK) and states["Beta"] == ("pending", "")


def test_a_book_that_is_new_comes_before_the_sure_books_that_are_looked_at_again(client: TestClient, operator: Account,
                                                                                  world: World) -> None:
    library_id = make_entries(world, {"Alpha": "sure", "Beta": "sure"})
    with SessionLocal() as db:
        matching.rematch_all(db)
    book(Path(BOOKS_DIR) / "order", "Neu.epub", title="Neu", authors=("Ada Feldkamp",), language="en")
    scan(library_id)
    assert titles_in_order(library_id) == ["Neu", "Alpha", "Beta"]


def test_the_sure_books_are_asked_about_last_when_the_run_goes(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    library_id = make_entries(world, {"Alpha": "sure", "Beta": "maybe", "Gamma": "sure"})
    world.settings(sources_enabled=True)
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    asked = [request.url.params.get("title") for request in world.fake.log if request.url.path == "/search.json"]
    assert [t for t in asked if t][:3] == ["Beta", "Alpha", "Gamma"]
    assert library_id


def test_a_library_read_by_older_rules_is_due_at_once_and_not_at_the_next_turn_of_the_interval(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("rules")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",))
    library_id = make_library(folder, "Rules")
    scan(library_id)
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 0, "read by the rules the code has"
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 0, "it has only just been read: not at once, after a short wait"
        db.execute(Library.__table__.update().values(scanned_at=clock.now() - scanner.timedelta(seconds=scanner.RULES_WAIT + 5)))
        db.commit()
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 1, "an hour was not needed"
    run_jobs()
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 0, "once"


@pytest.mark.parametrize("how", ["never_by_itself", "library_off"])
def test_a_library_the_operator_keeps_from_being_read_by_itself_is_not_read_for_newer_rules(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    from app.services import settings_service

    folder = books_folder("rules-off")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",))
    library_id = make_library(folder, "Off")
    scan(library_id)
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    with SessionLocal() as db:
        db.execute(Library.__table__.update().values(
            scanned_at=clock.now() - scanner.timedelta(hours=5), scan_enabled=how != "library_off"))
        if how == "never_by_itself":
            settings_service.save(db, {"scan_minutes": 0})
        db.commit()
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 0


def test_reading_again_for_newer_rules_is_paced_and_reading_a_new_file_is_not(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("pace")
    for n in range(3):
        book(folder, f"b{n}.epub", title=f"Book {n}", authors=("Ada Feldkamp",))
    library_id = make_library(folder, "Pace")
    rests: list[float] = []
    clockwork = itertools.count(step=0.25)
    monkeypatch.setattr(scanner, "ticks", lambda: next(clockwork))
    monkeypatch.setattr(scanner, "rest", lambda seconds: rests.append(seconds) or False)
    scan(library_id)
    assert rests == [], "files that are new are read at full speed"
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    # Every file took a quarter of a second: the run rests as long again after each, half the time for the disk.
    assert rests == [0.25, 0.25, 0.25]
    rests.clear()
    clockwork = itertools.count(step=40.0)
    monkeypatch.setattr(scanner, "ticks", lambda: next(clockwork))
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    assert rests == [scanner.REREAD_PAUSE_MAX] * 3, "never longer than the cap"


def test_a_changed_file_is_read_at_full_speed_also_when_the_rules_are_newer(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("pace2")
    path = book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",))
    library_id = make_library(folder, "Pace")
    scan(library_id)
    book(folder, "a.epub", title="Alpha neu", authors=("Ada Feldkamp",), text_bytes=7000)
    age(path)
    rests: list[float] = []
    monkeypatch.setattr(scanner, "rest", lambda seconds: rests.append(seconds) or False)
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    row = scan(library_id)
    assert row.report["changed"] == 1 and rests == []


# ---- Reading everything again by hand -----------------------------------------------------------------------------------------


def test_a_member_with_manage_may_ask_to_read_everything_again_and_the_value_must_be_a_real_boolean(
        client: TestClient, operator: Account) -> None:
    folder = books_folder("hand")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",))
    library_id = make_library(folder, "Hand")
    scan(library_id)
    eva, dan, stranger = make_account("eva"), make_account("dan"), make_account("fremd")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library_id, user_id=eva.id, right="manage"))
        db.add(LibraryMember(library_id=library_id, user_id=dan.id, right="read"))
        db.commit()

    def reread() -> int:
        with SessionLocal() as db:
            return int(db.get(Library, library_id).reread_rev)  # type: ignore[union-attr]

    with new_client(dan) as reader:
        assert reader.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": True}).status_code == 403
    with new_client(stranger) as outsider:
        assert outsider.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": True}).status_code == 404
    assert reread() == 0
    with new_client(eva) as manager:
        for value in ("yes", "true", 1, "1", [], {}, None):
            assert manager.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": value}).status_code == 422, value
        assert reread() == 0
        assert manager.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": True}).status_code == 202
    assert reread() == 1


# ---- The lanes of the queue ----------------------------------------------------------------------------------------------------


def test_each_lane_puts_back_only_its_own_work_after_a_stop(client: TestClient) -> None:
    with SessionLocal() as db:
        for kind, ref in (("match", 1), ("scan", 1), ("scan", 2)):
            db.add(Job(kind=kind, ref=ref, state="running"))
        db.add(Job(kind="scan", ref=2, state="queued"))
        db.commit()
        jobs.recover(db, "match")
        states = {(j.kind, j.ref, j.state) for j in db.scalars(select(Job))}
    assert ("match", 1, "queued") in states and ("scan", 1, "running") in states and ("scan", 2, "running") in states
    with SessionLocal() as db:
        jobs.recover(db, "main")
        states = sorted((j.kind, j.ref, j.state) for j in db.scalars(select(Job)))
    # The scan of library 1 waits again; the scan of library 2 would wait twice, so the one that was running is done.
    assert states == [("match", 1, "queued"), ("scan", 1, "queued"), ("scan", 2, "done"), ("scan", 2, "queued")]


def test_the_matching_lane_alone_puts_back_the_match_job_a_stopped_server_left(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[int] = []
    monkeypatch.setitem(jobs.HANDLERS, "match", lambda db, job: ran.append(job.ref))
    with SessionLocal() as db:
        db.add(Job(kind="match", ref=7, state="running"))
        db.commit()

    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(jobs.run_forever(stop, lane="match"))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if ran:
                break
        stop.set()
        await task

    asyncio.run(go())
    assert ran == [7], "found, put back and run by the lane that owns it, with no other lane started first"


# ---- A record of the National Library that XML does not allow ----------------------------------------------------------------


@pytest.mark.parametrize("damage", [b"Die Nebel&#1;wacht :", b"Die Nebel\x01wacht :", b"Die Nebel&#xB;wacht :"])
def test_a_character_xml_does_not_allow_costs_the_one_record_and_not_the_answer(damage: bytes) -> None:
    whole = fixture("dnb_sru_books.xml")
    records, _page, _total = dnb.parse_page(whole)
    broken = whole.replace(b"Die Nebelwacht :", damage, 1)
    kept, page, total = dnb.parse_page(broken)
    assert len(kept) == len(records) - 1 and total == 9
    assert "2000000001" not in {r.ident for r in kept} and page == len(kept)


def test_an_answer_that_is_cut_off_or_carries_a_dtd_is_still_refused() -> None:
    whole = fixture("dnb_sru_books.xml")
    damaged = whole.replace(b"Die Nebelwacht :", b"Die Nebel\x01wacht :", 1)
    for bad in (whole[: len(whole) // 2], b'<!DOCTYPE x [<!ENTITY a "b">]>' + whole, b"", damaged[:-30],
                b'<!DOCTYPE x [<!ENTITY a "b">]>' + damaged):
        with pytest.raises(ValueError):
            dnb.parse_page(bad)
    # Something that is XML but no answer holds no record.
    assert dnb.parse_page(b"<html><body>no</body></html>")[0] == []


def test_a_search_with_one_bad_record_still_finds_the_book_and_does_not_make_the_source_rest(
        client: TestClient, operator: Account, world: World) -> None:
    from app.services.sources import throttle

    world.fake.dnb = fixture("dnb_sru_books.xml").replace(b"Das Gezeitenbuch", b"Das Gezeiten&#2;buch")
    with SessionLocal() as db:
        found = dnb.search(db, Query(kind="ebook", title="Die Nebelwacht", authors=["Ada Feldkamp"], language="de"))
    assert found and throttle.state_of("dnb").fail_count == 0 and throttle.state_of("dnb").blocked_until is None
