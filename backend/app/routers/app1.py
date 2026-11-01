"""The surface of the iPhone app: ``/api/app/1``.

**A promise, but only to our own app.** The app in the store and the servers people run are never updated at the same
moment. So the app does not speak to the interface of the browser, which changes when the web app does. As long as
``app/1`` stands in the address, nothing here breaks; if something must, ``/api/app/2`` is made beside it and older apps
keep working. The promise is kept by ``tests/test_app_promise.py`` and ``tests/app1_fields.json`` (every address named
in ``PROMISED``, and the fields of every answer, which may grow and never shrink).

**The way in** are the two addresses that need no token, because they are how the app gets one
(``services/pairing.py`` tells how a code is made and used). Both are for programs: a request that carries an
``Origin`` is a web page's and is turned away before it is looked at, and a wrong secret or code counts against the
sender (``deps.pair_failed``: a count of its own, so a typing mistake shuts neither the devices nor the sign-in out).

**Everything else wants a device token** (``deps.require_device``: no cookie, no API key) and is the same as in the
browser: the same services answer, so the rights are exactly the rights there (a book in a library the person is not a
member of is one that does not exist), and the progress, bookmarks and history are the person's own. **No answer names a
place on the disk**: no path, no folder, no file name (a file is sent as ``<id>.<format>``).

**The library** comes page by page with a marker (``services/app_changes.py``): the first time from nothing, then only
what changed since the marker, and what went or is no longer visible in ``removed``. A marker the server cannot answer
for is ``410 marker_too_old``: the app starts from nothing again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__, clock
from ..deps import DbSession, Device, open_sender, pair_guard, refuse_pairing, too_many
from ..errors import error
from ..models import Account, AppRequest, BookFile, Library, LibraryMember
from ..security import brake
from ..services import app_changes, chapter_groups, device_request, pairing
from ..services import history as history_service
from ..services import later as later_service
from ..services import progress as progress_service
from ..services import works as works_service
from ..services.works import MEDIA_TYPES, EditionView, WorkView
from . import works as works_router
from .history import FinishedIn, HistoryOut, history_out
from .reading import BookmarkIn, BookmarkOut, NoteIn, bookmark_out, progress_out

router = APIRouter(prefix="/api/app/1", tags=["app"])

#: Every address under ``/api/app/1``. A test compares this list with the routes there are.
PROMISED = (
    "/api/app/1/pairing/{pairing_id}/claim",
    "/api/app/1/pairing/claim",
    "/api/app/1/pairing/request",
    "/api/app/1/pairing/request/confirm",
    "/api/app/1/pairing/request/preview",
    "/api/app/1/pairing/request/{request_id}",
    "/api/app/1/pairing/request/{request_id}/confirm",
    "/api/app/1/me",
    "/api/app/1/library",
    "/api/app/1/works/{work_id}",
    "/api/app/1/works/{work_id}/cover",
    "/api/app/1/files/{file_id}/content",
    "/api/app/1/progress",
    "/api/app/1/editions/{edition_id}/progress",
    "/api/app/1/editions/{edition_id}/bookmarks",
    "/api/app/1/bookmarks/{bookmark_id}",
    "/api/app/1/editions/{edition_id}/finished",
    "/api/app/1/history",
    "/api/app/1/history/{entry_id}",
    "/api/app/1/later",
    "/api/app/1/later/{work_id}",
)

#: What this server can do for the app, so that an app newer than the server knows what it may ask for. A name is
#: never taken away while ``app/1`` stands.
FEATURES = ("pairing", "library", "works", "covers", "files", "progress", "bookmarks", "history", "pairing_request",
            "pairing_request_confirm", "pairing_request_preview", "later")


#: A name is judged by the service (``devices.clean_name``: 1 to 80 characters after the spaces are gathered, no control
#: characters), and a name it cannot keep is ``device_name_invalid`` however long it is. So the model sets no limit of
#: its own (the body of these two addresses is limited to a few KB before it is read): a limit here would answer with
#: ``invalid_input`` and the app could not tell the two apart.
_DEVICE_NAME = "How the device is named in the list of devices, e.g. \"Ada's iPhone\". 1 to 80 characters."


class ClaimBySecret(BaseModel):
    secret: str = Field(min_length=1, max_length=200, description="The secret of the QR code.")
    device_name: str = Field(default="", description=_DEVICE_NAME)


class ClaimByCode(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="The code a person typed, with or without the dash.")
    device_name: str = Field(default="", description=_DEVICE_NAME)


class ClaimedAccount(BaseModel):
    name: str


class ClaimedServer(BaseModel):
    version: str


class Claimed(BaseModel):
    #: The device token, exactly once. Send it as ``Authorization: Bearer``.
    token: str
    account: ClaimedAccount
    server: ClaimedServer


def _refuse(request: Request, refused: pairing.Refused) -> NoReturn:
    refuse_pairing(request, refused)


def _program_only(request: Request) -> None:
    if request.headers.get("origin"):
        raise error("origin_refused", "The API is for programs, not web pages.", 403)


def _answer(done: pairing.Claimed) -> JSONResponse:
    body = Claimed(token=done.token, account=ClaimedAccount(name=done.account_name),
                   server=ClaimedServer(version=__version__))
    # A token must not stay in any cache (the app's own rule for the whole interface says the same).
    return JSONResponse(status_code=200, content=body.model_dump(), headers={"Cache-Control": "no-store"})


def _header(text: str, kind: str = "string") -> dict[str, object]:
    return {"description": text, "schema": {"type": kind}}


#: ``Retry-After`` of every 429: the seconds to wait.
_RETRY = {"Retry-After": _header("Seconds to wait before the next try.", "integer")}
#: The answers that carry a token must not stay in any cache.
_NO_STORE = {"Cache-Control": _header("`no-store`: the answer carries a token.")}
#: The counts of the television that asks how its request stands (``deps.pair_keys``), apart from those of the typing.
TV = "pairtv"

_ERRORS: dict[int | str, dict[str, object]] = {
    200: {"headers": _NO_STORE},
    403: {"description": "`origin_refused`: the request has an `Origin` header (a web page's; the app sends none)."},
    404: {"description": "`pairing_unknown`: no such code, or a wrong secret (the same answer, so nobody can search)."},
    409: {"description": "`too_many_devices`: the account holds the most devices there may be; the code stays open."},
    410: {"description": "`pairing_expired` or `pairing_used`: told only to whoever shows the right secret or code."},
    422: {"description": "`pairing_code`: not a code at all (no attempt counted); `device_name_invalid`: the name "
                         "cannot be kept (1 to 80 characters, no control characters; the code stays open); "
                         "`invalid_input`: a field is missing or of the wrong kind."},
    429: {"description": "`too_many_attempts`: too many wrong secrets or codes from this address, wait for "
                         "`Retry-After` (a count of the pairing's own, it does not touch the sign-in or the devices); "
                         "`too_many_codes` (by code only): too many wrong codes were tried on this server by "
                         "anybody, scan the QR code instead or wait for `Retry-After`.", "headers": _RETRY},
}


@router.post("/pairing/{pairing_id}/claim", response_model=Claimed, responses=_ERRORS,
             summary="Fetch the device token with the secret of the QR code",
             description="Needs no token: this is how the app gets one. The token is in this answer and in no other. "
                         "A wrong secret and an unknown id answer the same 404 `pairing_unknown` and count against "
                         "the sender: five, then a quarter of an hour of rest (a count of the pairing's own); a "
                         "secret that fits is not turned away by that rest.")
def claim_with_secret(pairing_id: Annotated[str, Path(min_length=1, max_length=200)], body: ClaimBySecret,
                      request: Request, db: DbSession) -> JSONResponse:
    _program_only(request)
    try:
        done = pairing.claim_with_secret(db, pairing_id, body.secret, body.device_name)
    except pairing.Refused as refused:
        if refused.guess:
            # Only a secret that does not fit meets the brake: one that fits cannot be guessed (256 bits), so a person
            # who mistyped the code five times can still scan the QR code. A sender at rest hears 429, not 404.
            pair_guard(request)
        _refuse(request, refused)
    return _answer(done)


@router.post("/pairing/claim", response_model=Claimed, responses=_ERRORS,
             summary="Fetch the device token with the code a person typed",
             description="Needs no token. The code has eight letters and digits (`ABCD-2345`). A wrong code counts "
                         "against the sender (five, then a quarter of an hour of rest) and against all senders "
                         "together (30 in ten minutes, then fetching by code rests); the code has about forty bits, "
                         "which must not be tried out within the ten minutes it lives.")
def claim_with_code(body: ClaimByCode, request: Request, db: DbSession) -> JSONResponse:
    _program_only(request)
    pair_guard(request)
    try:
        done = pairing.claim_with_code(db, body.code, body.device_name)
    except pairing.Refused as refused:
        _refuse(request, refused)
    return _answer(done)


# --- A device without a camera asks for a code ------------------------------------------------------------------------


class DeviceRequestIn(BaseModel):
    device_name: str = Field(default="", description=_DEVICE_NAME + " Without one it is \"Apple TV\".")


class DeviceRequestMade(BaseModel):
    #: The id of the request; it stands in the address the device asks at and decides nothing alone.
    id: str
    #: Only the device keeps it. It is sent in the header ``X-Pairing-Secret`` with every question.
    secret: str
    #: To show large on the screen, as people read it: ``ABCD-2345``.
    code: str
    expires_at: datetime
    #: How many seconds to wait between two questions.
    poll_seconds: int


class DeviceRequestState(BaseModel):
    #: ``waiting`` (nobody confirmed yet), ``confirmed`` (the answer holds the token, once) or ``expired``.
    state: Literal["waiting", "confirmed", "expired"]
    token: str | None = None
    account: ClaimedAccount | None = None
    server: ClaimedServer | None = None


_REQUEST_ERRORS: dict[int | str, dict[str, object]] = {
    201: {"headers": _NO_STORE},
    403: {"description": "`origin_refused`: the request has an `Origin` header (a web page's; the app sends none)."},
    422: {"description": "`device_name_invalid`: the name cannot be kept (1 to 80 characters, no control characters); "
                         "`invalid_input`: a field is of the wrong kind."},
    429: {"description": "`too_many_requests`: the server holds as many codes that wait for a confirmation as may be; "
                         "wait for `Retry-After`. (A sender that holds its share does not meet this: its oldest code "
                         "runs out and the new one takes its place.)", "headers": _RETRY},
}
_POLL_ERRORS: dict[int | str, dict[str, object]] = {
    200: {"headers": _NO_STORE},
    403: {"description": "`origin_refused`: the request has an `Origin` header (a web page's; the app sends none)."},
    404: {"description": "`pairing_unknown`: no such request, or a wrong secret (the same answer, so nobody can "
                         "search)."},
    409: {"description": "`too_many_devices`: the account holds the most devices there may be; the request stays "
                         "confirmed until it runs out."},
    410: {"description": "`pairing_used`: the token was handed over already (told only to whoever shows the right "
                         "secret)."},
    422: {"description": "`invalid_input`: the header `X-Pairing-Secret` is missing."},
    429: {"description": "`too_many_attempts`: too many wrong secrets from this address, wait for `Retry-After` (the "
                         "count of the pairing's own, it does not touch the sign-in or the devices).",
          "headers": _RETRY},
}


@router.post("/pairing/request", response_model=DeviceRequestMade, status_code=201, responses=_REQUEST_ERRORS,
             summary="A device without a camera asks for a code of its own",
             description="Needs no token. The device shows the code; a signed-in person types it in the browser and "
                         "confirms, and the device then fetches its token with the secret (`GET "
                         "/pairing/request/{id}`). The code alone gives nothing. A request lives ten minutes. Each "
                         "sender may have ten waiting at once (the eleventh takes the place of the oldest), the server "
                         "as a whole 300. The sender is the address of the connection; `X-Forwarded-For` counts only "
                         "from a proxy the operator named.")
def make_request(body: DeviceRequestIn, request: Request, db: DbSession) -> JSONResponse:
    _program_only(request)
    made = device_request.create(db, body.device_name, device_request.Sender.of(open_sender(request)))
    answer = DeviceRequestMade(id=made.id, secret=made.secret, code=made.code, expires_at=made.expires_at,
                         poll_seconds=device_request.POLL_SECONDS)
    return JSONResponse(status_code=201, content=answer.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


class ConfirmRequestIn(BaseModel):
    id: str = Field(min_length=1, max_length=200, description="The `id` of the request the device asked for.")
    code: str = Field(min_length=1, max_length=64, description="Its `code`, with or without the dash, in any case.")


class RequestConfirmed(BaseModel):
    state: Literal["confirmed"]
    #: How the device that asked calls itself. It was typed by whoever made the request, so it proves nothing.
    device_name: str
    expires_at: datetime


class RequestPreview(BaseModel):
    #: ``waiting``: nobody confirmed it yet, the person may decide.
    state: Literal["waiting"]
    #: How the device named itself when it asked, as the server keeps it (and as it will stand in the list of devices).
    #: It was typed by whoever made the request: the app shows it next to what the device says over the home network.
    device_name: str
    #: When the device asked.
    requested_at: datetime
    expires_at: datetime


_APP_PAIR_ERRORS: dict[int | str, dict[str, object]] = {
    400: {"description": "`client_required`: a change without any token (with a device token it is none of the "
                         "browser's business)."},
    401: {"description": "`token_invalid`: the token is wrong or revoked, or the account is blocked; "
                         "`device_token_required`: no device token (a cookie is no way in here); `token_wrong_kind`: "
                         "an API key, not a device token; `sign_in_required`: nothing to sign in with at all."},
    403: {"description": "`second_factor_setup_required`: the operator asks for a second factor, and the account has "
                         "to set it up in a browser first; `device_chain`: this device was itself connected by another "
                         "device (it confirmed a code from the app) and connects none: use the browser."},
    404: {"description": "`pairing_unknown`: no such request, a code that does not fit it, or a request that is "
                         "another person's (the same answer for all, so nobody can search). It counts against the "
                         "sender, the account and the request."},
    410: {"description": "`pairing_expired`: the code ran out or the device let it go (it shows a new one); "
                         "`pairing_used`: somebody confirmed it already, or the device fetched its token."},
    422: {"description": "`pairing_code`: the text cannot be a code (eight letters and digits; counts for nothing); "
                         "`invalid_input`: a field is missing or of the wrong kind."},
    429: {"description": "`too_many_attempts`: too many wrong codes from this address, by this account or against this "
                         "request, wait for `Retry-After`; `too_many_codes`: too many wrong codes were typed on the "
                         "whole server. A right id and code are not turned away by what others tried, only by wrong "
                         "codes against this very request.", "headers": _RETRY},
}
_CONFIRM_ERRORS: dict[int | str, dict[str, object]] = {
    200: {"headers": _NO_STORE},
    **_APP_PAIR_ERRORS,
    409: {"description": "`too_many_devices`: the account holds the most devices there may be; the request stays as it "
                         "was."},
}
_PREVIEW_ERRORS: dict[int | str, dict[str, object]] = {
    200: {"headers": {"Cache-Control": _header("`no-store`.")}},
    **_APP_PAIR_ERRORS,
}
_WITHDRAW_ERRORS: dict[int | str, dict[str, object]] = {
    204: {"headers": {"Cache-Control": _header("`no-store`.")}},
    **{status: _APP_PAIR_ERRORS[status] for status in (400, 401)},
    403: {"description": "`second_factor_setup_required`: the operator asks for a second factor, and the account has "
                         "to set it up in a browser first."},
    404: {"description": "`pairing_unknown`: this device confirmed no such request (or it is long gone). Not counted."},
    410: {"description": "`pairing_used`: the device fetched its token already; it stands in the list of devices and "
                         "is removed there."},
    429: {"description": "`too_many_attempts`: a token that does not exist, tried too often from this address; wait "
                         "for `Retry-After`. Taking back is never counted as a guess.", "headers": _RETRY},
}


def _held(request_id: str) -> str:
    """The count of wrong codes against one request (five, then it rests, also for the right code). A hash of the id,
    so that the count is not a place where ids are kept."""
    return "pair-request:" + hashlib.sha256(request_id.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _app_pair(request: Request, account: Account, db: Session, body: ConfirmRequestIn,
              act: Callable[[int], AppRequest]) -> AppRequest:
    """What confirming and looking from the app share: a device connected through another one is turned away first
    (``device_chain``), then a request at rest; a wrong pair counts against the sender, the account, the server and
    **always** the request itself, also when the sender or the server already rest (else whoever rests his own counts
    first could try the code of a known id without end)."""
    device_id = int(request.state.device_id)
    device_request.may_confirm(db, device_id)
    typing = f"pair-account:{account.id}"
    # Five wrong codes against one request and it rests, also for the right one: whoever knows an id (it is not a
    # secret from the network of the device) must not be able to try out the code of that request without limit.
    held = _held(body.id)
    wait = brake.wait_seconds(held)
    if wait:
        raise too_many(wait)
    try:
        return act(device_id)
    except pairing.Refused as refused:
        if refused.guess:
            try:
                pair_guard(request, typing, held)  # a sender at rest hears 429, not 404
                full = device_request.count_wrong_code()
                if full is not None:
                    refuse_pairing(request, full)
            except HTTPException:
                brake.failed(held)  # a wrong pair counts for its request, whoever rests
                raise
        refuse_pairing(request, refused, typing, held)


@router.post("/pairing/request/confirm", response_model=RequestConfirmed, responses=_CONFIRM_ERRORS,
             summary="Confirms the request of a device that has no camera, for the account of this device",
             description=(
                 "Needs a device token (`nbd_`; an API key or a cookie is no way in). The device without a camera "
                 "(the Apple TV) asked for a code (`POST /pairing/request`) and hands `id` and `code` to this app; "
                 "sending both here ties that request to the account of the token, as typing the code in the browser "
                 "does. The device then fetches a token of its own (`GET /pairing/request/{id}`, with the secret "
                 "that only it knows) and stands in the list of devices by itself, **connected through this "
                 "device**: revoking this device revokes it too, and it cannot connect a device itself (403 "
                 "`device_chain`). **Both `id` and `code` must fit**; anything else is 404 `pairing_unknown`. Wrong "
                 "pairs count like wrong codes typed in the browser (per sender, per account, over the whole "
                 "server) and against the request; a right pair is not turned away by the first three."))
def confirm_request(body: ConfirmRequestIn, request: Request, account: Device, db: DbSession) -> JSONResponse:
    row = _app_pair(request, account, db, body, lambda device_id: device_request.confirm_from_app(
        db, account, body.id, body.code, str(request.state.device_name), device_id))
    answer = RequestConfirmed(state="confirmed", device_name=row.device_name, expires_at=row.expires_at)
    return JSONResponse(status_code=200, content=answer.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.post("/pairing/request/preview", response_model=RequestPreview, responses=_PREVIEW_ERRORS,
             summary="How the server knows the device of a request, before this app confirms it; changes nothing",
             description=(
                 "The same body, token and answers as `POST /pairing/request/confirm`, but nothing is decided: the "
                 "answer names the device as it named itself when it asked (`device_name`, as it would stand in the "
                 "list of devices) and when (`requested_at`). The app shows it before the person confirms, next to "
                 "what the device says over the home network. A wrong pair counts exactly as there."))
def preview_request(body: ConfirmRequestIn, request: Request, account: Device, db: DbSession) -> JSONResponse:
    row = _app_pair(request, account, db, body, lambda device_id: device_request.preview_from_app(
        db, account, device_id, body.id, body.code))
    answer = RequestPreview(state="waiting", device_name=row.device_name, requested_at=row.created_at,
                            expires_at=row.expires_at)
    return JSONResponse(status_code=200, content=answer.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.delete("/pairing/request/{request_id}/confirm", status_code=204, response_class=Response,
               responses=_WITHDRAW_ERRORS,
               summary="Takes back what this app confirmed, as long as the device has not fetched its token",
               description=(
                   "Needs the device token that confirmed the request. The request runs out at once and the device "
                   "hears `expired` (it shows a new code). Again and again the answer is 204. A request this device "
                   "did not confirm is 404 `pairing_unknown` (not counted); one the device has fetched is 410 "
                   "`pairing_used`: the device stands in the list of devices then, and is removed there."))
def withdraw_request(request_id: Annotated[str, Path(min_length=1, max_length=200)], request: Request,
                     account: Device, db: DbSession) -> Response:
    try:
        device_request.withdraw_from_app(db, account, int(request.state.device_id), request_id)
    except pairing.Refused as refused:
        refuse_pairing(request, refused)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/pairing/request/{request_id}", response_model=DeviceRequestState, response_model_exclude_none=True,
            responses=_POLL_ERRORS, summary="How a request for a code stands; once confirmed, it hands over the token",
            description="Needs no token; the proof is the secret in the header `X-Pairing-Secret`. `waiting` until a "
                        "person confirmed the code in the browser, then `confirmed` **once** with `token`, `account` "
                        "and `server` as the other ways to connect give them, and from then on `pairing_used`. A wrong "
                        "secret and an unknown id answer the same 404 and count against the sender: five, then a "
                        "quarter of an hour of rest (a count of the television's own, it does not touch the typing "
                        "of codes in the browser); a secret that fits is not turned away by that rest.")
def poll_request(request_id: Annotated[str, Path(min_length=1, max_length=200)],
                 secret: Annotated[str, Header(alias="X-Pairing-Secret", min_length=1, max_length=200)],
                 request: Request, db: DbSession) -> JSONResponse:
    _program_only(request)
    try:
        found = device_request.poll(db, request_id, secret)
    except pairing.Refused as refused:
        if refused.guess:
            pair_guard(request, kind=TV)  # a sender at rest hears 429, not 404; a right secret never meets this
        refuse_pairing(request, refused, kind=TV)
    if found.claimed is None:
        content: dict[str, object] = DeviceRequestState(
            state=found.state).model_dump(exclude_none=True)  # type: ignore[arg-type]
    else:
        content = DeviceRequestState(state="confirmed", token=found.claimed.token,
                               account=ClaimedAccount(name=found.claimed.account_name),
                               server=ClaimedServer(version=__version__)).model_dump(exclude_none=True)
    return JSONResponse(status_code=200, content=content, headers={"Cache-Control": "no-store"})


_CANCEL_ERRORS: dict[int | str, dict[str, object]] = {
    204: {"headers": {"Cache-Control": _header("`no-store`.")}},
    403: {"description": "`origin_refused`: the request has an `Origin` header (a web page's; the app sends none)."},
    422: {"description": "`invalid_input`: the header `X-Pairing-Secret` is missing."},
}


@router.delete("/pairing/request/{request_id}", status_code=204, response_class=Response, responses=_CANCEL_ERRORS,
               summary="The device lets its request go",
               description="Needs no token; the proof is the secret in the header `X-Pairing-Secret`. The request "
                           "runs out at once and gives its place back (a device whose screen was left should call "
                           "this). It works again and again for the right secret, and leaves a request that was "
                           "fetched as it is. **The answer is always 204**, also for a wrong secret or an unknown id "
                           "(then nothing happens), and it never counts against the sender: a device that "
                           "lets go of what the server forgot long ago has made no mistake, and the secret (256 bits) "
                           "cannot be guessed by trying this.")
def cancel_request(request_id: Annotated[str, Path(min_length=1, max_length=200)],
                   secret: Annotated[str, Header(alias="X-Pairing-Secret", min_length=1, max_length=200)],
                   request: Request, db: DbSession) -> Response:
    _program_only(request)
    device_request.cancel(db, request_id, secret)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


# --- Who asks -----------------------------------------------------------------------------------------------------


class AppAccount(BaseModel):
    id: int
    name: str
    #: The language of the interface the person chose (``de``, ``en`` ...); empty: none, the app follows the phone.
    language: str


class AppLibrary(BaseModel):
    id: int
    name: str
    #: What the library takes: ``ebook``, ``audio`` or ``both``.
    kind: str
    #: The person's right in it: ``read``, ``acquire`` or ``manage``.
    right: str


class AppServer(BaseModel):
    version: str


class AppThisDevice(BaseModel):
    #: How this device stands in the list of devices.
    name: str
    #: Whether it may confirm the code of another device (``POST /pairing/request/confirm``): not when it was itself
    #: connected that way (then the answer there is 403 ``device_chain``, and the browser is the way).
    may_confirm: bool


class AppMe(BaseModel):
    account: AppAccount
    #: The libraries the person is a member of, by name.
    libraries: list[AppLibrary]
    server: AppServer
    #: A random name of this server, the same for ever: the app tells two servers apart by it.
    installation_id: str
    #: What this server can do for the app (``FEATURES``); an app newer than the server asks only for what is named.
    features: list[str]
    #: The device whose token asks.
    device: AppThisDevice


_TOKEN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "`token_invalid`: the token is wrong or revoked, or the account is blocked; "
                         "`device_token_required`: no device token (a cookie is no way in here); `token_wrong_kind`: "
                         "an API key, not a device token; `sign_in_required`: nothing to sign in with at all."},
    403: {"description": "`second_factor_setup_required`: the operator asks for a second factor, and the account has "
                         "to set it up in a browser first."},
    429: {"description": "`too_many_attempts`: a token that does not exist, tried too often from this address; wait "
                         "for `Retry-After`. A token that exists is never turned away by it, and the same wrong token "
                         "again counts once: an app with a revoked token keeps hearing 401, not 429.",
          "headers": _RETRY},
}
_CHANGES: dict[int | str, dict[str, object]] = {
    400: {"description": "`client_required`: a change without any token (with a device token it is none of the "
                         "browser's business)."},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    **_TOKEN_ERRORS,
    404: {"description": "`not_found`: there is no such thing, or it lies only in libraries the person is not in "
                         "(the same answer)."},
}
#: For what changes something: the same, and what a change without a token hears.
_CHANGING = {**_NOT_FOUND, **_CHANGES}


@router.get("/me", response_model=AppMe, responses=_TOKEN_ERRORS, summary="Who the device speaks for, and the server")
def me(request: Request, account: Device, db: DbSession) -> AppMe:
    rows = db.execute(select(Library.id, Library.name, Library.kind, LibraryMember.right)
                      .join(LibraryMember, LibraryMember.library_id == Library.id)
                      .where(LibraryMember.user_id == account.id).order_by(Library.name, Library.id)).all()
    return AppMe(account=AppAccount(id=account.id, name=account.name, language=account.language or ""),
                 libraries=[AppLibrary(id=ident, name=name, kind=kind, right=right)
                            for ident, name, kind, right in rows],
                 server=AppServer(version=__version__), installation_id=app_changes.installation_id(db),
                 features=list(FEATURES),
                 device=AppThisDevice(name=str(request.state.device_name),
                                      may_confirm=request.state.device_via is None))


# --- The library ----------------------------------------------------------------------------------------------------


class AppPerson(BaseModel):
    id: int
    name: str


class AppSeries(BaseModel):
    id: int
    name: str
    #: The place in the series; 2.5 is allowed. Null: not known.
    pos: float | None


class AppEdition(BaseModel):
    id: int
    library_id: int
    kind: Literal["ebook", "audio"]
    #: Of a recording: ``unabridged``, ``abridged`` or ``dramatized``; empty for an e-book.
    variant: str
    language: str
    #: Of a recording: seconds, all parts together; 0 for an e-book.
    duration: int
    #: Of an e-book: pages (counted for a PDF, estimated for the rest); 0 for a recording.
    pages: int
    #: The formats of its files, best for reading first: ``["epub", "azw3"]``, ``["m4b"]``.
    formats: list[str]
    #: Bytes of all its files.
    size: int
    narrators: list[str]


class AppWork(BaseModel):
    """A work as the person sees it: only the editions of their libraries, and only what those say."""

    id: int
    title: str
    authors: list[AppPerson]
    series: AppSeries | None
    genre: str
    year: int | None
    #: The libraries its editions lie in (of the person's).
    library_ids: list[int]
    #: The main colour of the cover, ``#rrggbb``.
    color: str
    #: Changes when the cover does (the ``ETag`` of ``/works/{id}/cover`` without its quotes); null: no cover.
    cover_etag: str | None
    editions: list[AppEdition]


class AppLibraryPage(BaseModel):
    items: list[AppWork]
    #: Works the app may have had and must forget: gone, or not in a library of the person any more.
    removed: list[int]
    #: Ask with ``after=<marker>`` next time. Below ``latest``: there is more now, ask again at once.
    marker: int
    latest: int


def _cover_etag(view: WorkView) -> str | None:
    return view.cover[:20] if view.cover else None


def _edition(view: EditionView, sizes: dict[int, int]) -> AppEdition:
    edition = view.edition
    return AppEdition(id=edition.id, library_id=edition.library_id, kind=edition.kind,  # type: ignore[arg-type]
                      variant=edition.variant, language=edition.language, duration=edition.duration,
                      pages=edition.pages, formats=view.formats, size=sizes.get(edition.id, 0),
                      narrators=view.narrators)


def _work(view: WorkView, sizes: dict[int, int]) -> AppWork:
    return AppWork(
        id=view.work.id, title=view.title, authors=[AppPerson(id=ident, name=name) for ident, name in view.authors],
        series=AppSeries(id=view.series_id, name=view.series_name, pos=view.series_pos) if view.series_id else None,
        genre=view.genre, year=view.year, library_ids=sorted({v.edition.library_id for v in view.editions}),
        color=view.color, cover_etag=_cover_etag(view), editions=[_edition(v, sizes) for v in view.editions])


@router.get("/library", response_model=AppLibraryPage,
            responses={**_TOKEN_ERRORS, 410: {"description": "`marker_too_old`: start again without `after`."}},
            summary="The works the person sees, from nothing or what changed after a marker, a page at a time",
            description="Without `after` (or with 0) the whole library comes, oldest change first; then the app asks "
                        "with the `marker` of the answer and gets only what changed after it, and in `removed` what "
                        "it must forget. While `marker` is below `latest`, there is more: ask again at once. Starting "
                        "from nothing, `removed` is empty: the app keeps the works of all pages and forgets the rest "
                        "when `marker` reaches `latest`. A marker the server cannot answer for (older than the "
                        "removals it keeps for 90 days, or one it never gave out) is 410 `marker_too_old`: start "
                        "again.")
def library(request: Request, account: Device, db: DbSession,
            after: Annotated[int, Query(ge=0, le=2**62, description="The marker of the last answer")] = 0,
            limit: Annotated[int, Query(ge=1, le=app_changes.MAX_LIMIT,
                                        description="Works and removals at most")] = app_changes.MAX_LIMIT,
            ) -> AppLibraryPage:
    found = app_changes.page(db, account.id, int(request.state.device_id), after, limit)
    return AppLibraryPage(items=[_work(view, found.sizes) for view in found.views], removed=found.removed,
                          marker=found.marker, latest=found.latest)


# --- One work -------------------------------------------------------------------------------------------------------


class AppFile(BaseModel):
    id: int
    #: The number of the part of a recording (1 for the rest).
    part: int
    #: Seconds; 0 for an e-book.
    duration: float
    size: int
    #: Of the whole file, made when nexbooks read it (the scan, a filed download or upload). Empty while the file on the
    #: disk is not the one that was read (changed since, by size or time): the next scan reads it again.
    sha256: str
    format: str
    mime: str


class AppChapter(BaseModel):
    number: int
    title: str
    #: Seconds from the beginning of all the parts.
    start: float


class AppEditionDetail(AppEdition):
    title: str
    subtitle: str
    isbn: str
    publisher: str
    published: str
    translators: list[str]
    #: In the order to play or read: the parts of a recording by number.
    files: list[AppFile]
    #: Of a recording: its chapters. Empty for an e-book (the app reads the table of contents out of the book).
    chapters: list[AppChapter]


class AppWorkDetail(AppWork):
    description: str
    original_title: str
    original_language: str
    editions: list[AppEditionDetail]  # type: ignore[assignment]


def _checksum(row: BookFile, folders: dict[int, str]) -> str:
    """The checksum of a file, but only while the file on the disk is the one it was made of: a file that changed since
    the scan read it would be checked against a value of the old one. It is not worked out here: that reads the whole
    file (a recording of hundreds of MB) inside a request, which anybody with a device could ask for again and again.
    The scan sees the change by size and time and makes it again; until then the app is told nothing (empty)."""
    path = works_service.book_path(folders.get(row.library_id, ""), row.path)
    try:
        info = path.stat() if path is not None else None
    except OSError:
        info = None
    if info is None or info.st_size != row.size or info.st_mtime_ns != row.mtime_ns:
        return ""
    return row.sha256


@router.get("/works/{work_id}", response_model=AppWorkDetail, responses=_NOT_FOUND,
            summary="One work with its editions, files and chapters")
def work(work_id: Annotated[int, Path(ge=1, le=2**62)], account: Device, db: DbSession) -> AppWorkDetail:
    view = works_service.get_work(db, account.id, work_id)
    editions = [item.edition.id for item in view.editions]
    chapters = works_service.chapters_of(db, editions)
    sizes = {item.edition.id: sum(f.size for f in item.files) for item in view.editions}
    folders = dict(db.execute(select(Library.id, Library.path).where(
        Library.id.in_({f.library_id for item in view.editions for f in item.files}))).all())
    brief = _work(view, sizes)
    details = []
    for item in view.editions:
        edition = item.edition
        details.append(AppEditionDetail(
            **_edition(item, sizes).model_dump(), title=edition.title, subtitle=edition.subtitle, isbn=edition.isbn,
            publisher=edition.publisher, published=edition.published, translators=item.translators,
            files=[AppFile(id=f.id, part=f.part, duration=round(f.duration_ms / 1000, 3), size=f.size,
                           sha256=_checksum(f, folders), format=f.format,
                           mime=MEDIA_TYPES.get(f.format, "application/octet-stream"))
                   for f in item.files],
            chapters=[AppChapter(number=c.number, title=chapter_groups.label(
                c.title, c.number, edition.language or works_router.part_language(view, account)),
                                 start=c.start_ms / 1000)
                      for c in chapters.get(edition.id, [])] if edition.kind == "audio" else []))
    return AppWorkDetail(**brief.model_dump(exclude={"editions"}), description=view.description,
                         original_title=view.original_title, original_language=view.original_language,
                         editions=details)


#: The headers of a request the app may send, named so that the description holds them (the answer reads them itself,
#: ``works.cover_response`` and ``works.content_response``).
_IF_NONE_MATCH = Annotated[str | None, Header(alias="If-None-Match", description="The `ETag` the app holds: 304 while "
                                                                                   "it is the same")]
_RANGE = Annotated[str | None, Header(alias="Range", description="`bytes=<first>-<last>`, `bytes=<first>-` or "
                                                                  "`bytes=-<last bytes>`")]
_IF_RANGE = Annotated[str | None, Header(alias="If-Range", description="The `ETag` a download began with: the rest "
                                                                        "only while the file is the same, else all")]


@router.get("/works/{work_id}/cover", response_model=None, responses={
    **_NOT_FOUND, 200: {"content": {"image/webp": {}}, "headers": {
        "ETag": _header("The `cover_etag` in quotes."),
        "Cache-Control": _header("`immutable` for a year while `v` is the `cover_etag`, else `no-cache`.")}},
    304: {"description": "Not changed (`If-None-Match`)"}},
    summary="The cover of a work, as a WebP picture")
def cover(work_id: Annotated[int, Path(ge=1, le=2**62)], request: Request, account: Device, db: DbSession,
          v: Annotated[str | None, Query(max_length=40, description="The `cover_etag`: the answer may be kept for "
                                                                      "ever while it is the one asked for")] = None,
          _if_none_match: _IF_NONE_MATCH = None) -> Response:
    return works_router.cover_response(db, account.id, work_id, request, v)


@router.get("/files/{file_id}/content", response_model=None, responses={
    **_NOT_FOUND, 200: {"content": {"application/octet-stream": {}}, "headers": {
        "ETag": _header("Changes when the file on the disk does; for `If-Range` and `If-None-Match`."),
        "Accept-Ranges": _header("`bytes`."),
        "Content-Length": _header("The bytes of the whole file.", "integer"),
        "Content-Disposition": _header("`attachment; filename=\"<id>.<format>\"`, never the name on the disk."),
        "Last-Modified": _header("When the file on the disk last changed.")}},
    206: {"description": "A part of the file (`Range`; with `If-Range` only while the file is the same)", "headers": {
        "Content-Range": _header("`bytes <first>-<last>/<size>`."),
        "ETag": _header("As in the answer 200.")}},
    304: {"description": "Not changed (`If-None-Match`)"},
    405: {"description": "`HEAD` is not answered: ask with `Range: bytes=0-0` for the size."},
    416: {"description": "The `Range` lies past the end of the file."},
    429: {"description": "`too_many_streams`: too many files open at once for the person (12 by default), "
                         "`Retry-After` says when to try again (2 seconds); `stream_too_often`: more than 600 requests "
                         "for files in a minute; `too_many_attempts`: a token that does not exist, tried too often "
                         "from this address.", "headers": _RETRY}},
    summary="The bytes of one file, for reading, playing and keeping on the device")
def content(file_id: Annotated[int, Path(ge=1, le=2**62)], request: Request, account: Device,
            db: DbSession, _range: _RANGE = None, _if_range: _IF_RANGE = None,
            _if_none_match: _IF_NONE_MATCH = None) -> Response:
    """With ``Range`` a part of the file. A download that broke off goes on with ``Range`` and ``If-Range`` set to the
    ``ETag`` it began with: while the file is the same the rest comes (206), a file that changed meanwhile comes whole
    (200). The name in ``Content-Disposition`` is the number and the format, never the name on the disk."""
    return works_router.content_response(db, account.id, file_id, request, name_by_id=True)


# --- Where the person is ---------------------------------------------------------------------------------------------


class AppProgress(BaseModel):
    edition_id: int
    work_id: int
    #: A CFI for an EPUB, MOBI or AZW3, ``page:12`` for a PDF, ``time:8040.5`` for a recording.
    locator: str
    #: How far in the whole book, 0 to 100.
    percent: float
    #: Of a recording: seconds from the beginning of all its parts. Null else.
    position: float | None
    chapter_title: str
    #: The name of the device that reported it ("Ada's iPad", "Firefox on Linux").
    device: str
    #: The device that asks reported it.
    this_device: bool
    #: When the reader was at this place, and when the server heard of it.
    moved_at: datetime
    received_at: datetime
    #: How many seconds ago the reader was at this place, by the server's clock now: compare ages, never times of day.
    age: float


class AppProgressList(BaseModel):
    items: list[AppProgress]
    #: Ask with ``since=<until>`` next time. Places that arrived up to a minute before ``since`` come again.
    until: datetime


class AppProgressIn(BaseModel):
    locator: str = Field(min_length=1, max_length=progress_service.MAX_LOCATOR)
    #: How far in the whole book, 0 to 100.
    percent: float = Field(ge=0, le=100)
    #: How many seconds ago the reader was at this place (at most 7 days count; older counts as 7 days).
    age: float = Field(default=0, ge=0, le=60 * 60 * 24 * 365)
    #: A name for people; empty: the name the device was paired with.
    device: str = Field(default="", max_length=80)
    chapter: int = Field(default=0, ge=0, le=1_000_000)
    chapter_count: int = Field(default=0, ge=0, le=1_000_000)
    chapter_title: str = Field(default="", max_length=300)


class AppProgressSaved(BaseModel):
    #: Whether this report is the one that stands. False: another device was there later (``current`` says where).
    kept: bool
    current: AppProgress


def _progress(row: object, work_id: int, device_uid: str) -> AppProgress:
    out = progress_out(row, work_id)  # type: ignore[arg-type]
    return AppProgress(edition_id=out.edition_id, work_id=out.work_id, locator=out.locator,
                       percent=round(out.fraction * 100, 4), position=out.position, chapter_title=out.chapter_title,
                       device=out.device, this_device=bool(device_uid) and out.device_key == device_uid,
                       moved_at=out.moved_at, received_at=out.received_at, age=out.age)


def _device_uid(request: Request) -> str:
    return str(getattr(request.state, "device", "") or "")


@router.get("/progress", response_model=AppProgressList, responses=_TOKEN_ERRORS,
            summary="The person's places in their books, all or those that arrived since a moment")
def list_progress(request: Request, account: Device, db: DbSession,
                  since: Annotated[datetime | None, Query(description="The `until` of the last answer")] = None,
                  ) -> AppProgressList:
    until = clock.now()
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    rows = progress_service.changed_since(db, account.id, since)
    uid = _device_uid(request)
    return AppProgressList(items=[_progress(row, work_id, uid) for row, work_id in rows], until=until)


@router.put("/editions/{edition_id}/progress", response_model=AppProgressSaved,
            responses={**_CHANGING, 422: {"description": "`bad_place`: the place does not fit the edition."}},
            summary="Reports where the reader is; the newest report of all devices stays")
def save_progress(edition_id: Annotated[int, Path(ge=1, le=2**62)], payload: AppProgressIn, request: Request,
                  account: Device, db: DbSession) -> AppProgressSaved:
    """The same rule as in the browser: the device says how long ago the reader was at the place (``age``), the server
    counts back from the moment the report arrived, and the newest place stays. ``kept`` false: another device was
    later, ``current`` says where. A report for an edition that went into another one counts for that one."""
    uid = _device_uid(request)
    # Without control and direction characters (``progress.device_label``); a name with nothing left, or none, is the
    # name the device was paired with.
    name = progress_service.device_label(payload.device) or str(request.state.device_name)
    seconds = progress_service.seconds_of(payload.locator)
    unit = "time" if seconds is not None else ("page" if payload.locator.startswith("page:") else "chapter")
    kept, row = progress_service.save(db, account.id, edition_id, progress_service.Report(
        locator=payload.locator, fraction=payload.percent / 100, unit=unit, chapter=payload.chapter,
        chapter_count=payload.chapter_count, chapter_title=payload.chapter_title, device_key=uid[:40],
        device=name, age=payload.age))
    edition = progress_service.visible_edition(db, account.id, row.edition_id)
    return AppProgressSaved(kept=kept, current=_progress(row, edition.work_id, uid))


# --- Bookmarks and the history: as in the browser -------------------------------------------------------------------


@router.get("/editions/{edition_id}/bookmarks", response_model=list[BookmarkOut], responses=_NOT_FOUND,
            summary="The person's bookmarks in an edition, in the order of the book")
def list_bookmarks(edition_id: Annotated[int, Path(ge=1, le=2**62)], account: Device,
                   db: DbSession) -> list[BookmarkOut]:
    return [bookmark_out(row) for row in progress_service.bookmarks(db, account.id, edition_id)]


@router.post("/editions/{edition_id}/bookmarks", response_model=BookmarkOut, status_code=201,
             responses={**_CHANGING, 200: {"description": "The bookmark at this place was there already."},
                        409: {"description": "`bookmark_limit`: too many bookmarks in this book."}},
             summary="Sets a bookmark; the same place twice gives the one bookmark")
def add_bookmark(edition_id: Annotated[int, Path(ge=1, le=2**62)], payload: BookmarkIn, response: Response,
                 account: Device, db: DbSession) -> BookmarkOut:
    row, made = progress_service.add_bookmark(db, account.id, edition_id, payload.locator, payload.fraction,
                                              payload.chapter_title, payload.note.strip())
    if not made:
        response.status_code = 200
    return bookmark_out(row)


@router.patch("/bookmarks/{bookmark_id}", response_model=BookmarkOut, responses=_CHANGING,
              summary="Changes the note of one of the person's own bookmarks")
def change_bookmark(bookmark_id: Annotated[int, Path(ge=1, le=2**62)], payload: NoteIn, account: Device,
                    db: DbSession) -> BookmarkOut:
    return bookmark_out(progress_service.change_note(db, account.id, bookmark_id, payload.note.strip()))


@router.delete("/bookmarks/{bookmark_id}", status_code=204, responses=_CHANGING,
               summary="Removes one of the person's own bookmarks")
def remove_bookmark(bookmark_id: Annotated[int, Path(ge=1, le=2**62)], account: Device, db: DbSession) -> Response:
    progress_service.remove_bookmark(db, account.id, bookmark_id)
    return Response(status_code=204)


@router.post("/editions/{edition_id}/finished", response_model=HistoryOut, status_code=201,
             responses={**_CHANGING, 200: {"description": "The edition was entered on that day already."}},
             summary="Enters the edition as finished (read or heard); the same day twice gives the one entry")
def finished(edition_id: Annotated[int, Path(ge=1, le=2**62)], payload: FinishedIn, response: Response,
             account: Device, db: DbSession) -> HistoryOut:
    row, made = history_service.add(db, account.id, edition_id, payload.finished_on)
    if not made:
        response.status_code = 200
    return history_out(row)


@router.get("/history", response_model=list[HistoryOut], responses={
    **_TOKEN_ERRORS, 200: {"headers": {"X-Total-Count": _header("How many entries there are in all.", "integer")}}},
            summary="The books the person finished, the newest first, a page at a time")
def list_history(response: Response, account: Device, db: DbSession,
                 work: Annotated[int | None, Query(ge=1, le=2**31 - 1, description="Only this book")] = None,
                 limit: Annotated[int, Query(ge=1, le=history_service.LIST_LIMIT)] = history_service.LIST_LIMIT,
                 offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0) -> list[HistoryOut]:
    """The header ``X-Total-Count`` says how many entries there are in all."""
    rows, total = history_service.listing(db, account.id, work, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [history_out(row) for row in rows]


@router.delete("/history/{entry_id}", status_code=204, responses=_CHANGING,
               summary="Takes one of the person's own entries away (the book counts as not finished on that day)")
def remove_history(entry_id: Annotated[int, Path(ge=1, le=2**62)], account: Device, db: DbSession) -> Response:
    history_service.remove(db, account.id, entry_id)
    return Response(status_code=204)


# --- The list "for later": books on the person's own shelves ----------------------------------------------------------


class AppLaterEntry(BaseModel):
    work_id: int
    added_at: datetime


class AppLaterList(BaseModel):
    items: list[AppLaterEntry]


@router.get("/later", response_model=AppLaterList, responses=_TOKEN_ERRORS,
            summary="The books on the person's list for later that they see, the newest first")
def list_later(account: Device, db: DbSession) -> AppLaterList:
    """An entry for a work the person does not see at the moment stays and comes back with the work. A book that is
    finished, on whichever device, leaves the list by itself."""
    return AppLaterList(items=[AppLaterEntry(work_id=row.work_id, added_at=row.added_at)
                               for row in later_service.listing(db, account.id)])


@router.put("/later/{work_id}", status_code=204, responses={
    **_CHANGING, 409: {"description": "`later_full`: the list holds as many books as it may (500)."}},
            summary="Puts a book on the person's list for later; a book that is on it stays as it is")
def add_later(work_id: Annotated[int, Path(ge=1, le=2**62)], account: Device, db: DbSession) -> Response:
    later_service.add(db, account.id, work_id)
    return Response(status_code=204)


@router.delete("/later/{work_id}", status_code=204, responses={**_TOKEN_ERRORS, **_CHANGES},
               summary="Takes a book off the person's list for later; a book that is not on it is no error")
def remove_later(work_id: Annotated[int, Path(ge=1, le=2**62)], account: Device, db: DbSession) -> Response:
    later_service.remove(db, account.id, work_id)
    return Response(status_code=204)

