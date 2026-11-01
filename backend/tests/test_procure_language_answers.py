"""How the languages of a download are answered and kept: the answer to a question of the
title never answers the one of the language, the language a person names is the edition's and stays, a wish that rests
loads nothing, a book met another way in a format below the goal is an upgrade, a damaged chapter never makes a book
unreadable, the category of a recording is a witness, a text in two languages or in one nexbooks has no words for is
no yes. Only invented books, made here."""

from __future__ import annotations

import zipfile
import zlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import Edition, ProcureWish
from app.services.catalog import Meta
from app.services.procure import checking, formats, rss, wishes
from app.services.readers import epub, text

from .bookmaker import make_epub
from .filing_helpers import download_roots, ebook_world, editions, finish, german_epub, the_download
from .follow_helpers import audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, allow_languages

GERMAN = ("Der Regen fiel auf die alte Straße, und die Laternen waren noch nicht an. Sie ging mit dem Hund nach Hause, "
          "weil es schon spät war und der Wind über die Felder kam. Im Haus war es still, nur die Uhr in der Küche "
          "schlug, als er die Tür hinter sich schloss und sich an den Tisch setzte.")
SPANISH = ("El viento de la tarde llegó por el camino y la casa estaba en silencio. Ella no sabía por qué, pero los "
           "perros de los vecinos se quedaron con su madre en el patio, como cada noche cuando el pueblo duerme y "
           "nadie sale a la calle.")
ENGLISH = ("The rain fell on the old street and the lamps were not lit yet. She walked home with the dog because it was "
           "late and the wind came over the fields, and the house was quiet when he closed the door behind him.")
#: Portuguese: a language the sample has no words for.
PORTUGUESE = ("A chuva caía sobre a rua velha e os candeeiros ainda não estavam acesos. Ela voltou para casa com o cão "
              "porque já era tarde e o vento vinha dos campos; em casa estava tudo calmo, só o relógio da cozinha "
              "batia quando ele fechou a porta e se sentou à mesa.")
UNMARKED = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.EPUB-EXAMPLE"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def read(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return epub.read(handle)


# --- "Übernehmen" answers the title, the language is still looked at ---------------------------------------------


def test_taking_the_files_for_a_question_of_the_title_still_holds_their_language(operator: Any, net: FakeNetwork,
                                                                               roots: Path) -> None:
    world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    allow_languages("de", "en")
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, title="Ein ganz anderer Titel", language="es", prose=SPANISH, text_bytes=9000,
                series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "files_unassigned", (row.state, row.problem_code)
    taken = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    assert taken.status_code == 200, taken.text
    run_jobs()
    row = the_download()
    assert row.state == "rejected" and row.failed_reason == "other_language", (row.state, row.problem_code)
    assert not any(edition.language == "es" for edition in editions())


# --- the language a person names is the edition's, and stays -------------------------------------------------------


def test_the_language_a_person_names_is_the_language_of_the_edition_and_stays(operator: Any, net: FakeNetwork,
                                                                              roots: Path) -> None:
    world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="nl", prose=GERMAN, text_bytes=9000)
    finish(sab, job, folder)
    row = the_download()
    assert row.problem_code == "language_unclear"
    taken = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    assert taken.status_code == 200, taken.text
    run_jobs()
    assert the_download().state == "imported"
    filed = [edition for edition in editions() if edition.kind == "ebook" and edition.language == "de"]
    assert filed, [(edition.kind, edition.language) for edition in editions()]
    scan(world.library_id)
    with SessionLocal() as db:
        again = db.get(Edition, filed[0].id)
        assert again is not None and again.language == "de", "a new reading keeps what the person said"
    # The book has its German e-book now: no second wish for it is made.
    again_wished = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                                    "kinds": ["ebook"], "language": "de"})
    assert again_wished.status_code in (200, 201, 409), again_wished.text
    assert [item for item in wishes_of(world) if item["kind"] == "ebook" and item["state"] != "done"] == []


# --- a wish that rests hands nothing over -------------------------------------------------------------------------


def test_a_wish_that_rests_for_its_language_drops_the_release_it_would_hand_over(operator: Any,
                                                                                 net: FakeNetwork) -> None:
    world = audio_world(net, Item("Rune Falkner-Die Laternenstrasse-MP3-ENGLISH-2021-EXAMPLE", size=400_000_000,
                                  categories=(3030,)))
    allow_languages("de", "en")
    wish(world, language="en")
    run_jobs()
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert row is not None
        row.state, row.handover = "not_found", {"search": 1, "release": 1, "due": clock.now().isoformat(), "tries": 0}
        db.commit()
        formats.save(db, {**formats.config(db), "languages": ["de"], "original": False})
        assert wishes.language_off(db, row)
        assert row.handover is None


def test_the_newest_entries_load_nothing_for_a_wish_that_rests(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item("Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                  categories=(3130,)))
    allow_languages("de", "en")
    wish(world, language="en")
    run_jobs()
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "languages": ["de"], "original": False})
        db.execute(update(ProcureWish).values(state="not_found", detail=None))
        db.commit()
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-MP3-ENGLISH-2021-EXAMPLE", size=400_000_000,
                                categories=(3030,))]
    rss.sync()
    [shown] = wishes_of(world)
    assert shown["state"] == "idle" and shown["detail"]["reason"] == "language_off"
    assert sab.jobs == {}


# --- met another way, in a format below the goal or not ticked -------------------------------------------------


@pytest.mark.parametrize(("fmt", "state"), [("epub", "done"), ("mobi", "upgrade"), ("pdf", "not_found")])
def test_a_book_met_another_way_goes_on_for_a_better_format_and_a_format_not_ticked_meets_nothing(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, fmt: str, state: str) -> None:
    world = audio_world(net, Item("Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                  categories=(3130,)))
    wish(world, kinds=("ebook",), language="de")
    run_jobs()

    def have(db: Any, uid: int, work_id: int, kind: str, languages: list[str]) -> tuple[bool, dict[str, str]]:
        order = [item["f"] for item in formats.config(db)[kind]]
        return order.index(fmt) > 0, {"have": fmt, "goal": "epub"}

    monkeypatch.setattr(wishes, "_upgrade_of", have)
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(state="not_found"))
        db.commit()
        wishes.met_elsewhere(db)
        row = db.scalar(select(ProcureWish))
        assert row is not None and row.state == state, (row.state, row.detail)


def test_looking_for_wishes_met_another_way_never_fails_what_called_it(operator: Any, net: FakeNetwork,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(db: Any, work_ids: Any = None) -> int:
        raise RuntimeError("made up")

    monkeypatch.setattr(wishes, "_met_elsewhere", broken)
    with SessionLocal() as db:
        assert wishes.met_elsewhere(db) == 0


def test_another_wish_of_the_book_is_met_when_a_download_is_filed(operator: Any, net: FakeNetwork,
                                                                  roots: Path) -> None:
    _world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    with SessionLocal() as db:
        first = db.scalar(select(ProcureWish))
        assert first is not None
        moment = clock.now()
        db.add(ProcureWish(user_id=first.user_id, work_id=first.work_id, kind="ebook", language="",
                           library_id=first.library_id, state="not_found", created_at=moment, updated_at=moment,
                           next_try_at=moment + timedelta(hours=6)))
        db.commit()
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, prose=GERMAN, text_bytes=9000)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    with SessionLocal() as db:
        other = db.scalar(select(ProcureWish).where(ProcureWish.language == ""))
        assert other is not None and other.state == "done" and (other.detail or {}).get("reason") == "met_elsewhere"


# --- a damaged chapter is left out of the sample ------------------------------------------------------------------


@pytest.mark.parametrize("error", [zlib.error("bad"), RuntimeError("bad"), KeyError("bad")])
def test_a_damaged_chapter_in_the_sample_never_makes_the_book_unreadable(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch,
                                                                         error: Exception) -> None:
    path = make_epub(tmp_path / "a.epub", language="de", prose=GERMAN, text_bytes=9000,
                     chapters=("Eins", "Zwei", "Drei", "Vier", "Fünf", "Sechs"))
    real = zipfile.ZipExtFile.read
    damaged: list[str] = []

    def failing(self: zipfile.ZipExtFile, n: int | None = -1) -> bytes:
        if "/text/" in self.name and not damaged:
            damaged.append(self.name)
            raise error
        return real(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", failing)
    found = read(path)
    assert damaged and found["title"] and found["text_language"] == "de"


# --- The sample: from the middle, small, a few chapters (each bound is held by a test) -------------------------


def opened_chapters(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[int]]:
    names: list[str] = []
    sizes: list[int] = []
    real = zipfile.ZipExtFile.read

    def counting(self: zipfile.ZipExtFile, n: int | None = -1) -> bytes:
        data = real(self, n)
        if "/text/" in self.name:
            if self.name not in names:
                names.append(self.name)
            sizes.append(len(data))
        return data

    monkeypatch.setattr(zipfile.ZipExtFile, "read", counting)
    read(path)
    return names, sizes


def test_the_sample_starts_in_the_middle_reads_at_most_64_kib_and_four_chapters(tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    chapters = tuple(f"Kapitel {number}" for number in range(1, 13))
    small = make_epub(tmp_path / "small.epub", language="de", prose="Ja.", text_bytes=12 * 40, chapters=chapters)
    names, _sizes = opened_chapters(small, monkeypatch)
    assert 1 <= len(names) <= 4, names
    assert "4" in names[0].rsplit("/", 1)[-1], f"the first chapter read is the one at a third: {names}"
    monkeypatch.undo()
    big = make_epub(tmp_path / "big.epub", language="de", prose=GERMAN, text_bytes=4 * 1024 * 1024,
                    chapters=("Eins", "Zwei"))
    _names, sizes = opened_chapters(big, monkeypatch)
    assert sizes and max(sizes) <= 64 * 1024


# --- two languages in the text, or one without words --------------------------------------------------------


def test_a_text_in_two_languages_or_in_one_without_words_is_no_witness_for_any(tmp_path: Path) -> None:
    assert text.sample_language(GERMAN) == "de"
    assert text.sample_language(f"{GERMAN} {ENGLISH} {GERMAN} {ENGLISH}") == text.MIXED_TEXT
    # Portuguese: no words of its own in the sample, it reads as a mix of Spanish and Italian or as none: no yes.
    assert text.sample_language(PORTUGUESE) in (text.MIXED_TEXT, text.OTHER_TEXT)
    assert text.sample_language("Kxq vrt plmn zzt " * 30) == text.OTHER_TEXT
    assert text.sample_language("Ja.") == ""
    unit = checking.Unit(key="u", kind="ebook", files=[type("F", (), {"probe": {"text_language": "other"},
                                                                       "path": "a/b.epub"})()],
                         meta=Meta(kind="ebook", language="de"))
    wanted = checking.Wanted(kind="ebook", language="de", accepted=["de"], subject=None, config={})
    found = checking._language_verdict(unit, wanted)
    assert found is not None and found.code == "language_unclear"


# --- the category of a recording is a witness -------------------------------------------------------------------


def test_the_category_with_a_language_is_kept_however_the_release_got_its_language(operator: Any,
                                                                                   net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item("Rune Falkner - Die Laternenstrasse - MP3", size=400_000_000, categories=(3130,)))
    allow_languages("de", "en")
    wish(world, language="de")
    run_jobs()
    [row] = downloads()
    assert (row.decision or {}).get("_category") == "de", row.decision
    assert sab.jobs


def test_the_operator_must_keep_one_language(operator: Any, client: Any) -> None:
    body = client.get("/api/v1/procure/formats").json()
    body = {key: value for key, value in body.items()
            if key not in ("libraries", "known_ebook", "known_audio", "known_languages", "samples")}
    body["languages"], body["original"] = [], False
    answer = client.put("/api/v1/procure/formats", json=body)
    assert answer.status_code == 422, answer.text
