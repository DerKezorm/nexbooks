"""Who may answer the questions of the check, read and undo a note, see and empty the trash, and give a foreign job to a
wish: the owner of the right, nobody else. A stranger, a reader of the library and the operator who is no
member get the answer for a thing that is not there, and never a title."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, ProcureNotice

from .bookmaker import make_pdf
from .conftest import make_account, new_client
from .filing_helpers import download_roots, ebook_world, finish, german_epub, the_download, world_with
from .follow_helpers import BOOK, sabnzbd, wish
from .library_helpers import join, operator_account, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def outsiders(world: Any, operator: Any) -> dict[str, Any]:
    reader = make_account("rea")
    join(world.library_id, reader.id, "read")
    return {"a stranger": new_client(make_account("tom")), "a reader": new_client(reader),
            "the operator": new_client(operator)}


def test_only_who_may_ask_for_books_answers_a_question_of_the_check(operator: Any, net: FakeNetwork,
                                                                   roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "r"
    folder.mkdir(parents=True)
    german_epub(folder, title="Ein Titel aus dem Verlag", series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.problem_code == "files_unassigned"
    for name, client in outsiders(world, operator).items():
        for action in ("take", "block", "acknowledge"):
            answer = client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": action})
            assert answer.status_code in (403, 404), (name, action, answer.status_code)
            assert TITLE not in answer.text and "Verlag" not in answer.text
        assert client.get("/api/v1/procure/problems").status_code in (200, 403)
        listed = client.get("/api/v1/procure/problems")
        assert "Verlag" not in listed.text, name
    assert the_download().state == "problem"
    # Two answers at once: one goes on, the second finds no question any more.
    first = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    second = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    assert first.status_code == 200 and second.status_code == 409
    run_jobs()
    assert the_download().state == "imported"


def test_a_note_is_its_person_s_own_and_the_trash_its_keeper_s(operator: Any, net: FakeNetwork,
                                                              roots: Path) -> None:
    from app import clock
    from app.models import Progress

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: make_pdf(folder / "falkner" / "laternenstrasse.pdf", title=TITLE,
                                                    author=AUTHOR, pages=5))
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    colleague = make_account("cleo")
    join(world.library_id, colleague.id, "acquire")
    from .filing_helpers import editions

    [pdf] = editions()
    with SessionLocal() as db:
        for person in (world.person.id, colleague.id):
            db.add(Progress(user_id=person, edition_id=pdf.id, locator="page:3", fraction=0.6, unit="page",
                            moved_at=clock.now(), received_at=clock.now()))
        db.commit()
    wish(world, kinds=("ebook",), language="de")
    run_jobs()
    folder = roots / "complete" / "r"
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, the_download().client_ref, folder)
    with SessionLocal() as db:
        notes = {row.user_id: row.id for row in db.scalars(select(ProcureNotice))}
    assert set(notes) == {world.person.id, colleague.id}
    mine = notes[world.person.id]
    outside = outsiders(world, operator)
    others = {**outside, "a colleague": new_client(colleague)}
    for name, client in others.items():
        for path in (f"/api/v1/procure/notes/{mine}/acknowledge", f"/api/v1/procure/notes/{mine}/restore"):
            assert client.post(path).status_code == 404, (name, path)
        shown = client.get("/api/v1/procure/problems")
        if shown.status_code == 200:
            assert all(note["id"] != mine for note in shown.json()["notes"]), name
    keeper = new_client(operator_account())
    [entry] = keeper.get("/api/v1/procure/trash").json()["entries"]
    for name, client in {**outside, "who may ask": world.client}.items():
        assert client.post(f"/api/v1/procure/trash/{entry['id']}/restore").status_code == 404, name
        assert client.delete(f"/api/v1/procure/trash/{entry['id']}").status_code == 404, name
        assert client.get("/api/v1/procure/trash").json()["entries"] == [], name
        emptied = client.delete("/api/v1/procure/trash")
        # Who looks after no library has no trash to empty (403); who looks after another empties nothing here.
        assert (emptied.status_code == 403 and emptied.json()["detail"]["code"] == "forbidden") or             emptied.json() == {"deleted": 0}, name
    assert keeper.post(f"/api/v1/procure/trash/{entry['id']}/restore").status_code == 204
    assert world.client.post(f"/api/v1/procure/notes/{mine}/acknowledge").status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(Download.state)) == "imported"
