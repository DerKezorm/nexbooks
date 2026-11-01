"""Connecting a device that has no camera: the device shows a code, a signed-in person confirms it in the browser.

The other way round from ``pairing`` (where the browser shows a QR code and the app fetches a token with it):

1. The device asks for a code of its own (``create``). It gets an id, a secret that only it keeps, and a short code that
   its screen shows large. Nobody is signed in on the device, so no account is known yet. Only the SHA-256 of the secret
   and of the code are kept.
2. A signed-in person types the code in the browser (``look_up``), sees how the device is named and is asked whether to
   connect it to their account. **Only that confirmation** (``confirm``) ties the request to the account.
3. The device asks how it stands, with its secret (``poll``). Once the request is confirmed, the answer holds the device
   token, once. **The token is made at that moment and not before**, so it is never kept (not even in a backup): what
   waits in the database is the decision, not the key. A request nobody confirmed leaves no device behind.

Instead of the browser, the iPhone app may confirm with its own device token (``confirm_from_app``): the
television hands it the id and the code over the home network. The app can first ask how the server knows the device
(``preview_from_app``, it changes nothing) and take its confirmation back until the device fetched
(``withdraw_from_app``). **A device token makes a device that way**, and so the device that is fetched is connected
**through** the phone (``confirmed_by`` on the request, ``via_device_id`` on the device): revoking the phone ends it too
(``devices.revoke``), a device connected through another one confirms nothing itself (``device_chain``, 403), and the
list of devices says through which device it came.

**The code alone gives nothing.** Whoever reads the code off the screen can at most put the device on their own account
(that is wanted: the television in the living room shows a code, the person at the sofa confirms it). The token goes to
whoever shows the secret, and only the device knows it. Whoever types a code he was told, as a trick ("enter this code
to prove it is you"), connects a stranger's device to his own account: the dialog says so before it asks.

**Walls.**

* A request lives ten minutes. It is made without any sign-in, so how many may be open (not yet confirmed) is limited:
  per sender (``OPEN_PER_SENDER``) and in all (``OPEN_TOTAL``). The sender is the address the connection came from;
  ``X-Forwarded-For`` is believed only from a proxy the operator named in ``NEXBOOKS_TRUSTED_PROXIES``
  (``deps.open_sender``): anybody can write that header, so from any other peer it would buy as many senders as the
  caller likes. Behind a proxy nexbooks was not told about, everybody behind it is one sender (the log and "Ready for
  the internet?" say so). A sender at its limit does not meet a wall: its oldest open request runs out and the new
  one takes its place (a television that starts again does not lock itself out); only the limit over all senders turns
  a request away (429). The sender is kept as a salted hash that changes with every start and goes with the request.
* The secret is compared in constant time (``hmac.compare_digest``) and so is the code. An id nobody made and a wrong
  secret answer the same (``pairing_unknown``), so ids cannot be searched; only whoever shows the right secret learns
  that the request ran out or was used.
* A wrong secret when the device asks how its request stands counts against the sender (the caller feeds
  ``deps.pair_failed`` with ``kind="pairtv"``: five, then a quarter of an hour of rest). A right secret is not turned
  away by that rest (256 bits, it cannot be guessed). Letting go (``cancel``) is always answered the same and never
  counts. A wrong code typed in the browser counts in the count of the pairing with a QR code (``pair``), apart from
  the television's, so that the mistakes of a device do not keep a person from typing; over all senders together only
  ``pairing.CODE_GUESSES`` wrong codes pass in ``pairing.CODE_WINDOW`` seconds (``lookup_watch``, a watch of its own:
  the code is short, but whoever tries it has to be signed in, and must not be able to rest the typing of the codes of
  the other way).
* Whatever ends the sessions of an account (a new password, "sign out everywhere") also ends its requests that are
  confirmed and not yet fetched (``security.end_all_sessions``): they would bring a new device a moment later. So does
  revoking the device that confirmed one from the app (``devices.revoke``), and the device fetches nothing either while
  that device is revoked (``poll``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, insert, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import Account, AppRequest, Device, UtcDateTime
from . import devices
from .pairing import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_TRIES,
    ID_BYTES,
    SECRET_BYTES,
    CodeWatch,
    Refused,
    clean_code,
    hash_code,
    hash_secret,
    show_code,
)

logger = logging.getLogger("nexbooks.auth")

#: How long a request can be confirmed and fetched.
LIFETIME = timedelta(minutes=10)
#: A request that ran out or was used stays this much longer, so that the device that asks still hears "expired";
#: then it is deleted.
KEEP = timedelta(minutes=10)
#: How many requests that wait for a confirmation one sender may have open at once. A household has a few screens, and a
#: screen that shows a new code every time it is opened leaves one behind each time; ten ten-minute codes are room for
#: that and for nobody else to fill a server with them. The eleventh takes the place of the oldest.
OPEN_PER_SENDER = 10
#: And over all senders together. 300 open codes against 30 guesses in ten minutes (``lookup_watch``): the chance to hit
#: one is below 3e-8 per window (31**8 is about 8.5e11 codes).
OPEN_TOTAL = 300
#: How often the device is told to ask how it stands.
POLL_SECONDS = 3
#: What a device is called that sends no name.
DEFAULT_DEVICE = "Apple TV"
#: The wrong codes typed in browsers, over all senders. Not the watch of the pairing with a QR code
#: (``pairing.code_watch``).
lookup_watch = CodeWatch()

_SALT = secrets.token_bytes(16)
#: What a missing request is compared with, so that an id nobody made takes as long to answer as a wrong secret.
_NOTHING = hash_secret("")


@dataclass(frozen=True)
class Sender:
    """Where a request came from, as a salted hash of the address (``deps.open_sender``). Only to count how many
    requests are open. (The ``proxy`` column of the table stays empty: it is part of schema 21, which is fixed.)"""

    address: str

    @staticmethod
    def of(key: str) -> Sender:
        return Sender(hmac.new(_SALT, key.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest())


@dataclass(frozen=True)
class Made:
    """A new request: the only place the secret and the code are known in the clear."""

    id: str
    secret: str
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class Claimed:
    """What the device takes home: the token, shown in this one answer, and whose account it speaks for."""

    token: str
    account_name: str
    device_name: str


@dataclass(frozen=True)
class Polled:
    """How a request stands for the device that made it: ``waiting``, ``confirmed`` (with what it takes home) or
    ``expired``."""

    state: str
    claimed: Claimed | None = None


def _gone(row: AppRequest, now: datetime) -> bool:
    return row.expires_at + KEEP <= now


def sweep(db: Session, now: datetime | None = None) -> int:
    """Deletes what ran out or was used long enough ago; commits."""
    now = now or clock.now()
    result = db.execute(delete(AppRequest).where(AppRequest.expires_at <= now - KEEP))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _open(column: Any, value: str, now: datetime) -> Select[tuple[int]]:
    return (select(func.count()).select_from(AppRequest)
            .where(column == value, AppRequest.state == "waiting", AppRequest.expires_at > now))


def _wait_for_room(db: Session, now: datetime) -> int:
    """Seconds until a place is free again at the wall of the whole server: the open request that ends first."""
    soonest = db.scalar(select(func.min(AppRequest.expires_at)).where(
        AppRequest.state == "waiting", AppRequest.expires_at > now))
    if soonest is None:
        return 1
    return max(1, int((soonest - now).total_seconds() + 0.999))


def _make_room(db: Session, sender: Sender, now: datetime) -> None:
    """A sender that holds as many open requests as it may lets its oldest ones run out, so that one place is free (in
    the open transaction, not committed). It is one statement that counts and ends them, and the statement that makes
    the request comes right after it in the same transaction: of many at the same moment each ends what is too much
    and none gets past the number."""
    mine = (AppRequest.sender == sender.address, AppRequest.state == "waiting", AppRequest.expires_at > now)
    open_now = select(func.count()).select_from(AppRequest).where(*mine).scalar_subquery()
    oldest = (select(AppRequest.id).where(*mine).order_by(AppRequest.created_at, AppRequest.id)
              .limit(func.max(0, open_now - (OPEN_PER_SENDER - 1))))
    db.execute(update(AppRequest).where(AppRequest.id.in_(oldest)).values(expires_at=now))


def create(db: Session, device_name: str, sender: Sender) -> Made:
    """A new request for a device; commits. A sender that holds as many open ones as may be loses its oldest (that one
    runs out: its device hears ``expired``). ``too_many_requests`` (429, with ``Retry-After``) when the server holds as
    many open ones as may be: counting and making are one statement, so many at the same moment do not get past the
    number. The name is judged first (``device_name_invalid``, 422), as a name of the other way is."""
    name = devices.clean_name(device_name.strip() or DEFAULT_DEVICE)
    now = clock.now()
    sweep(db, now)
    for _ in range(CODE_TRIES):
        request_id = secrets.token_urlsafe(ID_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        code = _new_code()
        total = (select(func.count()).select_from(AppRequest)
                 .where(AppRequest.state == "waiting", AppRequest.expires_at > now).scalar_subquery())
        made = select(literal(request_id), literal(hash_secret(secret)), literal(hash_code(code)), literal(name),
                      literal("waiting"), literal(sender.address),
                      literal(now, type_=UtcDateTime()), literal(now + LIFETIME, type_=UtcDateTime())).where(
            _open(AppRequest.sender, sender.address, now).scalar_subquery() < OPEN_PER_SENDER, total < OPEN_TOTAL)
        try:
            _make_room(db, sender, now)
            result = db.execute(insert(AppRequest).from_select(
                ["id", "secret_hash", "code_hash", "device_name", "state", "sender", "created_at", "expires_at"], made))
            if result.rowcount == 1:
                db.commit()
        except IntegrityError:
            db.rollback()
            continue  # the code is taken by another request: another one
        if result.rowcount != 1:
            db.rollback()  # whatever _make_room ended stays as it was: nobody got a place
            wait = _wait_for_room(db, now)
            exc = error("too_many_requests", "Too many codes are open. Try again in a moment.", 429, retry_after=wait)
            exc.headers = {"Retry-After": str(wait)}
            raise exc
        return Made(request_id, secret, show_code(code), now + LIFETIME)
    raise error("busy", "The server is busy. Try again in a moment.", 503)


# --- The device asks how it stands --------------------------------------------------------------------------------


def _may_pair(db: Session, row: AppRequest) -> Account | None:
    account = db.get(Account, row.account_id) if row.account_id is not None else None
    now = clock.now()
    if account is None or account.blocked_at is not None or (
            account.locked_until is not None and account.locked_until > now):
        return None
    return account


def poll(db: Session, request_id: str, secret: str) -> Polled:
    """How the request stands, for whoever shows its secret. ``Refused`` for an unknown id and a wrong secret alike
    (``pairing_unknown``, counted as a guess); only the right secret learns ``pairing_used``.

    A confirmed request gives its token **once**: one conditional ``UPDATE`` takes it (only a confirmed one that has not
    run out), the device is made, and both are committed together. Of two polls at the same moment exactly one gets the
    token and the other hears ``pairing_used``; if the device cannot be made (too many devices) nothing was taken."""
    row = db.get(AppRequest, request_id[:64]) if request_id else None
    stored = row.secret_hash if row is not None else _NOTHING
    # Always compared, also when there is no such request: the answer takes as long.
    same = hmac.compare_digest(stored.encode("ascii"), hash_secret(secret).encode("ascii"))
    if row is None or not same:
        raise Refused("pairing_unknown", "This code does not exist.", 404, guess=True)
    now = clock.now()
    if _gone(row, now):
        raise Refused("pairing_unknown", "This code does not exist.", 404)
    if row.state == "used":
        raise Refused("pairing_used", "This code was used already. Ask for a new one.", 410)
    if row.expires_at <= now:
        return Polled("expired")
    if row.state == "waiting":
        return Polled("waiting")
    account = _may_pair(db, row)
    if account is None:
        return Polled("expired")  # the account is blocked or gone: nothing is given
    account_name, row_id, name, via = account.name, row.id, row.device_name, row.confirmed_by
    moment = clock.now()
    # A confirmation from the app holds only while the device that gave it does: revoked, it gave nothing.
    vouched = or_(AppRequest.confirmed_by.is_(None),
                  AppRequest.confirmed_by.in_(select(Device.id).where(Device.revoked_at.is_(None))))
    taken = db.execute(update(AppRequest).where(AppRequest.id == row_id, AppRequest.state == "confirmed",
                                                AppRequest.expires_at > moment, vouched)
                       .values(state="used", used_at=moment))
    if taken.rowcount != 1:
        db.rollback()
        fresh = db.get(AppRequest, row_id)
        if fresh is not None and fresh.state == "used":
            raise Refused("pairing_used", "This code was used already. Ask for a new one.", 410)
        return Polled("expired")
    try:
        device, token = devices.add(db, account.id, name, via=via)
    except Exception:
        db.rollback()  # nothing was taken: the request is confirmed as it was
        raise
    db.execute(update(AppRequest).where(AppRequest.id == row_id).values(device_id=device.id))
    db.commit()
    logger.info("A device of %s was paired by code: %s", account_name, name)
    return Polled("confirmed", Claimed(token, account_name, name))


def cancel(db: Session, request_id: str, secret: str) -> None:
    """The device lets its request go (its screen was left): it runs out now and gives its place back. For whoever
    shows the right secret it works whatever the state, and again and again (a request that is used or has run out
    stays as it is). **For anybody else nothing happens, and nothing is told or counted**: the secret has 256 bits and
    cannot be found out by letting go, and a device that lets go of a request the server forgot is no guesser."""
    row = db.get(AppRequest, request_id[:64]) if request_id else None
    stored = row.secret_hash if row is not None else _NOTHING
    same = hmac.compare_digest(stored.encode("ascii"), hash_secret(secret).encode("ascii"))
    if row is None or not same:
        return
    now = clock.now()
    # The condition is part of the statement: a request the device fetched in the meantime must stay.
    db.execute(update(AppRequest).where(AppRequest.id == row.id, AppRequest.state.in_(("waiting", "confirmed")),
                                        AppRequest.expires_at > now)
               .values(state="waiting", account_id=None, confirmed_at=None, expires_at=now))
    db.commit()


# --- The person in the browser ------------------------------------------------------------------------------------


def find_by_code(db: Session, typed: str) -> AppRequest:
    """The request a person typed the code of. A text that cannot be a code is ``pairing_code`` (422) and counts for
    nothing; one that fits and is not there is a guess (``pairing_unknown``, 404). Tried by the typing of all senders
    together at most ``pairing.CODE_GUESSES`` times in ``pairing.CODE_WINDOW`` seconds (``too_many_codes``, 429); a
    right code gives its place back."""
    clean = clean_code(typed)
    if clean is None:
        raise Refused("pairing_code", "A code has eight letters and digits, like ABCD-2345.", 422)
    stamp, wait = lookup_watch.take()
    if wait:
        raise Refused("too_many_codes", "Too many wrong codes were typed on this server. Try again later.", 429,
                      wait=wait)
    wanted = hash_code(clean)
    row = db.scalar(select(AppRequest).where(AppRequest.code_hash == wanted))
    stored = row.code_hash if row is not None else _NOTHING
    same = hmac.compare_digest(stored.encode("ascii"), wanted.encode("ascii"))
    if row is None or not same or _gone(row, clock.now()):
        raise Refused("pairing_unknown", "This code does not exist or has run out.", 404, guess=True)
    lookup_watch.give_back(stamp)
    return row


def _unknown_pair() -> Refused:
    return Refused("pairing_unknown", "This code does not exist or has run out.", 404, guess=True)


def _spent(row: AppRequest | None, now: datetime) -> Refused:
    """Why a request whose code was right cannot be confirmed."""
    if row is None or _gone(row, now):
        return Refused("pairing_unknown", "This code does not exist or has run out.", 404)
    if row.state != "waiting":
        return Refused("pairing_used", "This code was used already.", 410)
    return Refused("pairing_expired", "This code ran out. Ask the device for a new one.", 410)


def look_up(db: Session, typed: str) -> AppRequest:
    """The request a person typed the code of, to show them how the device is named before they decide. It is no
    decision: nothing changes. ``Refused`` as ``find_by_code`` and, for a right code, ``pairing_expired`` or
    ``pairing_used`` (a request somebody confirmed already is not shown again)."""
    row = find_by_code(db, typed)
    now = clock.now()
    if row.state != "waiting" or row.expires_at <= now:
        raise _spent(row, now)
    return row


def _decide(db: Session, account: Account, row: AppRequest, *, repeat: bool, log: str, who: tuple[Any, ...],
            hide_others: bool = False, by: int | None = None) -> AppRequest:
    """The one place where a request is tied to an account, whoever confirms it (a signed-in browser, or the iPhone app
    with its own device token). The caller has shown that the request is the one meant (the id and the code fit).
    ``too_many_devices`` (409) while the account holds the most devices there may be; the request stays as it was then.
    With ``repeat`` the same person may confirm twice (a double click in the browser); without it a request that is
    confirmed already is ``pairing_used`` (410), whoever asks (with ``hide_others``: for another account's it is
    ``pairing_unknown``, 404, a guess). The conditional ``UPDATE`` is the decision: of two at the
    same moment exactly one takes the request. ``log`` and ``who`` are what the log says about it (never a secret).
    ``by`` is the device that confirms from the app (none for a browser): the device fetched later is connected through
    it. A request that is confirmed already or ran out says so before the limit of devices is looked at (410, not
    409)."""
    now = clock.now()
    row_id, account_id = row.id, account.id
    took = 0
    if row.state == "waiting" and row.expires_at > now:
        live = db.scalar(select(func.count()).select_from(Device)
                         .where(Device.account_id == account.id, Device.revoked_at.is_(None))) or 0
        if live >= devices.MAX_DEVICES:
            raise error("too_many_devices", f"An account holds at most {devices.MAX_DEVICES} devices.", 409)
        taken = db.execute(update(AppRequest).where(AppRequest.id == row_id, AppRequest.state == "waiting",
                                                    AppRequest.expires_at > now)
                           .values(state="confirmed", account_id=account_id, confirmed_at=now, confirmed_by=by))
        took = int(taken.rowcount or 0)
        db.commit()
    db.expire_all()
    fresh = db.get(AppRequest, row_id)
    if took != 1:
        if fresh is not None and fresh.state in ("confirmed", "used"):
            if repeat and fresh.account_id == account_id:
                return fresh  # the same person once more
            if hide_others and fresh.account_id != account_id:
                raise _unknown_pair()
        raise _spent(fresh, now)
    assert fresh is not None
    logger.info(log, *who)
    return fresh


def confirm(db: Session, account: Account, request_id: str, typed: str) -> AppRequest:
    """The person decided in the browser: connect the device to **their** account. Needs the id from ``look_up`` **and**
    the code (the id alone, which stands in the address the device asks at, decides nothing). The same account may
    confirm twice (a double click)."""
    row = find_by_code(db, typed)
    if not hmac.compare_digest(row.id.encode("ascii"), request_id[:64].encode("ascii", "replace")):
        raise Refused("pairing_unknown", "This code does not exist or has run out.", 404, guess=True)
    return _decide(db, account, row, repeat=True, log="A device request was confirmed by %s", who=(account.name,))


def count_wrong_code() -> Refused | None:
    """One more wrong code typed on this server, over all senders (``lookup_watch``): ``None`` when it was counted, else
    the refusal (``too_many_codes``, 429) that tells how long the window is full."""
    _stamp, wait = lookup_watch.take()
    if wait:
        return Refused("too_many_codes", "Too many wrong codes were typed on this server. Try again later.", 429,
                       wait=wait)
    return None


def may_confirm(db: Session, device_id: int) -> None:
    """Whether this device may confirm the request of another one from the app: not when it was itself connected
    through a device (``device_chain``, 403). So a phone may bring a television, and the television brings nothing: what
    a lost phone did is ended with it (``devices.revoke``), and nothing it brought can carry on in its place."""
    row = db.get(Device, device_id)
    if row is None or row.via_device_id is not None:
        raise error("device_chain", "This device was connected by another device and cannot connect one itself. "
                                    "Use the browser.", 403)


def _pair_for_app(db: Session, account: Account, request_id: str, typed: str) -> AppRequest:
    """The request an app means with an id **and** a code it got from the device. ``Refused`` as ``confirm_from_app``
    says; nothing changes here."""
    clean = clean_code(typed)
    if clean is None:
        raise Refused("pairing_code", "A code has eight letters and digits, like ABCD-2345.", 422)
    row = db.get(AppRequest, request_id[:64]) if request_id else None
    stored = row.code_hash if row is not None else _NOTHING
    same = hmac.compare_digest(stored.encode("ascii"), hash_code(clean).encode("ascii"))
    if row is None or not same or _gone(row, clock.now()):
        raise _unknown_pair()
    if row.state in ("confirmed", "used") and row.account_id != account.id:
        raise _unknown_pair()  # another person's: as if it were not there
    return row


def preview_from_app(db: Session, account: Account, device_id: int, request_id: str, typed: str) -> AppRequest:
    """How the server knows the device that asks, for the app to show **before** the person decides: the name the device
    gave when it asked, and when it asked. The same id and code as for confirming, the same answers and the same
    counts (a wrong pair is a guess); it decides nothing. ``pairing_used`` for one that is confirmed already,
    ``pairing_expired`` for one that ran out or was let go."""
    may_confirm(db, device_id)
    row = _pair_for_app(db, account, request_id, typed)
    now = clock.now()
    if row.state != "waiting" or row.expires_at <= now:
        raise _spent(row, now)
    return row


def confirm_from_app(db: Session, account: Account, request_id: str, typed: str, app_name: str,
                     device_id: int) -> AppRequest:
    """The person confirms from the iPhone app, which got the id and the code from the device (the device shows nothing
    to type): the same decision as in the browser (``_decide``), for the account of the device token that asks. The
    device fetched later is connected **through** the device that confirms (``device_id``): revoking that one ends it
    too. A device that was itself connected that way confirms nothing (``device_chain``, 403).

    **Both must fit**, the id of the request and its code; an id nobody made, a code that does not fit and a request
    that another person confirmed already look the same (``pairing_unknown``, 404, a guess). Unlike in the browser the
    request is found by its id, a number of 128 bits that nobody can try, so a right pair is known as right before
    anything is counted: the caller does not turn it away because the sender, the account or the server has been trying
    wrong codes (as a right secret is not turned away when the device asks how it stands). Wrong ones are counted by the
    caller (``deps.refuse_pairing``, ``count_wrong_code``), also against the request itself. A text that cannot be a
    code is ``pairing_code`` (422) and counts for nothing. A request that this account confirmed already is
    ``pairing_used`` (410): the app sends once. ``app_name`` is how the device that confirms is named in the list of
    devices (cleaned already)."""
    may_confirm(db, device_id)
    row = _pair_for_app(db, account, request_id, typed)
    return _decide(db, account, row, repeat=False, who=(account.name, app_name, row.device_name),
                   log="A device of %s was confirmed from the app %s: %s", hide_others=True, by=device_id)


def withdraw_from_app(db: Session, account: Account, device_id: int, request_id: str) -> None:
    """The app takes back what it confirmed, as long as the device has not fetched its token: the request runs out now
    and the device that asks hears ``expired``. Only a request **this device** confirmed; any other is
    ``pairing_unknown`` (404, not counted: the id of a request one did not confirm gives nothing to find). A request
    that was fetched is ``pairing_used`` (410): the device is in the list then, and is revoked there. Again and again it
    is the same: one that is taken back or ran out stays so (204)."""
    row = db.get(AppRequest, request_id[:64]) if request_id else None
    if row is None or row.confirmed_by != device_id or _gone(row, clock.now()):
        raise Refused("pairing_unknown", "This code does not exist or has run out.", 404)
    if row.state == "used":
        raise Refused("pairing_used", "The device fetched its token already. Remove it in the list of devices.", 410)
    # The condition is part of the statement: the device may fetch between the look and the change.
    result = db.execute(update(AppRequest).where(AppRequest.id == row.id, AppRequest.state == "confirmed",
                                                 AppRequest.confirmed_by == device_id)
                        .values(state="waiting", account_id=None, confirmed_at=None, expires_at=clock.now()))
    db.commit()
    if getattr(result, "rowcount", 0) == 1:
        logger.info("A device request was taken back from the app by %s", account.name)
        return
    db.expire_all()
    fresh = db.get(AppRequest, row.id)
    if fresh is not None and fresh.state == "used":
        raise Refused("pairing_used", "The device fetched its token already. Remove it in the list of devices.", 410)


def _own(db: Session, account: Account, request_id: str) -> AppRequest:
    row = db.get(AppRequest, request_id[:64]) if request_id else None
    # Another person's request, and one nobody confirmed, look like one that is not there.
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    return row


def state(db: Session, account: Account, request_id: str) -> tuple[str, Device | None]:
    """How a request the account confirmed stands: ``confirmed`` (the device has not fetched yet), ``connected`` (with
    the device) or ``expired``."""
    row = _own(db, account, request_id)
    if row.state == "used":
        return "connected", (db.get(Device, row.device_id) if row.device_id is not None else None)
    return ("expired" if row.expires_at <= clock.now() else "confirmed"), None


def withdraw(db: Session, account: Account, request_id: str) -> None:
    """The person takes the confirmation back before the device fetched its token: the request runs out now, and the
    device that asks is told so. One that was fetched stays (its device is revoked in the list of devices). The
    condition is part of the statement: the device may fetch between the look and the change."""
    row = _own(db, account, request_id)
    result = db.execute(update(AppRequest).where(AppRequest.id == row.id, AppRequest.state == "confirmed")
                        .values(state="waiting", account_id=None, confirmed_at=None, expires_at=clock.now()))
    db.commit()
    if getattr(result, "rowcount", 0) == 1:
        logger.info("A device request was taken back by %s", account.name)
