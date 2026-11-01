"""The frames that show a book and a PDF: what the server hands them and what it forbids them.

A book is HTML, CSS and pictures made by somebody else. It is drawn in a frame of its own that has no network (so it can
call nobody) and no way to open windows, and whose policy its pages inherit; no script runs in a page of a book. The page
that holds the frame hands it the bytes. These tests keep the policy the way it was thought out: a looser one fails here,
whoever loosens it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.main import FRAMES, PDF_FRAME, PDF_FRAME_POLICY, READ_FRAME, READ_FRAME_POLICY
from app.middleware import CSP


def policy(text: str) -> dict[str, list[str]]:
    parts = [part.strip() for part in text.split(";") if part.strip()]
    return {part.split()[0]: part.split()[1:] for part in parts}


@pytest.fixture
def served(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (dist / READ_FRAME).write_text("<!doctype html><title>read</title>", encoding="utf-8")
    (dist / PDF_FRAME).write_text("<!doctype html><title>pdf</title>", encoding="utf-8")
    (dist / "assets" / "a.js").write_text("export {}", encoding="utf-8")
    (dist / "pdfjs").mkdir()
    (dist / "pdfjs" / "LICENSE").write_text("Apache License", encoding="utf-8")
    app = FastAPI()
    main._mount_frontend(app, dist)
    return TestClient(app)


def test_the_frames_come_with_their_own_policy_and_only_the_app_may_hold_them(served: TestClient) -> None:
    for name, wanted in FRAMES.items():
        answer = served.get(f"/{name}")
        assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/html")
        assert answer.headers["content-security-policy"] == wanted
        assert answer.headers["x-frame-options"] == "SAMEORIGIN"
        assert answer.headers["cache-control"] == "no-cache"
    # The app itself carries the policy of the middleware, not one of the frames.
    assert "content-security-policy" not in served.get("/").headers


def test_a_license_file_without_an_ending_is_shown_as_text(served: TestClient) -> None:
    answer = served.get("/pdfjs/LICENSE")
    assert answer.status_code == 200 and answer.text == "Apache License"
    assert answer.headers["content-type"] == "text/plain; charset=utf-8"


def test_the_scripts_of_the_app_may_be_loaded_by_a_frame_without_an_origin(served: TestClient) -> None:
    asset = served.get("/assets/a.js")
    assert asset.status_code == 200 and asset.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-origin" not in served.get(f"/{READ_FRAME}").headers
    assert "access-control-allow-origin" not in served.get("/").headers


def test_the_frame_of_a_book_has_no_network_no_popups_and_no_scripts_but_its_own() -> None:
    rules = policy(READ_FRAME_POLICY)
    # The origin of nexbooks (the reader reaches into the pages), but no popups, no forms, no navigation of the top page.
    assert set(rules["sandbox"]) == {"allow-same-origin", "allow-scripts"}
    assert rules["default-src"] == ["'none'"]
    assert rules["connect-src"] == ["'none'"]
    assert rules["script-src"] == ["'self'"], "no inline script, no eval, no blob, no other host"
    assert rules["worker-src"] == ["'none'"]
    assert set(rules["img-src"]) == {"blob:", "data:"}, "a picture of a book comes out of the book, never from an address"
    assert set(rules["font-src"]) == {"'self'", "blob:", "data:"}
    assert rules["media-src"] == ["'none'"]
    assert rules["frame-src"] == ["blob:"]
    assert set(rules["style-src"]) == {"'self'", "'unsafe-inline'", "blob:"}
    assert rules["frame-ancestors"] == ["'self'"]
    assert rules["form-action"] == ["'none'"] and rules["base-uri"] == ["'none'"] and rules["object-src"] == ["'none'"]
    for sources in rules.values():
        assert not any(source.startswith(("http:", "https:", "*", "ws:", "wss:")) for source in sources)


def test_the_frame_of_a_pdf_has_no_origin_and_no_network() -> None:
    rules = policy(PDF_FRAME_POLICY)
    assert rules["sandbox"] == ["allow-scripts"]
    assert rules["default-src"] == ["'none'"] and rules["connect-src"] == ["'none'"]
    assert "'unsafe-eval'" not in rules["script-src"] and "'unsafe-inline'" not in rules["script-src"]
    assert rules["frame-ancestors"] == ["'self'"]


def test_the_app_itself_may_hold_the_frames_and_nothing_from_outside() -> None:
    rules = policy(CSP.decode())
    # The page that holds the frame gets frames from nexbooks only (``default-src 'self'``), and no scripts but its own.
    assert "frame-src" not in rules and rules["default-src"] == ["'self'"]
    assert "script-src" not in rules, "scripts follow default-src: nexbooks itself"
    assert rules["connect-src"] == ["'self'"]
    assert rules["frame-ancestors"] == ["'none'"]
