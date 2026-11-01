"""What the tests of the matching share: the sources switched on with a stand-in for the services, a clock that jumps when
the throttle makes the code wait, and small ways to look at what the matching made."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Edition, MatchEntry
from app.security import encrypt_secret
from app.services import settings_service
from app.services.sources import config, http

from .fake_sources import FakeSources


class World:
    """The sources on, the services faked, time under control. Audible is on too (it is off from the start; the tests that
    are about that say ``audible=False``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, audible: bool = True) -> None:
        self.fake = FakeSources()
        self.offset = 0.0
        start = clock.now()
        # Frozen at the start; it moves only when the code waits or the test lets time pass.
        monkeypatch.setattr(clock, "now", lambda: start + timedelta(seconds=self.offset))
        monkeypatch.setattr(http, "sleep", self.advance)
        monkeypatch.setattr(http, "transport", self.fake)
        self.slept = 0.0
        with SessionLocal() as db:
            settings_service.save(db, {"sources_enabled": True, **({"source_audible": True} if audible else {})})

    def advance(self, seconds: float) -> None:
        """Time passes (the code waited for its turn, or the test lets a day go by)."""
        self.offset += seconds
        self.slept += seconds

    def days(self, days: float) -> None:
        self.offset += days * 86400

    def settings(self, **values: Any) -> None:
        with SessionLocal() as db:
            settings_service.save(db, values)

    def connect_hardcover(self) -> str:
        """Hardcover with a token made for the test."""
        token = secrets.token_urlsafe(24)
        self.settings(hardcover_token_enc=encrypt_secret(token, config.TOKEN_CONTEXT))
        return token

    def only(self, *names: str) -> None:
        """Only these sources are on."""
        self.settings(**{f"source_{name}": name in names for name in ("hardcover", "openlibrary", "audible", "dnb", "apple")})


def member(library_id: int, name: str = "tester", right: str = "manage") -> None:
    """The account ``name`` becomes a member of the library with ``right`` (the one who signed in is not the operator
    ``ops`` that made the library)."""
    from app.models import Account, LibraryMember

    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.name == name))
        assert account is not None
        row = db.scalar(select(LibraryMember).where(LibraryMember.library_id == library_id,
                                                    LibraryMember.user_id == account.id))
        if row is None:
            db.add(LibraryMember(library_id=library_id, user_id=account.id, right=right))
        else:
            row.right = right
        db.commit()


def entries() -> list[tuple[str, str, str, str]]:
    """``(title, state, reason, decision)`` of every entry, by title."""
    with SessionLocal() as db:
        rows = db.execute(select(Edition.title, MatchEntry.state, MatchEntry.reason, MatchEntry.decision)
                          .join(Edition, Edition.id == MatchEntry.edition_id).order_by(Edition.title, Edition.id)).all()
    return [(a, b, c, d) for a, b, c, d in rows]


def entry_for(title: str) -> MatchEntry:
    with SessionLocal() as db:
        row = db.scalar(select(MatchEntry).join(Edition, Edition.id == MatchEntry.edition_id)
                        .where(Edition.title == title).order_by(MatchEntry.id))
        assert row is not None, f"no entry for {title}"
        db.expunge(row)
        return row


def edition_of(title: str) -> Edition:
    with SessionLocal() as db:
        row = db.scalar(select(Edition).where(Edition.title == title).order_by(Edition.id))
        assert row is not None, f"no edition {title}"
        db.expunge(row)
        return row
