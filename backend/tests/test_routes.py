"""The interface is one: every route of the app is under ``/api/v1`` (and the few of the iPhone app under
``/api/app/1``, ``routers/app1.py``), every route that is not open says 401 without a sign-in, the description of the interface (``/api/v1/openapi.json``) is kept as a snapshot, and its page works under
the strict policy of the app.

When a route is added or changed on purpose, the snapshot is renewed by hand:

    NEXBOOKS_RECORD_OPENAPI=1 python -m pytest tests/test_routes.py

and the open routes below are named here if the new route is open to everybody (it almost never should be).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.models import Account

SNAPSHOT = Path(__file__).parent / "schema" / "openapi.json"

#: Routes that answer without a sign-in, on purpose: the state before and while signing in, the things the sign-in page
#: shows, the way back in (a link, a code), the provider's return, and the description of the interface itself.
OPEN = {
    ("GET", "/api/v1/health"),
    # The plugin of KOReader asks to register an account; it is told, with a message it shows, that accounts are made in
    # nexbooks (402). Nothing is made and nothing is read.
    ("POST", "/kosync/users/create"),
    ("GET", "/api/v1/about"),
    ("GET", "/api/v1/docs"),
    ("GET", "/api/v1/locales"),
    ("GET", "/api/v1/locales/{code}"),
    ("GET", "/api/v1/setup"),
    ("POST", "/api/v1/setup"),
    ("GET", "/api/v1/auth/methods"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/login/totp"),
    ("POST", "/api/v1/auth/login/totp/cancel"),
    ("POST", "/api/v1/auth/passkey/begin"),
    ("POST", "/api/v1/auth/passkey"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/forgot"),
    # Sign-in through a provider: the buttons, off to the provider, and back (the second return belongs to the entry
    # ``oidc``, the standard address of the shared module).
    ("GET", "/api/v1/oidc/providers"),
    ("GET", "/api/v1/oidc/{slug}/start"),
    ("GET", "/api/v1/oidc/{slug}/callback"),
    ("GET", "/api/v1/oidc/callback"),
    ("GET", "/api/v1/invite/{token}"),
    ("POST", "/api/v1/invite/{token}"),
    ("GET", "/api/v1/email/confirm/{token}"),
    ("POST", "/api/v1/email/confirm/{token}"),
    ("GET", "/api/v1/reset/{token}"),
    ("POST", "/api/v1/reset/{token}"),
    # The app fetches its first token with the secret or the code of a pairing it was given: there is no token
    # to show yet. Wrong ones count against the sender (``test_pairing_claim``).
    ("POST", "/api/app/1/pairing/{pairing_id}/claim"),
    ("POST", "/api/app/1/pairing/claim"),
    # A device without a camera asks for a code of its own and then asks how it stands, with its secret. The
    # code is confirmed by a person in a browser (``test_device_request``).
    ("POST", "/api/app/1/pairing/request"),
    ("GET", "/api/app/1/pairing/request/{request_id}"),
    ("DELETE", "/api/app/1/pairing/request/{request_id}"),
}


def concrete(path: str) -> str:
    """A path with its parameters filled in, so that the router finds the route."""
    return re.sub(r"\{([^}:]+)(?::[^}]*)?\}", lambda m: "1" if m.group(1).endswith("id") else "a" * 32, path)


def every_route() -> list[Any]:
    """The routes of the app, the included routers opened (this FastAPI keeps them as a router inside a router)."""
    found: list[Any] = []
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        found.extend(inner.routes if inner is not None else [route])
    return found


def api_routes() -> list[tuple[str, str]]:
    found = []
    for route in every_route():
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found.append((method, route.path))
    return sorted(found)


#: Where the routes of the app may lie: the interface of the browser and of programs, the surface of the iPhone app, and
#: the catalogue for reading apps (``/opds``: only ``GET``, signed in with ``Authorization: Basic``, no cookie), the
#: progress sync of KOReader (``/kosync``: signed in with two headers of its own, no cookie) and the sync of a Kobo
#: (``/kobo/<secret>``: signed in with the secret in the address, no cookie).
INTERFACES = ("/api/v1", "/api/app/1", "/opds", "/kosync", "/kobo")


def test_every_route_is_under_the_one_interface() -> None:
    outside = [route.path for route in every_route() if not str(getattr(route, "path", "")).startswith(INTERFACES)]
    # The frontend is served from files and mounted only where it was built (not in the tests).
    assert outside == [], f"Routes outside /api/v1: {outside}"
    assert len(api_routes()) > 80


def test_outside_the_interface_there_are_only_the_files_of_the_built_app(tmp_path: Path) -> None:
    """Where the interface is built and mounted, the routes outside ``/api/v1`` are counted: the folder of the scripts
    and styles, the manifest, and the one route that serves a file of the app (or its first page). Nothing else, and no
    route of this list ever changes anything (a ``POST`` outside the interface would be a way in around it)."""
    from fastapi import FastAPI

    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>nexbooks</title>", encoding="utf-8")
    built = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
    main._mount_frontend(built, dist)
    routes = [(sorted(getattr(route, "methods", None) or ["MOUNT"]), route.path) for route in built.routes]
    assert routes == [
        (["MOUNT"], "/assets"),
        (["GET"], "/manifest.webmanifest"),
        (["GET"], "/{path:path}"),
    ]
    with TestClient(built) as browser:
        assert browser.post("/anything", json={}).status_code == 405
        assert browser.get("/api/v1/nothing-here").status_code == 404


def test_the_app_has_one_health_route_and_no_second_one_outside() -> None:
    assert [path for method, path in api_routes() if path.endswith("health")] == ["/api/v1/health"]



def test_nothing_is_called_under_the_old_prefix() -> None:
    with TestClient(app, base_url="http://testserver") as client:
        for path in ("/api/auth/me", "/api/health", "/api/setup", "/api/about"):
            assert client.get(path).status_code == 404, path


def test_every_route_that_is_not_open_says_401_without_a_sign_in() -> None:
    wrong: list[str] = []
    open_found: set[tuple[str, str]] = set()
    with TestClient(app, base_url="http://testserver", headers={"X-Nexbooks-Client": "tab-routes000"}) as client:
        for method, path in api_routes():
            if (method, path) in OPEN:
                open_found.add((method, path))
                continue
            answer = client.request(method, concrete(path), json={} if method in ("POST", "PUT", "PATCH") else None)
            if answer.status_code != 401:
                wrong.append(f"{method} {path} -> {answer.status_code}")
    assert wrong == [], "Routes that do not ask for a sign-in (name them in OPEN if that is meant): " + "; ".join(wrong)
    stale = sorted(OPEN - open_found)
    assert stale == [], f"Named open here, but not routes any more: {stale}"


def test_a_device_token_that_is_wrong_is_401_on_every_route_that_asks_for_one() -> None:
    """The same list with a token that does not exist: still 401 (or 403 where only a browser may come)."""
    odd: list[str] = []
    with TestClient(app, base_url="http://testserver") as client:
        for method, path in api_routes():
            if (method, path) in OPEN or path == "/api/v1/me":
                continue
            answer = client.request(method, concrete(path), headers={"Authorization": "Bearer nbd_" + "w" * 43},
                                    json={} if method in ("POST", "PUT", "PATCH") else None)
            if answer.status_code not in (401, 403, 429):
                odd.append(f"{method} {path} -> {answer.status_code}")
    assert odd == [], odd


def _called(dependant: Any) -> set[Any]:
    """Every function a route asks for before it runs, the ones asked for by the ones asked for included."""
    found: set[Any] = set()
    for sub in dependant.dependencies:
        found.add(sub.call)
        found |= _called(sub)
    return found


def routes_asking_for(*guards: Any) -> list[tuple[str, str]]:
    found = []
    for route in every_route():
        if isinstance(route, APIRoute) and _called(route.dependant) & set(guards):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found.append((method, route.path))
    return sorted(found)


#: The routes a device token reaches (the sign-in state and the own profile, the push registration of the device):
#: everything else needs the browser. A new route lands here only on purpose.
TOKEN_ROUTES = {
    # The catalogue of an author by name, made when somebody opens it: a device may, a key only reads.
    ("POST", "/api/v1/catalog/authors"),
    # Watching an author: a device switches the watch on and off; a key only reads the list.
    ("PUT", "/api/v1/watching/authors/{token}"),
    ("DELETE", "/api/v1/watching/authors/{token}"),
    ("GET", "/api/v1/about/updates"),
    ("POST", "/api/v1/auth/setup/done"),
    ("POST", "/api/v1/auth/setup/codes"),
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/auth/sessions"),
    ("PUT", "/api/v1/me/profile"),
    ("POST", "/api/v1/me/whats-new/seen"),
    ("PUT", "/api/v1/me/language"),
    ("PUT", "/api/v1/me/preferences"),
    ("PUT", "/api/v1/auth/avatar"),
    ("DELETE", "/api/v1/auth/avatar"),
    ("GET", "/api/v1/avatars/{account_id}"),
    ("GET", "/api/v1/push"),
    ("POST", "/api/v1/push/devices"),
    ("POST", "/api/v1/push/devices/lookup"),
    ("PUT", "/api/v1/push/devices/{device_id}"),
    ("DELETE", "/api/v1/push/devices/{device_id}"),
    ("POST", "/api/v1/push/test"),
    # Reading: a device sets where its reader is and the bookmarks; a key only reads them.
    ("PUT", "/api/v1/editions/{edition_id}/progress"),
    ("POST", "/api/v1/editions/{edition_id}/bookmarks"),
    ("DELETE", "/api/v1/bookmarks/{bookmark_id}"),
    # Listening: the note of a bookmark.
    ("PATCH", "/api/v1/bookmarks/{bookmark_id}"),
    # Discover: the person's own wish list.
    ("PUT", "/api/v1/wishlist/{token}"),
    ("DELETE", "/api/v1/wishlist/{token}"),
    # The reading stack: a device enters a book as finished and takes the entry away; a key only reads.
    ("POST", "/api/v1/editions/{edition_id}/finished"),
    ("DELETE", "/api/v1/history/{entry_id}"),
    # Fetching books: a device searches, loads a release, blocks and unblocks one; a key only reads.
    ("POST", "/api/v1/procure/searches"),
    ("POST", "/api/v1/procure/searches/{search_id}/releases/{release_id}/load"),
    ("POST", "/api/v1/procure/searches/{search_id}/releases/{release_id}/block"),
    ("DELETE", "/api/v1/procure/blocklist/{entry_id}"),
    # Mailing an EPUB the caller may download to their Kindle: a device may (the address is set in a browser); a key
    # only reads.
    ("POST", "/api/v1/files/{file_id}/kindle"),
    # Fetching books: a device wishes for books, takes a wish away, searches again, and deals with a
    # download; a key only reads. The jobs nobody asked for are the operator's, in a browser.
    ("POST", "/api/v1/procure/wishes"),
    ("DELETE", "/api/v1/procure/wishes/{wish_id}"),
    ("POST", "/api/v1/procure/wishes/{wish_id}/search"),
    # A wish whose language is no longer allowed is turned to the languages allowed.
    ("POST", "/api/v1/procure/wishes/{wish_id}/language"),
    ("POST", "/api/v1/procure/downloads/{download_id}/cancel"),
    ("POST", "/api/v1/procure/downloads/{download_id}/recheck"),
    ("POST", "/api/v1/procure/downloads/{download_id}/library"),
    ("POST", "/api/v1/procure/downloads/{download_id}/stop-seeding"),
    # Checking and filing: a device answers the questions of the check, reads and undoes the notes of the
    # filing, brings back what is in the trash (deleting it for good is for the browser), and uploads books; a key only
    # reads.
    ("POST", "/api/v1/procure/downloads/{download_id}/decide"),
    ("POST", "/api/v1/procure/notes/{note_id}/acknowledge"),
    ("POST", "/api/v1/procure/notes/{note_id}/restore"),
    ("POST", "/api/v1/procure/trash/{entry_id}/restore"),
    ("POST", "/api/v1/uploads"),
    ("GET", "/api/v1/uploads/{upload_id}"),
    ("PUT", "/api/v1/uploads/{upload_id}/files"),
    ("POST", "/api/v1/uploads/{upload_id}/finish"),
    ("DELETE", "/api/v1/uploads/{upload_id}"),
}

#: The routes that only read and that a program with an API key reaches too (as the browser and a device do). A route
#: that changes something is never in this list.
READ_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/libraries"),
    ("GET", "/api/v1/libraries/{library_id}"),
    ("GET", "/api/v1/works"),
    ("GET", "/api/v1/works/stats"),
    ("GET", "/api/v1/works/{work_id}"),
    ("GET", "/api/v1/works/{work_id}/cover"),
    ("GET", "/api/v1/series/{series_id}"),
    ("GET", "/api/v1/people/{person_id}"),
    ("GET", "/api/v1/editions/{edition_id}/cover"),
    ("GET", "/api/v1/files/{file_id}/download"),
    ("GET", "/api/v1/search"),
    ("GET", "/api/v1/libraries/{library_id}/review"),
    ("GET", "/api/v1/review"),
    ("GET", "/api/v1/review/{entry_id}/file"),
    ("GET", "/api/v1/review/{entry_id}/file-cover"),
    ("GET", "/api/v1/files/{file_id}/content"),
    ("GET", "/api/v1/editions/{edition_id}"),
    ("GET", "/api/v1/progress"),
    ("GET", "/api/v1/editions/{edition_id}/progress"),
    ("GET", "/api/v1/editions/{edition_id}/bookmarks"),
    # Discover.
    ("GET", "/api/v1/discover/lists/{name}"),
    ("GET", "/api/v1/discover/for-you"),
    ("GET", "/api/v1/discover/series/{series_id}"),
    ("GET", "/api/v1/discover/books/{token}"),
    ("GET", "/api/v1/discover/books/{token}/cover"),
    ("GET", "/api/v1/wishlist"),
    # Browsing like in a bookshop: the themed shelves, one shelf opened, the shelves made for the caller.
    ("GET", "/api/v1/discover/browse"),
    ("GET", "/api/v1/discover/browse/personal"),
    ("GET", "/api/v1/discover/shelves/{key}"),
    # The reading stack, the authors and their photos.
    ("GET", "/api/v1/history"),
    ("GET", "/api/v1/stack"),
    ("GET", "/api/v1/authors"),
    ("GET", "/api/v1/people/{person_id}/photo"),
    # The catalogue of an author's works and the authors in the search: reading only.
    ("GET", "/api/v1/people/{person_id}/catalog"),
    ("GET", "/api/v1/catalog/authors/{token}"),
    ("GET", "/api/v1/catalog/authors/{token}/photo"),
    ("GET", "/api/v1/search/people"),
    # The authors the caller watches: reading only.
    ("GET", "/api/v1/watching"),
    # Fetching books: where the caller may ask for books.
    ("GET", "/api/v1/procure/targets"),
    # Fetching books: what may be looked for of a work, a search with its releases, the blocklist.
    ("GET", "/api/v1/procure/works/{work_id}"),
    ("GET", "/api/v1/procure/searches/{search_id}"),
    ("GET", "/api/v1/procure/blocklist"),
    # Fetching books: the wishes, what runs, what needs somebody, the history, the numbers, the options.
    ("GET", "/api/v1/procure/wishes"),
    ("GET", "/api/v1/procure/running"),
    ("GET", "/api/v1/procure/problems"),
    ("GET", "/api/v1/procure/downloads/{download_id}/look"),
    ("GET", "/api/v1/procure/downloads/{download_id}/files/{file_id}/content"),
    ("GET", "/api/v1/procure/history"),
    ("GET", "/api/v1/procure/counts"),
    ("GET", "/api/v1/procure/book-options"),
    # Checking and filing: the trash of what upgrades replaced.
    ("GET", "/api/v1/procure/trash"),
}


def test_the_routes_a_device_token_reaches_are_the_ones_named_here() -> None:
    reached = set(routes_asking_for(deps.require_account, deps.require_reader))
    assert reached == TOKEN_ROUTES | READ_ROUTES, (
        f"new: {sorted(reached - TOKEN_ROUTES - READ_ROUTES)}; gone: {sorted(TOKEN_ROUTES | READ_ROUTES - reached)}")


def test_the_routes_a_key_reaches_only_read() -> None:
    reading = set(routes_asking_for(deps.require_reader))
    assert reading == READ_ROUTES, f"new: {sorted(reading - READ_ROUTES)}; gone: {sorted(READ_ROUTES - reading)}"
    assert {method for method, _path in reading} <= {"GET"}


def test_no_operator_route_is_reached_with_a_device_token(client: TestClient, operator: Account) -> None:
    """The operator's routes are found in the route list, not named by hand, so a route added tomorrow is covered."""
    operator_routes = routes_asking_for(deps.require_operator)
    assert ("GET", "/api/v1/settings") in operator_routes and ("POST", "/api/v1/invites") in operator_routes
    assert len(operator_routes) > 20, "the guard is found on the routes (a floor: this list may grow, not shrink)"
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    odd: list[str] = []
    with TestClient(app, base_url="http://testserver") as device:
        for method, path in operator_routes:
            answer = device.request(method, concrete(path), headers={"Authorization": f"Bearer {token}"},
                                    json={} if method in ("POST", "PUT", "PATCH") else None)
            code = answer.json().get("detail", {}).get("code") if answer.headers.get("content-type", "").startswith(
                "application/json") else None
            if (answer.status_code, code) != (403, "browser_only"):
                odd.append(f"{method} {path} -> {answer.status_code} {code}")
    assert odd == [], odd
    # The same account in its browser still gets in.
    assert client.get("/api/v1/settings").status_code == 200


def test_the_description_of_the_interface_is_the_snapshot() -> None:
    current = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("NEXBOOKS_RECORD_OPENAPI") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8", newline="\n")
    assert SNAPSHOT.is_file(), "No snapshot of the description; record it (see the top of this file)."
    assert SNAPSHOT.read_text(encoding="utf-8") == current, (
        "The interface changed. If that is on purpose, renew the snapshot: "
        "NEXBOOKS_RECORD_OPENAPI=1 python -m pytest tests/test_routes.py (and commit the file)."
    )


def test_the_description_is_served_and_names_the_ways_in() -> None:
    with TestClient(app, base_url="http://testserver") as client:
        answer = client.get("/api/v1/openapi.json")
        assert answer.status_code == 200 and answer.json()["info"]["title"] == "nexbooks"
        assert all(path.startswith(("/api/v1/", "/api/app/1/")) for path in answer.json()["paths"])
        assert "HTTPBearer" in str(answer.json()["components"]["securitySchemes"])


def test_the_page_of_the_interface_needs_no_script_and_loads_nothing_from_elsewhere() -> None:
    with TestClient(app, base_url="http://testserver") as client:
        page = client.get("/api/v1/docs")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        text = page.text
        assert "<script" not in text and "http://" not in text and "https://" not in text
        assert "/api/v1/auth/login" in text and "Authorization: Bearer" in text
        policy = page.headers["content-security-policy"]
        assert "default-src 'self'" in policy and "script-src" not in policy


@pytest.mark.parametrize("path", ["/api/v1/me", "/api/v1/auth/me", "/api/v1/devices"])
def test_what_the_interface_answers_is_never_kept(path: str) -> None:
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get(path).headers["cache-control"] == "no-store"
