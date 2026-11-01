"""Requests a stranger writes by hand, against the routes that hand out bytes: ranges that overlap, run backwards, ask
for nothing or for a hundred pieces; addresses that climb out of the folder of the built app. None of them gets more
than the file, an answer of the server's fault, or a byte from outside."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from .conftest import new_client
from .test_rights_everywhere import World, world  # noqa: F401 - the fixture

FILE = "/api/v1/files/{}/content"


def test_ranges_written_by_hand_get_the_file_a_part_of_it_or_a_plain_refusal(world: World) -> None:  # noqa: F811
    with new_client(world.owner) as owner:
        path = FILE.format(world.ids.epub_file)
        whole = owner.get(path).content
        size = len(whole)
        assert size > 1000

        def ask(value: str) -> tuple[int, bytes]:
            answer = owner.get(path, headers={"Range": value})
            assert answer.status_code < 500, value
            return answer.status_code, answer.content

        # Overlapping pieces are one piece, as many times as asked.
        assert ask("bytes=0-5,3-9") == (206, whole[:10])
        assert ask(",".join(["bytes=0-9"] + ["0-9"] * 99)) == (206, whole[:10])
        # A hundred and one pieces: the file once, as it is (SECURITY.md).
        status, body = ask("bytes=" + ",".join(f"{n * 2}-{n * 2}" for n in range(101)))
        assert (status, body) == (200, whole)
        # A hundred apart: never more than the pieces and their heads.
        status, body = ask("bytes=" + ",".join(f"{n * 10}-{n * 10}" for n in range(100)))
        assert status == 206 and len(body) < 100 + 100 * 200
        # Backwards, a negative start, another unit, nothing at all, past the end.
        assert ask("bytes=9-2")[0] == 400
        assert ask("bytes=--5")[0] == 416
        assert ask(f"bytes={size}-")[0] == 416
        assert ask("items=0-5")[0] == 400
        assert ask("bytes=")[0] == 400
        assert ask("bytes=x-y")[0] == 400
        # A start written with ten thousand digits is no number Python reads: no range, no fault of the server.
        assert ask("bytes=" + "9" * 10_000 + "-")[0] in (400, 416, 431)
        # The suffix form and the open end.
        assert ask("bytes=-5") == (206, whole[-5:])
        assert ask(f"bytes={size - 3}-") == (206, whole[-3:])


def test_no_address_climbs_out_of_the_folder_of_the_built_app(tmp_path: Path) -> None:
    from app import main

    dist = tmp_path / "site" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>start</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "site" / "secret.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    built = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
    main._mount_frontend(built, dist)
    with TestClient(built) as browser:
        for address in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/..%5csecret.txt",
                        "/assets/../../secret.txt", "/assets/..%2f..%2fsecret.txt", "/assets/%2e%2e/%2e%2e/secret.txt",
                        "/./../../secret.txt", "/%252e%252e/secret.txt", "/..%c0%afsecret.txt",
                        "/" + "%2e%2e/" * 8 + "secret.txt"):
            answer = browser.get(address)
            assert b"SECRET-OUTSIDE" not in answer.content, address
            assert answer.status_code in (200, 404), address
        assert browser.get("/assets/app.js").text == "console.log(1)"
