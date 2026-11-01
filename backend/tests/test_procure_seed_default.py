"""Seeding is off from the start: a torrent leaves its program once the book is filed. A choice the operator made stays;
a value that was never changed follows the default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, Setting
from app.services import settings_service
from app.services.procure import automation

from .follow_helpers import round_of_tracking
from .procure_fakes import FakeNetwork
from .test_procure_tracking import roots, seed_ready  # noqa: F401 - the fixture and the world of a filed torrent


def stored(value: dict[str, Any]) -> None:
    with SessionLocal() as db:
        db.merge(Setting(key=automation.KEY, value=value))
        db.commit()


def old_row(**changes: Any) -> dict[str, Any]:
    """The row an earlier version saved: the whole config, no mark of a choice."""
    return {"enabled": True, "rss_minutes": 15, "retry_hours": 6, "replace": True, "replace_max": 3, "seed": "ratio",
            "retention_days": 0, **changes}


def test_the_default_is_no_seeding(client: TestClient, operator: Account) -> None:
    assert automation.default_config()["seed"] == "no"
    assert client.get("/api/v1/procure/automation").json()["seed"] == "no"
    with SessionLocal() as db:
        assert automation.config(db)["seed"] == "no"


def test_a_row_saved_by_an_earlier_version_with_the_old_default_follows_the_new_one(operator: Account) -> None:
    stored(old_row())
    with SessionLocal() as db:
        assert automation.config(db)["seed"] == "no"


def test_a_choice_the_operator_made_before_stays(operator: Account) -> None:
    stored(old_row(seed="7d"))
    with SessionLocal() as db:
        assert automation.config(db)["seed"] == "7d"


def test_choosing_the_ratio_now_is_a_choice_and_stays_through_other_changes(client: TestClient,
                                                                            operator: Account) -> None:
    body = client.get("/api/v1/procure/automation").json()
    assert client.put("/api/v1/procure/automation", json={**body, "seed": "ratio"}).json()["seed"] == "ratio"
    again = client.get("/api/v1/procure/automation").json()
    assert again["seed"] == "ratio"
    # Another setting changes: the page sends the whole config back, the ratio is still the operator's.
    assert client.put("/api/v1/procure/automation", json={**again, "rss_minutes": 60}).json()["seed"] == "ratio"
    with SessionLocal() as db:
        assert automation.config(db)["seed"] == "ratio"


def test_saving_other_settings_over_an_old_default_does_not_make_it_a_choice(client: TestClient,
                                                                           operator: Account) -> None:
    stored(old_row())
    body = client.get("/api/v1/procure/automation").json()
    assert body["seed"] == "no"
    assert client.put("/api/v1/procure/automation", json={**body, "rss_minutes": 30}).json()["seed"] == "no"
    with SessionLocal() as db:
        assert automation.config(db)["seed"] == "no"


def test_the_mark_of_a_choice_is_not_part_of_what_the_interface_shows(client: TestClient, operator: Account) -> None:
    body = client.get("/api/v1/procure/automation").json()
    client.put("/api/v1/procure/automation", json={**body, "seed": "7d"})
    assert "seed_set" not in client.get("/api/v1/procure/automation").json()
    with SessionLocal() as db:
        assert "seed_set" not in automation.config(db)
        assert settings_service.get(db, automation.KEY)["seed"] == "7d"


def test_with_the_default_a_filed_torrent_leaves_its_program_at_once(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:  # noqa: F811
    """Nothing was set: the book is filed, the torrent has not seeded a second, and the program lets it go."""
    with SessionLocal() as db:
        assert db.get(Setting, automation.KEY) is None
    qbit, info_hash, _row = seed_ready(net, roots, seed=None)
    qbit.put(info_hash, ratio=0.0, state="uploading")
    round_of_tracking()
    assert info_hash not in qbit.torrents


def test_the_choice_of_the_ratio_keeps_the_torrent_until_the_goal(operator: Any, net: FakeNetwork,
                                                                  roots: Path) -> None:  # noqa: F811
    qbit, info_hash, _row = seed_ready(net, roots, seed="ratio")
    qbit.put(info_hash, ratio=0.4, seeding_time=100, state="uploading")
    round_of_tracking()
    assert info_hash in qbit.torrents
