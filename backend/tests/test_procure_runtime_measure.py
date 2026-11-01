"""The running time a recording that comes is held against: only a recording of the library whose reading is proven
unabridged (its words, a source, a person) is the measure; one without a word may be a shortened reading. Only invented
books."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Work
from app.services.procure import search

from .library_helpers import book, books_folder, make_library, operator_account, recording, scan


@pytest.mark.parametrize(("album", "measure"), [("Der Spiegelsaal (ungekürzt)", True), ("Der Spiegelsaal", False)])
def test_only_a_reading_proven_unabridged_is_the_measure_of_the_running_time(operator: Any, album: str,
                                                                             measure: bool) -> None:
    folder = books_folder(f"measure-{measure}")
    book(folder, "Ida Brandt/Der Spiegelsaal/spiegelsaal.epub", title="Der Spiegelsaal", authors=("Ida Brandt",),
         language="de")
    recording(folder, "Ida Brandt/Der Spiegelsaal/01.mp3", seconds=30.0, album=album, title="Der Spiegelsaal",
              artist="Ida Brandt", track=1)
    scan(make_library(folder))
    with SessionLocal() as db:
        work_id = db.scalar(select(Work.id))
        assert work_id is not None
        subject = search.subject_of_work(db, operator_account().id, work_id, ["de"])
    assert (subject["duration"] is not None) is measure
