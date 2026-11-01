"""Never another book than the one wished (a case that happened): a short title is never a part of a longer one.

The real case had a wish for a book of one short word; releases of another book of the same author, whose title holds
that word, were loaded, passed the check (the recording even proved by the running time of that other book) and
renamed the work. Here the same shapes with invented books: "Ruß" by Rune Falkner beside "Wir wollen Ruß sehen" of the
same author. Also: what was wished is kept beside the work, and the history shows it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BlocklistEntry, Download, Edition, ExternalRef, ForeignBook, ProcureWish, Work
from app.services.procure import checking, importing, judge, release_match, release_name, search
from app.services.procure.release_match import Book, Title
from app.services.procure.release_name import parse
from app.services.sources.model import Candidate

from .bookmaker import make_epub
from .filing_helpers import download_roots, ebook_world, finish, german_epub, history, the_download, world_with
from .follow_helpers import rows, sabnzbd
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, Item

SHORT = Book(titles=(Title("Ruß", "de"),), authors=(AUTHOR,))
LONGER = "Wir wollen Ruß sehen"
TOKEN = "tok" + "r" * 13
OTHER_TOKEN = "tok" + "w" * 13


def found(name: str, book: Book = SHORT) -> release_match.Match:
    return release_match.match(parse(name), book)


# --- The title as a whole ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "Falkner.Rune.-.Wir.wollen.Russ.sehen",
    "Rune Falkner-Wir wollen Ruß sehen-AUDIOBOOK-WEB-MP3-DE-2024-EXAMPLE",
    "Rune.Falkner.Wir.wollen.Russ.sehen.GERMAN.EPUB-EXAMPLE",
    "Rune Falkner - Wir wollen Ruß sehen (ungekürzt)",
    "Rune Falkner - Ruß und Asche EPUB",
    "Rune Falkner - Kein Ruß EPUB",
])
def test_a_short_title_is_never_a_part_of_a_longer_one(name: str) -> None:
    match = found(name)
    assert match.author and not match.title and match.longer, name
    offer = judge.Offer(title=name, size=3_000_000, published_at=None, protocol="usenet")
    from datetime import UTC, datetime

    from app.services.procure import formats

    verdict = judge.judge(parse(name), offer, match, judge.Wanted(book=SHORT, language="de"),
                          formats.default_config(), datetime(2026, 10, 17, tzinfo=UTC))
    assert "title_mismatch" in [item["code"] for item in verdict.reasons], name


@pytest.mark.parametrize("name", [
    "Rune Falkner - Ruß (m4b)",
    "Rune.Falkner.-.Russ.GERMAN.EPUB-EXAMPLE",
    "Falkner, Rune - Ruß (2024) azw3",
    "Rune Falkner-Ruß-AUDIOBOOK-WEB-MP3-DE-2024-EXAMPLE",
    "Rune.Falkner.Russ.Roman.GERMAN.EPUB",
    "Ida Leser liest Rune Falkner - Ruß",
    "Rune Falkner - Ruß - Thriller",
    "Ruß von Rune Falkner",
])
def test_the_short_title_alone_is_the_book(name: str) -> None:
    match = found(name)
    assert match.title and not match.longer, name


@pytest.mark.parametrize(("title", "name"), [
    ("Sie", "Rune Falkner - Was sie nicht sah EPUB"),
    ("The", "Rune Falkner - The Quiet Lane EPUB"),
    ("Es", "Rune.Falkner.-.Es.war.einmal"),
])
def test_a_word_that_is_a_title_of_its_own_is_never_found_inside_another(title: str, name: str) -> None:
    assert not found(name, Book(titles=(Title(title, "de"),), authors=(AUTHOR,))).title


@pytest.mark.parametrize("name", [
    # The series and its volume before the title, a second author, the series after the title with a group behind it.
    "Rune.Falkner.Nebelchor.03.Die.Brandung.Kriminalroman",
    "Rune.Falkner.and.Ida.Leser.Die.Brandung.GERMAN.2019.RETAiL.ePub.eBook-EXAMPLE",
    "Rune Falkner Die Brandung Nebelchor 03 Example",
    # What follows the tags of the scene is the release's, not the book's.
    "Die.Brandung.German.Edition.Rune.Falkner.&.I",
    "Rune.Falkner.Die.Brandung.GERMAN.EPUB.Ida.Leser",
    "Rune.Falkner.-.Die.Brandung.ABOOK.2010.TbT.128",
    # The name of an uploader as the last word.
    "Rune Falkner - Die Brandung   Upl04der",
])
def test_what_may_stand_beside_a_title_is_no_more_of_it(name: str) -> None:
    book = Book(titles=(Title("Die Brandung", "de"),), authors=(AUTHOR,))
    assert found(name, book).title, name


def test_a_volume_of_a_series_whose_name_begins_like_the_title_is_another_book() -> None:
    ring = Book(titles=(Title("Der Ring", "de"),), authors=(AUTHOR,))
    assert not found("Rune Falkner - Der Ring der Wellen 01 Gegen den Wind", ring).title


def test_the_series_and_the_subtitle_the_catalogue_knows_are_no_more_of_the_title() -> None:
    plain = Book(titles=(Title("Die Stunde der Erben", "de"),), authors=(AUTHOR,))
    name = "Rune Falkner-Sturmwind Die Stunde Der Erben-MP3CD-Audiobook-DE-2018-EXAMPLE"
    assert not found(name, plain).title
    assert found(name, Book(titles=plain.titles, authors=(AUTHOR,), series="Sturmwind")).title
    sub = "Rune.Falkner.-.Lichtjahr.12.Sieben.kurze.Geschichten"
    assert not found(sub, Book(titles=(Title("Lichtjahr 12", "de"),), authors=(AUTHOR,))).title
    known = Book(titles=(Title("Lichtjahr 12", "de"), Title("Lichtjahr 12: Sieben kurze Geschichten", "de")),
                 authors=(AUTHOR,))
    assert found(sub, known).title


def test_a_name_that_is_another_book_of_the_author_as_a_whole_is_that_book() -> None:
    # "Ruß Roman" is a book of the author too: the name holds it as a whole, though "Ruß" fits it by the rule alone.
    knowing = Book(titles=(Title("Ruß", "de"),), authors=(AUTHOR,), others=("Ruß Roman",))
    match = found("Rune Falkner - Ruß Roman", knowing)
    assert not match.title and match.other_book
    assert found("Rune Falkner - Ruß", knowing).title


def test_one_rule_for_the_titles_of_files() -> None:
    assert not release_match.title_is(LONGER, SHORT)
    assert release_match.title_is("Ruß", SHORT)
    assert release_match.title_is("Ruß: Roman", SHORT)
    subject = {"title": "Ruß", "titles": [{"text": "Ruß", "language": "de"}], "authors": [AUTHOR]}
    assert checking.title_against(LONGER, subject) == "no"
    assert checking.title_against(LONGER, {**subject, "others": [LONGER]}) == "other"
    assert checking.title_against("Ruß", subject) == "yes"


def test_the_running_time_proves_only_the_book_wished(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.sources import audible

    answers = [Candidate(source="audible", key="B01", title=LONGER, authors=[AUTHOR], runtime_min=600,
                         variant="unabridged"),
               Candidate(source="audible", key="B02", title="Ruß", authors=[AUTHOR], runtime_min=420,
                         variant="unabridged")]
    monkeypatch.setattr(audible, "search", lambda *_args, **_kwargs: answers)
    subject = {"title": "Ruß", "titles": [{"text": "Ruß", "language": "de"}], "authors": [AUTHOR]}
    times = importing._audible_runtimes(None, subject, "de", {"unabridged": [], "abridged": []})  # type: ignore[arg-type]
    assert times == {"unabridged": [420 * 60], "abridged": []}


def test_the_titles_of_the_authors_other_books_come_from_the_library_and_the_sources(operator: Any,
                                                                                     net: FakeNetwork) -> None:
    world = world_with(net, lambda folder: make_epub(folder / "f" / "w.epub", title=LONGER, authors=(AUTHOR,),
                                                      language="de"))
    with SessionLocal() as db:
        db.add(ForeignBook(token=OTHER_TOKEN, ref="hardcover:91", title="Asche im Wind", authors=[AUTHOR],
                           language="de"))
        db.add(ForeignBook(token=TOKEN, ref="hardcover:90", title="Ruß", authors=[AUTHOR], language="de"))
        db.add(ForeignBook(token="tok" + "x" * 13, ref="hardcover:92", title="Fremdes", authors=["Ida Brandt"]))
        db.commit()
        mine = db.scalar(select(ForeignBook).where(ForeignBook.token == TOKEN))
        subject = search.subject_of_foreign(db, mine, ["de"], world.person.id)
        nobody = search.subject_of_foreign(db, mine, ["de"])
    assert set(subject["others"]) == {LONGER, "Asche im Wind"}
    assert nobody["others"] == ["Asche im Wind"], "without a person, no library is read"


# --- Loading and filing ----------------------------------------------------------------------------------------------


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def nothing_of_ours(folder: Path) -> None:
    make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")


def wish_for_the_short_book(net: FakeNetwork, items: list[Item], *, known_other: bool = False) -> Any:
    world = world_with(net, nothing_of_ours)
    world.indexer.items = items
    with SessionLocal() as db:
        db.add(ForeignBook(token=TOKEN, ref="hardcover:90", title="Ruß", authors=[AUTHOR], language="de",
                           released="2024"))
        if known_other:
            db.add(ForeignBook(token=OTHER_TOKEN, ref="hardcover:91", title=LONGER, authors=[AUTHOR], language="de"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook"],
                                                              "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    return world


def test_the_releases_of_the_longer_book_are_never_loaded_for_the_short_one(operator: Any, net: FakeNetwork,
                                                                           roots: Path) -> None:
    sabnzbd(net)
    wish_for_the_short_book(net, [Item("Falkner.Rune.-.Wir.wollen.Russ.sehen.GERMAN.EPUB", size=2_000_000,
                                       categories=(7120,))])
    assert rows(Download) == []
    [wish] = rows(ProcureWish)
    assert wish.state == "not_found" and wish.foreign_id is not None and wish.work_id is None
    assert [item["code"] for item in wish.detail["reasons"]] == ["title_mismatch"]
    assert wish.wished == {"title": "Ruß", "authors": [AUTHOR], "kind": "ebook", "language": "de"}


def test_a_file_of_another_title_under_the_right_name_is_a_question_never_the_book(operator: Any, net: FakeNetwork,
                                                                                   roots: Path) -> None:
    sab = sabnzbd(net)
    world = wish_for_the_short_book(net, [Item("Rune.Falkner.-.Russ.GERMAN.EPUB", size=2_000_000,
                                               categories=(7120,))])
    [row] = rows(Download)
    folder = roots / "complete" / "short"
    folder.mkdir(parents=True)
    make_epub(folder / "buch.epub", title=LONGER, authors=(AUTHOR,), language="de")
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "files_unassigned", (row.state, row.problem_code)
    assert row.wished == {"title": "Ruß", "authors": [AUTHOR], "kind": "ebook", "language": "de"}
    with SessionLocal() as db:
        assert db.scalar(select(Edition.id).where(Edition.title == LONGER)) is None
        assert db.scalar(select(ExternalRef.id).where(ExternalRef.key == "90")) is None
    assert world.client.get("/api/v1/procure/wishes").json()["wishes"][0]["state"] != "done"


def test_a_file_that_is_another_book_of_the_author_is_turned_down_and_blocked(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    sab = sabnzbd(net)
    world = wish_for_the_short_book(net, [Item("Rune.Falkner.-.Russ.GERMAN.EPUB", size=2_000_000,
                                               categories=(7120,))], known_other=True)
    [row] = rows(Download)
    folder = roots / "complete" / "short"
    folder.mkdir(parents=True)
    make_epub(folder / "buch.epub", title=LONGER, authors=(AUTHOR,), language="de")
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "rejected" and row.failed_reason == "title_mismatch"
    [block] = rows(BlocklistEntry)
    assert block.reason == "title_mismatch" and block.foreign_id is not None
    [line] = world.client.get("/api/v1/procure/history").json()["entries"]
    assert line["result"] == "rejected" and line["note"]["code"] == "title_mismatch"
    assert line["note"]["title"] == LONGER
    assert line["wished"] == {"title": "Ruß", "authors": [AUTHOR], "kind": "ebook", "language": "de"}
    assert line["filed_as"] is None and line["differs"] is False


def test_a_book_the_reading_calls_another_never_renames_the_work_wished(operator: Any, net: FakeNetwork, roots: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The last guard: even when the check took the files (here it is made to), a book whose files name another title
    goes in as a book of its own; the work wished keeps its name, the wish goes on, the release is blocked, and the
    history shows what was wished beside what went in."""
    world, sab, job = ebook_world(net)
    with SessionLocal() as db:
        before = db.get(Work, world.work_id)
        assert before is not None
        name_before = before.title
    real = checking.hold

    def blind(unit: checking.Unit, subject: dict[str, Any], named: bool = False) -> None:
        real(unit, subject, named)
        unit.title = "yes"

    monkeypatch.setattr(checking, "hold", blind)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, title=LONGER, series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    with SessionLocal() as db:
        work = db.get(Work, world.work_id)
        assert work is not None and work.title == name_before
        stranger = db.scalar(select(Edition).where(Edition.title == LONGER))
        assert stranger is not None and stranger.work_id != world.work_id
    [wish] = rows(ProcureWish)
    assert wish.state != "done" and wish.work_id == world.work_id
    [block] = rows(BlocklistEntry)
    assert block.reason == "title_mismatch"
    [line] = world.client.get("/api/v1/procure/history").json()["entries"]
    assert line["result"] == "done" and line["differs"] is True and line["filed_as"] == LONGER
    assert line["wished"]["title"] == name_before
    assert history()[0].note["other_book"] is True


def test_what_was_wished_stays_when_the_work_is_renamed_later(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    with SessionLocal() as db:
        work = db.get(Work, world.work_id)
        assert work is not None
        wished_title = work.title
        for edition in db.scalars(select(Edition).where(Edition.work_id == world.work_id)):
            edition.title = "Ein ganz neuer Name"
        work.title = "Ein ganz neuer Name"
        db.commit()
    sab.fail(job)
    from .follow_helpers import round_of_tracking

    round_of_tracking()
    [line] = world.client.get("/api/v1/procure/history").json()["entries"]
    assert line["wished"]["title"] == wished_title != line["book_title"]
    assert line["wished"]["authors"] == [AUTHOR] and line["wished"]["kind"] == "ebook"


def test_the_names_of_the_release_parser_are_unchanged_for_the_book_alone() -> None:
    # The parser itself knows nothing of the book: the head of the longer name is the longer title.
    assert "wollen" in release_name.parse("Rune Falkner-Wir wollen Ruß sehen-AUDIOBOOK-WEB-MP3-DE-2024-EXAMPLE").head
