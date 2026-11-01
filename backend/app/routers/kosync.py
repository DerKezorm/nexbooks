"""The progress sync of KOReader under ``/kosync``: the few routes the plugin of KOReader calls on a sync server (the
protocol of the sync server KOReader's makers run), so that its address can be entered in the plugin. KOReader reports
where a person is in an e-book and asks where else they were; ``services/kosync.py`` says how that meets the progress
nexbooks keeps.

**This is not a way for a browser.** Nothing here reads a cookie, and so a page on another site cannot use the session
of anybody against it. KOReader signs in with two headers of its own: ``x-auth-user``, the name of the account, and
``x-auth-key``, the MD5 of the secret of a connection of the kind ``kosync`` that the person made under My account,
Devices and apps (``services/ereaders.py``). The password of the account is no way in. A wrong key is ``401``; wrong
keys count against the sender like wrong passwords (a count of their own), and a sender that has failed too often rests
(``429``).

**Answers as the plugin reads them.** An error is ``{"code": number, "message": text}`` with the status of the official
server, because the plugin shows ``message`` to the person. Accounts are not made here (``402``, and the message says
where they are made). A switched-off server is ``401`` with its reason as the message, not ``403``: that is the one
status the plugin shows a message for. The routes are not part of the description of the interface
(``/api/v1/openapi.json``); ``docs/api.md`` describes them.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..deps import DbSession, pair_failed, pair_guard, pair_keys
from ..models import Account as AccountRow
from ..security import brake
from ..services import brakes, ereaders, kosync, logs

logger = logging.getLogger("nexbooks.kosync")

router = APIRouter(prefix="/kosync", tags=["kosync"], include_in_schema=False)

#: The name of a count of wrong keys (``deps.pair_keys``).
KIND = "kosync"
#: The numbers of the official server's errors, which a plugin or a person reading a log may know.
UNAUTHORIZED, INVALID, DOCUMENT_MISSING, NOT_SERVABLE, REGISTRATION_OFF = 2001, 2003, 2004, 2007, 2005
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
#: Longest text taken for a name or an identifier of a device.
TEXT_MAX = 200
#: Longest place of KOReader taken (``services/kosync`` keeps a place only up to its own limit).
POINTER_MAX = 2000


class Refusal(Exception):
    """An answer in the shape KOReader reads: the status, a number and a text."""

    def __init__(self, status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def refused(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Refusal)
    return _json(exc.status, {"code": exc.code, "message": exc.message})


def _json(status: int, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers=HEADERS)


def require_sync(request: Request, db: DbSession) -> AccountRow:
    """The account behind the connection, or the answer that turns the app away. Looked up before the count of wrong
    keys is asked, as for a device token: a connection that exists is never turned away because somebody else on the
    same address guessed wrong, and the same wrong key again counts once."""
    if not ereaders.allowed(db):
        raise Refusal(401, UNAUTHORIZED, "The operator has switched e-readers off.")
    user = request.headers.get("x-auth-user", "")
    key = request.headers.get("x-auth-key", "")
    if not user or not key:
        raise Refusal(401, UNAUTHORIZED, "Unauthorized")
    found = ereaders.authenticate_key(db, user, key)
    if found is None:
        pair_guard(request, kind=KIND)
        if brake.first_sighting([name for name, _free in pair_keys(request, KIND)], user.casefold() + ":" + key):
            pair_failed(request, kind=KIND)
        raise Refusal(401, UNAUTHORIZED, "Unauthorized")
    logs.set_actor(found.account.name)
    brakes.take("kosync", found.account.id)
    return found.account


Syncing = Annotated[AccountRow, Depends(require_sync)]


@router.get("/users/auth")
def authorize(_account: Syncing) -> JSONResponse:
    return _json(200, {"authorized": "OK"})


@router.post("/users/create")
def create_user() -> JSONResponse:
    """Accounts are made in nexbooks, never by a program that asks. The message is what the plugin shows."""
    raise Refusal(402, REGISTRATION_OFF, "Accounts are made in nexbooks. Make a connection for KOReader under My "
                                         "account, Devices and apps, and log in here with its name and password.")


def _text(value: Any, limit: int, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value):
        raise Refusal(403, INVALID, "Invalid request")
    return value[:limit]


def _percentage(value: Any) -> float:
    try:
        number = float(value) if isinstance(value, (int, float, str)) and not isinstance(value, bool) else math.nan
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise Refusal(403, INVALID, "Invalid request")
    return min(max(number, 0.0), 1.0)


def _store(db: DbSession, uid: int, data: dict[str, Any]) -> JSONResponse:
    document = data.get("document")
    if not isinstance(document, str) or not document or ":" in document:
        raise Refusal(403, DOCUMENT_MISSING, "Field 'document' not provided.")
    if not kosync.DOCUMENT.match(document):
        raise Refusal(403, NOT_SERVABLE, "Field 'document' contains characters the read route cannot serve.")
    percentage = _percentage(data.get("percentage"))
    pointer = _text(data.get("progress"), POINTER_MAX, required=True)
    device = _text(data.get("device"), TEXT_MAX, required=True)
    device_id = _text(data.get("device_id"), TEXT_MAX, required=False)
    # A book the account does not see, or nobody knows, takes the report like any other and keeps nothing: the answer
    # does not say which of the two it is.
    kosync.store(db, uid, document, pointer=pointer, percentage=percentage, device=device, device_id=device_id)
    return _json(200, {"document": document, "timestamp": kosync.now_stamp()})


@router.put("/syncs/progress")
async def put_progress(request: Request, account: Syncing, db: DbSession) -> JSONResponse:
    raw = await request.body()
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise Refusal(403, INVALID, "Invalid request")
    return await run_in_threadpool(_store, db, account.id, data)


@router.get("/syncs/progress/{document}")
def get_progress(document: str, account: Syncing, db: DbSession) -> JSONResponse:
    """Where the account was in the book, in the fields of the official server; ``{}`` for a book nobody reported and
    for a book the account does not see (the same)."""
    return _json(200, kosync.fetch(db, account.id, document))
