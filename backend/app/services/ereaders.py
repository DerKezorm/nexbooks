"""Connections for e-readers and reading apps: a person makes one for the catalogue their reading app opens (OPDS,
``routers/opds.py``), for the progress sync of KOReader (``routers/kosync.py``) and for the sync of a Kobo
(``routers/kobo.py``). Each acts as the account that made it and sees no more than that account.

**On from the start, switched off by the operator** (``ereaders_allowed``): every person makes connections of their
own, under My account, Devices and apps. The operator sees every connection and may block one for good
(``blocked_at``); a blocked connection answers like none.

**Only a browser makes, lists and deletes them** (``BrowserAccount``): a device token or an API key never does, so a
lost phone cannot make itself a door that outlives the phone.

**The secret.** A prefix by kind and 32 random characters, shown once. Only its SHA-256 is stored, and its first
characters to tell connections apart. A reading app takes it as the password of the account's name; it travels in
``Authorization: Basic``, never in an address, and never reaches the log. The account's own password is not a way in
here, whatever the app asks for.

**A Kobo has no way to send a header of its own.** All it takes is an address (``api_endpoint`` in its settings
file), so the secret of a connection of the kind ``kobo`` is a part of that address, ``/kobo/<secret>/...``. That
makes it the one secret that travels in a path, which is why the log masks it (``services/logs.py``), no answer
repeats it but the one that hands the device its addresses, and nothing redirects. It is looked up by its hash like
the others (``authenticate_path``); a name is not asked for, there is none to ask.

**KOReader sends the MD5 of the password, not the password.** The secret of a connection of the kind ``kosync`` is what
a person types as the password; KOReader turns it into its MD5 (32 hexadecimal digits) and sends that as the key on
every request. So for this kind the stored hash is the SHA-256 of that MD5 (``stored_hash``): a request is checked by
hashing the key it brings, and what the database holds is neither the password nor a key that works. The secret is 192
random bits, so the MD5 of it adds no way to guess it (there is nothing to look up or to attack but a key of 128 bits).
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import EREADER_KOBO, EREADER_KOSYNC, EREADER_OPDS, Account, EreaderLink, utcnow
from . import settings_service, totp

logger = logging.getLogger("nexbooks.ereaders")

#: The start of the secret of each kind (a test keeps them apart from the tokens of devices and keys).
PREFIXES = {EREADER_OPDS: "nbo_", EREADER_KOSYNC: "nbs_", EREADER_KOBO: "nbe_"}
#: The kinds a person can make a connection for.
CREATABLE = (EREADER_OPDS, EREADER_KOSYNC, EREADER_KOBO)
#: What KOReader sends as the key: the MD5 of the password in lower case.
KEY = re.compile(r"^[0-9a-f]{32}$")
#: How many connections an account may hold.
MAX_LINKS = 20
#: ``last_used_at`` is written at most this often.
USED_EVERY = timedelta(minutes=1)


def base_of(db: Session, request: Request) -> str:
    """The address nexbooks hands a Kobo for everything else it asks: the public address the operator set, else the one
    this request came to (behind a proxy it has the scheme the proxy told it, if nexbooks is told to believe it)."""
    return settings_service.public_url(db) or str(request.base_url).rstrip("/")


class LinkProblem(Exception):
    def __init__(self, code: str, text: str, status: int = 400) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status


def allowed(db: Session) -> bool:
    return bool(settings_service.get(db, "ereaders_allowed"))


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def stored_hash(kind: str, secret: str) -> str:
    """What is kept of the secret of a connection: its SHA-256, and for the kind whose app sends the MD5 of the password
    (``kosync``) the SHA-256 of that MD5, which is what a request brings."""
    if kind == EREADER_KOSYNC:
        return digest(hashlib.md5(secret.encode("utf-8"), usedforsecurity=False).hexdigest())
    return digest(secret)


def make(db: Session, account: Account, name: str, kind: str) -> tuple[EreaderLink, str]:
    """A new connection; the secret is returned once and never stored."""
    if kind not in CREATABLE:
        raise LinkProblem("invalid_input", "No such kind of connection.", 422)
    if db.scalar(select(EreaderLink.id).where(EreaderLink.account_id == account.id, EreaderLink.kind == kind,
                                              EreaderLink.blocked_at.is_not(None)).limit(1)) is not None:
        # The operator blocked a connection of this kind for good: a new one would be the door shut for nothing.
        raise LinkProblem("kind_blocked", "The operator blocked a connection of this kind. Ask the operator.", 409)
    secret = PREFIXES[kind] + secrets.token_urlsafe(24)
    hashed = stored_hash(kind, secret)
    # The limit is kept by the statement that writes the connection (one INSERT ... SELECT that selects nothing once
    # the account holds ``MAX_LINKS``): counting first and writing after would let a crowd of requests at the same
    # moment all see the same count and all write.
    held = select(func.count()).select_from(EreaderLink).where(EreaderLink.account_id == account.id).scalar_subquery()
    source = select(literal(account.id), literal(kind), literal(name.strip()[:100] or kind.upper()), literal(hashed),
                    literal(secret[: len(PREFIXES[kind]) + 4]),
                    literal(utcnow(), type_=EreaderLink.created_at.type)).where(held < MAX_LINKS)
    columns = ["account_id", "kind", "name", "token_hash", "prefix", "created_at"]
    written = db.execute(sqlite_insert(EreaderLink).from_select(columns, source)).rowcount
    if written != 1:
        db.rollback()
        raise LinkProblem("too_many_links", "An account holds at most 20 connections for e-readers.", 409)
    db.commit()
    row = db.scalar(select(EreaderLink).where(EreaderLink.token_hash == hashed))
    assert row is not None
    logger.info("E-reader connection made link_id=%s kind=%s", row.id, kind)
    return row, secret


@dataclass
class Caller:
    """Who comes in with a connection: the account, detached, and the connection."""

    account: Account
    link_id: int


def authenticate(db: Session, kind: str, username: str, secret: str) -> Caller | None:
    """The account behind a secret of ``kind``, or None: no such connection of that kind, blocked, the name not the
    account's, or the account blocked or waiting for its second factor. The switch of the operator is the
    caller's to check (``allowed``)."""
    prefix = PREFIXES.get(kind)
    if prefix is None or not secret.startswith(prefix) or len(secret) > 200:
        return None
    return _caller(db, kind, digest(secret), username)


def authenticate_path(db: Session, secret: str) -> Caller | None:
    """The account behind the secret in the address of a Kobo, or None for the reasons ``authenticate`` names (no name
    is asked for: a Kobo has no way to say one). The switch of the operator is the caller's to check (``allowed``)."""
    prefix = PREFIXES[EREADER_KOBO]
    if not secret.startswith(prefix) or len(secret) > 200:
        return None
    return _caller(db, EREADER_KOBO, digest(secret), None)


def authenticate_key(db: Session, username: str, key: str) -> Caller | None:
    """The account behind the key KOReader sends (the MD5 of the secret of a connection of the kind ``kosync``), or None
    for the reasons ``authenticate`` names. A key that is not 32 hexadecimal digits is not looked up."""
    if not KEY.match(key):
        return None
    return _caller(db, EREADER_KOSYNC, digest(key), username)


def _caller(db: Session, kind: str, stored: str, username: str | None) -> Caller | None:
    row = db.scalar(select(EreaderLink).where(EreaderLink.token_hash == stored, EreaderLink.kind == kind))
    if row is None or row.blocked_at is not None:
        return None
    account = db.get(Account, row.account_id)
    now = utcnow()
    # The lock after wrong passwords is for the password only: a connection has a secret of its own, and anyone who
    # knows a name could otherwise switch off every e-reader of the account. A block by the operator stops it all.
    if account is None or account.blocked_at is not None:
        return None
    # The secret is the proof; the name must be the account's own, so that a secret does not open the door for any name
    # (a Kobo names none: its secret is the whole address).
    if username is not None and not secrets.compare_digest(
            account.name.encode("utf-8"), username.strip().casefold().encode("utf-8")):
        return None
    if totp.setup_required(db, account):
        # The operator requires a second factor this account has not set up: its connections wait like its sessions.
        return None
    if row.last_used_at is None or now - row.last_used_at >= USED_EVERY:
        row.last_used_at = now
        db.commit()
    link_id = row.id
    db.expunge(account)
    return Caller(account=account, link_id=link_id)
