"""Transmission through its RPC (``<url>/transmission/rpc``), user and password by HTTP Basic. (Measured on
Transmission 4.1, RPC 19.)

* **Handshake**: without the right user or password 401, before anything else. With them, the first request
  answers 409 with ``X-Transmission-Session-Id``; the same request again with that header goes through. The
  session id is kept for the life of the client. A 403 is Transmission's address whitelist
  (``rpc-whitelist``) refusing nexbooks.
* Every answer is ``{"result": "success", "arguments": ...}``; anything else in ``result`` is a refusal.
  ``session-get`` tells ``version`` and ``download-dir``.
* **No categories.** Transmission has none to create or to read back: nexbooks keeps its jobs in a folder below
  Transmission's ``download-dir`` named after the category, as Radarr does, and from RPC 17 (Transmission 4.0) also
  gives them that label. The test says so (``has_categories`` is false) and never claims a category was made.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from .. import gate
from .base import (
    Answer,
    CategoryJob,
    ClientBase,
    ClientError,
    Job,
    SeedGoal,
    Seeding,
    address_refused,
    auth_failed,
    join,
    looks_absolute,
    nfc,
    number,
    percent,
    refused,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexbooks.procure")

SESSION_HEADER = "X-Transmission-Session-Id"
#: From RPC 17 (Transmission 4.0) a torrent can carry labels.
LABELS_SINCE_RPC = 17
_VERSION = re.compile(r"^(\d+(?:\.\d+){0,3})")


def rpc_url(base: str) -> str:
    """Transmission's RPC address from what the operator entered: the bare address, the web interface's
    ``.../transmission`` (Radarr's URL base) or the RPC address itself."""
    trimmed = base.rstrip("/")
    path = urlsplit(trimmed).path
    if path.endswith("/rpc"):
        return trimmed
    if path in ("", "/"):
        return trimmed + "/transmission/rpc"
    return trimmed + "/rpc"


STOPPED, CHECK_WAIT, CHECKING, DOWNLOAD_WAIT, DOWNLOADING, SEED_WAIT, SEEDING = range(7)
QUEUED_STATUSES = frozenset({CHECK_WAIT, CHECKING, DOWNLOAD_WAIT})


DONE_STATUSES = frozenset({STOPPED, SEED_WAIT, SEEDING})


LOCAL_ERROR = 3


FIELDS = (
    "hashString", "name", "status", "percentDone", "leftUntilDone", "eta", "sizeWhenDone", "totalSize", "downloadDir",
    "labels", "error",
)


#: What the seed goal reads of a finished torrent.
SEED_FIELDS = (
    "hashString", "name", "status", "leftUntilDone", "isFinished", "secondsSeeding", "uploadRatio", "downloadDir",
    "labels",
)


class Transmission(ClientBase):
    kind = "transmission"
    has_categories = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = ""
        self._values: dict[str, Any] | None = None

    def _post(self, body: str) -> Answer:
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers[SESSION_HEADER] = self._session_id
        if self.target.username or self.target.secret:
            headers["Authorization"] = gate.basic_auth(self.target.username, self.target.secret)
        return self.call("POST", rpc_url(self.target.url), content=body, headers=headers)

    def _rpc(self, method: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"method": method, "arguments": arguments or {}})
        answer = self._post(body)
        if answer.status == 409 and answer.headers.get(SESSION_HEADER):
            self._session_id = answer.headers[SESSION_HEADER][:128]
            answer = self._post(body)
        if answer.status == 401:
            raise auth_failed("wrong_password")
        if answer.status == 403:
            raise address_refused()
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except (ValueError, RecursionError) as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data:
            raise wrong_kind()
        if data["result"] != "success":
            raise refused()
        arguments = data.get("arguments")
        return arguments if isinstance(arguments, dict) else {}

    def _session_values(self) -> dict[str, Any]:
        if self._values is None:
            self._values = self._rpc("session-get")
        return self._values

    def version(self) -> str:
        found = _VERSION.match(text(self._session_values().get("version"), 64))
        if found is None:
            raise wrong_kind()
        return found.group(1)

    def categories(self) -> list[str]:
        return []

    def create_category(self) -> None:
        """Transmission has no categories: the folder of nexbooks comes with the first torrent."""

    def download_folder(self) -> str | None:
        folder = text(self._session_values().get("download-dir"))
        return join(folder, self.target.category) if looks_absolute(folder) else None

    def add_torrent(self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str,
                    urgent: bool | None = None, seed: SeedGoal | None = None) -> str:
        arguments: dict[str, Any] = {"paused": False}
        folder = self.download_folder()
        if folder is not None:
            arguments["download-dir"] = folder
        version = self._session_values().get("rpc-version")
        if isinstance(version, int) and version >= LABELS_SINCE_RPC:
            arguments["labels"] = [self.target.category]
        if content is not None:
            arguments["metainfo"] = base64.b64encode(content).decode()
        elif magnet:
            arguments["filename"] = magnet
        else:
            raise refused()
        try:
            answer = self._rpc("torrent-add", arguments)
        except ClientError as exc:
            raise self.unsure_on_timeout(exc, info_hash.lower()) from exc
        if isinstance(answer.get("torrent-duplicate"), dict):
            raise refused("duplicate")
        added = answer.get("torrent-added")
        found = text(added.get("hashString"), 64).lower() if isinstance(added, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{40}", found):
            raise refused()
        if seed is not None and seed.ratio is not None:
            # The seed time is for nexbooks to watch: Transmission's idle limit means something else.
            try:
                self._rpc("torrent-set", {"ids": [found], "seedRatioLimit": seed.ratio, "seedRatioMode": 1})
            except ClientError as exc:
                logger.info("Transmission did not take the seed ratio: %s", exc.code)
        if urgent:
            try:
                self._rpc("queue-move-top", {"ids": [found]})
            except ClientError as exc:
                logger.info("Transmission did not move the torrent to the top: %s", exc.code)
        return found

    # --- Following the jobs ------------------------------

    def _torrents(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"fields": list(FIELDS)}
        if ids is not None:
            arguments["ids"] = ids
        torrents = (self._rpc("torrent-get", arguments)).get("torrents")
        if not isinstance(torrents, list):
            raise wrong_kind()
        return [item for item in torrents if isinstance(item, dict)]

    def _is_ours(self, item: dict[str, Any]) -> bool:
        labels = item.get("labels")
        if isinstance(labels, list) and self.target.category in labels:
            return True
        folder = self.download_folder()
        return folder is not None and text(item.get("downloadDir")).rstrip("/\\") == folder

    def _ours_by_hash(self, info_hash: str) -> bool:
        return any(self._is_ours(item) for item in self._torrents([info_hash]))

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        wanted = sorted({ref.lower() for ref in refs if ref})
        if not wanted:
            return {}
        found: dict[str, Job] = {}
        for item in self._torrents(wanted):
            job = self._job(item) if self._is_ours(item) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        return found

    def category_jobs(self) -> list[CategoryJob]:
        found: list[CategoryJob] = []
        for item in self._torrents():
            job = self._job(item) if self._is_ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _job(item: dict[str, Any]) -> Job | None:
        ref = text(item.get("hashString"), 64).lower()
        status = whole(item.get("status"))
        if not ref or status is None:
            return None
        done = number(item.get("percentDone"))
        left = whole(item.get("leftUntilDone"))
        eta = whole(item.get("eta"))
        size = whole(item.get("sizeWhenDone")) or whole(item.get("totalSize"))
        common: dict[str, Any] = {
            "ref": ref,
            "client_state": f"status {status}, error {whole(item.get('error')) or 0}",
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(done * 100 if done is not None else None),
        }
        if whole(item.get("error")) == LOCAL_ERROR:
            return Job(state="problem", problem="client_error", **common)
        if size is None or size <= 0:
            # A magnet without its metadata yet: nothing to measure (Radarr: queued).
            return Job(state="queued", **common)
        if left == 0 and status in DONE_STATUSES:
            folder, name = text(item.get("downloadDir")), text(item.get("name"), 1024)
            if not folder or not name:
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            return Job(state="completed", remaining_seconds=0, path=join(folder, name), **common)
        remaining = eta if eta is not None and eta >= 0 else None
        if status in QUEUED_STATUSES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if status == STOPPED:
            return Job(state="paused", **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    def holds(self, ref: str) -> bool:
        wanted = ref.lower()
        return any(text(item.get("hashString"), 64).lower() == wanted and self._is_ours(item)
                   for item in self._torrents([wanted]))

    def remove(self, ref: str, *, delete_files: bool) -> None:
        self._rpc("torrent-remove", {"ids": [ref.lower()], "delete-local-data": delete_files})

    def seeding(self, refs: list[str]) -> dict[str, Seeding]:
        wanted = sorted({ref.lower() for ref in refs if ref})
        if not wanted:
            return {}
        answer = self._rpc("torrent-get", {"fields": list(SEED_FIELDS), "ids": wanted})
        torrents = answer.get("torrents")
        if not isinstance(torrents, list):
            raise wrong_kind()
        found: dict[str, Seeding] = {}
        for item in torrents:
            if not isinstance(item, dict) or not self._is_ours(item):
                continue
            ref = text(item.get("hashString"), 64).lower()
            status = whole(item.get("status"))
            if ref not in wanted or status is None:
                continue
            done = whole(item.get("leftUntilDone")) == 0
            stopped = done and status == STOPPED
            folder, name = text(item.get("downloadDir")), text(item.get("name"), 1024)
            found[ref] = Seeding(
                ref=ref,
                done=done,
                stopped=stopped,
                goal_reached=stopped and item.get("isFinished") is True,
                seeding_seconds=whole(item.get("secondsSeeding")),
                ratio=number(item.get("uploadRatio")),
                path=join(folder, name) if folder and name else None,
            )
        return found

    def stop(self, ref: str) -> None:
        self._rpc("torrent-stop", {"ids": [ref.lower()]})
