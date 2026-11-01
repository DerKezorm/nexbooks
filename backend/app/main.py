"""Entry point: the FastAPI app, routers, background tasks, the built frontend."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, private
from .config import get_settings
from .db import SessionLocal, init_db
from .errors import detail
from .middleware import (
    FileGateMiddleware,
    ForwardedProtoMiddleware,
    GuardMiddleware,
    RequestContextMiddleware,
    SignInGate,
    unhandled_error,
)
from .routers import (
    about,
    apikeys,
    app1,
    auth,
    authors,
    devices,
    docs,
    emails,
    ereaders,
    health,
    history,
    invites,
    kobo,
    libraries,
    oidc,
    opds,
    organize,
    passkeys,
    procure,
    push,
    reading,
    relocate,
    resets,
    search,
    works,
)
from .routers import avatars as avatars_router
from .routers import backups as backups_router
from .routers import browse as browse_router
from .routers import catalog as catalog_router
from .routers import discover as discover_router
from .routers import (
    kosync as kosync_router,
)
from .routers import locales as locales_router
from .routers import logs as logs_router
from .routers import procure_downloads as procure_downloads_router
from .routers import procure_search as procure_search_router
from .routers import procure_uploads as procure_uploads_router
from .routers import procure_wishes as procure_wishes_router
from .routers import review as review_router
from .routers import settings as settings_router
from .routers import sources as sources_router
from .routers import storage as storage_router
from .routers import totp as totp_router
from .routers import v1 as v1_router
from .routers import watching as watching_router
from .security import HashingBusy, purge_sessions
from .services import (
    accounts,
    app_changes,
    backups,
    device_request,
    jobs,
    locales,
    logs,
    mounts,
    notices,
    oidc_store,
    pairing,
    people_info,
    pictures,
    scanner,
    settings_service,
    totp,
)
from .services import kosync as kosync_service
from .services import manifest as manifest_service
from .services import passkeys as passkeys_service
from .services import portraits as _portraits  # noqa: F401 - registers the job of the photos in the folders
from .services.browse import fetch as _browse_fetch  # noqa: F401 - registers the job of the themed shelves
from .services.discover import lists as discover_lists
from .services.discover import owned as owned_shelves
from .services.oeuvre import store as _oeuvre_store  # noqa: F401 - registers the job of the catalogue of an author
from .services.organize import run as _organize_run  # noqa: F401 - registers the job of tidying
from .services.procure import indexer_admin as procure_indexer_admin
from .services.procure import loading as procure_loading
from .services.procure import planner as planner_service
from .services.procure import prowlarr as prowlarr_service  # registers the job of the Prowlarr sync
from .services.procure import tracking as tracking_service
from .vendor import nexoidc

logger = logging.getLogger("nexbooks")

ROUTERS = [
    health, about, docs, locales_router, logs_router, auth, totp_router, oidc, invites, settings_router,
    backups_router, avatars_router, emails, apikeys, ereaders, devices, v1_router, app1, push, passkeys, resets,
    storage_router, libraries, works, sources_router, review_router, search, reading, discover_router, browse_router,
    history, authors, catalog_router, organize,
    procure, procure_search_router, procure_wishes_router, procure_downloads_router, procure_uploads_router,
    relocate, watching_router, opds, kosync_router, kobo,
]


def _read_log_mode() -> tuple[str, datetime | None]:
    with SessionLocal() as db:
        mode = settings_service.get(db, "log_mode")
        raw = settings_service.get(db, "log_mode_until")
    until = datetime.fromisoformat(raw).astimezone(UTC) if raw else None
    return str(mode or logs.DEFAULT_MODE), until


def _write_log_mode(mode: str, until: datetime | None) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"log_mode": mode, "log_mode_until": until.isoformat() if until else None})


async def _sweep_forever(stop: asyncio.Event) -> None:
    """Enrolments and sign-ins waiting for their second factor run out; what ran out goes from memory, ended
    sessions from the database once an hour (they stayed until someone showed them again)."""
    rounds = 0
    while not stop.is_set():
        totp.sweep()
        passkeys_service.sweep()
        # Shelves nobody looked at for a while leave memory (a server at rest goes back near where it started).
        owned_shelves.drop_stale()
        if rounds % 60 == 0:
            with SessionLocal() as db:
                purge_sessions(db)
                # What went more than 90 days ago is forgotten; a device away longer starts its library again.
                app_changes.prune(db)
        if rounds % 5 == 0:
            # The codes that connect the app: what ran out stays a few minutes for the browser that asks, then goes.
            with SessionLocal() as db:
                pairing.sweep(db)
                device_request.sweep(db)
        rounds += 1
        try:
            await asyncio.wait_for(stop.wait(), 60)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logs.setup()
    backups.apply_pending()
    init_db()
    # A hand-over a stop of the server left half done (no request is running now): over, and its wish searches again.
    with SessionLocal() as db:
        procure_loading.reap_sending(db)
        # Photos of authors that earlier versions took from sources without an author and a licence.
        people_info.drop_unlicensed(db)
        # A restored database: every device of the app starts its library again.
        app_changes.after_restore(db)
        # Indexers whose languages an older version saved by itself take the languages their caps state (once).
        procure_indexer_admin.adopt_stated_languages(db)
    # A restore that could not be put in place at this start: the operators hear of it once.
    backups.report_failed_restore()
    mounts.warn_at_start(get_settings().data_dir, get_settings().database_path)
    # The TLS context of the sign-in module, read once in a thread now: the first sign-in does not wait for it.
    await nexoidc.warm()
    private.tighten_all()
    logs.attach_store(_read_log_mode, _write_log_mode)
    logs.apply_stored_mode()
    settings = get_settings()
    if settings.locales_dir is not None:
        settings.locales_dir.mkdir(parents=True, exist_ok=True)
    added = locales.available()
    if added:
        logger.info("Languages added by the operator: %s", ", ".join(locale.code for locale in added))
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    settings.resolved_secret_key()
    if not settings.disable_background:
        tasks.append(asyncio.create_task(logs.run_forever(stop)))
        tasks.append(asyncio.create_task(backups.run_forever(stop)))
        tasks.append(asyncio.create_task(_sweep_forever(stop)))
        tasks.append(asyncio.create_task(jobs.run_forever(stop)))
        tasks.append(asyncio.create_task(jobs.run_forever(stop, lane="match")))
        tasks.append(asyncio.create_task(jobs.run_forever(stop, lane="procure")))
        tasks.append(asyncio.create_task(jobs.run_forever(stop, lane="catalog")))
        tasks.append(asyncio.create_task(jobs.run_forever(stop, lane="discover")))
        tasks.append(asyncio.create_task(scanner.run_forever(stop)))
        tasks.append(asyncio.create_task(kosync_service.run_forever(stop)))
        tasks.append(asyncio.create_task(discover_lists.run_forever(stop)))
        tasks.append(asyncio.create_task(prowlarr_service.run_forever(stop)))
        tasks.append(asyncio.create_task(tracking_service.run_forever(stop)))
        tasks.append(asyncio.create_task(planner_service.run_forever(stop)))
    logger.info("nexbooks %s started", __version__)
    with SessionLocal() as db:
        accounts.announce_setup_code(db)
    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(notices.settle, 10)
        logger.info("nexbooks stopped")


app = FastAPI(
    title="nexbooks",
    version=__version__,
    summary="The interface the web app speaks, and the one native apps and scripts use.",
    lifespan=lifespan,
    # The description is public and part of the interface (``tests/schema/openapi.json`` keeps it); the page that
    # lists it is ``routers/docs.py`` (the usual page loads its script from a CDN, which the policy of the app forbids).
    docs_url=None,
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
)
# Added last, runs first: the request id and headers wrap everything, the guard sits inside it, inside that the gate for
# the bytes of files (a turned-away request costs nothing: it has not asked the database who it is), and innermost the
# door before the sign-in (a request that cannot be anybody's is answered before the framework or the database).
app.add_middleware(SignInGate, routes=lambda: app.routes)
app.add_middleware(FileGateMiddleware)
app.add_middleware(GuardMiddleware)
app.add_middleware(RequestContextMiddleware)
# Outermost: the scheme a trusted proxy reports is the request's for everything inside (``request.base_url``).
app.add_middleware(ForwardedProtoMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [".".join(str(part) for part in error.get("loc", ()) if part != "body") for error in exc.errors()]
    return JSONResponse(
        status_code=422, content={"detail": detail("invalid_input", "The input is not valid.", fields=fields)}
    )


@app.exception_handler(OverflowError)
async def _too_large_a_number(_request: Request, _exc: OverflowError) -> JSONResponse:
    # A number past what SQLite holds (an id like 99999999999999999999 in the address): not a fault of the server.
    return JSONResponse(status_code=422, content={"detail": detail("invalid_input", "The input is not valid.")})


@app.exception_handler(HashingBusy)
async def _hashing_busy(_request: Request, _exc: HashingBusy) -> JSONResponse:
    # Every slot for checking a password stayed taken for 15 s: many sign-ins at once, as in a flood of guesses.
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": detail("busy", "The server is busy. Try again in a moment.")})


@app.exception_handler(pictures.DecodersBusy)
async def _decoders_busy(_request: Request, _exc: pictures.DecodersBusy) -> JSONResponse:
    # Every decoder stayed taken: many large photos at once. A moment later it works.
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": detail("busy", "The server is busy. Try again in a moment.")})


@app.exception_handler(UnicodeEncodeError)
async def _not_unicode(_request: Request, _exc: UnicodeEncodeError) -> JSONResponse:
    # JSON may carry a lone surrogate ("\ud800"); no file name, path or database text can hold it.
    return JSONResponse(status_code=422, content={"detail": detail("invalid_input", "The input is not valid.")})


@app.exception_handler(StarletteHTTPException)
async def _plain_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # FastAPI's own 400 for a body that is not JSON carries only a text; give it a code like every other answer.
    if exc.status_code == 400 and isinstance(exc.detail, str):
        return JSONResponse(status_code=400, content={"detail": detail("invalid_input", "The input is not valid.")})
    return await http_exception_handler(request, exc)


app.add_exception_handler(Exception, unhandled_error)
app.add_exception_handler(kosync_router.Refusal, kosync_router.refused)

for module in ROUTERS:
    app.include_router(module.router)


#: Files a browser asks the server about again each time: the service worker (an old one would keep its old ways for a
#: day). The manifest is asked about afresh too, and comes in the language of the browser (``_manifest`` below).
FRESH = ("sw.js",)
MANIFEST = "manifest.webmanifest"


#: The frame that shows a book (``frontend/readview.html``, the reader page draws the pages there). The reader has to
#: reach into the pages of the book (it turns them, finds the place, marks what a search found), which a browser allows
#: only between frames of one origin; so this frame has the origin of nexbooks, and what keeps a book away from the
#: session is that **no script runs in a page of a book**: the pages are cleaned before they are shown, each carries a
#: policy that forbids scripts, frames and every address but the book's own, and (outside Safari) the frame of each page
#: has no ``allow-scripts``. This policy is the frame's own and the pages inherit it: the frame fetches nothing
#: (the page hands it the book), scripts only from nexbooks itself, pictures, styles and fonts only out of the
#: book. It may not
#: open windows or forms or lead the top page away (``sandbox`` lacks ``allow-popups``, ``allow-forms``,
#: ``allow-top-navigation``).
READ_FRAME = "readview.html"
READ_FRAME_POLICY = (
    "sandbox allow-same-origin allow-scripts; default-src 'none'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' blob:; img-src blob: data:; font-src 'self' blob: data:; media-src 'none'; "
    "connect-src 'none'; frame-src blob:; worker-src 'none'; frame-ancestors 'self'; base-uri 'none'; "
    "form-action 'none'; object-src 'none'"
)
#: The frame that shows a PDF (``frontend/pdfview.html``, pdf.js draws the pages there): the same kind of sandbox, with
#: pdf.js's worker from a blob and WebAssembly for its image decoders, and no network at all.
PDF_FRAME = "pdfview.html"
PDF_FRAME_POLICY = (
    "sandbox allow-scripts; default-src 'none'; script-src 'self' 'wasm-unsafe-eval' blob:; worker-src blob:; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self' blob: data:; connect-src 'none'; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
)
FRAMES = {READ_FRAME: READ_FRAME_POLICY, PDF_FRAME: PDF_FRAME_POLICY}


class _AppFiles(StaticFiles):
    """The app's scripts and styles. They name their content and hold nothing private, so the frame of a PDF, which has
    no origin (``"null"``), may load them: module scripts, their styles and fonts are fetched with CORS."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response


def _mount_frontend(target: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():
        return
    if (dist / "assets").is_dir():
        target.mount("/assets", _AppFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()
    start_page = index.resolve()

    @target.get(f"/{MANIFEST}", include_in_schema=False, response_model=None)
    def _manifest(request: Request) -> Response:
        # The names of the shortcuts in the browser's language (services/manifest.py). The answer depends on
        # Accept-Language: a cache in between keeps one per language, the browser asks again each time.
        try:
            base = json.loads((dist / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        if not isinstance(base, dict):
            base = {}
        language = manifest_service.choose(request.headers.get("accept-language", ""))
        body = json.dumps(manifest_service.localized(base, language), ensure_ascii=False, indent=2)
        return Response(body, media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache", "Vary": "Accept-Language", "Content-Language": language})

    @target.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> FileResponse | JSONResponse:
        if path in ("api", "opds", "kosync", "kobo") or path.startswith(("api/", "opds/", "kosync/", "kobo/")):
            return JSONResponse(status_code=404, content={"detail": detail("not_found", "Not found.")})
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and root in candidate.parents and candidate != start_page:
            if path in FRESH:
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
            if path in FRAMES:
                return FileResponse(candidate, media_type="text/html", headers={
                    "Content-Security-Policy": FRAMES[path], "X-Frame-Options": "SAMEORIGIN",
                    "Cache-Control": "no-cache"})
            if not candidate.suffix:
                # A file with no ending (a LICENSE) is text, and a reader of it should see text, not get a download.
                return FileResponse(candidate, media_type="text/plain; charset=utf-8")
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


_mount_frontend(app, get_settings().frontend_dist)
# The shared sign-in module learns who it runs in, before the first request.
oidc_store.configure()
