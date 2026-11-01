"""API keys: programs such as a dashboard card read nexbooks over ``/api/v1`` (``routers/v1.py``).

**Closed until the operator opens it** (``api_keys_allowed``). Then every account makes keys of its own, under My
account, Devices and apps. A key acts as its account and never sees more.

**Scope.** ``read`` reads. A key never changes settings and never makes, lists or deletes keys or devices (those
routes want a signed-in browser).

**Limits.** At most ``PER_MINUTE`` requests a minute per key (in memory: a restart forgets them, which only ever
helps the key).

A key may run out (``expires_at``); the list marks it a week before. The operator sees every key and may block one for
good (``blocked_at``); a blocked key answers like none.

**The key.** ``nbk_`` and 43 random characters, shown once. Only its SHA-256 is stored, and its first characters to
tell keys apart. It travels in ``Authorization: Bearer``, never in an address, and never reaches the log.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, ApiKey, utcnow
from . import settings_service, totp

logger = logging.getLogger("nexbooks.api")

#: The scopes the routes know.
SCOPES = ("read",)
#: The scopes a person can make a key for.
CREATABLE = ("read",)
KEY_PREFIX = "nbk_"
#: How many keys an account may hold.
MAX_KEYS = 20
#: The choices for running out, in days; None: never (the default, design answer).
LIFETIMES = (30, 90, 365)
#: Requests one key may make per minute.
PER_MINUTE = 120
#: ``last_used_at`` is written at most this often.
USED_EVERY = timedelta(minutes=1)


class KeyProblem(Exception):
    def __init__(self, code: str, text: str, status: int = 400) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status


def allowed(db: Session) -> bool:
    return bool(settings_service.get(db, "api_keys_allowed"))


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make(db: Session, account: Account, name: str, scope: str, days: int | None = None) -> tuple[ApiKey, str]:
    """A new key; the key itself is returned once and never stored. ``days``: runs out then; None: never."""
    if scope not in CREATABLE:
        raise KeyProblem("invalid_input", "No such scope.", 422)
    if days is not None and days not in LIFETIMES:
        raise KeyProblem("invalid_input", "A key runs out after 30, 90 or 365 days, or never.", 422)
    count = len(db.scalars(select(ApiKey.id).where(ApiKey.account_id == account.id)).all())
    if count >= MAX_KEYS:
        raise KeyProblem("too_many_keys", "An account holds at most 20 keys.", 409)
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    row = ApiKey(account_id=account.id, name=name.strip()[:100] or "API", scope=scope, token_hash=digest(token),
                 prefix=token[: len(KEY_PREFIX) + 4], created_at=now,
                 expires_at=None if days is None else now + timedelta(days=days))
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("API key made key_id=%s scope=%s days=%s", row.id, scope, days or "never")
    return row, token


@dataclass
class Caller:
    """Who calls ``/api/v1`` with a key: the account, detached, and its key."""

    account: Account
    key_id: int
    scope: str


def authenticate(db: Session, token: str | None) -> Caller | None:
    """The account behind a key, or None: no such key, run out, blocked, or the account blocked or waiting for its
    second factor. Keys being switched off is the caller's to check (``allowed``)."""
    if not token or not token.startswith(KEY_PREFIX) or len(token) > 200:
        return None
    row = db.scalar(select(ApiKey).where(ApiKey.token_hash == digest(token)))
    now = utcnow()
    if row is None or row.blocked_at is not None or (row.expires_at is not None and row.expires_at <= now):
        return None
    account = db.get(Account, row.account_id)
    if account is None or account.blocked_at is not None:
        # Blocked means no way in at all, a key included. The lock after wrong passwords is for the password only: a
        # key is a secret of its own, and anyone who knows a name could otherwise cut off the programs of the account.
        return None
    if totp.setup_required(db, account):
        # The operator requires a second factor this account has not set up: its keys wait like its sessions.
        return None
    if row.last_used_at is None or now - row.last_used_at >= USED_EVERY:
        row.last_used_at = now
        db.commit()
    if row.scope not in SCOPES:
        # A scope this version does not know (a row edited by hand): it opens nothing, rather than something.
        return None
    scope = row.scope
    key_id = row.id
    db.expunge(account)
    return Caller(account=account, key_id=key_id, scope=scope)


_calls_lock = threading.Lock()
_calls: dict[int, deque[float]] = defaultdict(deque)


def brake(key_id: int, now: float | None = None) -> bool:
    """True while the key stays under ``PER_MINUTE`` requests in the last minute; counts this one."""
    now = time.monotonic() if now is None else now
    with _calls_lock:
        seen = _calls[key_id]
        while seen and now - seen[0] > 60:
            seen.popleft()
        if len(seen) >= PER_MINUTE:
            return False
        seen.append(now)
        return True


def forget() -> None:
    with _calls_lock:
        _calls.clear()
