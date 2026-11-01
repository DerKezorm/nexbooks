"""The watchers over ``/api/app/1``, the surface of the iPhone app.

The app in the store and the servers people run are never updated at the same moment. A rename nobody notices in the
browser stops every app that is already out there. So, as for ``/api/v1``, two things are held fast:

* the list of addresses (``PROMISED`` in ``routers/app1.py``) is exactly what lies under ``/api/app/1``: nothing there that
  is not promised, nothing promised that is not there;
* the fields of every answer (``tests/app1_fields.json``, taken from the description of the interface, down into the
  nested objects and lists): a field may be added, never taken away or renamed. What must go needs ``/api/app/2``
  beside it.

* the error codes of every address (``tests/app1_errors.json``, taken from what the description says for each failing
  status): the app branches on a code (``pairing_used`` says something else than ``pairing_unknown``), so a code that
  goes is as much a break as a field that goes. A code may be added, never taken away or moved to another status.

When a change is right, the records are renewed by hand:

    NEXBOOKS_RECORD_APP_FIELDS=1 python -m pytest tests/test_app_promise.py

and the files are committed with the change, where the diff shows which fields and codes came.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.main import app
from app.routers.app1 import PROMISED

RECORD = Path(__file__).with_name("app1_fields.json")
ERRORS = Path(__file__).with_name("app1_errors.json")
PREFIX = "/api/app/1"
DEPTH = 8


def _fields_per_path() -> dict[str, list[str]]:
    """``METHOD /path`` -> the fields of the answer, as paths (``account.name``, ``items[].id``)."""
    document = app.openapi()
    schemas = document.get("components", {}).get("schemas", {})

    def fields(schema: dict[str, Any], prefix: str = "", seen: frozenset[str] = frozenset(), depth: int = 0) -> list[str]:
        if depth > DEPTH:
            return []
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name in seen:
                return []
            return fields(schemas.get(name, {}), prefix, seen | {name}, depth + 1)
        for branch in ("anyOf", "oneOf", "allOf"):
            if branch in schema:
                found: list[str] = []
                for part in schema[branch]:
                    if part.get("type") != "null":
                        found += fields(part, prefix, seen, depth + 1)
                return sorted(set(found))
        if schema.get("type") == "array":
            return fields(schema.get("items", {}), prefix + "[]", seen, depth + 1)
        properties = schema.get("properties")
        if not properties:
            if schema.get("additionalProperties"):
                return [f"{prefix}{{*}}"] if prefix else ["{*}"]
            return [prefix] if prefix else []
        found = []
        for name, inner in sorted(properties.items()):
            path = f"{prefix}.{name}" if prefix else name
            found += fields(inner, path, seen, depth + 1) or [path]
        return sorted(set(found))

    result: dict[str, list[str]] = {}
    for path, methods in document["paths"].items():
        if not path.startswith(PREFIX):
            continue
        for method, operation in methods.items():
            for status, answer in operation.get("responses", {}).items():
                if not status.startswith("2"):
                    continue
                content = answer.get("content", {})
                if content.get("application/json", {}).get("schema"):
                    result[f"{method.upper()} {path}"] = fields(content["application/json"].get("schema", {}))
                else:
                    # An answer without JSON (a picture, the bytes of a file, nothing): its status and kind are held.
                    result[f"{method.upper()} {path}"] = [f"<{status} {' '.join(sorted(set(content) - {'application/json'})) or 'empty'}>"]
                break
    return result


def _routes() -> set[str]:
    found: set[str] = set()
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        for each in inner.routes if inner is not None else [route]:
            path = str(getattr(each, "path", ""))
            if path.startswith(PREFIX):
                found.add(path)
    return found


def test_the_surface_is_exactly_the_promised_one() -> None:
    there, promised = _routes(), set(PROMISED)
    assert there - promised == set(), f"Under {PREFIX} lies something that is not in PROMISED: {sorted(there - promised)}"
    assert promised - there == set(), f"Promised, but not reachable: {sorted(promised - there)}"
    assert len(PROMISED) == len(set(PROMISED)), "an address twice in PROMISED"


def test_every_promised_address_has_an_answer_that_can_be_held_fast() -> None:
    now = _fields_per_path()
    for path in PROMISED:
        assert any(key.endswith(" " + path) for key in now), f"no answer described for {path}"
    empty = sorted(key for key, found in now.items() if not found)
    assert empty == [], f"Answers without a field to hold fast: {empty}"


def _gone(now: dict[str, list[str]]) -> dict[str, list[str]]:
    """What the record holds and ``now`` does not."""
    before: dict[str, list[str]] = json.loads(RECORD.read_text(encoding="utf-8"))
    gone: dict[str, list[str]] = {}
    for key, fields in before.items():
        if key not in now:
            gone[key] = ["(the whole address)"]
        elif missing := [field for field in fields if field not in now[key]]:
            gone[key] = missing
    return gone


def test_the_fields_of_the_answers_stay() -> None:
    now = _fields_per_path()
    if os.environ.get("NEXBOOKS_RECORD_APP_FIELDS") == "1" or not RECORD.exists():
        text = json.dumps(now, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        RECORD.write_text(text, encoding="utf-8", newline="\n")
    gone = _gone(now)
    lines = [f"  {key}: {', '.join(value)}" for key, value in sorted(gone.items())]
    assert not gone, (
        "Fields are gone from an answer of the surface of the app:\n" + "\n".join(lines)
        + f"\n\nUnder {PREFIX} a promise holds. Either the field stays, or /api/app/2 comes beside it. "
        "If the change is right, renew the record: NEXBOOKS_RECORD_APP_FIELDS=1 python -m pytest tests/test_app_promise.py"
    )


def test_the_record_measures_something() -> None:
    """A record that is empty would be green for ever. It has to hold what the app reads."""
    before = json.loads(RECORD.read_text(encoding="utf-8"))
    claim = before["POST /api/app/1/pairing/{pairing_id}/claim"]
    assert {"token", "account.name", "server.version"} <= set(claim)
    assert set(before["POST /api/app/1/pairing/claim"]) == set(claim)
    # A device without a camera: the code it shows, and what it takes home once a person confirmed.
    assert {"id", "secret", "code", "expires_at", "poll_seconds"} <= set(before["POST /api/app/1/pairing/request"])
    assert {"state", "token", "account.name", "server.version"} <= set(
        before["GET /api/app/1/pairing/request/{request_id}"])
    # The iPhone app confirms the request of such a device with its own token.
    assert {"state", "device_name", "expires_at"} <= set(before["POST /api/app/1/pairing/request/confirm"])
    assert len(before) >= len(PROMISED)
    library = set(before["GET /api/app/1/library"])
    assert {"marker", "latest", "removed[]", "items[].id", "items[].title", "items[].cover_etag",
            "items[].editions[].id", "items[].editions[].size"} <= library
    assert {"items[].editions[].files[].sha256", "items[].editions[].chapters[].start"} & library == set()
    assert {"editions[].files[].sha256", "editions[].files[].mime", "editions[].chapters[].start",
            "description"} <= set(before["GET /api/app/1/works/{work_id}"])
    assert {"kept", "current.locator", "current.percent", "current.device", "current.moved_at"} <= set(
        before["PUT /api/app/1/editions/{edition_id}/progress"])
    assert {"items[].device", "items[].moved_at", "until"} <= set(before["GET /api/app/1/progress"])
    assert {"account.name", "account.language", "libraries[].id", "server.version", "installation_id",
            "features[]"} <= set(before["GET /api/app/1/me"])
    assert before["GET /api/app/1/files/{file_id}/content"] == ["<200 application/octet-stream>"]
    assert before["DELETE /api/app/1/history/{entry_id}"] == ["<204 empty>"]
    # Nowhere a path: the app is never told one.
    assert not [field for fields in before.values() for field in fields if "path" in field.split(".")[-1]]


def test_the_promise_notices_a_field_that_goes_and_an_address_that_goes() -> None:
    """The watcher itself: take a field or an address out of what the description says, and the comparison finds it."""
    now = _fields_per_path()
    key = "POST /api/app/1/pairing/claim"
    assert _gone(now) == {}
    assert _gone({**now, key: [field for field in now[key] if field != "token"]}) == {key: ["token"]}
    assert _gone({other: found for other, found in now.items() if other != key}) == {key: ["(the whole address)"]}
    # A field that comes is no harm.
    assert _gone({**now, key: [*now[key], "newer"]}) == {}


#: The addresses that need no token: they are how the app gets one.
CLAIMS = {"/api/app/1/pairing/{pairing_id}/claim", "/api/app/1/pairing/claim", "/api/app/1/pairing/request",
          "/api/app/1/pairing/request/{request_id}"}


def _called(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    for sub in dependant.dependencies:
        found.add(sub.call)
        found |= _called(sub)
    return found


def test_every_address_of_the_surface_asks_for_a_device_token_but_the_two_that_give_one() -> None:
    """A device token and nothing else (``deps.require_device``): not the browser's cookie, not an API key."""
    from fastapi.routing import APIRoute

    from app import deps

    seen = set()
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        for each in inner.routes if inner is not None else [route]:
            if isinstance(each, APIRoute) and each.path.startswith(PREFIX):
                seen.add(each.path)
                called = _called(each.dependant)
                assert (deps.require_device in called) is (each.path not in CLAIMS), each.path
                assert not called & {deps.require_account, deps.require_reader, deps.require_browser_account}, each.path
    assert CLAIMS <= seen and len(seen) == len(PROMISED)


# ---- The error codes ----------------------------------------------------------------------------------------------

#: A code in the description of a failing answer: in backticks, small letters and at least one underscore
#: (``pairing_used``); parameters and headers (``after``, ``Retry-After``) are no codes.
CODE = re.compile(r"`([a-z]+(?:_[a-z]+)+)`")


def _errors_per_path() -> dict[str, dict[str, list[str]]]:
    """``METHOD /path`` -> status -> the codes the description names for it. 3xx and 2xx are no failures."""
    document = app.openapi()
    result: dict[str, dict[str, list[str]]] = {}
    for path, methods in document["paths"].items():
        if not path.startswith(PREFIX):
            continue
        for method, operation in methods.items():
            found: dict[str, list[str]] = {}
            for status, answer in operation.get("responses", {}).items():
                if status[0] in "45":
                    codes = sorted(set(CODE.findall(str(answer.get("description", "")))))
                    if codes:
                        found[status] = codes
            result[f"{method.upper()} {path}"] = found
    return result


def _codes_gone(now: dict[str, dict[str, list[str]]]) -> dict[str, list[str]]:
    """What the record holds and ``now`` does not, as ``status code`` lines per address."""
    before: dict[str, dict[str, list[str]]] = json.loads(ERRORS.read_text(encoding="utf-8"))
    gone: dict[str, list[str]] = {}
    for key, statuses in before.items():
        for status, codes in statuses.items():
            for code in codes:
                if code not in now.get(key, {}).get(status, []):
                    gone.setdefault(key, []).append(f"{status} {code}")
    return gone


def test_the_error_codes_of_the_addresses_stay() -> None:
    now = _errors_per_path()
    if os.environ.get("NEXBOOKS_RECORD_APP_FIELDS") == "1" or not ERRORS.exists():
        text = json.dumps(now, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ERRORS.write_text(text, encoding="utf-8", newline="\n")
    gone = _codes_gone(now)
    lines = [f"  {key}: {', '.join(value)}" for key, value in sorted(gone.items())]
    assert not gone, (
        "Error codes are gone from the answers of the surface of the app:\n" + "\n".join(lines)
        + f"\n\nUnder {PREFIX} a promise holds: the app branches on these codes. Either the code stays on its status, or "
        "/api/app/2 comes beside it. If the change is right, renew the record: "
        "NEXBOOKS_RECORD_APP_FIELDS=1 python -m pytest tests/test_app_promise.py"
    )


def test_the_error_record_measures_something() -> None:
    """A record without the codes the app reads would be green for ever."""
    before: dict[str, dict[str, list[str]]] = json.loads(ERRORS.read_text(encoding="utf-8"))
    assert set(before) == set(_fields_per_path()), "every address of the surface is in the record"
    expected = {
        "403": ["origin_refused"],
        "404": ["pairing_unknown"],
        "409": ["too_many_devices"],
        "410": ["pairing_expired", "pairing_used"],
        "422": ["device_name_invalid", "invalid_input", "pairing_code"],
        "429": ["too_many_attempts", "too_many_codes"],
    }
    for key in ("POST /api/app/1/pairing/claim", "POST /api/app/1/pairing/{pairing_id}/claim"):
        assert before[key] == expected, key
    # A device without a camera: asking for a code, and how it stands.
    assert before["POST /api/app/1/pairing/request"] == {
        "403": ["origin_refused"], "422": ["device_name_invalid", "invalid_input"], "429": ["too_many_requests"]}
    assert before["GET /api/app/1/pairing/request/{request_id}"] == {
        "403": ["origin_refused"], "404": ["pairing_unknown"], "409": ["too_many_devices"], "410": ["pairing_used"],
        "422": ["invalid_input"], "429": ["too_many_attempts"]}
    # Letting go is always 204 (also for a secret that does not fit) and never counts: it has no 404 and no 429.
    assert before["DELETE /api/app/1/pairing/request/{request_id}"] == {
        "403": ["origin_refused"], "422": ["invalid_input"]}
    # The app confirms the request of a device without a camera: both id and code must fit, the same answer
    # for every pair that does not; a right pair is neither expired nor used.
    confirming = before["POST /api/app/1/pairing/request/confirm"]
    assert {status: codes for status, codes in confirming.items() if status != "401"} == {
        "400": ["client_required"], "403": ["device_chain", "second_factor_setup_required"],
        "404": ["pairing_unknown"], "409": ["too_many_devices"], "410": ["pairing_expired", "pairing_used"],
        "422": ["invalid_input", "pairing_code"], "429": ["too_many_attempts", "too_many_codes"]}
    # Looking first: the same answers, but it decides nothing, so no device can be too many.
    looking = before["POST /api/app/1/pairing/request/preview"]
    assert {status: codes for status, codes in looking.items() if status != "401"} == {
        "400": ["client_required"], "403": ["device_chain", "second_factor_setup_required"],
        "404": ["pairing_unknown"], "410": ["pairing_expired", "pairing_used"],
        "422": ["invalid_input", "pairing_code"], "429": ["too_many_attempts", "too_many_codes"]}
    # Taking back what this device confirmed: unknown for any other, used once the device fetched.
    taking = before["DELETE /api/app/1/pairing/request/{request_id}/confirm"]
    assert {status: codes for status, codes in taking.items() if status not in ("401", "429")} == {
        "400": ["client_required"], "403": ["second_factor_setup_required"], "404": ["pairing_unknown"],
        "410": ["pairing_used"]}
    # Every address that wants a token says what its 401 is, and the library says when to start again.
    for key, statuses in before.items():
        if key.split(" ", 1)[1] not in CLAIMS:
            assert {"token_invalid", "device_token_required", "token_wrong_kind", "sign_in_required"} <= set(
                statuses["401"]), key
            assert "too_many_attempts" in statuses["429"], key
    assert "marker_too_old" in before["GET /api/app/1/library"]["410"]
    assert {"too_many_streams", "too_many_attempts"} <= set(before["GET /api/app/1/files/{file_id}/content"]["429"])


def test_the_watcher_of_the_codes_notices_a_code_that_goes() -> None:
    now = _errors_per_path()
    key = "POST /api/app/1/pairing/claim"
    assert _codes_gone(now) == {}
    fewer = {**now, key: {**now[key], "410": ["pairing_used"]}}
    assert _codes_gone(fewer) == {key: ["410 pairing_expired"]}
    moved = {**now, key: {**{status: codes for status, codes in now[key].items() if status != "404"},
                          "400": ["pairing_unknown"]}}
    assert _codes_gone(moved) == {key: ["404 pairing_unknown"]}
    assert _codes_gone({**now, key: {**now[key], "409": ["too_many_devices", "newer_code"]}}) == {}


def test_the_codes_of_the_three_ways_to_fail_to_sign_in_are_what_every_address_really_says(client: Any,
                                                                                         operator: Any) -> None:
    """The description is only a promise while the server keeps it: no credential, a device token nobody made, an API key
    and the browser's cookie at an address of the app each get the code the record names for that address."""
    from fastapi.testclient import TestClient

    from app.security import brake

    recorded: dict[str, dict[str, list[str]]] = json.loads(ERRORS.read_text(encoding="utf-8"))
    document = app.openapi()
    asked = 0
    for path, methods in document["paths"].items():
        if not path.startswith(PREFIX) or path in CLAIMS:
            continue
        for method in methods:
            url = re.sub(r"\{[^}]+\}", "1", path)
            known = recorded[f"{method.upper()} {path}"]["401"]
            brake.forget()  # a made-up token counts against the sender, five of them rest it
            program = TestClient(app, base_url="http://testserver")
            body = {"json": {}} if method in ("post", "put", "patch") else {}
            seen = {
                "sign_in_required": program.request(method, url, **body),
                "token_invalid": program.request(method, url, headers={"Authorization": "Bearer nbd_" + "q" * 43}, **body),
                "token_wrong_kind": program.request(method, url, headers={"Authorization": "Bearer nbk_" + "q" * 43},
                                                    **body),
                "device_token_required": client.request(method, url, **body),
            }
            for code, answer in seen.items():
                if code == "sign_in_required" and method in ("post", "put", "patch", "delete"):
                    # A change that names no tab and brings no token is a form of another site: turned away first.
                    assert (answer.status_code, answer.json()["detail"]["code"]) == (400, "client_required"), path
                    assert "client_required" in recorded[f"{method.upper()} {path}"]["400"], (method, path)
                    code = "client_required"
                else:
                    assert answer.status_code == 401 and answer.json()["detail"]["code"] == code, (method, path, code)
                    assert code in known, (method, path, code)
                asked += 1
    assert asked >= 4 * (len(PROMISED) - len(CLAIMS))


# ---- What the app sends, and the headers it reads ------------------------------------------------------------------

REQUESTS = Path(__file__).with_name("app1_requests.json")


def _body_fields(schema: dict[str, Any], schemas: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The fields of a request body, and which of them are required."""
    if "$ref" in schema:
        schema = schemas.get(schema["$ref"].rsplit("/", 1)[-1], {})
    properties = schema.get("properties") or {}
    return sorted(properties), sorted(schema.get("required") or [])


def _requests_per_path() -> dict[str, dict[str, Any]]:
    """``METHOD /path`` -> the parameters the app may send (query and headers, and which are required), the fields of
    the body, and the headers of each answer the description names."""
    document = app.openapi()
    schemas = document.get("components", {}).get("schemas", {})
    result: dict[str, dict[str, Any]] = {}
    for path, methods in document["paths"].items():
        if not path.startswith(PREFIX):
            continue
        for method, operation in methods.items():
            parameters = [p for p in operation.get("parameters", []) if p.get("in") in ("query", "header")]
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            fields, required = _body_fields(body, schemas) if body else ([], [])
            headers = {status: sorted(answer.get("headers", {}))
                       for status, answer in operation.get("responses", {}).items() if answer.get("headers")}
            result[f"{method.upper()} {path}"] = {
                "parameters": sorted(f"{p['in']} {p['name']}" for p in parameters),
                "required_parameters": sorted(f"{p['in']} {p['name']}" for p in parameters if p.get("required")),
                "body": fields, "required_body": required, "headers": headers,
            }
    return result


def _requests_broken(now: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """What would break an app that is out there: a parameter, a body field or a header of an answer it knows is gone,
    or something it does not send is required now."""
    before: dict[str, dict[str, Any]] = json.loads(REQUESTS.read_text(encoding="utf-8"))
    broken: dict[str, list[str]] = {}
    for key, was in before.items():
        found = now.get(key)
        if found is None:
            broken[key] = ["(the whole address)"]
            continue
        lines = [f"parameter {name} gone" for name in was["parameters"] if name not in found["parameters"]]
        lines += [f"body field {name} gone" for name in was["body"] if name not in found["body"]]
        lines += [f"parameter {name} now required" for name in found["required_parameters"]
                  if name not in was["required_parameters"]]
        lines += [f"body field {name} now required" for name in found["required_body"]
                  if name not in was["required_body"]]
        lines += [f"header {name} of {status} gone" for status, names in was["headers"].items() for name in names
                  if name not in found["headers"].get(status, [])]
        if lines:
            broken[key] = lines
    return broken


def test_what_the_app_sends_and_the_headers_it_reads_stay() -> None:
    now = _requests_per_path()
    if os.environ.get("NEXBOOKS_RECORD_APP_FIELDS") == "1" or not REQUESTS.exists():
        REQUESTS.write_text(json.dumps(now, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8",
                            newline="\n")
    broken = _requests_broken(now)
    lines = [f"  {key}: {', '.join(value)}" for key, value in sorted(broken.items())]
    assert not broken, (
        "What the app sends or reads changed at the surface of the app:\n" + "\n".join(lines)
        + f"\n\nUnder {PREFIX} a promise holds: a parameter, a body field or a header may come, never go, and nothing "
        "the app does not send yet may become required. If the change is right, renew the record: "
        "NEXBOOKS_RECORD_APP_FIELDS=1 python -m pytest tests/test_app_promise.py"
    )


def test_the_record_of_what_the_app_sends_measures_something() -> None:
    before: dict[str, dict[str, Any]] = json.loads(REQUESTS.read_text(encoding="utf-8"))
    assert set(before) == set(_fields_per_path()), "every address of the surface is in the record"
    assert {"query after", "query limit"} <= set(before["GET /api/app/1/library"]["parameters"])
    assert before["GET /api/app/1/library"]["required_parameters"] == []
    assert "query since" in before["GET /api/app/1/progress"]["parameters"]
    assert {"query v", "header If-None-Match"} <= set(before["GET /api/app/1/works/{work_id}/cover"]["parameters"])
    content = before["GET /api/app/1/files/{file_id}/content"]
    assert {"header Range", "header If-Range", "header If-None-Match"} <= set(content["parameters"])
    assert {"ETag", "Accept-Ranges", "Content-Length", "Content-Disposition", "Last-Modified"} <= set(
        content["headers"]["200"])
    assert {"Content-Range", "ETag"} <= set(content["headers"]["206"])
    assert content["headers"]["429"] == ["Retry-After"]
    progress = before["PUT /api/app/1/editions/{edition_id}/progress"]
    assert {"locator", "percent", "age", "device", "chapter", "chapter_count", "chapter_title"} <= set(progress["body"])
    assert progress["required_body"] == ["locator", "percent"]
    marks = before["POST /api/app/1/editions/{edition_id}/bookmarks"]
    assert {"locator", "fraction", "chapter_title", "note"} <= set(marks["body"])
    assert before["PATCH /api/app/1/bookmarks/{bookmark_id}"]["body"] == ["note"]
    assert "finished_on" in before["POST /api/app/1/editions/{edition_id}/finished"]["body"]
    assert {"query limit", "query offset", "query work"} <= set(before["GET /api/app/1/history"]["parameters"])
    assert before["GET /api/app/1/history"]["headers"]["200"] == ["X-Total-Count"]
    for key in ("POST /api/app/1/pairing/claim", "POST /api/app/1/pairing/{pairing_id}/claim"):
        assert before[key]["headers"]["200"] == ["Cache-Control"] and before[key]["headers"]["429"] == ["Retry-After"]
    assert set(before["POST /api/app/1/pairing/claim"]["body"]) == {"code", "device_name"}
    assert set(before["POST /api/app/1/pairing/{pairing_id}/claim"]["body"]) == {"device_name", "secret"}
    # A device without a camera: the name goes in the body, the secret in a header of its own.
    ask = before["POST /api/app/1/pairing/request"]
    assert ask["body"] == ["device_name"] and ask["required_body"] == [] and ask["headers"]["429"] == ["Retry-After"]
    assert ask["headers"]["201"] == ["Cache-Control"]
    stands = before["GET /api/app/1/pairing/request/{request_id}"]
    assert stands["parameters"] == ["header X-Pairing-Secret"] == stands["required_parameters"]
    assert stands["headers"] == {"200": ["Cache-Control"], "429": ["Retry-After"]}
    lets_go = before["DELETE /api/app/1/pairing/request/{request_id}"]
    assert lets_go["parameters"] == ["header X-Pairing-Secret"] == lets_go["required_parameters"]
    # The app confirms, or looks first, with the id and the code in the body.
    for key in ("POST /api/app/1/pairing/request/confirm", "POST /api/app/1/pairing/request/preview"):
        assert before[key]["body"] == ["code", "id"] == before[key]["required_body"], key
        assert before[key]["headers"]["200"] == ["Cache-Control"], key
    for key, entry in before.items():
        if key.split(" ", 1)[1] not in CLAIMS:
            assert entry["headers"]["429"] == ["Retry-After"], key


def test_the_watcher_of_what_the_app_sends_notices_what_goes_and_what_becomes_required() -> None:
    now = _requests_per_path()
    key = "PUT /api/app/1/editions/{edition_id}/progress"
    assert _requests_broken(now) == {}
    less = {**now, key: {**now[key], "body": [name for name in now[key]["body"] if name != "age"]}}
    assert _requests_broken(less) == {key: ["body field age gone"]}
    stricter = {**now, key: {**now[key], "required_body": [*now[key]["required_body"], "device"]}}
    assert _requests_broken(stricter) == {key: ["body field device now required"]}
    library = "GET /api/app/1/library"
    asked = {**now, library: {**now[library], "required_parameters": ["query after"]}}
    assert _requests_broken(asked) == {library: ["parameter query after now required"]}
    content = "GET /api/app/1/files/{file_id}/content"
    headers = {**now[content]["headers"], "206": ["ETag"]}
    assert _requests_broken({**now, content: {**now[content], "headers": headers}}) == {
        content: ["header Content-Range of 206 gone"]}
    more = {**now, key: {**now[key], "body": [*now[key]["body"], "newer"]}}
    assert _requests_broken(more) == {}
