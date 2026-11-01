"""Who is asking.

Every route under ``/api/v1`` except setup, sign-in, invitations, languages and health needs the session cookie or a
device token (``Authorization: Bearer``, ``services/devices.py``). A browser's changing requests need
``X-Nexbooks-Client`` on top (``GuardMiddleware``): a page on another site can make a browser send a form with the
cookie, but not a request with a header of its own, and the ``Authorization`` header it cannot set either. A request
that carries the header is judged by its token alone; the cookie is not looked at, so a wrong token never falls back
to a cookie.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import clock
from .config import get_settings
from .db import SessionLocal, get_db
from .errors import WRONG_CREDENTIAL, detail, error
from .models import OPERATOR, SIGN_IN_PASSWORD, STAGE_CODES, STAGE_FULL, STAGE_SETUP
from .models import Account as AccountRow
from .security import SESSION_COOKIE, brake, current_session
from .services import accounts, apikeys, devices, logs, pairing, totp

DbSession = Annotated[Session, Depends(get_db)]

#: Only for the description of the interface (``/api/v1/openapi.json``): it names the way a device or a program
#: signs in.
#: The header itself is read in ``bearer_token``; this scheme never refuses anything.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="HTTPBearer",
                           description="A device token (nbd_...) or, on the routes for programs, an API key (nbk_...).")

logger = logging.getLogger("nexbooks.auth")


@lru_cache(maxsize=4)
def _trusted_networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def parse_address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address in a peer or a forwarded hop, without port, brackets or zone; IPv4 in IPv6 dress unwrapped."""
    text = text.strip()
    candidates = [text]
    if text.startswith("[") and "]" in text:
        candidates.insert(0, text[1 : text.index("]")])
    elif text.count(":") == 1:
        candidates.insert(0, text.split(":")[0])
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate.split("%")[0])
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return address
    return None


def normal_address(text: str) -> str:
    """One sender, one spelling, for the brake: IPv6 as its /64 (one connection usually holds a whole /64). Before,
    every port and every way of writing an address counted as a new sender."""
    address = parse_address(text)
    if address is None:
        return text.strip()[:64]
    if isinstance(address, ipaddress.IPv6Address) and not address.is_loopback:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _is_trusted_proxy(text: str) -> bool:
    networks = _trusted_networks(get_settings().trusted_proxies)
    address = parse_address(text) if networks else None
    return address is not None and any(address in network for network in networks)


def is_trusted_proxy(text: str) -> bool:
    """Whether a peer is one of the proxies in ``NEXBOOKS_TRUSTED_PROXIES`` (none from the start)."""
    return _is_trusted_proxy(text)


_warned_unknown_proxy = False


def behind_unknown_proxy(request: Request) -> bool:
    """A request that came through a proxy nexbooks was not told to believe: all senders look alike then."""
    global _warned_unknown_proxy
    if not request.headers.get("x-forwarded-for") or _trusted_networks(get_settings().trusted_proxies):
        return False
    if not _warned_unknown_proxy:
        _warned_unknown_proxy = True
        logger.warning("Requests arrive with X-Forwarded-For, but NEXBOOKS_TRUSTED_PROXIES is not set: every sender "
                       "looks like the proxy. Set it to the proxy's address so the sign-in brake can tell them apart.")
    return True


def client_ip(request: Request) -> str:
    """The sender's address, for the brake. ``X-Forwarded-For`` counts only from a configured trusted proxy, and
    then its rightmost hop that is not itself a trusted proxy: everything left of it the sender wrote itself."""
    peer = request.client.host if request.client else "-"
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or not _is_trusted_proxy(peer):
        return normal_address(peer)
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return normal_address(hop)
    return normal_address(hops[0] if hops else peer)


#: What a session that must still set up the second factor may reach: itself, the way out, and the setup (a code
#: from an app or a passkey). Nothing else, checked here for every route there is and every route still to come.
SETUP_ONLY_PATHS = frozenset({
    "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/totp/begin", "/api/v1/auth/totp/confirm",
    "/api/v1/auth/passkeys", "/api/v1/auth/passkeys/begin",
})
#: What a session may reach between setting up the second factor and confirming the recovery codes are kept.
CODES_ONLY_PATHS = frozenset({
    "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/setup/done", "/api/v1/auth/setup/codes",
})
#: How long after a sign-in through the provider an account without a password may change its own second factor.
FRESH_MINUTES = 10


@dataclass(frozen=True)
class SessionInfo:
    """What a route may want to know about the session that asks (``request.state.session``)."""

    uid: str
    stage: str
    remember: bool
    created_at: datetime


def bearer_token(request: Request) -> str | None:
    """The token of an ``Authorization: Bearer`` header; None when the request carries none."""
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def wrong_credential(request: Request, code: str, text: str) -> HTTPException:
    """The 401 for a credential that is wrong or ended (``sign_in_required``, ``token_invalid``, ``key_invalid``),
    marked on the request so that the door before the sign-in counts it as a wrong guess (``middleware.SignInGate``).
    A 401 for a mistake of a program (a key at a route for devices, keys switched off, a web page's ``Origin``) is no
    guess and is not marked."""
    setattr(request.state, WRONG_CREDENTIAL, True)
    return error(code, text, 401)


def _device_account(request: Request, token: str) -> AccountRow:
    """The account a device token speaks for. A wrong token counts like a wrong password (the brake per sender; the same
    wrong token once, ``unknown_credential``); a right one needs the account to be neither blocked nor locked, and, when
    the operator requires a second factor, to have set it up: a device cannot do that, so it waits until a browser
    has."""
    if token.startswith(apikeys.KEY_PREFIX):
        # An API key where a device token belongs: a mistake of a program, not a guess at a secret. It does not feed
        # the brake (else one script with the wrong header would shut its own address out).
        raise error("token_wrong_kind", "This is an API key; this route wants a device token.", 401)
    with SessionLocal() as db:
        # The token is looked up before the brake is asked: a token that exists is never turned away because somebody
        # else on the same address guessed wrong (a household shares one address; the app hangs on its token, and the
        # door before the sign-in already lets an existing credential through, ``middleware.SignInGate``). Guessing a
        # token of 256 bits gains nothing from knowing that the answer differs; the brake still shuts out every
        # token that does not exist.
        found = devices.authenticate(db, token)
        if found is None:
            unknown_credential(request, token)
            raise wrong_credential(request, "token_invalid", "No valid device token.")
        device, account = found
        if totp.setup_required(db, account):
            raise error("second_factor_setup_required", "Set up your second factor first.", 403)
        request.state.session = SessionInfo(f"device:{device.uid}", STAGE_FULL, True, device.created_at)
        request.state.device = device.uid
        request.state.device_id = device.id
        request.state.device_name = device.name
        request.state.device_via = device.via_device_id
        db.expunge(account)
    logs.set_actor(account.name)
    return account


def require_account(
    request: Request, _scheme: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None
) -> AccountRow:
    """The signed-in account, detached from the database session (routes open their own).

    A device token (``Authorization: Bearer``) is judged alone, see the top of this file. A session right after the
    password of an account without a second factor (stage ``setup``), one that has just set it up and not yet
    confirmed its recovery codes (``codes``), and a full one of an account that must set one up since the operator
    asks for it, reach only their few paths. A ``setup`` session whose account got a second factor elsewhere
    meanwhile is over: whoever holds it knows the password at most."""
    token = bearer_token(request)
    if token is not None:
        return _device_account(request, token)
    with SessionLocal() as db:
        found = current_session(db, request.cookies.get(SESSION_COOKIE), client_ip(request))
        if found is None:
            raise wrong_credential(request, "sign_in_required", "Sign in first.")
        session, account = found
        # A session through a provider that checks the second factor is not sent to set one up (``totp``).
        account.second_factor_by_provider = bool(session.second_factor_by_provider)
        path = request.url.path
        if session.stage == STAGE_SETUP:
            if totp.has_second_factor(db, account):
                raise wrong_credential(request, "sign_in_required", "Sign in first.")
            if path not in SETUP_ONLY_PATHS:
                raise error("second_factor_setup_required", "Set up your second factor first.", 403)
        elif session.stage == STAGE_CODES:
            if path not in CODES_ONLY_PATHS:
                raise error("second_factor_setup_required", "Set up your second factor first.", 403)
        elif session.stage != STAGE_FULL:
            raise wrong_credential(request, "sign_in_required", "Sign in first.")
        elif path not in SETUP_ONLY_PATHS and totp.setup_required(db, account):
            raise error("second_factor_setup_required", "Set up your second factor first.", 403)
        request.state.session = SessionInfo(session.uid, session.stage, session.remember, session.created_at)
        db.expunge(account)
    logs.set_actor(account.name)
    return account


def _key_account(request: Request, token: str, scope: str = "read") -> AccountRow:
    """The account an API key speaks for, on a route that reads (``require_reader``).
    The same walls as ``/api/v1/me`` (``routers/v1.py``): no ``Origin`` (a program, not a web page), the operator has to
    have switched keys on, a wrong key counts against the sender, and a key has a rate. A key does what its scope says
    and nothing else."""
    if request.headers.get("origin"):
        raise error("origin_refused", "The API is for programs, not web pages.", 403)
    with SessionLocal() as db:
        if not apikeys.allowed(db):
            raise error("api_off", "The operator has not switched API keys on.", 401)
        # Looked up before the brake, as a device token is (``_device_account``): a key that exists is never turned away
        # because others on the same address guessed wrong or a revoked key keeps coming.
        found = apikeys.authenticate(db, token)
    if found is None:
        unknown_credential(request, token)
        raise wrong_credential(request, "key_invalid", "No valid API key.")
    if not apikeys.brake(found.key_id):
        exc = error("slow_down", "Too many requests with this key. Wait a minute.", 429)
        exc.headers = {"Retry-After": "60"}
        raise exc
    if found.scope != scope:
        raise error("key_scope", "This key is not for this.", 403)
    request.state.session = SessionInfo(f"key:{found.key_id}", STAGE_FULL, True, found.account.created_at)
    request.state.api_key = True
    request.state.api_key_id = found.key_id
    logs.set_actor(found.account.name)
    return found.account


def require_reader(
    request: Request, _scheme: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None
) -> AccountRow:
    """For routes that only read: the signed-in browser or a device (``require_account``), or a program with an API key
    (``nbk_``, scope ``read``). A route that changes something takes ``require_account``, which turns a key away."""
    token = bearer_token(request)
    if token is not None and token.startswith(apikeys.KEY_PREFIX):
        return _key_account(request, token)
    return require_account(request)


def require_device(
    request: Request, _scheme: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None
) -> AccountRow:
    """For the surface of the iPhone app (``/api/app/1``): a device token, nothing else. A browser's cookie is no way in
    there (the app is not a page), and an API key is not either (``_device_account`` turns it away). The device acts
    with the rights of its account, exactly as in the browser; which device asks is on ``request.state.device``."""
    token = bearer_token(request)
    if token is None:
        raise error("device_token_required", "This needs a device token.", 401)
    return _device_account(request, token)


def require_browser_account(
    request: Request, _scheme: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None
) -> AccountRow:
    """Like ``require_account``, but for what only a browser the person sits at may do: changing the way in
    (password, second factor, passkeys), making device tokens and keys, listing and revoking devices. A device token is
    refused here, so that a lost phone cannot pair itself again or take the account over. The one way a device token
    brings a device is the iPhone app confirming the code of a television (``routers/app1.py``): that device is
    connected through the phone, falls when the phone is revoked, and connects none itself
    (``services/device_request.py``)."""
    if bearer_token(request) is not None:
        raise error("browser_only", "This needs a signed-in browser, not a device token.", 403)
    return require_account(request)


def session_of(request: Request) -> SessionInfo:
    """The session of a route behind ``Account`` (``require_account`` put it there)."""
    return request.state.session  # type: ignore[no-any-return]


def require_operator(request: Request, account: Annotated[AccountRow, Depends(require_browser_account)]) -> AccountRow:
    """The operator, in a signed-in browser: the settings, the accounts, the backups and the log are never reached with
    a device token (``403 browser_only``), so that a lost phone of the operator is no way into the server."""
    if account.role != OPERATOR:
        raise error("operator_only", "Only the operator may do this.", 403)
    networks = _trusted_networks(get_settings().operator_networks)
    if networks:
        try:
            sender = ipaddress.ip_address(client_ip(request).split("/")[0])
        except ValueError:
            sender = None
        # An IPv6 sender stands as its /64 here: its network's first address is inside the operator's networks
        # exactly when the /64 is (they are not smaller than /64 in a home network).
        if sender is None or not any(sender in network for network in networks):
            raise error("operator_network", "The operator's settings are open only from the home network.", 403)
    return account


Account = Annotated[AccountRow, Depends(require_account)]
Reader = Annotated[AccountRow, Depends(require_reader)]
Device = Annotated[AccountRow, Depends(require_device)]
BrowserAccount = Annotated[AccountRow, Depends(require_browser_account)]
OperatorAccount = Annotated[AccountRow, Depends(require_operator)]


# --- The password once more, while signed in ------------------------------------------------------------------------
#
# Changing the password or linking the account to a provider asks for the password again, and a wrong answer counts
# the way it does at sign-in: otherwise a stolen cookie would be a place to guess without limit.


def _reauth_key(request: Request) -> str:
    return "reauth:" + client_ip(request)


def reauth_guard(request: Request, account: AccountRow) -> None:
    if accounts.is_locked(account):
        raise error("account_locked", "Too many failed attempts. Try again later.", 429)
    wait = brake.wait_seconds(_reauth_key(request))
    if wait:
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )


def reauth_failed(request: Request, db: Session, account: AccountRow) -> None:
    brake.failed(_reauth_key(request))
    accounts.note_failure(db, account)


def reauth_succeeded(request: Request, db: Session, account: AccountRow) -> None:
    brake.succeeded(_reauth_key(request))
    accounts.note_success(db, account)


def confirm_self(request: Request, db: Session, row: AccountRow, password: str) -> None:
    """The person once more, before a change to their own way in: the password, counted like a sign-in. An account
    without a password (it comes through the provider) shows itself by a sign-in of the last few minutes instead; a
    session right after the sign-in, still setting up its second factor, is that."""
    if row.sign_in == SIGN_IN_PASSWORD:
        reauth_guard(request, row)
        if not accounts.check_password(row, password):
            reauth_failed(request, db, row)
            raise error("wrong_password", "The current password is wrong.", 401)
        reauth_succeeded(request, db, row)
        return
    session = session_of(request)
    if session.stage == STAGE_FULL and clock.now() - session.created_at > timedelta(minutes=FRESH_MINUTES):
        raise error("sign_in_again", "Sign in again through the provider, then try once more.", 403)


# --- The brake per address --------------------------------------------------------------------------------------------
#
# Five failures from one address, of any kind that lets somebody in (a password, a code, a recovery code, a passkey, a
# setup code, an invitation), and the address rests a quarter of an hour. A sign-in that works does not reset it:
# whoever has an account would otherwise reset it between guesses at others.
#
# Behind a proxy nexbooks was not told about, every sender looks like the proxy: five failures of a stranger would keep
# the whole household out. There the count goes by the proxy and the last address it names (most likely the sender),
# and the proxy as a whole gets more room (``UNKNOWN_PROXY_FREE``): made-up headers from a sender without a proxy
# buy no more than that. "Ready for the internet?" warns while it is so.

#: Failures the whole of a proxy nexbooks does not know may cause before it rests.
UNKNOWN_PROXY_FREE = 30


def address_keys(request: Request) -> list[tuple[str, int]]:
    """The counts a failure of this request goes to, with the failures each lets pass."""
    if not behind_unknown_proxy(request):
        return [("addr:" + client_ip(request), brake.FREE)]
    peer = normal_address(request.client.host if request.client else "-")
    hops = [hop.strip() for hop in request.headers.get("x-forwarded-for", "").split(",") if hop.strip()]
    last = normal_address(hops[-1])[:64] if hops else "-"
    return [("addr:" + peer + "|" + last, brake.FREE), ("proxy:" + peer, UNKNOWN_PROXY_FREE)]


def open_sender(request: Request) -> str:
    """Who is counted for what anybody may ask of the server without a sign-in and that stays open for a while (the
    codes a television asks for). Not ``address_keys``: those are counts of failures, each bounded by the count of the
    proxy as a whole, and a made-up ``X-Forwarded-For`` buys no more than that. Here a made-up header would buy a whole
    limit for every value, so ``X-Forwarded-For`` counts only from a proxy named in ``NEXBOOKS_TRUSTED_PROXIES``; from
    any other peer the sender is the peer (behind a proxy not named, all of them are one: the log says so once)."""
    if _trusted_networks(get_settings().trusted_proxies):
        return "addr:" + client_ip(request)
    behind_unknown_proxy(request)  # only for the warning in the log
    return "addr:" + normal_address(request.client.host if request.client else "-")


def pair_keys(request: Request, kind: str = "pair") -> list[tuple[str, int]]:
    """The counts a wrong secret or code for connecting the app goes to: the same senders and the same limits as the
    sign-in brake, but counts of their own. A mistyped code must not shut the app's devices and the sign-in of a whole
    address out for a quarter of an hour (``address_keys`` would). ``kind`` tells the counts apart: the television that
    asks how its request stands (``pairtv``) has one of its own, so that its mistakes do not keep a person from typing
    a code in the browser (``pair``)."""
    return [(kind + key[key.index(":"):] if key.startswith("addr:") else kind + "-proxy" + key[key.index(":"):], free)
            for key, free in address_keys(request)]


def too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
        headers={"Retry-After": str(wait)},
    )


def address_guard(request: Request, *extra: str) -> None:
    """429 while the address (or one of ``extra`` keys) rests."""
    keys = [*address_keys(request), *((key, brake.FREE) for key in extra if key)]
    wait = max((brake.wait_seconds(key, free) for key, free in keys), default=0)
    if wait:
        raise too_many(wait)


def address_failed(request: Request, *extra: str) -> None:
    for key, free in [*address_keys(request), *((key, brake.FREE) for key in extra if key)]:
        brake.failed(key, free)


def unknown_credential(request: Request, token: str) -> None:
    """A device token or API key that nobody holds, at a route that looked it up first: 429 while the sender rests, else
    one failure, and the same credential again from the same sender only once (``Brake.first_sighting``). A revoked
    token that an app keeps sending with every request is one mistake, not five: else one forgotten iPad would shut the
    devices and the sign-in of a whole household out (behind a proxy nexbooks was not told about: of everybody). Every
    new guess is a new credential and counts, so the brake against guessing stays as it was."""
    address_guard(request)
    if brake.first_sighting([key for key, _free in address_keys(request)], token):
        address_failed(request)


def pair_guard(request: Request, *extra: str, kind: str = "pair") -> None:
    """429 while this sender (or one of the ``extra`` keys, e.g. the person who types codes in a browser) rests from
    guessing secrets and codes for connecting a device (``pair_keys``)."""
    keys = [*pair_keys(request, kind), *((key, brake.FREE) for key in extra if key)]
    wait = max((brake.wait_seconds(key, free) for key, free in keys), default=0)
    if wait:
        raise too_many(wait)


def pair_failed(request: Request, *extra: str, kind: str = "pair") -> None:
    for key, free in [*pair_keys(request, kind), *((key, brake.FREE) for key in extra if key)]:
        brake.failed(key, free)


def refuse_pairing(request: Request, refused: pairing.Refused, *extra: str, kind: str = "pair") -> NoReturn:
    """The answer to a claim, a poll or a typed code that ``services/pairing`` or ``services/device_request`` turned
    away: a guess counts against the sender (and the ``extra`` keys); a refusal that tells a number of seconds carries
    ``Retry-After``."""
    if refused.guess:
        pair_failed(request, *extra, kind=kind)
    exc = error(refused.code, refused.text, refused.status, **({"retry_after": refused.wait} if refused.wait else {}))
    if refused.wait:
        exc.headers = {"Retry-After": str(refused.wait)}
    raise exc


def confirm_operator(
    request: Request, db: Session, operator: AccountRow, password: str, *, settle: bool = True
) -> None:
    """The operator's password once more, before an act a stolen session must not be enough for: carrying a backup
    away, giving another account a password, taking its second factor, changing a role, deleting an account. An
    operator who signs in through the provider has no password here: for them the sign-in must be fresh (as in
    ``confirm_self``), else ``sign_in_again``.

    A right password counts as a success (the failures so far are forgotten) only when ``settle`` is set. An act that
    asks for a second factor on top passes ``settle=False`` and calls ``reauth_succeeded`` itself once the factor
    passed too: else a right password would wipe the count before each guess at the code."""
    row = db.get(AccountRow, operator.id)
    assert row is not None
    if row.sign_in != SIGN_IN_PASSWORD:
        session = session_of(request)
        if session.stage == STAGE_FULL and clock.now() - session.created_at > timedelta(minutes=FRESH_MINUTES):
            raise error("sign_in_again", "Sign in again through the provider, then try once more.", 403)
        return
    reauth_guard(request, row)
    if not accounts.check_password(row, password):
        reauth_failed(request, db, row)
        raise error("wrong_password", "The current password is wrong.", 401)
    if settle:
        reauth_succeeded(request, db, row)
