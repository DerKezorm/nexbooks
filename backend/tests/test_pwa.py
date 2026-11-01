"""The app on the home screen: the manifest with its name and colours, its shortcuts and the share target of Android,
the service worker for Web Push and for files shared from other apps (it answers that one form and keeps nothing else),
and a Content Security Policy that lets it register in the built app, where it counts."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.middleware import CSP
from app.services import locales
from app.services import manifest as manifest_service

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"


def test_the_policy_lets_the_service_worker_and_the_manifest_in(client: TestClient) -> None:
    policy = client.get("/api/v1/health").headers["content-security-policy"]
    assert "worker-src 'self'" in policy and "manifest-src 'self'" in policy
    assert "worker-src 'none'" not in policy and "'unsafe-eval'" not in policy
    assert policy.encode() == CSP


def test_the_manifest_names_the_app_and_its_shortcuts() -> None:
    """Shortcuts to the library and to Discover. No share target: nexbooks takes no files from other
    apps."""
    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "nexbooks" and manifest["short_name"] == "nexbooks"
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    assert manifest["background_color"] == manifest["theme_color"] == "#f3ede3"
    assert manifest["icons"][0]["src"] == "/logo.svg"
    # The file speaks English; the server names the shortcuts in the browser's language (below).
    assert [(item["name"], item["url"]) for item in manifest["shortcuts"]] == [
        ("Library", "/"), ("Discover", "/discover")]
    assert "share_target" not in manifest


def test_the_service_worker_serves_push_only() -> None:
    worker = (PUBLIC / "sw.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in worker.splitlines() if not line.lstrip().startswith("//"))
    assert "addEventListener('push'" in code and "addEventListener('notificationclick'" in code
    # No fetch handler: the worker never stands between the page and the server, and keeps nothing.
    assert "'fetch'" not in code and "respondWith" not in code
    for never in ("fetch(", "importScripts", "caches."):
        assert never not in code, never
    # A tap opens a path of nexbooks, never another site.
    assert "startsWith('//')" in code


def test_the_built_app_serves_the_worker_and_the_manifest_fresh(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>nexbooks</title>", encoding="utf-8")
    (dist / "sw.js").write_text((PUBLIC / "sw.js").read_text(encoding="utf-8"), encoding="utf-8")
    (dist / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
    built = FastAPI()
    main._mount_frontend(built, dist)
    with TestClient(built) as browser:
        worker = browser.get("/sw.js")
        assert worker.status_code == 200 and "javascript" in worker.headers["content-type"]
        assert worker.headers["cache-control"] == "no-cache"
        assert browser.get("/manifest.webmanifest").headers["cache-control"] == "no-cache"
        assert "cache-control" not in browser.get("/logo.svg").headers


def test_phones_get_png_symbols_and_notifications_a_png_icon_and_a_badge() -> None:
    """iPhone and Android want PNG for the home screen, a maskable one, and notifications draw no SVG."""
    import struct

    def size_of(name: str) -> tuple[int, int]:
        data = (PUBLIC / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        return struct.unpack(">II", data[16:24])

    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    pngs = {icon["src"]: icon for icon in manifest["icons"] if icon["type"] == "image/png"}
    assert {(src, icon["sizes"], icon["purpose"]) for src, icon in pngs.items()} == {
        ("/icon-192.png", "192x192", "any"), ("/icon-512.png", "512x512", "any"),
        ("/icon-maskable-512.png", "512x512", "maskable")}
    for src, icon in pngs.items():
        width, height = size_of(src.lstrip("/"))
        assert f"{width}x{height}" == icon["sizes"], src
    assert size_of("apple-touch-icon.png") == (180, 180) and size_of("badge-96.png") == (96, 96)
    index = (PUBLIC.parent / "index.html").read_text(encoding="utf-8")
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png" />' in index
    worker = (PUBLIC / "sw.js").read_text(encoding="utf-8")
    assert "icon: '/icon-192.png'" in worker and "badge: '/badge-96.png'" in worker


# --- The manifest in the language of the browser ------------------------------------------------------------

I18N = PUBLIC.parent / "src" / "i18n"


@pytest.fixture
def home_screen(tmp_path: Path) -> Iterator[TestClient]:
    """The built app with the real manifest, and an empty folder of the operator's languages."""
    folder = locales.locales_dir()
    folder.mkdir(parents=True, exist_ok=True)
    assert Path(os.environ["NEXBOOKS_LOCALES_DIR"]) == folder, "never empty a directory that is not the test's own"
    for old in folder.iterdir():
        old.unlink()
    locales.forget()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>nexbooks</title>", encoding="utf-8")
    (dist / "manifest.webmanifest").write_bytes((PUBLIC / "manifest.webmanifest").read_bytes())
    built = FastAPI()
    main._mount_frontend(built, dist)
    with TestClient(built) as browser:
        yield browser
    for old in folder.iterdir():
        old.unlink()
    locales.forget()


def shortcuts(browser: TestClient, accept: str | None) -> tuple[str, list[tuple[str, str]], dict]:
    answer = browser.get("/manifest.webmanifest", headers={"Accept-Language": accept} if accept is not None else {})
    assert answer.status_code == 200
    manifest = answer.json()
    return manifest["lang"], [(item["name"], item["short_name"]) for item in manifest["shortcuts"]], manifest


def test_the_manifest_names_the_shortcuts_in_the_language_of_the_browser(home_screen: TestClient) -> None:
    base = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    german = ("de", [("Bibliothek", "Bibliothek"), ("Entdecken", "Entdecken")])
    english = ("en", [("Library", "Library"), ("Discover", "Discover")])
    for accept, wanted in (
        ("de-DE,de;q=0.9,en;q=0.8", german),
        ("de", german),
        ("en-US,en;q=0.9,de;q=0.8", english),
        # The weight counts, not only the order; a language nexbooks does not have is passed over.
        ("en;q=0.2, fr;q=1, de;q=0.7", german),
        ("fr-FR, fr;q=0.9", english),
        ("", english),
        (None, english),
        ("*", english),
        ("de;q=0", english),
    ):
        language, names, manifest = shortcuts(home_screen, accept)
        assert (language, names) == wanted, accept
        # Everything else is the file as it was built: the icons, the share target, the addresses.
        assert {k: v for k, v in manifest.items() if k not in ("lang", "description", "shortcuts")} == {
            k: v for k, v in base.items() if k not in ("description", "shortcuts")}
        assert [item["url"] for item in manifest["shortcuts"]] == ["/", "/discover"]
    assert shortcuts(home_screen, "de")[2]["description"].startswith("Deine E-Books und Hörbücher")
    answer = home_screen.get("/manifest.webmanifest", headers={"Accept-Language": "de"})
    assert answer.headers["content-type"] == "application/manifest+json"
    assert answer.headers["cache-control"] == "no-cache" and answer.headers["vary"] == "Accept-Language"
    assert answer.headers["content-language"] == "de"


def test_a_language_the_operator_added_names_the_shortcuts_too(home_screen: TestClient) -> None:
    folder = locales.locales_dir()
    (folder / "es.json").write_text(json.dumps({
        "_meta": {"name": "Español"}, "save": "Guardar",
        # "libraryShort" left out: English; a name far too long is no name.
        "manifest": {"library": "Biblioteca", "discover": "Descubrir", "discoverShort": "x" * 500},
    }), encoding="utf-8")
    (folder / "de.json").write_text(json.dumps({"manifest": {"discover": "Stöbern"}}), encoding="utf-8")
    (folder / "nl.json").write_text("{ broken", encoding="utf-8")
    locales.forget()
    assert shortcuts(home_screen, "es-ES,es;q=0.9")[:2] == ("es", [("Biblioteca", "Library"), ("Descubrir", "Discover")])
    # A file for a shipped language lays itself over the shipped words.
    assert shortcuts(home_screen, "de")[:2] == ("de", [("Bibliothek", "Bibliothek"), ("Stöbern", "Entdecken")])
    # A broken file is no language nexbooks has.
    assert shortcuts(home_screen, "nl")[:2] == ("en", [("Library", "Library"), ("Discover", "Discover")])


def test_a_language_with_a_region_comes_before_the_language_without(home_screen: TestClient) -> None:
    folder = locales.locales_dir()
    (folder / "pt.json").write_text(json.dumps({"manifest": {"discover": "Descobrir", "discoverShort": "Ver"}}), encoding="utf-8")
    (folder / "pt-BR.json").write_text(json.dumps({"manifest": {"discover": "Explorar", "discoverShort": "Achar"}}),
                                       encoding="utf-8")
    locales.forget()
    brazil = ("pt-BR", [("Library", "Library"), ("Explorar", "Achar")])
    portugal = ("pt", [("Library", "Library"), ("Descobrir", "Ver")])
    for accept, wanted in (
        ("pt-BR,pt;q=0.9,en;q=0.8", brazil),
        ("pt-br", brazil),
        # A region nexbooks has no file for falls back to the language, a region it has does not leak to others.
        ("pt-PT,pt;q=0.9", portugal),
        ("pt", portugal),
        ("pt-BR;q=0.2, de;q=0.9", ("de", [("Bibliothek", "Bibliothek"), ("Entdecken", "Entdecken")])),
    ):
        language, names, _ = shortcuts(home_screen, accept)
        assert (language, names) == wanted, accept


@pytest.mark.parametrize(("accept", "wanted"), [
    # The weight in any case, and after other parameters of the entry.
    ("de;Q=0.2, en;q=0.9", "en"),
    ("de;level=1;q=0.2, en;q=0.5", "en"),
    # Every entry counts, not only the first twenty.
    (", ".join(f"x{i:02d}" for i in range(30)) + ", de", "de"),
    # A weight that is no number from 0 to 1 makes the entry broken; it counts not at all.
    ("de;q=abc, en;q=0.1", "en"),
    ("de;q=2, en;q=0.1", "en"),
    ("de;q=nan, en;q=0.1", "en"),
])
def test_the_manifest_reads_every_entry_and_the_weight_wherever_it_stands(home_screen: TestClient, accept: str,
                                                                          wanted: str) -> None:
    assert shortcuts(home_screen, accept)[0] == wanted, accept


def test_the_language_of_a_new_account_follows_the_weight_too() -> None:
    """The interface asks without naming its language: the browser's first language by weight that nexbooks has."""
    from starlette.requests import Request

    from app.routers.auth import interface_language

    def ask(accept: str) -> str:
        return interface_language(Request({"type": "http", "headers": [(b"accept-language", accept.encode())]}))

    assert ask("en;q=0.2, de;q=0.9") == "de"
    assert ask("fr, de;level=1;Q=0.5") == "de"
    assert ask("DE-at") == "de"
    assert ask("fr") == ""


def test_the_words_of_the_server_are_those_of_the_interface() -> None:
    """The template of a new language is the interface's en.json: its ``manifest`` section is what a translator sees."""
    for language, words in manifest_service.SHIPPED.items():
        shipped = json.loads((I18N / f"{language}.json").read_text(encoding="utf-8"))["manifest"]
        assert shipped == words, language
        assert set(words) == set(manifest_service.KEYS)
