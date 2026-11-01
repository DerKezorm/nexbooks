"""Fetching books, the operator's side: the indexers, the Prowlarr of the instance, the download
programs, the formats and places, the settings of the automatic work. The operator in a signed-in browser,
like the settings.

The keys and passwords the operator types are sealed when they are saved and never read back: the answers
say only whether one is set. Every request to an indexer, to Prowlarr or to a program goes through
``services/procure/gate.py``.

``GET /api/v1/procure/targets`` is for everybody who is signed in: the libraries one may ask for books in
(the right ``acquire`` or ``manage``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession, OperatorAccount, Reader
from ..errors import error
from ..models import CLIENT_KINDS, INDEXER_KINDS, DownloadClient, Indexer, Library, PathMapping, ProwlarrConnection
from ..security import decrypt_secret, encrypt_secret
from ..services import libraries as library_service
from ..services.organize import scheme as schemes
from ..services.procure import (
    autolog,
    automation,
    formats,
    gate,
    indexer_admin,
    indexers,
    newznab,
    planner,
    programs,
    prowlarr,
    wishes,
)
from ..services.procure import clients as client_package
from ..services.procure.clients import ClientError, Target

logger = logging.getLogger("nexbooks.procure")

router = APIRouter(prefix="/api/v1/procure", tags=["procure"])

IndexerKind = Literal["newznab", "torznab"]
ClientKind = Literal["sabnzbd", "nzbget", "qbittorrent", "transmission", "deluge"]
assert set(INDEXER_KINDS) == {"newznab", "torznab"} and set(CLIENT_KINDS) == set(client_package.KINDS)

#: The English sentence for each code; the interface words them in its own language (``errors.byCode``).
MESSAGES = {
    "indexer_key_rejected": "The indexer did not accept the API key.",
    "indexer_limit_reached": "The request limit of the indexer is reached.",
    "indexer_daily_limit": "The limit of requests for today is reached.",
    "indexer_off": "The indexer is switched off.",
    "indexer_error": "The indexer reports an error.",
    "indexer_switched_off": "The indexer is switched off where it comes from.",
    "indexer_not_newznab": "Something answers at this address, but not as a Newznab or Torznab indexer.",
    "indexer_wrong_kind": "The answer does not fit the chosen kind: Newznab is for Usenet, Torznab for torrents.",
    "indexer_unreachable": "The indexer cannot be reached. Are address and port right?",
    "indexer_http_error": "The indexer reports an error.",
    "indexer_answer_too_large": "The answer of the indexer is larger than 10 MB and was refused.",
    "indexer_blocked": "nexbooks may not ask this address.",
    "indexer_timeout": "The indexer did not answer in time.",
    "prowlarr_unreachable": "Prowlarr cannot be reached. Are address and port right?",
    "prowlarr_key_rejected": "Prowlarr did not accept the API key.",
    "prowlarr_not_prowlarr": "Something answers at this address, but it is not Prowlarr.",
    "prowlarr_too_old": "This Prowlarr is too old; nexbooks needs 1.8.6 or newer.",
    "prowlarr_http_error": "Prowlarr reports an error.",
    "prowlarr_blocked": "nexbooks may not ask this address.",
    "prowlarr_timeout": "Prowlarr did not answer in time.",
    "client_unreachable": "The download program cannot be reached. Are address and port right?",
    "client_auth_failed": "The download program did not accept the credentials.",
    "client_host_refused": "SABnzbd refused the host name nexbooks used. Add it to SABnzbd's host whitelist.",
    "client_address_refused": "The download program refuses nexbooks's address. Allow it in the program's whitelist.",
    "client_wrong_kind": "Something answers at this address, but not the chosen download program.",
    "client_category_failed": "The category could not be created or used in the download program.",
    "client_category_unusable": "The download program handles this category in a way nexbooks cannot follow.",
    "client_refused": "The download program refused the request.",
    "client_blocked": "nexbooks may not ask this address.",
    "client_timeout": "The download program did not answer in time.",
}


def _raise(code: str, status: int, **values: Any) -> Exception:
    exc = error(code, MESSAGES.get(code, "The request failed."), status, **values)
    retry = values.get("retry_after")
    if isinstance(retry, int):
        exc.headers = {"Retry-After": str(retry)}
    return exc


def _from_indexer(exc: indexers.IndexerError) -> Exception:
    values = {key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in exc.values.items()}
    return _raise(exc.code, exc.status, **values)


def _from_client(exc: ClientError) -> Exception:
    return _raise(exc.code, exc.status, **exc.values)


def _from_prowlarr(exc: prowlarr.ProwlarrError) -> Exception:
    return _raise(exc.code, exc.status, **exc.values)


def _errors(codes: tuple[tuple[int, str], ...]) -> dict[int | str, dict[str, Any]]:
    result: dict[int | str, dict[str, Any]] = {}
    for status, code in codes:
        entry = result.setdefault(status, {"description": ""})
        entry["description"] = (entry["description"] + ", " if entry["description"] else "") + code
    return result


# --- Indexers ---------------------------------------------------------------------------------------------------------


class CategoryOut(BaseModel):
    id: int
    name: str
    type: Literal["ebook", "audio"]
    #: ``mixed`` or a language code.
    lang: str
    #: Whether the category is asked.
    on: bool
    #: Whether nexbooks suggests it.
    suggested: bool
    #: Whether a search may ask it: a category of a language books are not fetched in (settings, "Sprachen") never is.
    asked: bool = True
    #: The language the caps state for it (its name or the name of the one it stands under); ``lang`` differs when the
    #: operator set another.
    proposed: str = "mixed"


class SupportedParams(BaseModel):
    #: What the indexer lists as ``supportedParams`` of its plain search, its book search and its audio search.
    search: list[str]
    book: list[str]
    audio: list[str]
    book_search: bool
    audio_search: bool


class IndexerOut(BaseModel):
    id: int
    name: str
    kind: IndexerKind
    url: str
    key_set: bool
    enabled: bool
    priority: int
    daily_limit: int
    #: ``ready``, ``resting`` (a request limit, the limit of the day), ``key_rejected`` or ``off``.
    state: Literal["ready", "resting", "key_rejected", "off"]
    used_today: int
    grabs: int
    grabs_today: int = Field(description="Releases fetched today (counted apart from the requests).")
    rss_checked_at: datetime | None = Field(description="When its newest entries were read the last time.")
    rss_error: str = Field(description="The code of the failure of the last reading of its newest entries.")
    day_ends_at: datetime = Field(description="When the count of the day starts again (midnight of the instance).")
    via_prowlarr: bool
    categories: list[CategoryOut]
    resting_until: datetime | None
    #: ``request_limit``, ``api_limit`` or ``daily_limit``.
    resting_cause: str
    last_error: str
    api_current: int | None
    api_max: int | None
    grab_current: int | None
    grab_max: int | None
    supported_params: SupportedParams
    caps_checked_at: datetime | None
    #: The languages books are fetched in (settings, "Sprachen"): a category of another language is not asked.
    languages: list[str] = Field(default_factory=list)
    #: How many chosen categories have another language than the one the caps state ("Vorschläge übernehmen").
    proposals: int = 0


class IndexersOut(BaseModel):
    indexers: list[IndexerOut]


class IndexerIn(BaseModel):
    name: str = Field(min_length=1, max_length=indexers.NAME_MAX)
    kind: IndexerKind
    url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=indexers.KEY_MAX + 10)
    priority: int = indexers.PRIORITY_DEFAULT
    daily_limit: int = 0
    enabled: bool = True
    #: Without categories the suggestion of the indexer's caps is taken.
    categories: list[int] | None = Field(default=None, max_length=100)
    category_langs: dict[str, str] | None = None
    #: The interface language of the operator: the suggestion takes its categories only when no language is ticked
    #: under "Sprachen" (else the languages ticked there).
    language: str | None = Field(default=None, max_length=8)


class IndexerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=indexers.NAME_MAX)
    kind: IndexerKind | None = None
    url: str | None = Field(default=None, min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=indexers.KEY_MAX + 10)
    priority: int | None = None
    daily_limit: int | None = None
    enabled: bool | None = None
    categories: list[int] | None = Field(default=None, max_length=100)
    category_langs: dict[str, str] | None = None


class IndexerTestIn(BaseModel):
    #: A saved indexer whose key is used when ``api_key`` is left out.
    indexer_id: int | None = None
    kind: IndexerKind
    url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=indexers.KEY_MAX + 10)
    language: str | None = Field(default=None, max_length=8)


class IndexerTestOut(BaseModel):
    kind: IndexerKind | None
    book_search: bool
    audio_search: bool
    #: ``supportedParams`` of the plain, the book and the audio search.
    search_params: list[str]
    book_params: list[str]
    audio_params: list[str]
    categories: list[CategoryOut]
    category_count: int
    #: 0 or 1: whether a request in the suggested categories delivered anything.
    items: int
    indexer: IndexerOut | None = None
    #: The languages books are fetched in (settings, "Sprachen"): a category of another language is not asked.
    languages: list[str] = Field(default_factory=list)


def _test_out(result: indexers.TryResult, row: IndexerOut | None, languages: tuple[str, ...] = ()) -> IndexerTestOut:
    caps = result.caps or {}
    total = sum(1 + len(category.get("subcats") or []) for category in caps.get("categories") or [])
    return IndexerTestOut(
        kind=result.kind if result.kind in INDEXER_KINDS else None,  # type: ignore[arg-type]
        book_search=bool(caps.get("book_search")), audio_search=bool(caps.get("audio_search")),
        search_params=caps.get("search_params") or [], book_params=caps.get("book_params") or [],
        audio_params=caps.get("audio_params") or [],
        categories=[CategoryOut(id=item.id, name=item.name, type=item.type,  # type: ignore[arg-type]
                                lang=item.lang, on=item.suggested, suggested=item.suggested, proposed=item.lang,
                                asked=newznab.asked_language(item.lang, languages) if languages else True)
                    for item in result.categories],
        category_count=total, items=result.items, indexer=row, languages=list(languages))


@router.get("/indexers", response_model=IndexersOut, summary="The indexers and how they fare (the operator)")
def list_indexers(_operator: OperatorAccount, db: DbSession) -> IndexersOut:
    return IndexersOut(indexers=[IndexerOut(**indexer_admin.view(db, row)) for row in indexer_admin.listing(db)])


@router.post("/indexers", response_model=IndexerOut, status_code=201, summary="Add an indexer (the operator)",
             responses=_errors(newznab.ERRORS))
def add_indexer(payload: IndexerIn, operator: OperatorAccount, db: DbSession) -> IndexerOut:
    try:
        row = indexer_admin.create(db, payload.model_dump())
    except indexers.IndexerError as exc:
        raise _from_indexer(exc) from exc
    logger.info("Indexer added id=%s by=%s", row.id, operator.name)
    return IndexerOut(**indexer_admin.view(db, row))


@router.patch("/indexers/{indexer_id}", response_model=IndexerOut, summary="Change an indexer (the operator)",
              responses=_errors(newznab.ERRORS))
def change_indexer(indexer_id: int, payload: IndexerPatch, operator: OperatorAccount, db: DbSession) -> IndexerOut:
    row = indexer_admin.get(db, indexer_id)
    try:
        indexer_admin.update(db, row, payload.model_dump(exclude_unset=True))
    except indexers.IndexerError as exc:
        raise _from_indexer(exc) from exc
    logger.info("Indexer changed id=%s by=%s", row.id, operator.name)
    return IndexerOut(**indexer_admin.view(db, row))


@router.delete("/indexers/{indexer_id}", status_code=204, summary="Remove an indexer (the operator)")
def remove_indexer(indexer_id: int, operator: OperatorAccount, db: DbSession) -> Response:
    indexer_admin.remove(db, indexer_admin.get(db, indexer_id))
    logger.info("Indexer removed id=%s by=%s", indexer_id, operator.name)
    return Response(status_code=204)


@router.post("/indexers/test", response_model=IndexerTestOut, summary="Test an address and a key (the operator)",
             responses=_errors(newznab.ERRORS))
def test_indexer_address(payload: IndexerTestIn, _operator: OperatorAccount, db: DbSession) -> IndexerTestOut:
    """Caps, then one request in the suggested book categories. Nothing is saved. A key left out is taken from the
    saved indexer named by ``indexer_id``."""
    url = indexers.valid_address(payload.url)
    key = indexers.valid_key(payload.api_key or "") if payload.api_key else ""
    if not payload.api_key and payload.indexer_id is not None:
        saved = db.get(Indexer, payload.indexer_id)
        if saved is not None:
            _key_stays(saved.url, url, "api_key")
            key = indexers.key_of(db, saved)
    prefer = indexer_admin.fetch_languages(db, payload.language)
    try:
        result = indexers.try_unsaved(indexers.Target(url=url, kind=payload.kind, api_key=key), prefer)
    except indexers.IndexerError as exc:
        raise _from_indexer(exc) from exc
    return _test_out(result, None, prefer)


@router.post("/indexers/{indexer_id}/proposals", response_model=IndexerOut,
             summary="Give every chosen category of an indexer the language its caps state (the operator)")
def take_indexer_proposals(indexer_id: int, operator: OperatorAccount, db: DbSession) -> IndexerOut:
    """"Vorschläge übernehmen": a language the operator set apart from the caps (``lang`` other than ``proposed``) goes,
    and the category follows its caps again."""
    row = indexer_admin.take_proposals(db, indexer_admin.get(db, indexer_id))
    logger.info("Indexer took the languages of its caps id=%s by=%s", indexer_id, operator.name)
    return IndexerOut(**indexer_admin.view(db, row))


@router.post("/indexers/{indexer_id}/test", response_model=IndexerTestOut,
             summary="Test a saved indexer under its rules and keep what it says (the operator)",
             responses=_errors(newznab.ERRORS))
def test_saved_indexer(indexer_id: int, _operator: OperatorAccount, db: DbSession,
                       language: str | None = None) -> IndexerTestOut:
    row = indexer_admin.get(db, indexer_id)
    prefer = indexer_admin.fetch_languages(db, language)
    try:
        result = indexers.try_saved(db, row, prefer)
    except indexers.IndexerError as exc:
        raise _from_indexer(exc) from exc
    return _test_out(result, IndexerOut(**indexer_admin.view(db, row)), prefer)


def _key_stays(saved_url: str, typed_url: str, field: str) -> None:
    """A key that is saved is sent only to the place it was saved for (host and port). A test or a change that points
    somewhere else has to bring the key again: otherwise the saved key could be sent to any address typed."""
    if not gate.key_follows(saved_url, typed_url):
        raise error("key_required", "The address changed; enter the key again.", 422, field=field)


# --- Prowlarr ---------------------------------------------------------------------------------------------------------


class ProwlarrOut(BaseModel):
    connected: bool
    url: str
    key_set: bool
    auto_add: bool
    version: str
    last_sync_at: datetime | None
    #: The code of the last sync that failed; empty after one that went through.
    last_error: str
    #: What the last sync did: ``total``, ``added``, ``updated``, ``removed``, ``skipped_categories``,
    #: ``skipped_disabled``, ``skipped_unsupported``, ``skipped_limit`` (left out: nexbooks holds at most 200 indexers).
    counts: dict[str, int]
    #: How many indexers of nexbooks come from it.
    taken: int
    sync_state: Literal["idle", "queued", "running"]


class ProwlarrIn(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    #: Without a key the saved one stays (needed when connecting).
    api_key: str | None = Field(default=None, max_length=prowlarr.KEY_MAX + 10)
    auto_add: bool = True


class ProwlarrTestIn(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=prowlarr.KEY_MAX + 10)


class ProwlarrTestOut(BaseModel):
    version: str
    indexers: int
    with_books: int


def _prowlarr_view(db: DbSession) -> ProwlarrOut:
    connection = indexers.connection_of(db)
    if connection is None:
        return ProwlarrOut(connected=False, url="", key_set=False, auto_add=True, version="", last_sync_at=None,
                           last_error="", counts={}, taken=0, sync_state="idle")
    taken = db.scalar(select(func.count(Indexer.id)).where(Indexer.prowlarr_indexer_id.is_not(None))) or 0
    return ProwlarrOut(
        connected=True, url=connection.url, key_set=bool(connection.api_key_enc), auto_add=connection.auto_add,
        version=connection.version, last_sync_at=connection.last_sync_at, last_error=connection.last_error,
        counts={name: int(value) for name, value in (connection.last_counts or {}).items()
                if isinstance(value, int)},
        taken=int(taken), sync_state=prowlarr.sync_state(db, connection))  # type: ignore[arg-type]


def _prowlarr_address(url: str) -> str:
    try:
        return gate.normalize_address(url)
    except gate.GateError as exc:
        raise error("address_invalid", "This address cannot be used.", 422, field="url") from exc


@router.get("/prowlarr", response_model=ProwlarrOut, summary="The Prowlarr of the instance (the operator)")
def read_prowlarr(_operator: OperatorAccount, db: DbSession) -> ProwlarrOut:
    return _prowlarr_view(db)


@router.post("/prowlarr/test", response_model=ProwlarrTestOut, summary="Test an address and a key of Prowlarr",
             responses=_errors(prowlarr.ERRORS))
def test_prowlarr(payload: ProwlarrTestIn, _operator: OperatorAccount, db: DbSession) -> ProwlarrTestOut:
    url = _prowlarr_address(payload.url)
    key = payload.api_key or ""
    connection = indexers.connection_of(db)
    if not key and connection is not None:
        _key_stays(connection.url, url, "api_key")
        key = decrypt_secret(connection.api_key_enc, indexers.SECRET_CONTEXT)
    try:
        return ProwlarrTestOut(**prowlarr.check(url, key))
    except prowlarr.ProwlarrError as exc:
        raise _from_prowlarr(exc) from exc


@router.put("/prowlarr", response_model=ProwlarrOut, summary="Connect Prowlarr, or change the connection",
            responses=_errors(prowlarr.ERRORS))
def save_prowlarr(payload: ProwlarrIn, operator: OperatorAccount, db: DbSession) -> ProwlarrOut:
    """Tested first, saved when it went through, and a sync is queued. A key that is left out stays."""
    url = _prowlarr_address(payload.url)
    connection = indexers.connection_of(db)
    given = (payload.api_key or "").strip()
    if len(given) > prowlarr.KEY_MAX or any(ord(char) <= 32 or ord(char) == 127 for char in given):
        raise error("invalid_input", "The key is not valid.", 422, field="api_key")
    if connection is not None and not given:
        _key_stays(connection.url, url, "api_key")
    key = given or (decrypt_secret(connection.api_key_enc, indexers.SECRET_CONTEXT) if connection else "")
    try:
        found = prowlarr.check(url, key)
    except prowlarr.ProwlarrError as exc:
        raise _from_prowlarr(exc) from exc
    if connection is None:
        connection = ProwlarrConnection(url=url, api_key_enc=encrypt_secret(key, indexers.SECRET_CONTEXT),
                                        auto_add=payload.auto_add, version=found["version"])
        db.add(connection)
    else:
        connection.url, connection.auto_add, connection.version = url, payload.auto_add, found["version"]
        if given:
            connection.api_key_enc = encrypt_secret(given, indexers.SECRET_CONTEXT)
            # The indexers of Prowlarr use the key of the connection: a refused key or a rest ends with the new one.
            for row in db.scalars(select(Indexer).where(Indexer.prowlarr_indexer_id.is_not(None))):
                row.last_error, row.paused_until, row.pause_cause = "", None, ""
        connection.last_error = ""
    db.commit()
    logger.info("Prowlarr connected by=%s", operator.name)
    prowlarr.request_sync(db, connection)
    return _prowlarr_view(db)


@router.post("/prowlarr/sync", response_model=ProwlarrOut, status_code=202,
             summary="Read Prowlarr again now (queued, the operator)")
def sync_prowlarr(_operator: OperatorAccount, db: DbSession) -> ProwlarrOut:
    connection = indexers.connection_of(db)
    if connection is None:
        raise error("not_found", "Not found.", 404)
    prowlarr.request_sync(db, connection)
    return _prowlarr_view(db)


@router.delete("/prowlarr", status_code=204, summary="Disconnect Prowlarr; its indexers go with it (the operator)")
def disconnect_prowlarr(operator: OperatorAccount, db: DbSession) -> Response:
    connection = indexers.connection_of(db)
    if connection is None:
        raise error("not_found", "Not found.", 404)
    prowlarr.drop_jobs(db, connection.id)
    for row in db.scalars(select(Indexer).where(Indexer.prowlarr_indexer_id.is_not(None))):
        db.delete(row)
    db.delete(connection)
    db.commit()
    logger.info("Prowlarr disconnected by=%s", operator.name)
    return Response(status_code=204)


# --- Download programs -----------------------------------------------------------------------------------------------


class ClientOut(BaseModel):
    id: int
    name: str
    kind: ClientKind
    #: ``usenet`` or ``torrent``.
    protocol: Literal["usenet", "torrent"]
    url: str
    username: str
    secret_set: bool
    category: str
    #: Whether the program has categories at all; Transmission has none, its jobs go to a folder of nexbooks.
    has_categories: bool
    priority: int
    enabled: bool
    version: str
    #: ``ready`` while the last test went through, else the state the last failure names: ``unreachable``, ``login``
    #: (the credentials were refused), ``other``.
    state: Literal["ready", "unreachable", "login", "other"]
    last_error: str
    checked_at: datetime | None


class ClientsOut(BaseModel):
    clients: list[ClientOut]


class ClientIn(BaseModel):
    kind: ClientKind
    name: str | None = Field(default=None, max_length=programs.NAME_MAX)
    url: str = Field(min_length=1, max_length=1024)
    username: str = Field(default="", max_length=programs.USER_MAX)
    secret: str | None = Field(default=None, max_length=programs.SECRET_MAX + 10)
    category: str = Field(default=client_package.DEFAULT_CATEGORY, max_length=programs.CATEGORY_MAX + 10)
    priority: int = 1
    enabled: bool = True


class ClientPatch(BaseModel):
    kind: ClientKind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=programs.NAME_MAX)
    url: str | None = Field(default=None, min_length=1, max_length=1024)
    username: str | None = Field(default=None, max_length=programs.USER_MAX)
    secret: str | None = Field(default=None, max_length=programs.SECRET_MAX + 10)
    category: str | None = Field(default=None, max_length=programs.CATEGORY_MAX + 10)
    priority: int | None = None
    enabled: bool | None = None


class ClientTestIn(BaseModel):
    #: A saved program whose key is used when ``secret`` is left out.
    client_id: int | None = None
    kind: ClientKind
    url: str = Field(min_length=1, max_length=1024)
    username: str = Field(default="", max_length=programs.USER_MAX)
    secret: str | None = Field(default=None, max_length=programs.SECRET_MAX + 10)
    category: str = Field(default=client_package.DEFAULT_CATEGORY, max_length=programs.CATEGORY_MAX + 10)


class ClientTestOut(BaseModel):
    version: str
    #: Whether the category was there before the test (it is made when it was not).
    category_existed: bool
    #: False for a program that has no categories (Transmission): nothing was made or read back, the jobs go to a
    #: folder of nexbooks below the download folder of the program.
    has_categories: bool
    category: str
    #: Where the program puts finished jobs of the category, as it names the folder; empty when it does not tell.
    folder: str


def _client_view(row: DownloadClient) -> ClientOut:
    state = "ready"
    if row.last_error:
        state = ("unreachable" if row.last_error in ("client_unreachable", "client_timeout", "client_blocked")
                 else "login" if row.last_error == "client_auth_failed" else "other")
    return ClientOut(id=row.id, name=row.name, kind=row.kind, protocol=programs.protocol_of(row),  # type: ignore[arg-type]
                     url=row.url, username=row.username, secret_set=bool(row.secret_enc), category=row.category,
                     has_categories=row.kind != "transmission", priority=row.priority, enabled=row.enabled,
                     version=row.version,
                     state=state, last_error=row.last_error, checked_at=row.checked_at)  # type: ignore[arg-type]


def _client(db: DbSession, client_id: int) -> DownloadClient:
    row = db.get(DownloadClient, client_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


@router.get("/clients", response_model=ClientsOut, summary="The download programs (the operator)")
def list_clients(_operator: OperatorAccount, db: DbSession) -> ClientsOut:
    rows = db.scalars(select(DownloadClient).order_by(DownloadClient.priority, DownloadClient.id))
    return ClientsOut(clients=[_client_view(row) for row in rows])


@router.post("/clients", response_model=ClientOut, status_code=201, summary="Add a download program (the operator)",
             responses=_errors(client_package.ERRORS))
def add_client(payload: ClientIn, operator: OperatorAccount, db: DbSession) -> ClientOut:
    """Tested first (version, login, the category made and read back); saved when that went through."""
    try:
        row, _checked = programs.create(db, kind=payload.kind, name=payload.name, url=payload.url,
                                        username=payload.username, secret=payload.secret, category=payload.category,
                                        priority=payload.priority, enabled=payload.enabled)
    except ClientError as exc:
        raise _from_client(exc) from exc
    logger.info("Download program added id=%s by=%s", row.id, operator.name)
    return _client_view(row)


@router.patch("/clients/{client_id}", response_model=ClientOut, summary="Change a download program (the operator)",
              responses=_errors(client_package.ERRORS))
def change_client(client_id: int, payload: ClientPatch, operator: OperatorAccount, db: DbSession) -> ClientOut:
    row = _client(db, client_id)
    try:
        programs.update(db, row, payload.model_dump(exclude_unset=True))
    except ClientError as exc:
        raise _from_client(exc) from exc
    logger.info("Download program changed id=%s by=%s", row.id, operator.name)
    return _client_view(row)


@router.delete("/clients/{client_id}", status_code=204, summary="Remove a download program (the operator)")
def remove_client(client_id: int, operator: OperatorAccount, db: DbSession) -> Response:
    programs.remove(db, _client(db, client_id))
    logger.info("Download program removed id=%s by=%s", client_id, operator.name)
    return Response(status_code=204)


@router.post("/clients/test", response_model=ClientTestOut, summary="Test the connection to a download program",
             responses=_errors(client_package.ERRORS))
def test_client_address(payload: ClientTestIn, _operator: OperatorAccount, db: DbSession) -> ClientTestOut:
    """Version, login, the category made and read back. Nothing is saved. A key left out is taken from the saved
    program named by ``client_id``."""
    programs.valid_kind(payload.kind)
    url = programs.valid_address(payload.url)
    user, secret = programs.valid_credentials(payload.kind, payload.username, payload.secret)
    category = programs.valid_category(payload.category)
    if not secret and payload.client_id is not None:
        saved = db.get(DownloadClient, payload.client_id)
        if saved is not None:
            _key_stays(saved.url, url, "secret")
            secret = programs.secret_of(saved)
    try:
        checked = programs.run_check(Target(kind=payload.kind, url=url, username=user, secret=secret or "",
                                            category=category))
    except ClientError as exc:
        raise _from_client(exc) from exc
    return ClientTestOut(version=checked.version, category_existed=checked.category_existed,
                         has_categories=checked.has_categories, category=category, folder=checked.folder or "")


@router.post("/clients/{client_id}/test", response_model=ClientTestOut,
             summary="Test a saved download program and keep what it says", responses=_errors(client_package.ERRORS))
def test_saved_client(client_id: int, _operator: OperatorAccount, db: DbSession) -> ClientTestOut:
    row = _client(db, client_id)
    try:
        checked = programs.test(db, row)
    except ClientError as exc:
        raise _from_client(exc) from exc
    return ClientTestOut(version=checked.version, category_existed=checked.category_existed,
                         has_categories=checked.has_categories, category=row.category, folder=checked.folder or "")


class MappingOut(BaseModel):
    id: int
    client_id: int
    client: str
    remote: str
    local: str


class MappingsOut(BaseModel):
    mappings: list[MappingOut]


@router.get("/path-mappings", response_model=MappingsOut,
            summary="The path mappings the operator confirmed (the operator)")
def list_mappings(_operator: OperatorAccount, db: DbSession) -> MappingsOut:
    names = {row.id: row.name for row in db.scalars(select(DownloadClient))}
    return MappingsOut(mappings=[MappingOut(id=row.id, client_id=row.client_id, client=names.get(row.client_id, ""),
                                            remote=row.remote, local=row.local) for row in programs.mappings(db)])


@router.delete("/path-mappings/{mapping_id}", status_code=204, summary="Remove a path mapping (the operator)")
def remove_mapping(mapping_id: int, _operator: OperatorAccount, db: DbSession) -> Response:
    row = db.get(PathMapping, mapping_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


# --- Formats and places ---------------------------------------------------------------------------------------------


class RankedFormat(BaseModel):
    f: str
    on: bool


class SizeRange(BaseModel):
    ebook: list[float] = Field(min_length=2, max_length=2)
    audio: list[float] = Field(min_length=2, max_length=2)


class KindChoice(BaseModel):
    ebook: str
    audio: str


class LibraryChoice(BaseModel):
    ebook: int | None
    audio: int | None


class FormatsIn(BaseModel):
    ebook: list[RankedFormat] = Field(max_length=20)
    audio: list[RankedFormat] = Field(max_length=20)
    #: The goal per kind: from this format on nexbooks looks for nothing better.
    target: KindChoice
    #: The languages accepted (codes) in the operator's order, the preferred first, with ``original`` at the place of
    #: the language the book was written in; and whether that one counts (``original``).
    languages: list[str] = Field(max_length=20)
    original: bool
    #: ``unabridged`` (only) or ``prefer`` (unabridged first); abridged and dramatized versions are allowed one by one.
    variant: Literal["unabridged", "prefer"]
    abridged: bool
    dramatized: bool
    #: Size of a release in MB, per kind: from, to.
    size: SizeRange
    library: LibraryChoice
    schema_: str = Field(alias="schema", max_length=schemes.MAX_SCHEME + 50)
    trash_days: int = Field(default=formats.TRASH_DAYS, ge=1, le=formats.TRASH_DAYS_MAX,
                            description="How many days what an upgrade replaced lies in the trash of its library.")


class LibraryRow(BaseModel):
    id: int
    name: str
    kind: Literal["ebook", "audio", "both"]
    path: str
    #: Whether nexbooks may write into it, and why not (``missing``, ``read_only``, ``no_permission``).
    writable: bool
    write_problem: str


class FormatsOut(FormatsIn):
    #: The libraries that can take the books; the formats the app knows, in the order of quality.
    libraries: list[LibraryRow]
    known_ebook: list[str]
    known_audio: list[str]
    known_languages: list[str]
    samples: list[dict[str, Any]]


def _formats_out(db: DbSession) -> FormatsOut:
    config = formats.config(db)
    rows = db.scalars(select(Library).order_by(Library.name))
    return FormatsOut.model_validate({
        **config, "libraries": [{"id": row.id, "name": row.name, "kind": row.kind, "path": row.path,
                                 "writable": (state := formats.write_state(row.path))[0], "write_problem": state[1]}
                                for row in rows],
        "known_ebook": list(formats.EBOOK_FORMATS), "known_audio": list(formats.AUDIO_FORMATS),
        "known_languages": list(newznab.LANGUAGE_WORDS), "samples": formats.samples()})


@router.get("/formats", response_model=FormatsOut,
            summary="Formats, languages, sizes, places and naming (the operator)")
def read_formats(_operator: OperatorAccount, db: DbSession) -> FormatsOut:
    return _formats_out(db)


@router.put("/formats", response_model=FormatsOut, summary="Save the formats and places (the operator)")
def save_formats(payload: FormatsIn, operator: OperatorAccount, db: DbSession) -> FormatsOut:
    formats.save(db, payload.model_dump(by_alias=True))
    logger.info("Formats and places saved by=%s", operator.name)
    # A goal lowered (or a ranking changed) may be reached already: those searches for a better format end now, not at
    # the planner's next housekeeping. A goal raised makes no new wish (an upgrade is looked for only where one was
    # wished).
    wishes.end_reached_upgrades(db)
    # A language taken off the list: the wishes in it rest and say so (the person turns them to the languages allowed).
    wishes.hold_unallowed(db)
    return _formats_out(db)


class NamingPreviewIn(BaseModel):
    schema_: str = Field(alias="schema", max_length=schemes.MAX_SCHEME + 50)
    sample: int = Field(default=0, ge=0, le=1000)


class NamingPreviewOut(BaseModel):
    ebook: str
    audio: str
    sample: int


@router.post("/formats/preview", response_model=NamingPreviewOut,
             summary="Where a made-up book would be put by a naming scheme (the operator)")
def preview_naming(payload: NamingPreviewIn, _operator: OperatorAccount, db: DbSession) -> NamingPreviewOut:
    try:
        return NamingPreviewOut(**formats.preview(db, payload.schema_, payload.sample))
    except schemes.SchemeError as exc:
        raise error("scheme_invalid", "That scheme cannot be used.", 422, reason=exc.code, detail=exc.detail,
                    field="schema") from exc


class CheckLibraryIn(BaseModel):
    library_id: int


class DownloadFolder(BaseModel):
    client: str
    folder: str
    #: Whether nexbooks can see that folder at the same path; then whether it lies on the file system of the library
    #: (a file can be linked, not copied), or None when it cannot be seen.
    seen: bool
    same_filesystem: bool | None


class CheckLibraryOut(BaseModel):
    writable: bool
    #: ``missing``, ``read_only``, ``no_permission`` or ``write_failed``; empty when it can be written.
    reason: str
    #: Whether files can be linked (hard links) inside the library; None when nothing could be written.
    hardlink: bool | None
    downloads: list[DownloadFolder]


@router.post("/formats/check-library", response_model=CheckLibraryOut,
             summary="Whether nexbooks may write into a library, and whether files can be linked (the operator)")
def check_library(payload: CheckLibraryIn, _operator: OperatorAccount, db: DbSession) -> CheckLibraryOut:
    """A probe file is made in the folder of the library, written and removed again. The folders the download
    programs use are looked at for the file system they lie on: on the same one, a file is linked instead of
    copied."""
    library = db.get(Library, payload.library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    result = formats.probe_library(library.path)
    downloads: list[DownloadFolder] = []
    if result["writable"]:
        for row in db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True))
                              .order_by(DownloadClient.priority, DownloadClient.id)):
            try:
                with client_package.open_client(programs.target_of(row)) as client:
                    folder = client.download_folder()
            except ClientError:
                continue
            if folder:
                seen = os.path.isdir(folder)
                downloads.append(DownloadFolder(client=row.name, folder=folder, seen=seen,
                                                same_filesystem=formats.same_filesystem(folder, library.path)
                                                if seen else None))
    return CheckLibraryOut(writable=result["writable"], reason=result["reason"], hardlink=result["hardlink"],
                           downloads=downloads)


# --- The automatic work
# ------------------------------------------------------------------------------------------------


class AutomationIO(BaseModel):
    #: The main switch: wishes are searched, loaded and filed by themselves.
    enabled: bool
    #: How often the newest entries of the indexers are read, in minutes.
    rss_minutes: Literal[5, 15, 30, 60]
    #: How long a wish that found nothing waits before it is searched again, in hours.
    retry_hours: Literal[1, 3, 6, 12, 24]
    #: Whether another release is taken after one failed, and how many tries a wish gets in 24 hours.
    replace: bool
    replace_max: int = Field(ge=1, le=10)
    #: What happens to a torrent after loading: ``ratio`` (until 1.0), ``7d`` (7 days), ``no``.
    seed: Literal["ratio", "7d", "no"]
    #: How old a Usenet release may be, in days; 0 takes the number the news servers of the programs report, else none.
    retention_days: int = Field(default=0, ge=0, le=20000)
    #: What the news servers of the programs report, in days (read-only: left out when saving); null for none.
    retention_read: int | None = Field(default=None, ge=0)


@router.get("/automation", response_model=AutomationIO, summary="The settings of the automatic work (the operator)")
def read_automation(_operator: OperatorAccount, db: DbSession) -> AutomationIO:
    from ..services.procure import retention

    return AutomationIO(**automation.config(db), retention_read=retention.read_days(db))


@router.put("/automation", response_model=AutomationIO,
            summary="Save the settings of the automatic work (the operator)")
def save_automation(payload: AutomationIO, operator: OperatorAccount, db: DbSession) -> AutomationIO:
    before = automation.config(db)
    saved = automation.save(db, payload.model_dump(exclude={"retention_read"}))
    if saved["retry_hours"] != before["retry_hours"]:
        # The wishes that wait for their next try are due that long after their last search now.
        wishes.retime(db)
    logger.info("Automation settings saved enabled=%s by=%s", saved["enabled"], operator.name)
    return AutomationIO(**saved)


class AutoIndexerOut(BaseModel):
    id: int
    name: str
    checked_at: datetime | None = Field(description="When its newest entries were read the last time.")
    next_at: datetime = Field(description="When they are read next (at once when never).")
    error: str = Field(description="The code of the failure of the last reading; ``rss_share_used`` when the half of "
                                   "its limit of the day that the reading may use is spent.")
    resting_until: datetime | None = Field(description="Until when the indexer is left alone (a request limit, its "
                                                       "limit of the day).")


class AutoRunIndexerOut(BaseModel):
    id: int
    name: str = Field(description="Its name now; empty when the indexer is gone.")
    requests: int
    entries: int
    error: str


class AutoRunOut(BaseModel):
    id: int
    kind: Literal["rss", "search", "handover"]
    started_at: datetime
    finished_at: datetime | None
    error: str
    indexers: list[AutoRunIndexerOut] = Field(description="The requests made and the entries read at each indexer.")
    wishes: int = Field(description="Wishes looked at (a reading) or settled (a search).")
    matched: int = Field(description="Wishes with a release among the newest entries (a reading).")
    loaded: int = Field(description="Releases handed to a download program.")
    handovers: int = Field(description="Hand-overs made again.")


class AutomationStatusOut(BaseModel):
    enabled: bool
    last_run_at: datetime | None = Field(description="The last time the automatic work looked at an indexer or did "
                                                     "something.")
    next_run_at: datetime | None = Field(description="The next time it comes back to an indexer or a wish; null when "
                                                     "the main switch is off or nothing waits.")
    due: int = Field(description="Wishes whose time to search again has come.")
    waiting_for_a_day: int = Field(description="Wishes for books that are not out yet.")
    wishes_open: int
    indexers: list[AutoIndexerOut]
    day_ends_at: datetime = Field(description="When the count of the day of the indexers starts again.")
    zone: str = Field(default="", description="The name of the instance's time zone (``Europe/Berlin``), empty when it "
                                              "has none (a fixed offset): the zone the day is counted in.")
    runs: list[AutoRunOut] = Field(description="What the automatic work did last, the newest first.")


@router.get("/automation/status", response_model=AutomationStatusOut,
            summary="What the automatic work did last and when it comes back (the operator)")
def automation_status(_operator: OperatorAccount, db: DbSession, limit: int = 30) -> AutomationStatusOut:
    shown = planner.status(db)
    names = {row.id: row.name for row in db.scalars(select(Indexer))}
    runs: list[AutoRunOut] = []
    for run in autolog.recent(db, limit):
        counts = run.counts if isinstance(run.counts, dict) else {}
        lines = counts.get("indexers") if isinstance(counts.get("indexers"), dict) else {}
        runs.append(AutoRunOut(
            id=run.id, kind=run.kind, started_at=run.started_at, finished_at=run.finished_at, error=run.error,  # type: ignore[arg-type]
            indexers=[AutoRunIndexerOut(
                id=int(key), name=names.get(int(key), ""), requests=int(value.get("requests", 0)),
                entries=int(value.get("entries", 0)), error=str(value.get("error", "")))
                for key, value in lines.items() if str(key).isdigit() and isinstance(value, dict)],
            wishes=int(counts.get("wishes", 0)), matched=int(counts.get("matched", 0)),
            loaded=int(counts.get("loaded", 0)), handovers=int(counts.get("handovers", 0))))
    return AutomationStatusOut(
        enabled=shown["enabled"], last_run_at=shown["last_run_at"], next_run_at=shown["next_run_at"],
        due=shown["due"], waiting_for_a_day=shown["waiting_for_a_day"], wishes_open=shown["wishes_open"],
        indexers=[AutoIndexerOut(**line) for line in shown["indexers"]], day_ends_at=shown["day_ends_at"],
        zone=shown["zone"], runs=runs)


# --- For everybody: where may I ask for books?
# -------------------------------------------------------------------------


class TargetOut(BaseModel):
    id: int
    name: str
    kind: Literal["ebook", "audio", "both"]
    writable: bool = Field(description="Whether nexbooks may write into the library.")
    write_problem: str = Field(description="``missing``, ``read_only`` or ``no_permission``; empty when it may write.")


class TargetsOut(BaseModel):
    targets: list[TargetOut]


@router.get("/targets", response_model=TargetsOut,
            summary="The libraries the caller may ask for books in (the right to acquire)")
def targets(account: Reader, db: DbSession) -> TargetsOut:
    rows = db.scalars(select(Library).where(Library.id.in_(library_service.acquirable_library_ids(account.id)))
                      .order_by(Library.name))
    out: list[TargetOut] = []
    for row in rows:
        ok, why = formats.write_state(row.path)
        out.append(TargetOut(id=row.id, name=row.name, kind=row.kind, writable=ok, write_problem=why))  # type: ignore[arg-type]
    return TargetsOut(targets=out)

