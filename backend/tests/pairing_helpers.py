"""Helpers for the tests of connecting the iPhone app: a code made in a browser, the app that fetches with it, and
a clock the test moves by hand."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.main import app

ADDRESS = "https://books.example.com"


class Moving:
    """The clock of the app, moved by the test (``pass_by``); the whole app reads ``clock.now``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.offset = timedelta()
        start = clock.now()
        monkeypatch.setattr(clock, "now", lambda: start + self.offset)

    def pass_by(self, **kwargs: float) -> None:
        self.offset += timedelta(**kwargs)


def make(client: TestClient, address: str = ADDRESS) -> dict[str, Any]:
    """A pairing made in the browser; its answer holds the secret and the code once."""
    answer = client.post("/api/v1/devices/pairing", json={"address": address})
    assert answer.status_code == 201, answer.text
    return dict(answer.json())


def app_client() -> TestClient:
    """The app: no cookie of ours and no tab header."""
    return TestClient(app, base_url="http://testserver")


def claim_secret(app_box: TestClient, made: dict[str, Any], name: str = "Ada's iPhone") -> Any:
    return app_box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"], "device_name": name})


def claim_code(app_box: TestClient, code: str, name: str = "Ada's iPhone") -> Any:
    return app_box.post("/api/app/1/pairing/claim", json={"code": code, "device_name": name})


# --- A device without a camera: it asks for a code, a person confirms it in the browser ------------------


def ask(box: TestClient, name: str | None = "Living room TV") -> Any:
    """The device asks for a code of its own."""
    return box.post("/api/app/1/pairing/request", json={} if name is None else {"device_name": name})


def asked(box: TestClient | None = None, name: str | None = "Living room TV") -> dict[str, Any]:
    answer = ask(box or app_client(), name)
    assert answer.status_code == 201, answer.text
    return dict(answer.json())


def poll(box: TestClient, made: dict[str, Any], secret: str | None = None) -> Any:
    """The device asks how it stands, with its secret in the header."""
    return box.get(f"/api/app/1/pairing/request/{made['id']}",
                   headers={"X-Pairing-Secret": made["secret"] if secret is None else secret})


def look_up(browser: TestClient, code: str) -> Any:
    return browser.post("/api/v1/devices/pair-requests/lookup", json={"code": code})


def confirm(browser: TestClient, made: dict[str, Any], code: str | None = None) -> Any:
    return browser.post(f"/api/v1/devices/pair-requests/{made['id']}/confirm",
                        json={"code": made["code"] if code is None else code})


def standing(browser: TestClient, made: dict[str, Any]) -> Any:
    return browser.get(f"/api/v1/devices/pair-requests/{made['id']}")


def cancel(box: TestClient, made: dict[str, Any], secret: str | None = None) -> Any:
    """The device lets its request go."""
    return box.delete(f"/api/app/1/pairing/request/{made['id']}",
                      headers={"X-Pairing-Secret": made["secret"] if secret is None else secret})


def confirm_from_phone(phone: TestClient, made: dict[str, Any], code: str | None = None,
                       request_id: str | None = None) -> Any:
    """The iPhone app confirms the request of a device without a camera, with its own device token."""
    return phone.post("/api/app/1/pairing/request/confirm",
                      json={"id": made["id"] if request_id is None else request_id,
                            "code": made["code"] if code is None else code})
