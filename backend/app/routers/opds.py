"""The catalogue for reading apps under ``/opds`` (OPDS 1.2, Atom): the newest books, the authors, the series, one entry
for each library, a search, the covers and the files. For e-books only.

**This is not a way for a browser.** Only ``GET``; nothing here reads a cookie, and so a page on another site cannot use
the session of anybody against it. A reading app signs in with ``Authorization: Basic``: the account's name and the
secret of a connection of the kind ``opds`` that the person made under My account, Devices and apps
(``services/ereaders.py``). The password of the account is no way in. A wrong or blocked secret is ``401`` with
``WWW-Authenticate``; wrong secrets count against the sender like wrong passwords (a count of their own, so that a
mistyped app does not shut the sign-in out), and a sender that has failed too often rests (``429``).

**The rights are those of the interface:** a book, a cover or a file of a library the account is not a member of does
not exist (404), a file is sent only through the same checks as ``GET /api/v1/files/{id}/download``. The routes are not
part of the description of the interface (``/api/v1/openapi.json``); ``docs/api.md`` describes them.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response

from ..deps import DbSession, pair_failed, pair_guard, pair_keys
from ..errors import error
from ..models import EDITION_EBOOK, Edition
from ..models import Account as AccountRow
from ..security import brake
from ..services import brakes, ereaders, logs, opds
from ..services import works as works_service
from . import works as works_router

logger = logging.getLogger("nexbooks.opds")

router = APIRouter(prefix="/opds", tags=["opds"], include_in_schema=False)

CHALLENGE = 'Basic realm="nexbooks", charset="UTF-8"'
#: The name of a count of wrong secrets (``deps.pair_keys``).
KIND = "opds"


def _challenge() -> Exception:
    exc = error("sign_in_required", "Sign in with the name of your account and the secret of a connection.", 401)
    exc.headers = {"WWW-Authenticate": CHALLENGE}
    return exc


def _basic(header: str) -> tuple[str, str] | None:
    """The name and the secret of an ``Authorization: Basic`` header; None for anything else."""
    scheme, _space, value = header.partition(" ")
    if scheme.lower() != "basic" or not value or len(value) > 600:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    name, colon, secret = decoded.partition(":")
    return (name, secret) if colon and name and secret else None


def require_catalogue(request: Request, db: DbSession) -> AccountRow:
    """The account behind the connection, or the answer that turns the app away. Looked up before the count of wrong
    secrets is asked, as for a device token: a connection that exists is never turned away because somebody else on the
    same address guessed wrong, and the same wrong secret again counts once."""
    if not ereaders.allowed(db):
        raise error("ereaders_off", "The operator has switched e-readers off.", 403)
    given = _basic(request.headers.get("authorization", ""))
    if given is None:
        # The first request of an app carries nothing: it only learns how to sign in. No guess, no count.
        raise _challenge()
    found = ereaders.authenticate(db, ereaders.EREADER_OPDS, *given)
    if found is None:
        pair_guard(request, kind=KIND)
        if brake.first_sighting([key for key, _free in pair_keys(request, KIND)], given[0].casefold() + ":" + given[1]):
            pair_failed(request, kind=KIND)
        raise _challenge()
    logs.set_actor(found.account.name)
    brakes.take("opds", found.account.id)
    return found.account


Catalogue = Annotated[AccountRow, Depends(require_catalogue)]
PageNumber = Annotated[int, Query(ge=1, le=100_000)]


def _feed(feed: opds.Feed) -> Response:
    return Response(feed.xml(), media_type=feed.kind, headers={
        "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("", summary="The start of the catalogue")
@router.get("/", include_in_schema=False)
def start(account: Catalogue, db: DbSession) -> Response:
    return _feed(opds.root(db, account.id))


@router.get("/new")
def new(account: Catalogue, db: DbSession, page: PageNumber = 1) -> Response:
    return _feed(opds.new_books(db, account.id, page))


@router.get("/libraries/{library_id}")
def library(library_id: Annotated[int, Path(ge=1)], account: Catalogue, db: DbSession,
            page: PageNumber = 1) -> Response:
    return _feed(opds.library_books(db, account.id, library_id, page))


@router.get("/authors")
def authors(account: Catalogue, db: DbSession, page: PageNumber = 1) -> Response:
    return _feed(opds.authors(db, account.id, page))


@router.get("/authors/{person_id}")
def author(person_id: Annotated[int, Path(ge=1)], account: Catalogue, db: DbSession, page: PageNumber = 1) -> Response:
    return _feed(opds.author_books(db, account.id, person_id, page))


@router.get("/series")
def series(account: Catalogue, db: DbSession, page: PageNumber = 1) -> Response:
    return _feed(opds.series_list(db, account.id, page))


@router.get("/series/{series_id}")
def one_series(series_id: Annotated[int, Path(ge=1)], account: Catalogue, db: DbSession,
               page: PageNumber = 1) -> Response:
    return _feed(opds.series_books(db, account.id, series_id, page))


@router.get("/search.xml")
def search_description(_account: Catalogue) -> Response:
    return Response(opds.search_description(), media_type=opds.SEARCH_TYPE,
                    headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/search")
def search(account: Catalogue, db: DbSession, q: Annotated[str, Query(max_length=200)] = "") -> Response:
    brakes.take("search", account.id)
    return _feed(opds.search_results(db, account.id, q))


@router.get("/works/{work_id}/cover")
@router.get("/works/{work_id}/thumbnail", include_in_schema=False)
def cover(work_id: Annotated[int, Path(ge=1)], request: Request, account: Catalogue, db: DbSession) -> Response:
    return works_router.cover_response(db, account.id, work_id, request, None)


#: The part of a file's address after its number is for apps that read the format from the end of an address.
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


@router.get("/files/{file_id}/{name}")
def download(file_id: Annotated[int, Path(ge=1)], name: str, request: Request, account: Catalogue,
             db: DbSession) -> Response:
    """The file of an e-book as it lies in the library, with the checks of ``GET /api/v1/files/{id}/download``: the
    number alone names it (``name`` is only the ending an app reads), and a file the account may not see does not exist.
    Too many files at once, or too many requests, are ``429`` with ``Retry-After``."""
    if not _NAME.match(name):
        raise error("not_found", "Not found.", 404)
    brakes.take("stream", account.id)
    row, path = works_service.file_of(db, account.id, file_id)
    edition = db.get(Edition, row.edition_id) if row.edition_id else None
    if edition is None or edition.kind != EDITION_EBOOK:
        raise error("not_found", "Not found.", 404)
    title = re.sub(r"[^\w .,()'&-]+", " ", edition.title).strip(" .")
    filename = (" ".join(title.split())[:80] or str(row.id)) + "." + row.format
    return works_router.stream_response(account.id, request, path, row.format, f"{row.id}", filename)
