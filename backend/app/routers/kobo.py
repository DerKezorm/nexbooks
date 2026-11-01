"""The sync of a Kobo under ``/kobo/<secret>``: the few routes a Kobo calls on its store server (the protocol its own
store speaks), so that its address can be entered in the device. A person puts books on a list, the Kobo fetches the
list and keeps it, and the place read on the Kobo comes back (``services/kobo.py`` says how that meets the rest of
nexbooks).

**The secret is in the address.** A Kobo can be given one thing, the address of its server (``api_endpoint`` in
``.kobo/Kobo/Kobo eReader.conf``), and sends no header of its own. So the secret of a connection of the kind ``kobo``
that the person made under My account, Devices and apps (``services/ereaders.py``) is the second part of every
address here. It is kept as a hash, masked in every line of the log (``services/logs.py``) and in no error; no route
answers with a redirect (an address that ends in a slash, or in nothing, is answered where it is: the last route below
takes everything). The account's own password is no way in.

**This is not a way for a browser.** Nothing here reads a cookie, and the tokens the device is handed at its sign-in
(``/v1/auth/device``) are made up on the spot and read by nobody: the secret in the address is the only proof. A wrong
secret is ``401`` and counts against the sender like a wrong password (a count of its own, so that a mistyped device
does not shut the sign-in out; a secret that exists is never turned away by it), and a sender that has failed too often
rests (``429``).

**nexbooks passes nothing on to Kobo.** The device is handed the whole table Kobo's store hands out
(``services/kobo_resources.json``), with every address of the store's server changed to nexbooks and the pictures to
nexbooks: the programs that are known to work with a real Kobo send the whole table, and what a device does with a
shorter one is not known. The rest stays as it is (web pages, the sign-in of the shop), and no address that leads
elsewhere holds the secret. A route nexbooks has no use of answers empty (``{}``) for a connection that is good and
``401`` for one that is not, so the Kobo store and Kobo's own services do not work while the device is pointed here.

**The rights are those of the interface:** only the books on the person's list are reachable, and only while the
person still sees them: a book the person may not see (any more) answers ``404`` here, for its metadata, its place,
its file and its cover, like a name nobody has. The routes are not part of the description of the interface
(``/api/v1/openapi.json``); ``docs/api.md`` describes them.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..deps import DbSession, pair_failed, pair_guard, pair_keys
from ..errors import error
from ..models import Account as AccountRow
from ..security import brake
from ..services import brakes, ereaders, kobo, logs
from ..services import works as works_service
from . import works as works_router

logger = logging.getLogger("nexbooks.kobo")

router = APIRouter(prefix="/kobo/{token}", tags=["kobo"], include_in_schema=False)

#: The name of a count of wrong secrets (``deps.pair_keys``).
KIND = "kobo"
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
#: The methods the last route takes, so that no request for a good connection is answered with a "method not allowed".
EVERY_METHOD = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
#: A file answers ``HEAD`` itself: the last route would answer it with an empty object.
FILE_METHODS = ["GET", "HEAD"]
#: The table of Kobo's store, and the address of its server in it.
STORE_TABLE = json.loads(Path(kobo.__file__).with_name("kobo_resources.json").read_text(encoding="utf-8"))
STORE_SERVER = "https://storeapi.kobo.com"
SMALL_NUMBER = re.compile(r"^[0-9]{1,5}$")


@dataclass
class Door:
    """Who comes in with the address of a Kobo, and the address the device is handed back for everything else."""

    account: AccountRow
    link_id: int
    #: ``<address of nexbooks>/kobo/<secret>``: the root of every address the answers hand out.
    root: str


def _json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers={**HEADERS, **(headers or {})})


def require_kobo(request: Request, token: str, db: DbSession) -> Door:
    """The account behind the secret in the address, or the answer that turns the device away. Looked up before the
    count of wrong secrets is asked, as for a device token: a connection that exists is never turned away because
    somebody else on the same address guessed wrong, and the same wrong secret again counts once."""
    if not ereaders.allowed(db):
        raise error("ereaders_off", "The operator has switched e-readers off.", 403)
    found = ereaders.authenticate_path(db, token)
    if found is None:
        pair_guard(request, kind=KIND)
        if brake.first_sighting([key for key, _free in pair_keys(request, KIND)], token):
            pair_failed(request, kind=KIND)
        raise error("unauthorized", "Unauthorized.", 401)
    logs.set_actor(found.account.name)
    brakes.take("kobo", found.account.id)
    return Door(found.account, found.link_id, f"{ereaders.base_of(db, request)}/kobo/{token}")


Device = Annotated[Door, Depends(require_kobo)]


def resources(root: str) -> dict[str, Any]:
    """The table of Kobo's store with nexbooks in the place of its server and of its pictures. The device expands the
    names in braces itself."""
    found = {name: value.replace(STORE_SERVER, root) if isinstance(value, str) else value
             for name, value in STORE_TABLE.items()}
    found.update({
        "image_host": root,
        "image_url_template": f"{root}/image/{{ImageId}}/{{Width}}/{{Height}}/false/image.jpg",
        "image_url_quality_template":
            f"{root}/image/{{ImageId}}/{{Width}}/{{Height}}/{{Quality}}/{{IsGreyscale}}/image.jpg",
    })
    return found


@router.get("/v1/initialization")
def initialization(door: Device) -> JSONResponse:
    """The addresses the device goes on with. It is the first call a device makes after it was pointed here."""
    return _json({"Resources": resources(door.root)}, headers={"x-kobo-apitoken": "e30="})


def _auth_answer(body: Any) -> JSONResponse:
    """What the device takes as its sign-in: tokens made up here, which nothing reads again (the address is the
    proof)."""
    user_key = body.get("UserKey") if isinstance(body, dict) else None
    if not isinstance(user_key, str) or not user_key or len(user_key) > 100:
        user_key = str(uuid.uuid4())
    return _json({"AccessToken": secrets.token_urlsafe(32), "RefreshToken": secrets.token_urlsafe(32),
                  "TokenType": "Bearer", "TrackingId": str(uuid.uuid4()), "UserKey": user_key})


async def _body(request: Request) -> Any:
    """The JSON of a request, or None for anything that is not JSON (the size is limited before the route runs)."""
    raw = await request.body()
    try:
        return json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        return None


@router.post("/v1/auth/device")
async def sign_in(request: Request, _door: Device) -> JSONResponse:
    return _auth_answer(await _body(request))


@router.post("/v1/auth/refresh")
async def refresh(request: Request, _door: Device) -> JSONResponse:
    return _auth_answer(await _body(request))


@router.post("/v1/analytics/gettests")
def tests(_door: Device) -> JSONResponse:
    return _json({"Result": "Success", "TestKey": "", "Tests": {}})


@router.get("/v1/user/loyalty/benefits")
def benefits(_door: Device) -> JSONResponse:
    """Empty too, but with its field: a program known to work with a real Kobo answers this route so."""
    return _json({"Benefits": {}})


@router.get("/v1/library/tags")
def tags(_door: Device) -> JSONResponse:
    """The device asks for the shelves it should show; nexbooks has none for it."""
    return _json([])


@router.get("/v1/library/sync")
def library_sync(request: Request, door: Device, db: DbSession) -> JSONResponse:
    """What changed on the person's list since the answer whose token the device brings, one batch at a time: while
    there is more, the answer says ``x-kobo-sync: continue`` and the device asks again with the new token."""
    answer = kobo.sync(db, door.account.id, door.link_id, request.headers.get(kobo.TOKEN_HEADER, ""), door.root)
    headers = {kobo.TOKEN_HEADER: answer.token}
    if answer.more:
        headers["x-kobo-sync"] = "continue"
    return _json(answer.entries, headers=headers)


def _book(db: DbSession, door: Door, name: str) -> kobo.Want:
    found = kobo.book_named(db, door.account.id, name)
    if found is None:
        raise error("not_found", "Not found.", 404)
    return found


@router.get("/v1/library/{name}/metadata")
def metadata(name: str, door: Device, db: DbSession) -> JSONResponse:
    return _json([kobo.metadata_of(db, door.account.id, _book(db, door, name), door.root)])


@router.get("/v1/library/{name}/state")
def read_state(name: str, door: Device, db: DbSession) -> JSONResponse:
    return _json([kobo.state_of(db, door.account.id, _book(db, door, name))])


def _write(db: DbSession, door: Door, name: str, body: Any) -> kobo.Want:
    book = _book(db, door, name)
    kobo.put_state(db, door.account.id, door.link_id, book, body)
    return book


@router.put("/v1/library/{name}/state")
async def write_state(name: str, request: Request, door: Device, db: DbSession) -> JSONResponse:
    """A state the device reports. A body that is not the state of this book changes nothing and is answered like one
    that did."""
    book = await run_in_threadpool(_write, db, door, name, await _body(request))
    ok = {"Result": "Success"}
    return _json({"RequestResult": "Success", "UpdateResults": [{
        "EntitlementId": book.name, "CurrentBookmarkResult": ok, "StatisticsResult": ok, "StatusInfoResult": ok}]})


@router.delete("/v1/library/{name}")
def forget(name: str, door: Device, db: DbSession) -> Response:
    """The person took the book off the device: it is not sent to this device again until it is put on the list anew."""
    if not kobo.archive(db, door.account.id, door.link_id, name):
        raise error("not_found", "Not found.", 404)
    return Response(status_code=204, headers=HEADERS)


@router.api_route("/download/{file}", methods=FILE_METHODS)
def download(file: str, request: Request, door: Device, db: DbSession) -> Response:
    """The EPUB of a book on the list as it lies in the library, with the checks of ``GET /api/v1/files/{id}/download``.
    Too many files at once, or too many requests, are ``429`` with ``Retry-After``."""
    name = file[:-5] if file.endswith(".epub") else ""
    uid = door.account.id
    brakes.take("stream", uid)
    book = _book(db, door, name)
    row, path = works_service.file_of(db, uid, book.file.id)
    title = re.sub(r"[^\w .,()'&-]+", " ", book.edition.title).strip(" .")
    filename = (" ".join(title.split())[:80] or str(row.id)) + ".epub"
    return works_router.stream_response(uid, request, path, "epub", f"kobo-{row.id}", filename)


def _size(text: str) -> int:
    if not SMALL_NUMBER.match(text):
        raise error("not_found", "Not found.", 404)
    return int(text)


def _cover(name: str, width: str, height: str, request: Request, door: Door, db: DbSession) -> Response:
    """The cover of a book on the list as a JPEG no bigger than the device asks for (the device decodes no WebP)."""
    book = _book(db, door, name)
    wide, high = _size(width), _size(height)
    data, etag = kobo.cover_jpeg(db, door.account.id, book, wide, high)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="image/jpeg", headers=headers)


@router.api_route("/image/{name}/{width}/{height}/{quality}/image.jpg", methods=FILE_METHODS)
def cover_of_quality(name: str, width: str, height: str, quality: str, request: Request, door: Device,
                     db: DbSession) -> Response:
    return _cover(name, width, height, request, door, db)


@router.api_route("/image/{name}/{width}/{height}/{quality}/{greyscale}/image.jpg", methods=FILE_METHODS)
def cover_of_quality_and_colour(name: str, width: str, height: str, quality: str, greyscale: str, request: Request,
                                door: Device, db: DbSession) -> Response:
    return _cover(name, width, height, request, door, db)


@router.api_route("", methods=EVERY_METHOD)
@router.api_route("/{rest:path}", methods=EVERY_METHOD)
def nothing(_door: Device, rest: str = "") -> JSONResponse:
    """Every other route a device may call: nexbooks has no use of it and says so with an empty answer. It is also what
    takes an address with a slash too few, so that nothing is ever redirected (a slash too many is gone before the
    routes, ``middleware.GuardMiddleware``)."""
    return _json({})
