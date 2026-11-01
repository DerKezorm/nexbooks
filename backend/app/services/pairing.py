"""Connecting the iPhone app: a code in the browser instead of a typed token.

1. A signed-in person makes a pairing in the browser (``prepare``). It has an id, a secret and a short code. The id and
   the secret travel in a QR code (``nexbooks://connect?...``); the code is for typing by hand. Only the SHA-256 of the
   secret and of the code are kept, so neither can be read out of the database or a backup afterwards.
2. The app fetches **once** with the secret or with the code (``claim_with_secret``, ``claim_with_code``). The device
   token comes into being at that moment and is handed out in that one answer; a pairing nobody used leaves no device
   behind. The device stands in the list of devices like any other and can be revoked.
3. The browser asks how it stands (``state``) and can withdraw it as long as nobody used it (``withdraw``).

**Walls.** A pairing lives ten minutes. An account holds at most five that are open at once. The secret is compared in
constant time (``hmac.compare_digest``) and so is the code. A wrong secret and a wrong code count against the sender
(the caller feeds ``deps.pair_failed`` when ``Refused.guess`` is set): the code has about forty bits, which must not be
tried out in ten minutes. An id nobody made and a wrong secret answer the same, so the ids cannot be searched. Only
whoever shows the right secret or code learns that the pairing ran out or was used.

**Two brakes, both on their own.** Five wrong secrets or codes from one sender rest that sender a quarter of an hour
(``deps.pair_guard``). The count is not the one of the sign-in, so a typing mistake shuts out neither a device nor the
sign-in; and a right secret (256 bits, it cannot be guessed) is not turned away by that rest, so the QR code still works
after five mistakes with the typed code. Over all senders together only ``CODE_GUESSES`` wrong codes pass in
``CODE_WINDOW`` seconds (``CodeWatch``): the code is short enough to read out (about forty bits), and a net with many
addresses must not try it out five at a time. When that is spent, fetching by code rests until the window has room
again; fetching by the secret of the QR code goes on.

**One claim.** Claiming is one transaction: the pairing is taken with a conditional ``UPDATE`` (only an open one that
has not run out), the device is made, and both are committed together. Of two apps at the same moment exactly one gets
a token; if the device cannot be made (too many devices) nothing was taken.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import Account, AppPairing, Device, UtcDateTime
from ..vendor.nexoidc import flow
from . import devices, settings_service

logger = logging.getLogger("nexbooks.auth")

#: How long a pairing can be used.
LIFETIME = timedelta(minutes=10)
#: A pairing that ran out or was used stays this much longer, so that the browser that asks still reads "connected" or
#: "expired" and not "gone"; then it is deleted.
KEEP = timedelta(minutes=10)
#: How many pairings an account may have open (made, not used, not run out) at once.
OPEN_MAX = 5
#: Letters and digits a person reads without mixing them up (no 0, O, 1, I, L).
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8
#: The name of a device that sends none.
DEFAULT_DEVICE = "iPhone"
ID_BYTES = 16
SECRET_BYTES = 32
#: The scheme the app listens to: the camera app of the phone opens the link of the QR code in nexbooks.
APP_LINK = "nexbooks://connect"
#: How often a new code is tried when its hash is taken by another pairing (a few in a trillion).
CODE_TRIES = 5
#: Wrong codes that may be tried over all senders together within ``CODE_WINDOW`` seconds (a code's life). There are
#: 31**8 (about 8.5e11) codes; with 30 guesses in ten minutes the chance to hit one of 500 open codes is below 2e-8
#: per window, and a person who types by hand (a few mistakes, a few people) stays far below it.
CODE_GUESSES = 30
CODE_WINDOW = 600.0


class Refused(Exception):
    """A claim that is turned away. ``guess`` says the sender named a pairing, a secret or a code that does not exist or
    does not fit: that counts against it (``deps.pair_failed``). A refusal after the right secret or code does
    not."""

    def __init__(self, code: str, text: str, status: int, *, guess: bool = False, wait: int = 0) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status
        self.guess = guess
        #: Seconds until it may be tried again (``Retry-After``), for a refusal that rests the sender.
        self.wait = wait


@dataclass(frozen=True)
class Made:
    """A new pairing: the only place the secret and the code are known in the clear."""

    id: str
    secret: str
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class Claimed:
    """What the app takes home: the token, shown in this one answer, and whose account it speaks for."""

    token: str
    account_name: str
    device_name: str


class CodeWatch:
    """How many codes were tried over all senders in the last ``CODE_WINDOW`` seconds. In memory, like every brake
    (a restart forgets it, which costs the guesser nothing: a code lives ten minutes).

    A try is counted **before** the code is looked up (``take``), so that of many at the same moment only
    ``CODE_GUESSES`` get through; the one who showed a right code gets the place back (``give_back``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamps: deque[float] = deque()

    def take(self) -> tuple[float, int]:
        """``(stamp, 0)`` when one more try is allowed and counted, ``(0, seconds)`` when the window is full."""
        moment = clock.monotonic()
        with self._lock:
            while self._stamps and moment - self._stamps[0] >= CODE_WINDOW:
                self._stamps.popleft()
            if len(self._stamps) >= CODE_GUESSES:
                return 0.0, max(1, int(self._stamps[0] + CODE_WINDOW - moment + 0.999))
            # Two tries at the same instant must stay two entries, and a give-back must find its own.
            if self._stamps and moment <= self._stamps[-1]:
                moment = self._stamps[-1] + 1e-9
            self._stamps.append(moment)
        return moment, 0

    def give_back(self, stamp: float) -> None:
        with self._lock:
            try:
                self._stamps.remove(stamp)
            except ValueError:
                pass

    def forget(self) -> None:
        """For the tests."""
        with self._lock:
            self._stamps.clear()


#: The one watch of the process.
code_watch = CodeWatch()


def hash_secret(secret: str) -> str:
    return hashlib.sha256(b"nexbooks-pairing-secret:" + secret.encode("utf-8")).hexdigest()


def hash_code(code: str) -> str:
    return hashlib.sha256(b"nexbooks-pairing-code:" + code.encode("ascii")).hexdigest()


#: What a missing pairing is compared with, so that an id nobody made takes as long to answer as a wrong secret.
_NOTHING = hash_secret("")


def show_code(code: str) -> str:
    """``ABCD2345`` as people read and type it: ``ABCD-2345``."""
    return f"{code[: CODE_LENGTH // 2]}-{code[CODE_LENGTH // 2 :]}"


def clean_code(text: str) -> str | None:
    """The code as stored, from what a person typed (any case, with or without the dash or spaces); None when it cannot
    be a code at all (wrong length or a letter that is not in the alphabet). Such an input is no guess at a code."""
    if not text.isascii() or len(text) > 40:
        return None
    value = "".join(char for char in text.upper() if char not in "- ")
    if len(value) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in value):
        return None
    return value


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _gone(row: AppPairing, now: datetime) -> bool:
    return row.expires_at + KEEP <= now


def sweep(db: Session, now: datetime | None = None) -> int:
    """Deletes what ran out or was used long enough ago; commits. Nothing a person could still be waiting for."""
    now = now or clock.now()
    result = db.execute(delete(AppPairing).where(AppPairing.expires_at <= now - KEEP))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def prepare(db: Session, account: Account) -> Made:
    """A new pairing for ``account``; commits. ``pairing_limit`` (429) when five are open already: counting and making
    are one statement, so many at the same moment do not get past five."""
    now = clock.now()
    sweep(db, now)
    for _ in range(CODE_TRIES):
        pairing_id = secrets.token_urlsafe(ID_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        code = _new_code()
        opened = (select(func.count()).select_from(AppPairing)
                  .where(AppPairing.account_id == account.id, AppPairing.state == "open", AppPairing.expires_at > now))
        made = (select(literal(pairing_id), literal(account.id), literal(hash_secret(secret)),
                       literal(hash_code(code)), literal("open"), literal(now, type_=UtcDateTime()),
                       literal(now + LIFETIME, type_=UtcDateTime()))
                .where(opened.scalar_subquery() < OPEN_MAX))
        try:
            result = db.execute(insert(AppPairing).from_select(
                ["id", "account_id", "secret_hash", "code_hash", "state", "created_at", "expires_at"], made))
            db.commit()
        except IntegrityError:
            db.rollback()
            continue  # the code is taken by another pairing: another one
        if result.rowcount != 1:
            raise error("pairing_limit", f"{OPEN_MAX} codes are open already. Wait until one runs out or withdraw one.",
                        429, max=OPEN_MAX)
        logger.info("A pairing for the app was made name=%s", account.name)
        return Made(pairing_id, secret, show_code(code), now + LIFETIME)
    raise error("busy", "The server is busy. Try again in a moment.", 503)


def _own(db: Session, account: Account, pairing_id: str) -> AppPairing:
    row = db.get(AppPairing, pairing_id[:64]) if pairing_id else None
    # Another person's pairing looks like one that is not there.
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    return row


def state(db: Session, account: Account, pairing_id: str) -> tuple[str, Device | None]:
    """How a pairing of the account stands: ``waiting``, ``connected`` (with the device) or ``expired``."""
    row = _own(db, account, pairing_id)
    if row.state == "used":
        return "connected", (db.get(Device, row.device_id) if row.device_id is not None else None)
    return ("expired" if row.expires_at <= clock.now() else "waiting"), None


def withdraw(db: Session, account: Account, pairing_id: str) -> None:
    """The browser closes a pairing nobody used: it goes. One that was used stays (its device is revoked in the list of
    devices), and so does asking again after it went (404 for the first time only)."""
    row = _own(db, account, pairing_id)
    # The condition is part of the statement: the app may take the pairing between the look and the delete, and a
    # pairing that was used must stay (it holds the proof of which device it made).
    result = db.execute(delete(AppPairing).where(AppPairing.id == row.id, AppPairing.state == "open"))
    db.commit()
    if getattr(result, "rowcount", 0) == 1:
        logger.info("A pairing for the app was withdrawn name=%s", account.name)


def _spent(row: AppPairing | None, now: datetime) -> Refused:
    """Why a pairing whose secret or code was right cannot be claimed."""
    if row is None or _gone(row, now):
        return Refused("pairing_unknown", "This code does not exist.", 404)
    if row.state == "used":
        return Refused("pairing_used", "This code was used already. Make a new one in the browser.", 410)
    return Refused("pairing_expired", "This code ran out. Make a new one in the browser.", 410)


def _may_pair(db: Session, row: AppPairing) -> Account | None:
    account = db.get(Account, row.account_id)
    now = clock.now()
    if account is None or account.blocked_at is not None or (
            account.locked_until is not None and account.locked_until > now):
        return None
    return account


def _redeem(db: Session, row: AppPairing, device_name: str) -> Claimed:
    """The right secret or code was shown: take the pairing and make the device, as one transaction. There is no look
    before the taking: the one statement takes an open pairing that has not run out or takes nothing, and only then is
    asked why (``_spent``)."""
    account = _may_pair(db, row)
    if account is None:
        raise Refused("pairing_unknown", "This code does not exist.", 404)
    name = devices.clean_name(device_name.strip() or DEFAULT_DEVICE)
    account_name, row_id = account.name, row.id
    moment = clock.now()
    taken = db.execute(update(AppPairing).where(AppPairing.id == row_id, AppPairing.state == "open",
                                                AppPairing.expires_at > moment).values(state="used", used_at=moment))
    if taken.rowcount != 1:
        # Used, run out, or taken by another claim a moment ago.
        db.rollback()
        raise _spent(db.get(AppPairing, row_id), clock.now())
    try:
        device, token = devices.add(db, account.id, name)
    except Exception:
        db.rollback()  # nothing was taken: the pairing is open as it was
        raise
    db.execute(update(AppPairing).where(AppPairing.id == row_id).values(device_id=device.id))
    db.commit()
    logger.info("A device of %s was paired: %s", account_name, name)
    return Claimed(token, account_name, name)


def claim_with_secret(db: Session, pairing_id: str, secret: str, device_name: str) -> Claimed:
    """The app shows the id and the secret of the QR code. ``Refused`` for an unknown id and a wrong secret alike
    (``pairing_unknown``, counted as a guess); only the right secret learns ``pairing_expired`` or ``pairing_used``."""
    row = db.get(AppPairing, pairing_id[:64]) if pairing_id else None
    stored = row.secret_hash if row is not None else _NOTHING
    # Always compared, also when there is no such pairing: the answer takes as long.
    same = hmac.compare_digest(stored.encode("ascii"), hash_secret(secret).encode("ascii"))
    if row is None or not same:
        raise Refused("pairing_unknown", "This code does not exist.", 404, guess=True)
    return _redeem(db, row, device_name)


def claim_with_code(db: Session, code: str, device_name: str) -> Claimed:
    """The app shows the code a person typed. A code that cannot be one (``clean_code``) is turned away as
    ``pairing_code`` (422) without counting: it names nothing. One that fits but is not there is a guess."""
    clean = clean_code(code)
    if clean is None:
        raise Refused("pairing_code", "A code has eight letters and digits, like ABCD-2345.", 422)
    stamp, wait = code_watch.take()
    if wait:
        raise Refused("too_many_codes", "Too many wrong codes were tried on this server. Scan the QR code instead, or "
                      "try again later.", 429, wait=wait)
    wanted = hash_code(clean)
    row = db.scalar(select(AppPairing).where(AppPairing.code_hash == wanted))
    stored = row.code_hash if row is not None else _NOTHING
    same = hmac.compare_digest(stored.encode("ascii"), wanted.encode("ascii"))
    if row is None or not same:
        raise Refused("pairing_unknown", "This code does not exist.", 404, guess=True)
    code_watch.give_back(stamp)  # a right code is no guess, also when it has run out or was used
    return _redeem(db, row, device_name)


# --- The address the app will use --------------------------------------------------------------------------------


def _clean_address(text: str) -> str | None:
    """``scheme://host[:port]`` in small letters, or None. Two checks, the second the strict one of the sign-in module
    (a host is letters, digits, dots and dashes or an IPv6 address; nothing a link could be bent with)."""
    try:
        plain = settings_service.normalize_public_url(text)
        return flow.clean_public_url(plain) if plain else None
    except ValueError:
        return None


def choose_address(db: Session, from_browser: str, origin: str | None) -> str:
    """The address the app remembers: the public address of the settings goes first (a person at home who opens nexbooks
    by its address in the house wants the app to reach it from outside too); else the one the browser says it sees
    nexbooks at. That one is only taken when it is a plain ``http(s)://host[:port]`` and, if the browser sent an
    ``Origin`` (every browser does with a change), the very same address: nobody can put another host into a code this
    way. ``pairing_address`` (422) when there is none."""
    configured = _clean_address(settings_service.public_url(db))
    if configured:
        return configured
    claimed = _clean_address(from_browser)
    if claimed is not None and origin:
        seen = _clean_address(origin)
        if seen is None or seen.casefold() != claimed.casefold():
            claimed = None
    if claimed is None:
        raise error("pairing_address", "The code needs the address nexbooks is reached at; the operator sets it in the "
                    "settings.", 422)
    return claimed


def link(address: str, pairing_id: str, secret: str) -> str:
    """What the QR code holds."""
    return (f"{APP_LINK}?address={quote(address, safe='')}&pairing={quote(pairing_id, safe='')}"
            f"&secret={quote(secret, safe='')}")
