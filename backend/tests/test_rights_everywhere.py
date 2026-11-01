"""The walls of every route, tried from outside in one place: a person who is not a member of a library, and the operator
who is not one either, get from every route that names a thing of that library (a work, an edition, a file, a cover, a
series, a person, an entry of the review, a bookmark, a finished book) exactly what a number nobody had gets, and no
list, count or search names its books. Each refused request is set beside the same request of the owner, which works:
a 404 that only came from a wrong address proves nothing.

The list of routes is checked against the app: a route with a number or a name in its address that is in neither list
below fails the test, so a route added later is tried here or says why it needs not be.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    OPERATOR,
    Account,
    AuthSession,
    BookFile,
    Bookmark,
    Edition,
    ForeignBook,
    HistoryEntry,
    LibraryMember,
    MatchEntry,
    Person,
    Work,
)
from app.services import apikeys, settings_service

from .bookmaker import cover_image, make_mp3
from .conftest import make_account, new_client
from .library_helpers import age, book, books_folder, join, make_library, run_jobs

# Made at run time: a literal that looks like a token would stop the commit guard.
WISH_TOKEN = secrets.token_hex(9)

SECRET = "Orchard Of Whispers"
SERIES = "Lantern Saga"
AUTHOR = "Ada Feldkamp"
WISH = "Quiet Harbour Unseen"


@dataclass
class Ids:
    library: int
    work: int
    ebook: int
    audio: int
    epub_file: int
    mp3_file: int
    series: int
    author: int
    entry: int
    bookmark: int
    history: int
    wish_token: str
    #: A second mark and a second entry, for the addresses of the app (the browser's cases take the first ones away).
    bookmark2: int = 0
    history2: int = 0


class World:
    """One library, ``Secret``, whose only member is ``owner`` (manage). ``tom`` is a member of nothing, ``ops`` is the
    operator and no member either, ``rita`` reads it (``read``)."""

    def __init__(self) -> None:
        folder = books_folder("secret")
        book(folder, "orchard.epub", title=SECRET, authors=(AUTHOR,), language="en", series=SERIES, series_pos=1,
             cover=cover_image((40, 90, 160)), text_bytes=20_000)
        part = make_mp3(folder / "Orchard Audio" / "01.mp3", 4.0, title=SECRET, album=SECRET, artist=AUTHOR)
        age(part)
        library_id = make_library(folder, "Secret", scan=True)
        self.owner = make_account("owner")
        self.tom = make_account("tom")
        self.rita = make_account("rita")
        with SessionLocal() as db:
            ops = db.scalar(select(Account).where(Account.role == OPERATOR, Account.name == "ops"))
            assert ops is not None
            self.ops = ops
            db.expunge(ops)
            db.query(LibraryMember).delete()
            db.add(LibraryMember(library_id=library_id, user_id=self.owner.id, right="manage"))
            db.add(LibraryMember(library_id=library_id, user_id=self.rita.id, right="read"))
            foreign = ForeignBook(token=WISH_TOKEN, ref="openlibrary:OL1W", title=WISH, authors=["Pia Example"],
                                  seen_at=datetime.now(UTC), created_at=datetime.now(UTC))
            db.add(foreign)
            db.commit()
        run_jobs()
        with SessionLocal() as db:
            ebook = db.scalars(select(Edition).where(Edition.kind == "ebook")).one()
            audio = db.scalars(select(Edition).where(Edition.kind == "audio")).one()
            work = db.get(Work, ebook.work_id)
            assert work is not None and work.series_id is not None
            files = {row.format: row.id for row in db.scalars(select(BookFile))}
            author = db.scalars(select(Person.id).where(Person.name == AUTHOR)).first()
            entry = db.scalars(select(MatchEntry.id).where(MatchEntry.edition_id == ebook.id)).first()
            assert author is not None and entry is not None, "the scan made no person or no entry of the review"
            ids = {"library": library_id, "work": work.id, "ebook": ebook.id, "audio": audio.id, "series": work.series_id,
                       "author": author, "entry": entry, "epub_file": files["epub"], "mp3_file": files["mp3"]}
        with new_client(self.owner) as owner:
            assert owner.put(f"/api/v1/editions/{ids['ebook']}/progress",
                             json={"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.4}).status_code == 200
            answer = owner.post(f"/api/v1/editions/{ids['ebook']}/bookmarks",
                                json={"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.4, "note": "mine"})
            assert answer.status_code in (200, 201), answer.text
            assert owner.post(f"/api/v1/editions/{ids['audio']}/finished", json={}).status_code in (200, 201)
            assert owner.put(f"/api/v1/wishlist/{WISH_TOKEN}").status_code in (200, 201, 204)
            second = owner.post(f"/api/v1/editions/{ids['ebook']}/bookmarks",
                                json={"locator": "epubcfi(/6/6!/4/2)", "fraction": 0.6, "note": "mine too"})
            assert second.status_code == 201, second.text
            read = owner.post(f"/api/v1/editions/{ids['ebook']}/finished", json={})
            assert read.status_code == 201, read.text
        with SessionLocal() as db:
            bookmark = db.scalars(select(Bookmark.id).where(Bookmark.note == "mine")).one()
            history = db.scalars(select(HistoryEntry.id).where(HistoryEntry.edition_id == ids["audio"])).one()
        self.ids = Ids(**ids, bookmark=bookmark, history=history, wish_token=WISH_TOKEN,
                       bookmark2=second.json()["id"], history2=read.json()["id"])


@pytest.fixture
def world() -> World:
    return World()


@dataclass(frozen=True)
class Case:
    method: str
    #: The route as the app names it, and the address with the numbers put in (``{...}`` from ``Ids``).
    route: str
    body: object = None
    #: What the owner gets for the same request: proof that the address is right.
    owner: tuple[int, ...] = (200,)
    #: Whether the operator, who is no member, may do this by their role (they manage the libraries themselves).
    operator_may: bool = False
    #: An address of the iPhone app (``/api/app/1``): asked with each one's device token instead of the browser.
    device: bool = False


def cases() -> list[Case]:
    return [
        Case("GET", "/api/v1/libraries/{library}"),
        Case("POST", "/api/v1/libraries/{library}/scan", {}, owner=(200, 202, 409), operator_may=True),
        Case("GET", "/api/v1/libraries/{library}/problems", operator_may=True),
        Case("GET", "/api/v1/libraries/{library}/review"),
        Case("GET", "/api/v1/works/{work}"),
        Case("GET", "/api/v1/works/{work}/cover"),
        Case("GET", "/api/v1/files/{epub_file}/download"),
        Case("GET", "/api/v1/files/{epub_file}/content"),
        Case("GET", "/api/v1/files/{mp3_file}/content"),
        Case("GET", "/api/v1/series/{series}"),
        Case("GET", "/api/v1/people/{author}"),
        Case("GET", "/api/v1/people/{author}/photo", owner=(404,)),
        Case("POST", "/api/v1/people/{author}/split", {"alias_id": 1}, owner=(404,)),
        # (The owner meets the same author, or an author whose books she cannot see: neither is joined.)
        Case("POST", "/api/v1/people/{author}/merge", {"other_id": 1, "keep": "this"}, owner=(404, 422)),
        # The catalogue of an author's works by the number of the person: only who sees a book of theirs.
        Case("GET", "/api/v1/people/{author}/catalog"),
        Case("GET", "/api/v1/editions/{ebook}"),
        Case("GET", "/api/v1/editions/{ebook}/cover"),
        Case("GET", "/api/v1/editions/{ebook}/progress"),
        Case("PUT", "/api/v1/editions/{ebook}/progress", {"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.1}),
        Case("GET", "/api/v1/editions/{ebook}/bookmarks"),
        Case("POST", "/api/v1/editions/{ebook}/bookmarks", {"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.1},
             owner=(200, 201)),
        Case("PATCH", "/api/v1/bookmarks/{bookmark}", {"note": "changed"}),
        Case("POST", "/api/v1/editions/{ebook}/finished", {}, owner=(200, 201)),
        # The list for the Kobo: only an edition of a library the caller is in can be put on it.
        Case("PUT", "/api/v1/editions/{ebook}/kobo"),
        Case("PATCH", "/api/v1/editions/{ebook}", {"genre": "krimi"}),
        Case("POST", "/api/v1/editions/{ebook}/unlink", owner=(200, 409)),
        Case("POST", "/api/v1/editions/{audio}/join", {"work_id": "{work}"}, owner=(200, 409)),
        Case("POST", "/api/v1/editions/{audio}/split", owner=(200, 409)),
        Case("POST", "/api/v1/review/{entry}/keep", owner=(200, 409)),
        Case("POST", "/api/v1/review/{entry}/accept", {"proposal_id": "none"}, owner=(404, 409)),
        Case("POST", "/api/v1/review/{entry}/search", {"title": "x"}, owner=(200, 409, 503)),
        Case("GET", "/api/v1/review/{entry}/cover?id=OL1W", owner=(404,)),
        # What the files of a book say, and the picture inside them: the same walls as the entry they belong to.
        Case("GET", "/api/v1/review/{entry}/file"),
        Case("GET", "/api/v1/review/{entry}/file-cover"),
        # Sending a book away that nexbooks fetched: the owner of the library is refused only because this one was not fetched.
        Case("POST", "/api/v1/editions/{ebook}/wrong-book", owner=(409,)),
        Case("GET", "/api/v1/discover/series/{series}"),
        Case("DELETE", "/api/v1/history/{history}", owner=(200, 204)),
        Case("DELETE", "/api/v1/bookmarks/{bookmark}", owner=(200, 204)),
        # Fetching books: what may be looked for of a work tells of the work only to who sees it.
        Case("GET", "/api/v1/procure/works/{work}"),
        # Moving an edition into another library: the options name the libraries and the files.
        Case("GET", "/api/v1/editions/{ebook}/move"),
        Case("POST", "/api/v1/editions/{ebook}/move", {"library_id": 1, "confirm": False}, owner=(422,)),
        # The surface of the iPhone app: the same walls, with a device token.
        Case("GET", "/api/app/1/works/{work}", device=True),
        Case("GET", "/api/app/1/works/{work}/cover", device=True),
        Case("GET", "/api/app/1/files/{epub_file}/content", device=True),
        Case("GET", "/api/app/1/files/{mp3_file}/content", device=True),
        Case("PUT", "/api/app/1/editions/{ebook}/progress", {"locator": "epubcfi(/6/2!/4/2)", "percent": 10},
             device=True),
        Case("GET", "/api/app/1/editions/{ebook}/bookmarks", device=True),
        Case("POST", "/api/app/1/editions/{ebook}/bookmarks", {"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.1},
             owner=(200, 201), device=True),
        Case("PATCH", "/api/app/1/bookmarks/{bookmark2}", {"note": "changed"}, device=True),
        Case("POST", "/api/app/1/editions/{ebook}/finished", {}, owner=(200, 201), device=True),
        Case("DELETE", "/api/app/1/history/{history2}", owner=(200, 204), device=True),
        Case("DELETE", "/api/app/1/bookmarks/{bookmark2}", owner=(200, 204), device=True),
        Case("PUT", "/api/app/1/later/{work}", owner=(200, 204), device=True),
        # An edition in the trash (last: the owner takes the recording off the shelf).
        Case("POST", "/api/v1/editions/{audio}/trash", owner=(200,)),
    ]


#: Routes with something in their address that are not things of a library, each with the reason.
NOT_OF_A_LIBRARY = {
    # The person's own things: the id is looked up among the caller's own rows (tried in ``test_own_things``).
    "/api/v1/auth/sessions/{uid}", "/api/v1/api-keys/{key_id}", "/api/v1/devices/{uid}",
    "/api/v1/push/devices/{device_id}", "/api/v1/auth/passkeys/{uid}/remove",
    # A connection for an e-reader is the account's own (a browser only; ``test_ereaders``).
    "/api/v1/ereaders/links/{link_id}",
    # The operator blocks or deletes a connection by its number, which names no library (``test_ereaders``).
    "/api/v1/admin/ereaders/links/{link_id}", "/api/v1/admin/ereaders/links/{link_id}/block",
    # Mailing a file to a Kindle has the rights of the download: a file of a library the caller is not in is a thing that
    # is not there (``test_kindle``, which sets the address and the mail server up first, as the route asks for them).
    "/api/v1/files/{file_id}/kindle",
    # The catalogue for reading apps and the sync of KOReader answer to a connection of an account and show no more
    # than the account sees: a library, a book, a cover, a file or a checksum of one the account is not in is a thing
    # that is not there (``test_opds``, ``test_kosync``).
    "/opds/authors/{person_id}", "/opds/files/{file_id}/{name}", "/opds/libraries/{library_id}",
    "/opds/series/{series_id}", "/opds/works/{work_id}/cover", "/opds/works/{work_id}/thumbnail",
    "/kosync/syncs/progress/{document}",
    # Taking an edition off the own list for the Kobo (the list is the caller's own, looked up among their rows; the
    # answer is the same for an edition that is not on it: ``test_kobo``).
    "/api/v1/editions/{edition_id}/kobo",
    # The sync of a Kobo answers to the secret in its address and shows no more than the account sees: the metadata, the
    # place, the file and the cover of a book that is not on the person's list, or that they do not see, are a thing
    # that is not there (``test_kobo``).
    "/kobo/{token}", "/kobo/{token}/{rest:path}", "/kobo/{token}/v1/initialization", "/kobo/{token}/v1/auth/device",
    "/kobo/{token}/v1/auth/refresh", "/kobo/{token}/v1/analytics/gettests", "/kobo/{token}/v1/user/loyalty/benefits",
    "/kobo/{token}/v1/library/tags",
    "/kobo/{token}/v1/library/sync", "/kobo/{token}/v1/library/{name}/metadata",
    "/kobo/{token}/v1/library/{name}/state", "/kobo/{token}/v1/library/{name}", "/kobo/{token}/download/{file}",
    "/kobo/{token}/image/{name}/{width}/{height}/{quality}/image.jpg",
    "/kobo/{token}/image/{name}/{width}/{height}/{quality}/{greyscale}/image.jpg",
    # Public data of the sources (the same for everybody; whether a person has the book is worked out per caller).
    "/api/v1/discover/books/{token}", "/api/v1/discover/books/{token}/cover", "/api/v1/discover/lists/{name}",
    # A themed shelf of the sources by its key: the same for everybody, what the caller has is worked out per
    # caller from the libraries they see (``test_browse_api``).
    "/api/v1/discover/shelves/{key}",
    "/api/v1/wishlist/{token}",
    # The catalogue of an author by its token: public data like the books of the sources; what the caller has
    # of it comes from their own libraries (``test_oeuvre_api`` tries a stranger, who sees nothing of theirs on it). A
    # move needs the operator or the right to manage a library, anywhere (``test_oeuvre_api``); the photo is the one of
    # Wikidata for an author nobody of the libraries is (``test_oeuvre_info``).
    "/api/v1/catalog/authors/{token}", "/api/v1/catalog/authors/{token}/works/{key}/kind",
    "/api/v1/catalog/authors/{token}/photo",
    # A search of the catalogues asked again by its ticket: the caller's own run (another person's ticket is not found,
    # ``test_booksearch``), books of the sources; what the caller has of them comes from their own libraries.
    "/api/v1/search/catalog/{ticket}",
    # The cover of a hit of the caller's own search, and the page of the hit made when the caller opens it (another
    # person's ticket is not found: ``test_booksearch_cases``).
    "/api/v1/search/catalog/{ticket}/covers/{key}", "/api/v1/search/catalog/{ticket}/books",
    # Watching an author: the watch is the caller's own (it is looked up among their rows; another person's watch of the
    # same author is neither read nor changed, ``test_watching`` tries it), and the author is public data.
    "/api/v1/watching/authors/{token}",
    # Pictures of the accounts of the same server: every signed-in person sees them, by design.
    "/api/v1/avatars/{account_id}",
    # The operator's side (the operator in a browser; ``test_routes`` keeps devices and keys away from it).
    "/api/v1/accounts/{account_id}", "/api/v1/accounts/{account_id}/sign-out", "/api/v1/accounts/{account_id}/block",
    "/api/v1/accounts/{account_id}/unblock", "/api/v1/accounts/{account_id}/role",
    "/api/v1/accounts/{account_id}/totp/reset", "/api/v1/accounts/{account_id}/reset-link",
    "/api/v1/invites/{invite_id}", "/api/v1/backups/{name}", "/api/v1/backups/{name}/check",
    "/api/v1/backups/{name}/restore", "/api/v1/backups/{name}/download", "/api/v1/locales/{code}",
    "/api/v1/admin/api-keys/{key_id}/block", "/api/v1/libraries/{library_id}/members",
    "/api/v1/oidc/admin/providers/{provider_id}", "/api/v1/oidc/admin/providers/{provider_id}/impact",
    "/api/v1/oidc/admin/accounts/{account_id}/links/{provider_id}",
    "/api/v1/organize/runs/{run_id}", "/api/v1/organize/runs/{run_id}/moves", "/api/v1/organize/runs/{run_id}/start",
    "/api/v1/organize/runs/{run_id}/undo",
    # Signing in, links in mails and the providers: open on purpose (``test_routes.OPEN``) or the caller's own.
    "/api/v1/invite/{token}", "/api/v1/email/confirm/{token}", "/api/v1/reset/{token}", "/api/v1/oidc/{slug}/start",
    "/api/v1/oidc/{slug}/callback", "/api/v1/oidc/{slug}/link",
    # Fetching books, the operator's side: indexers, download programs and their path mappings are the
    # server's, not a library's (``test_procure_*`` try them with the operator, a member and nobody).
    "/api/v1/procure/indexers/{indexer_id}", "/api/v1/procure/indexers/{indexer_id}/test",
    "/api/v1/procure/indexers/{indexer_id}/proposals",
    "/api/v1/procure/clients/{client_id}", "/api/v1/procure/clients/{client_id}/test",
    "/api/v1/procure/path-mappings/{mapping_id}",
    # Fetching books: a search and its releases are the caller's own (the id is looked up among their
    # searches, ``test_procure_search``, ``test_procure_loading``); an entry of the blocklist is shown and taken off only
    # by who may ask for books and sees its book (``test_procure_blocklist``).
    "/api/v1/procure/searches/{search_id}", "/api/v1/procure/searches/{search_id}/releases/{release_id}/load",
    "/api/v1/procure/searches/{search_id}/releases/{release_id}/block", "/api/v1/procure/blocklist/{entry_id}",
    # Fetching books: wishes, downloads and jobs nobody asked for belong to the libraries the caller may
    # ask for books in (the right ``acquire``, in the SQL of every answer; ``test_procure_wishes`` and
    # ``test_procure_downloads`` try them with the owner, another member, a reader and the operator).
    "/api/v1/procure/wishes/{wish_id}", "/api/v1/procure/wishes/{wish_id}/search",
    # ``test_procure_wish_language`` tries the language of a wish with a stranger.
    "/api/v1/procure/wishes/{wish_id}/language",
    "/api/v1/procure/downloads/{download_id}/cancel", "/api/v1/procure/downloads/{download_id}/recheck",
    "/api/v1/procure/downloads/{download_id}/library", "/api/v1/procure/downloads/{download_id}/stop-seeding",
    "/api/v1/procure/downloads/{download_id}/mapping", "/api/v1/procure/foreign/{job_id}",
    "/api/v1/procure/foreign/{job_id}/ignore",
    # Checking and filing: the answers to a download's questions follow the download's rights; a note is
    # the caller's own (looked up among their notes); the trash belongs to whoever looks after its library (``manage``);
    # an upload is its person's until it is filed; a foreign job is the operator's (``test_procure_filing_rights``,
    # ``test_procure_uploads`` try each with the owner, another member, a reader, a stranger and the operator).
    "/api/v1/procure/downloads/{download_id}/decide", "/api/v1/procure/notes/{note_id}/acknowledge",
    "/api/v1/procure/notes/{note_id}/restore", "/api/v1/procure/notes/{note_id}/drop",
    "/api/v1/procure/trash/{entry_id}",
    "/api/v1/procure/trash/{entry_id}/restore", "/api/v1/procure/foreign/{job_id}/assign",
    "/api/v1/uploads/{upload_id}", "/api/v1/uploads/{upload_id}/files", "/api/v1/uploads/{upload_id}/finish",
    # A move into another library is shown and tried again only for who manages its source or its target
    # (``test_relocate`` tries it with the owner, a reader, a stranger and the operator who is no member).
    "/api/v1/moves/{move_id}", "/api/v1/moves/{move_id}/retry",
    # The list for later: taking a book off it names the person's own entry and is no error for any number, so it says
    # nothing about a work (``test_app_later``). Putting one on it is tried above.
    "/api/app/1/later/{work_id}",
    # Connecting the iPhone app: a code is the person's own (looked up among their codes, another person's is
    # not found) and the claim of the app is open on purpose, its proof is the secret (``test_pairing``,
    # ``test_pairing_claim`` try them with the owner, another person and a stranger).
    "/api/v1/devices/pairing/{pairing_id}", "/api/app/1/pairing/{pairing_id}/claim",
    # A device without a camera: the request is the confirming person's own (looked up among theirs and
    # confirmed, another person's is not found), and the device's poll is open on purpose, its proof is the secret
    # (``test_device_request`` tries them with the owner, another person and a stranger).
    "/api/v1/devices/pair-requests/{request_id}", "/api/v1/devices/pair-requests/{request_id}/confirm",
    "/api/app/1/pairing/request/{request_id}",
    # The app takes back what it confirmed: only a request this very device confirmed is found, any
    # other is not (``test_device_request_chain`` tries it with the device that confirmed, another device of the same
    # person, another person's device and a browser).
    "/api/app/1/pairing/request/{request_id}/confirm",
    # Looking into the files of a download that waits for an answer: only the people who may ask for books in the library
    # the book goes into, only the download's own files (``test_procure_look`` tries them with the one who asked, a
    # member who only reads, a stranger, a person who looks after the library, and a file number of another download).
    "/api/v1/procure/downloads/{download_id}/look", "/api/v1/procure/downloads/{download_id}/files/{file_id}/content",
}
#: The routes of the operator's side that name a library and whose answer could carry a title.
OPERATOR_LIBRARY = {"/api/v1/libraries/{library_id}"}


def _address(world: World, route: str) -> str:
    return route.format(**{key: getattr(world.ids, key) for key in Ids.__dataclass_fields__})


def _body(world: World, body: object) -> object:
    if isinstance(body, dict):
        return {key: _address(world, value) if isinstance(value, str) and value.startswith("{") else value
                for key, value in body.items()}
    return body


def _ask(client: TestClient, world: World, case: Case) -> tuple[int, str]:
    body = _body(world, case.body)
    if isinstance(body, dict) and "work_id" in body:
        body = {**body, "work_id": int(body["work_id"])}
    answer = client.request(case.method, _address(world, case.route), json=body)
    return answer.status_code, answer.text


def _app_routes() -> set[tuple[str, str]]:
    from fastapi.routing import APIRoute as Route

    found: set[tuple[str, str]] = set()

    def walk(routes: list[object], prefix: str = "") -> None:
        for route in routes:
            if isinstance(route, Route):
                found.update((method, prefix + route.path) for method in route.methods)
                continue
            inner = getattr(route, "original_router", None) or getattr(route, "router", None)
            if inner is not None and hasattr(inner, "routes"):
                context = getattr(route, "include_context", None)
                walk(inner.routes, prefix + (getattr(context, "prefix", None) or getattr(route, "prefix", "") or ""))

    walk(app.routes)
    # A walk that found nothing would let every route through unseen.
    assert len(found) > 150, len(found)
    return found


def _route_of(case: Case) -> str:
    """The address of a case as the app names it (``{work}`` -> ``{work_id}``)."""
    names = {"library": "library_id", "work": "work_id", "ebook": "edition_id", "audio": "edition_id",
             "epub_file": "file_id", "mp3_file": "file_id", "series": "series_id", "author": "person_id",
             "entry": "entry_id", "bookmark": "bookmark_id", "history": "entry_id", "bookmark2": "bookmark_id",
             "history2": "entry_id"}
    route = case.route.split("?")[0]
    for short, long in names.items():
        route = route.replace("{" + short + "}", "{" + long + "}")
    return route


def test_every_route_with_something_in_its_address_is_tried_here_or_named_with_a_reason() -> None:
    tried = {(case.method, _route_of(case)) for case in cases()}
    missing = sorted(
        f"{method} {path}" for method, path in _app_routes()
        if "{" in path and (method, path) not in tried and path not in NOT_OF_A_LIBRARY | OPERATOR_LIBRARY
        and method != "HEAD")
    assert missing == [], missing


def test_nobody_outside_the_library_reaches_any_of_its_things_and_the_owner_does(world: World) -> None:
    from .app_helpers import app_of, device_token

    apps = [app_of(device_token(account)) for account in (world.tom, world.ops, world.owner)]
    with new_client(world.tom) as web_tom, new_client(world.ops) as web_ops, new_client(world.owner) as web_owner:
        for case in cases():
            tom, ops, owner = apps if case.device else (web_tom, web_ops, web_owner)
            for name, client in (("a stranger", tom), ("the operator", ops)):
                if name == "the operator" and case.operator_may:
                    continue
                status, text = _ask(client, world, case)
                where = f"{case.method} {case.route} as {name}"
                assert status in (403, 404), f"{where}: {status} {text[:200]}"
                assert SECRET not in text and AUTHOR not in text, where
            status, text = _ask(owner, world, case)
            assert status in case.owner, f"{case.method} {case.route} as the owner: {status} {text[:200]}"


def test_a_reader_changes_nothing_that_only_a_manager_may(world: World) -> None:
    with new_client(world.rita) as rita:
        for method, route, body in (
            ("PATCH", "/api/v1/editions/{ebook}", {"genre": "krimi"}),
            ("POST", "/api/v1/editions/{ebook}/unlink", None),
            ("POST", "/api/v1/editions/{audio}/split", None),
            ("POST", "/api/v1/editions/{ebook}/trash", None),
            ("POST", "/api/v1/review/{entry}/keep", None),
            ("POST", "/api/v1/libraries/{library}/scan", {}),
        ):
            answer = rita.request(method, _address(world, route), json=body)
            assert answer.status_code in (403, 404), f"{method} {route}: {answer.status_code}"
        # What she may: read the book and keep her own place in it.
        assert rita.get(_address(world, "/api/v1/works/{work}")).status_code == 200
        assert rita.put(_address(world, "/api/v1/editions/{ebook}/progress"),
                        json={"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.2}).status_code == 200


def test_no_list_count_or_search_names_a_book_of_a_library_the_caller_is_not_in(world: World) -> None:
    lists = ["/api/v1/works", "/api/v1/works?view=list", "/api/v1/works/stats", f"/api/v1/search?q={SECRET[:7]}",
             "/api/v1/search?q=Feldkamp", "/api/v1/progress", "/api/v1/history", "/api/v1/stack", "/api/v1/authors",
             "/api/v1/wishlist", "/api/v1/discover/for-you", "/api/v1/libraries"]
    with new_client(world.tom) as tom, new_client(world.ops) as ops, new_client(world.owner) as owner:
        seen_by_owner = "".join(owner.get(path).text for path in lists)
        assert SECRET in seen_by_owner and WISH in seen_by_owner, "the owner's lists do not name the book"
        for name, client in (("a stranger", tom), ("the operator", ops)):
            for path in lists:
                answer = client.get(path)
                assert answer.status_code == 200, f"{path} as {name}: {answer.status_code}"
                assert SECRET not in answer.text and AUTHOR not in answer.text and WISH not in answer.text, \
                    f"{path} as {name}"
        # The operator's own pages of the libraries name the library and its numbers, not its books.
        for path in ("/api/v1/libraries/admin", f"/api/v1/libraries/{world.ids.library}", "/api/v1/storage"):
            answer = ops.get(path)
            assert SECRET not in answer.text and AUTHOR not in answer.text, path


def test_own_things_of_another_person_are_not_found_and_stay(world: World) -> None:
    with new_client(world.owner) as owner, new_client(world.tom) as tom:
        with SessionLocal() as db:
            uid = db.scalars(select(AuthSession.uid).where(AuthSession.account_id == world.owner.id)).first()
        assert tom.delete(f"/api/v1/auth/sessions/{uid}").status_code == 404
        assert owner.get("/api/v1/auth/me").status_code == 200
        made = owner.post("/api/v1/devices", json={"name": "Tablet"})
        assert made.status_code in (200, 201), made.text
        device_uid = owner.get("/api/v1/devices").json()[0]["id"]
        assert tom.delete(f"/api/v1/devices/{device_uid}").status_code == 404
        assert [row["id"] for row in owner.get("/api/v1/devices").json()] == [device_uid]


def test_a_key_reads_a_real_route_only_without_an_origin(world: World) -> None:
    """The wall of ``require_reader`` for programs, on routes that read books (not only on ``/api/v1/me``): no web page
    may use a key, whatever it asks for."""
    apikeys.forget()
    with new_client(world.ops) as ops:
        assert ops.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    with new_client(world.owner) as owner:
        made = owner.post("/api/v1/api-keys", json={"name": "script"})
        assert made.status_code == 201, made.text
        key = made.json()["secret"]
    with TestClient(app, base_url="http://testserver") as program:
        bearer = {"Authorization": f"Bearer {key}"}
        for path in ("/api/v1/works", f"/api/v1/works/{world.ids.work}", f"/api/v1/files/{world.ids.epub_file}/content"):
            assert program.get(path, headers=bearer).status_code == 200, path
            refused = program.get(path, headers={**bearer, "Origin": "https://example.com"})
            assert refused.status_code == 403 and refused.json()["detail"]["code"] == "origin_refused", path
    apikeys.forget()


def test_tidying_wants_an_operator_who_manages_the_library_as_a_member(world: World) -> None:
    """A preview names the books and their files: an operator who is no member of a library gets none, nor a run of it."""
    with SessionLocal() as db:
        settings_service.save(db, {"organize_enabled": True})
    library = world.ids.library
    preview = {"library_id": library, "scheme": "{Author}/{Title}"}
    with new_client(world.ops) as ops:
        assert ops.get(f"/api/v1/organize/status?library={library}").status_code == 404
        assert ops.post("/api/v1/organize/preview", json=preview).status_code == 404
        join(library, world.ops.id, "read")
        assert ops.post("/api/v1/organize/preview", json=preview).status_code == 403
        join(library, world.ops.id, "manage")
        made = ops.post("/api/v1/organize/preview", json=preview)
        assert made.status_code == 201, made.text
        run = made.json()["id"]
        assert SECRET in ops.get(f"/api/v1/organize/runs/{run}/moves").text
        with SessionLocal() as db:
            db.query(LibraryMember).filter_by(library_id=library, user_id=world.ops.id).delete()
            db.commit()
        for method, path, body in (("GET", f"/api/v1/organize/runs/{run}", None),
                                   ("GET", f"/api/v1/organize/runs/{run}/moves", None),
                                   ("POST", f"/api/v1/organize/runs/{run}/start", {"confirm": True}),
                                   ("POST", f"/api/v1/organize/runs/{run}/undo", {"confirm": True}),
                                   ("DELETE", f"/api/v1/organize/runs/{run}", None),
                                   ("GET", f"/api/v1/organize/status?library={library}", None)):
            answer = ops.request(method, path, json=body)
            assert answer.status_code == 404 and SECRET not in answer.text, f"{method} {path}: {answer.status_code}"


def test_an_acquirer_reads_like_a_reader_and_changes_nothing_a_manager_alone_may(world: World) -> None:
    """The right to acquire sits between reading and managing: it includes reading, and it is no right to
    look after the books of a library (what a book is called, which book it is, a scan, the list of problems)."""
    sam = make_account("sam")
    join(world.ids.library, sam.id, "acquire")
    with new_client(sam) as client:
        for method, route, body in (
            ("PATCH", "/api/v1/editions/{ebook}", {"genre": "krimi"}),
            ("POST", "/api/v1/editions/{ebook}/unlink", None),
            ("POST", "/api/v1/editions/{audio}/split", None),
            ("POST", "/api/v1/review/{entry}/keep", None),
            ("POST", "/api/v1/libraries/{library}/scan", {}),
            ("GET", "/api/v1/libraries/{library}/problems", None),
        ):
            answer = client.request(method, _address(world, route), json=body)
            assert answer.status_code == 403, f"{method} {route}: {answer.status_code}"
        # What an acquirer may: read the book, keep their own place in it, see the library as theirs.
        assert client.get(_address(world, "/api/v1/works/{work}")).status_code == 200
        assert client.put(_address(world, "/api/v1/editions/{ebook}/progress"),
                          json={"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.2}).status_code == 200
        mine = [item for item in client.get("/api/v1/libraries").json() if item["id"] == world.ids.library]
        assert [item["my_right"] for item in mine] == ["acquire"]
        assert client.get("/api/v1/procure/targets").json() == {"targets": [
            {"id": world.ids.library, "name": "Secret", "kind": "both", "writable": True, "write_problem": ""}]}


def test_where_one_may_ask_for_books_is_where_one_has_the_right_to_acquire_or_manage(world: World) -> None:
    sam = make_account("sam")
    join(world.ids.library, sam.id, "acquire")
    shown = {}
    for name, account in (("owner", world.owner), ("rita", world.rita), ("tom", world.tom), ("ops", world.ops),
                          ("sam", sam)):
        with new_client(account) as client:
            shown[name] = [item["id"] for item in client.get("/api/v1/procure/targets").json()["targets"]]
    assert shown == {"owner": [world.ids.library], "rita": [], "tom": [], "ops": [], "sam": [world.ids.library]}
    assert SECRET not in str(shown)

