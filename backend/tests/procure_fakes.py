"""Stand-ins for the indexers, Prowlarr and the five download programs, served through one transport (the
gate's own: ``gate.transport``). They answer as the real ones do where the real ones are known (the answers in
``fixtures/procure`` are written from the specs and from what the programs are known to do), keep every request they get,
and can be told to fail. Only example.com, keys and passwords made at run time.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "procure"
XML = "application/rss+xml; charset=utf-8"

Handler = Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def json_fixture(name: str) -> Any:
    return json.loads(fixture(name))


def text_response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/plain; charset=utf-8"})


def json_response(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json"})


def form(request: httpx.Request) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(request.content.decode(), keep_blank_values=True).items()}


class FakeNetwork(httpx.BaseTransport):
    """Hands each request to the stand-in that owns the host (the Host header the gate sends); a host nobody owns is a
    connection that fails."""

    def __init__(self) -> None:
        self.hosts: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []
        #: Hosts that do not answer at all (a program that is switched off).
        self.down: set[str] = set()

    def add(self, stand_in: Any) -> Any:
        self.hosts[stand_in.host] = stand_in
        return stand_in

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw = request.headers.get("host", "")
        stand_in = self.hosts.get(raw) or self.hosts.get(raw.rsplit(":", 1)[0])
        if stand_in is None or stand_in.host in self.down:
            raise httpx.ConnectError("nobody listens here", request=request)
        return stand_in.handle(request)


# --- Indexers ------------------------------------------------------------------------------------------------------


def error_document(code: int | str, description: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<error code="{code}" description="{description}"/>\n'.encode()


def xml_response(body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": XML, **(headers or {})})


def without_items(feed: bytes) -> bytes:
    return re.sub(rb"\s*<item>.*?</item>", b"", feed, flags=re.DOTALL)


class FakeIndexer:
    """A Newznab or Torznab endpoint. The key is read from the query parameter ``apikey`` only; a wrong one is the error
    document code 100 with HTTP 200, as the specs say."""

    def __init__(self, host: str, api_key: str, *, kind: str = "newznab", path: str = "/api") -> None:
        self.host = host
        self.api_key = api_key
        self.kind = kind
        self.path = path
        self.caps = fixture(f"{kind}_caps.xml")
        self.feed = fixture(f"{kind}_search.xml")
        self.empty = False
        #: ``t`` value to a handler that answers instead of the normal logic.
        self.overrides: dict[str, Handler] = {}
        self.requests: list[httpx.Request] = []

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.path}"

    def functions(self) -> list[str]:
        return [request.url.params.get("t", "") for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != self.path:
            return httpx.Response(404, text="<!doctype html><html><body>made-up 404</body></html>",
                                  headers={"content-type": "text/html"})
        function = request.url.params.get("t", "")
        if function in self.overrides:
            return self.overrides[function](request)
        if bool(self.api_key) and request.url.params.get("apikey") != self.api_key:
            return xml_response(error_document(100, "Incorrect user credentials"))
        if function == "caps":
            return xml_response(self.caps)
        if function in ("search", "book", "audio"):
            return xml_response(without_items(self.feed) if self.empty else self.feed)
        return xml_response(error_document(202, "No such function"))


class FakeProwlarr:
    """Prowlarr's JSON API (key in ``X-Api-Key``, a wrong one is 401 with an empty body) and the per-indexer endpoints
    ``/{id}/api``, which are ``FakeIndexer`` instances this fake hands the requests to."""

    def __init__(self, host: str, api_key: str, *, url_base: str = "") -> None:
        self.host = host
        self.api_key = api_key
        self.url_base = url_base
        self.status: dict[str, Any] = json_fixture("prowlarr_status.json")
        self.status["urlBase"] = url_base
        self.indexers: list[dict[str, Any]] = json_fixture("prowlarr_indexers.json")
        self.overrides: dict[str, Handler] = {}
        self.endpoints: dict[int, FakeIndexer] = {}
        self.requests: list[httpx.Request] = []

    @property
    def base_url(self) -> str:
        return f"http://{self.host}{self.url_base}"

    def serve(self, indexer_id: int, kind: str = "newznab") -> FakeIndexer:
        endpoint = FakeIndexer(self.host, self.api_key, kind=kind, path=f"{self.url_base}/{indexer_id}/api")
        self.endpoints[indexer_id] = endpoint
        return endpoint

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw = request.url.raw_path.decode("ascii").split("?", 1)[0]
        if self.url_base and not raw.startswith(self.url_base + "/"):
            return httpx.Response(200, text="<!doctype html><html><body>Prowlarr</body></html>",
                                  headers={"content-type": "text/html"})
        path = raw[len(self.url_base):]
        if path in self.overrides:
            return self.overrides[path](request)
        found = re.match(r"^/(\d+)/api$", path)
        if found is not None:
            served = self.endpoints.get(int(found.group(1)))
            return served.handle(request) if served is not None else httpx.Response(404)
        if not path.startswith("/api/"):
            return httpx.Response(200, text="<!doctype html><html><body>Prowlarr</body></html>",
                                  headers={"content-type": "text/html"})
        if request.headers.get("x-api-key") != self.api_key:
            return httpx.Response(401)
        if path == "/api/v1/system/status":
            return json_response(self.status)
        if path == "/api/v1/indexer":
            return json_response(self.indexers)
        return httpx.Response(404)


# --- Download programs -----------------------------------------------------------------------------------------------


class FakeSabnzbd:
    """SABnzbd: ``mode=version`` answers without a key; every other call is 403 with a plain text for a missing or wrong
    key and for the NZB key; category names are stored in lower case."""

    def __init__(self, host: str, api_key: str, *, nzb_key: str | None = None, version: str = "4.5.1") -> None:
        self.host = host
        self.api_key = api_key
        self.nzb_key = nzb_key or "n" + secrets.token_hex(6)
        self.version = version
        self.categories: list[dict[str, Any]] = []
        self.sorters: list[dict[str, Any]] = []
        self.misc: dict[str, Any] = {"complete_dir": "/downloads/complete"}
        self.host_refused = False
        #: The category list never gets the new category (a program that ignores ``set_config``).
        self.ignore_create = False
        self.requests: list[httpx.Request] = []
        #: The jobs handed over: nzo_id to (category, file name, NZB).
        self.jobs: dict[str, tuple[str, str, bytes]] = {}
        #: Put every new job into "*", as SABnzbd does with a category it does not know.
        self.unknown_category = False
        #: Answer an addfile with this instead (a timeout is raised as one).
        self.add_answer: httpx.Response | None = None
        self.add_timeout = False
        #: Takes the job, then does not answer in time.
        self.take_then_timeout = False
        #: What the program says about each job: ``where`` (queue or history) and its own fields.
        self.slots: dict[str, dict[str, Any]] = {}
        self.servers: list[dict[str, Any]] = [{"enable": 1, "retention": 3000}]
        self.removed: list[str] = []
        #: The whole queue stands (``paused`` in the answer of the queue).
        self.queue_paused = False

    def put(self, job: str, **fields: Any) -> None:
        """A job as SABnzbd shows it; ``where`` is ``queue`` or ``history``."""
        self.slots.setdefault(job, {}).update(fields)

    def progress(self, job: str, percent: float, *, size_mb: float = 10.0) -> None:
        self.put(job, where="queue", status="Downloading", percentage=str(percent), mb=str(size_mb),
                 mbleft=str(size_mb * (100 - percent) / 100), timeleft="0:02:30")

    def finish(self, job: str, storage: str) -> None:
        name = self.slots.get(job, {}).get("filename") or job
        self.put(job, where="history", status="Completed", storage=storage, name=name, bytes="10485760",
                 fail_message="")

    def fail(self, job: str, message: str = "Aborted, cannot be completed") -> None:
        name = self.slots.get(job, {}).get("filename") or job
        self.put(job, where="history", status="Failed", storage="", name=name, bytes="10485760",
                 fail_message=message)

    def vanish(self, job: str) -> None:
        self.slots.pop(job, None)
        self.jobs.pop(job, None)

    def foreign_job(self, name: str, category: str = "nexbooks") -> str:
        job = "SABnzbd_nzo_" + secrets.token_hex(4)
        self.jobs[job] = (category, name, b"")
        self.put(job, where="queue", status="Downloading", percentage="10", mb="5", mbleft="4.5", timeleft="0:01:00",
                 filename=name, cat=category, priority="Normal")
        return job

    def modes(self) -> list[str]:
        return [request.url.params.get("mode", "") for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != "/api":
            return text_response(404, "Not found")
        params = request.url.params
        mode = params.get("mode", "")
        if self.host_refused:
            return text_response(403, "Access denied - Hostname verification failed: https://sabnzbd.org/hostname-check")
        if mode == "version":
            return json_response({"version": self.version})
        if mode == "auth":
            key = params.get("key")
            answer = "apikey" if key == self.api_key else "nzbkey" if key == self.nzb_key else "badkey"
            return json_response({"auth": answer})
        if params.get("apikey") != self.api_key:
            return text_response(403, "API Key Incorrect")
        if mode == "get_cats":
            return json_response({"categories": ["*", *(entry["name"] for entry in self.categories)]})
        if mode == "set_config" and params.get("section") == "categories":
            if not self.ignore_create:
                self.categories.append({"name": params["keyword"].lower(), "dir": params.get("dir", ""), "pp": "3"})
            return json_response({"config": {"categories": []}})
        if mode == "get_config":
            section = params.get("section", "")
            data = {"categories": self.categories, "sorters": self.sorters, "misc": self.misc,
                    "servers": self.servers}.get(section, [])
            return json_response({"config": {section: data}})
        if mode == "addfile":
            if self.add_timeout:
                raise httpx.ReadTimeout("no answer", request=request)
            if self.add_answer is not None:
                return self.add_answer
            body = request.read()
            taken_but_silent = self.take_then_timeout
            name = re.search(rb'filename="([^"]+)"', body)
            job = "SABnzbd_nzo_" + secrets.token_hex(4)
            category = "*" if self.unknown_category else params.get("cat", "")
            filename = name.group(1).decode() if name else ""
            self.jobs[job] = (category, filename, body)
            self.put(job, where="queue", status="Queued", percentage="0", mb="10.0", mbleft="10.0",
                     timeleft="0:03:00", filename=filename, cat=category, priority="Normal")
            if taken_but_silent:
                raise httpx.ReadTimeout("no answer", request=request)
            return json_response({"status": True, "nzo_ids": [job]})
        if mode in ("queue", "history") and params.get("name") == "delete":
            job = params.get("value", "")
            self.removed.append(job)
            self.jobs.pop(job, None)
            self.slots.pop(job, None)
            return json_response({"status": True})
        if mode in ("queue", "history"):
            wanted = {item for item in params.get("nzo_ids", "").split(",") if item}
            category = params.get("category")
            slots = []
            for job, entry in self.jobs.items():
                info = self.slots.get(job, {})
                if info.get("where", "queue") != mode or (wanted and job not in wanted):
                    continue
                if category and entry[0].lower() != category.lower():
                    continue
                if mode == "queue":
                    slots.append({"nzo_id": job, "cat": entry[0], "filename": entry[1], "status": "Queued",
                                  **{key: value for key, value in info.items() if key != "where"}})
                else:
                    slots.append({"nzo_id": job, "category": entry[0], "name": entry[1], "status": "Completed",
                                  **{key: value for key, value in info.items() if key != "where"}})
            if mode == "queue":
                return json_response({"queue": {"paused": self.queue_paused, "slots": slots}})
            return json_response({"history": {"slots": slots}})
        return json_response({"status": False, "error": "not implemented"})


class FakeNzbget:
    def __init__(self, host: str, user: str, password: str, *, version: str = "24.5",
                 categories: tuple[str, ...] = ("Movies", "nexbooks")) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.version = version
        self.config = [{"Name": "DestDir", "Value": "/downloads/dst"}, {"Name": "AppendCategoryDir", "Value": "yes"},
                       *({"Name": f"Category{number}.Name", "Value": name}
                         for number, name in enumerate(categories, start=1))]
        self.requests: list[httpx.Request] = []
        self.appended: list[Any] = []
        self.groups: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.paused = False
        self.commands: list[tuple[str, list[int]]] = []

    def put(self, number: int, *, where: str = "queue", **fields: Any) -> None:
        """A job as NZBGet shows it, in its queue (``groups``) or its history."""
        base = {"NZBID": number, "NZBName": f"job{number}", "Name": f"job{number}", "Category": "nexbooks",
                "Status": "DOWNLOADING", "Kind": "NZB", "FileSizeLo": 10_000_000, "FileSizeHi": 0,
                "RemainingSizeLo": 5_000_000, "RemainingSizeHi": 0}
        target = self.groups if where == "queue" else self.history
        other = self.history if where == "queue" else self.groups
        other[:] = [item for item in other if item["NZBID"] != number]
        found = next((item for item in target if item["NZBID"] == number), None)
        if found is None:
            found = base
            target.append(found)
        found.update(fields)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        import base64

        expected = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, text="Unauthorized")
        if request.url.path != "/jsonrpc":
            return httpx.Response(404)
        method = json.loads(request.content).get("method")
        if method == "version":
            return json_response({"version": "1.1", "result": self.version})
        if method == "config":
            return json_response({"version": "1.1", "result": self.config})
        if method == "append":
            params = json.loads(request.content).get("params")
            self.appended.append(params)
            number = 40 + len(self.appended)
            self.put(number, NZBName=str(params[0]).removesuffix(".nzb"), Status="QUEUED")
            return json_response({"version": "1.1", "result": number})
        if method == "listgroups":
            return json_response({"version": "1.1", "result": list(self.groups)})
        if method == "history":
            return json_response({"version": "1.1", "result": list(self.history)})
        if method == "status":
            return json_response({"version": "1.1", "result": {"DownloadPaused": self.paused}})
        if method == "editqueue":
            params = json.loads(request.content).get("params")
            self.commands.append((params[0], list(params[2])))
            for number in params[2]:
                self.groups[:] = [item for item in self.groups if item["NZBID"] != number]
                self.history[:] = [item for item in self.history if item["NZBID"] != number]
            return json_response({"version": "1.1", "result": True})
        return json_response({"version": "1.1", "error": {"code": 1, "message": "no such method"}})


class FakeQbittorrent:
    """qBittorrent: login 204 with a cookie, 401 for a wrong password and for any Origin or Referer, 403 without a
    session; a second ``createCategory`` of the same name is 409."""

    def __init__(self, host: str, user: str, password: str, *, webapi: str = "2.11.2") -> None:
        self.host = host
        self.user = user
        self.password = password
        self.webapi = webapi
        self.sid = secrets.token_hex(8)
        self.categories: dict[str, dict[str, str]] = {}
        self.preferences: dict[str, Any] = {"save_path": "/downloads"}
        #: The local network is let in without a login (qBittorrent's bypass).
        self.open_access = False
        self.logins = 0
        self.requests: list[httpx.Request] = []
        #: What ``torrents/add`` got: the form fields and whether a file came.
        self.added: list[dict[str, Any]] = []
        self.add_status = 200
        #: The torrents it holds, by info hash, in its own words (``torrents/info``).
        self.torrents: dict[str, dict[str, Any]] = {}
        self.removed: list[tuple[str, str]] = []

    def put(self, info_hash: str, **fields: Any) -> None:
        base = {"hash": info_hash, "name": info_hash[:8], "category": "nexbooks", "state": "downloading",
                "progress": 0.0, "eta": 120, "size": 10_000_000, "save_path": "/downloads", "content_path": "",
                "ratio": 0.0, "seeding_time": 0, "max_ratio": -1, "max_seeding_time": -1,
                "max_inactive_seeding_time": -1, "last_activity": 0, "force_start": False}
        self.torrents.setdefault(info_hash, base).update(fields)

    def progress(self, info_hash: str, fraction: float) -> None:
        self.put(info_hash, state="downloading", progress=fraction)

    def finish(self, info_hash: str, content_path: str) -> None:
        self.put(info_hash, state="uploading", progress=1.0, content_path=content_path, eta=8640000)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if "origin" in request.headers or "referer" in request.headers:
            return httpx.Response(401)
        if path == "/api/v2/auth/login":
            self.logins += 1
            fields = form(request)
            if fields.get("username") == self.user and fields.get("password") == self.password:
                return httpx.Response(204, headers={"set-cookie": f"QBT_SID_8080={self.sid}; HttpOnly; path=/"})
            return text_response(200, "Fails.")
        if not self.open_access and f"QBT_SID_8080={self.sid}" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="Forbidden")
        if path == "/api/v2/app/webapiVersion":
            return text_response(200, self.webapi)
        if path == "/api/v2/app/preferences":
            return json_response(self.preferences)
        if path == "/api/v2/torrents/categories":
            return json_response(self.categories)
        if path == "/api/v2/torrents/createCategory":
            name = form(request)["category"]
            if name in self.categories:
                return httpx.Response(409)
            self.categories[name] = {"name": name, "savePath": ""}
            return httpx.Response(200)
        if path == "/api/v2/torrents/add":
            body = request.read()
            fields = {name.decode(): value.decode() for name, value in re.findall(
                rb'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', body)}
            fields["has_file"] = str(b'filename="' in body)
            self.added.append(fields)
            return text_response(self.add_status, "Ok." if self.add_status == 200 else "Fails.")
        if path == "/api/v2/torrents/topPrio":
            return httpx.Response(200)
        if path == "/api/v2/torrents/info":
            params = request.url.params
            wanted = {item for item in params.get("hashes", "").split("|") if item}
            rows = [dict(item) for key, item in self.torrents.items()
                    if (not wanted or key in wanted)
                    and (not params.get("category") or item["category"] == params["category"])]
            return json_response(rows)
        if path == "/api/v2/torrents/delete":
            fields = form(request)
            for key in fields.get("hashes", "").split("|"):
                self.torrents.pop(key, None)
                self.removed.append((key, fields.get("deleteFiles", "")))
            return httpx.Response(200)
        return httpx.Response(404)


class FakeTransmission:
    def __init__(self, host: str, user: str, password: str, *, version: str = "4.0.6 (38c164933e)") -> None:
        self.host = host
        self.user = user
        self.password = password
        self.version = version
        self.session_id = secrets.token_hex(8)
        #: Answer 403, as an address that is not on the whitelist gets.
        self.whitelist_refuses = False
        self.requests: list[httpx.Request] = []
        self.added: list[dict[str, Any]] = []
        #: The info hash Transmission names for a new torrent.
        self.hash = "0" * 40
        self.torrents: dict[str, dict[str, Any]] = {}
        self.removed: list[tuple[str, bool]] = []
        self.stopped: list[str] = []

    def put(self, info_hash: str, **fields: Any) -> None:
        base = {"hashString": info_hash, "name": "Some.Book", "status": 4, "percentDone": 0.0,
                "leftUntilDone": 5_000_000, "eta": 100, "sizeWhenDone": 10_000_000, "totalSize": 10_000_000,
                "downloadDir": "/data/torrents", "labels": ["nexbooks"], "error": 0, "isFinished": False,
                "secondsSeeding": 0, "uploadRatio": 0.0}
        self.torrents.setdefault(info_hash, base).update(fields)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        import base64

        if request.url.path != "/transmission/rpc":
            return httpx.Response(404)
        if self.whitelist_refuses:
            return httpx.Response(403, text="Forbidden")
        expected = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, text="Unauthorized")
        if request.headers.get("x-transmission-session-id") != self.session_id:
            return httpx.Response(409, headers={"x-transmission-session-id": self.session_id}, text="409: Conflict")
        body = json.loads(request.content)
        method = body.get("method")
        if method == "session-get":
            return json_response({"result": "success", "arguments": {
                "version": self.version, "rpc-version": 17, "download-dir": "/data/torrents"}})
        if method == "torrent-add":
            self.added.append(body.get("arguments") or {})
            return json_response({"result": "success", "arguments": {"torrent-added": {"hashString": self.hash}}})
        if method in ("torrent-set", "queue-move-top"):
            return json_response({"result": "success", "arguments": {}})
        if method == "torrent-get":
            ids = (body.get("arguments") or {}).get("ids")
            rows = [dict(item) for key, item in self.torrents.items() if ids is None or key in ids]
            return json_response({"result": "success", "arguments": {"torrents": rows}})
        if method == "torrent-remove":
            arguments = body.get("arguments") or {}
            for key in arguments.get("ids", []):
                self.torrents.pop(key, None)
                self.removed.append((key, bool(arguments.get("delete-local-data"))))
            return json_response({"result": "success", "arguments": {}})
        if method == "torrent-stop":
            for key in (body.get("arguments") or {}).get("ids", []):
                self.stopped.append(key)
                if key in self.torrents:
                    self.torrents[key]["status"] = 0
            return json_response({"result": "success", "arguments": {}})
        return json_response({"result": "no method name", "arguments": {}})


class FakeDeluge:
    def __init__(self, host: str, password: str, *, version: str = "2.1.1") -> None:
        self.host = host
        self.password = password
        self.version = version
        self.cookie = secrets.token_hex(8)
        self.plugins: list[str] = []
        self.labels: list[str] = []
        self.connected = True
        self.added: list[dict[str, Any]] = []
        self.torrent_labels: dict[str, str] = {}
        self.removed: list[str] = []
        self.label_fails = False
        self.hash = "1" * 40
        self.requests: list[httpx.Request] = []
        self.torrents: dict[str, dict[str, Any]] = {}
        self.paused: list[str] = []

    def put(self, info_hash: str, **fields: Any) -> None:
        base = {"hash": info_hash, "name": "Some.Book", "state": "Downloading", "progress": 0.0, "eta": 100,
                "total_wanted": 10_000_000, "total_size": 10_000_000, "save_path": "/downloads/deluge",
                "label": "nexbooks", "is_finished": False, "ratio": 0.0, "seeding_time": 0, "stop_at_ratio": False,
                "stop_ratio": 2.0}
        self.torrents.setdefault(info_hash, base).update(fields)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != "/json":
            return httpx.Response(404)
        body = json.loads(request.content)
        method, params, ident = body["method"], body.get("params", []), body.get("id", 1)

        def result(value: Any, headers: dict[str, str] | None = None) -> httpx.Response:
            return httpx.Response(200, content=json.dumps({"id": ident, "result": value, "error": None}).encode(),
                                  headers={"content-type": "application/json", **(headers or {})})

        def failure(code: int, message: str) -> httpx.Response:
            return json_response({"id": ident, "result": None, "error": {"code": code, "message": message}})

        if method == "auth.login":
            ok = params[0] == self.password
            return result(ok, {"set-cookie": f"_session_id={self.cookie}; path=/"} if ok else None)
        if f"_session_id={self.cookie}" not in request.headers.get("cookie", ""):
            return failure(1, "Not authenticated")
        if method == "web.connected":
            return result(self.connected)
        if method == "daemon.get_version":
            return result(self.version)
        if method == "core.get_enabled_plugins":
            return result(list(self.plugins))
        if method == "core.enable_plugin":
            if params[0] not in self.plugins:
                self.plugins.append(params[0])
            return result(True)
        if method == "label.get_labels":
            if "Label" not in self.plugins:
                return failure(2, "Unknown method")
            return result(list(self.labels))
        if method == "label.add":
            if "Label" not in self.plugins:
                return failure(2, "Unknown method")
            if params[0] in self.labels:
                return failure(3, "Label already exists")
            self.labels.append(params[0])
            return result(None)
        if method == "core.get_config_value":
            return result("/downloads/deluge" if params[0] == "download_location" else None)
        if method in ("core.add_torrent_file", "core.add_torrent_magnet"):
            self.added.append({"method": method, "options": params[-1]})
            return result(self.hash)
        if method == "label.set_torrent":
            if self.label_fails:
                return failure(4, "Unknown Label")
            self.torrent_labels[params[0]] = params[1]
            return result(None)
        if method == "core.remove_torrent":
            self.removed.append(params[0])
            self.torrents.pop(params[0], None)
            return result(True)
        if method in ("core.set_torrent_options", "core.queue_top"):
            return result(None)
        if method == "core.get_torrents_status":
            wanted = params[0] if params else {}
            ids = wanted.get("id")
            return result({key: dict(item) for key, item in self.torrents.items()
                           if (ids is None or key in ids)
                           and item.get("label") == wanted.get("label", item.get("label"))})
        if method == "core.pause_torrent":
            self.paused.extend(params[0])
            for key in params[0]:
                if key in self.torrents:
                    self.torrents[key]["state"] = "Paused"
            return result(None)
        return failure(2, "Unknown method")
