"""Deluge through the JSON-RPC of its web interface (``<url>/json``), with the web password. (Measured on Deluge 2.2.)

* **Login**: ``auth.login`` with the password answers ``true`` and a cookie ``_session_id``; a wrong
  password answers ``false`` with HTTP 200. Without the cookie every call answers the error ``Not
  authenticated`` (code 1): nexbooks logs in again, once. Errors come as ``{"result": null, "error":
  {"message", "code"}}``.
* The web interface has to be **connected to a daemon**: ``web.connected``; if not, ``web.get_hosts`` lists
  them and ``web.connect`` takes the first, as Radarr does. ``daemon.get_version`` tells the version.
* **Category** is a label of the **Label plugin**, which Deluge ships switched off. Without it ``label.*``
  is an unknown method. Saving the client switches it on (``core.enable_plugin``) and adds the label; a
  label that exists already answers an error, which is fine. Deluge keeps labels in lower case.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from .base import (
    CategoryJob,
    ClientBase,
    ClientError,
    Job,
    SeedGoal,
    Seeding,
    auth_failed,
    category_failed,
    nfc,
    number,
    percent,
    refused,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexbooks.procure")

NOT_AUTHENTICATED = 1
LABEL_PLUGIN = "Label"
_VERSION = re.compile(r"^(\d+(?:\.\d+){0,3})")
_HASH = re.compile(r"^[0-9a-f]{40}$")


def json_url(base: str) -> str:
    trimmed = base.rstrip("/")
    return trimmed if urlsplit(trimmed).path.endswith("/json") else trimmed + "/json"


class DelugeError(ClientError):
    """An error Deluge answered, with its code; its message is never shown and never logged whole."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__("client_refused")
        self.deluge_code = code
        self.deluge_message = message


QUEUED_STATES = frozenset({"queued", "checking", "allocating", "moving"})


KEYS = ("hash", "name", "state", "progress", "eta", "total_wanted", "total_size", "save_path", "label", "is_finished")


#: What the seed goal reads of a finished torrent.
SEED_KEYS = (
    "hash", "name", "state", "ratio", "seeding_time", "is_finished", "stop_at_ratio", "stop_ratio", "save_path",
)


class Deluge(ClientBase):
    kind = "deluge"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._logged_in = False
        self._counter = 0

    def _raw(self, method: str, *params: Any) -> Any:
        self._counter += 1
        body = json.dumps({"method": method, "params": list(params), "id": self._counter})
        answer = self.call("POST", json_url(self.target.url), content=body,
                           headers={"Content-Type": "application/json"})
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except (ValueError, RecursionError) as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data or "error" not in data:
            raise wrong_kind()
        error = data["error"]
        if isinstance(error, dict):
            raise DelugeError(whole(error.get("code")), text(error.get("message"), 400))
        return data["result"]

    def _login(self) -> None:
        if self._raw("auth.login", self.target.secret) is not True:
            raise auth_failed("wrong_password")
        self._logged_in = True
        if self._raw("web.connected") is True:
            return
        hosts = self._raw("web.get_hosts")
        if not isinstance(hosts, list) or not hosts or not isinstance(hosts[0], list) or not hosts[0]:
            raise refused("no_daemon")
        self._raw("web.connect", text(hosts[0][0], 64))
        if self._raw("web.connected") is not True:
            raise refused("no_daemon")

    def _rpc(self, method: str, *params: Any) -> Any:
        if not self._logged_in:
            self._login()
        try:
            return self._raw(method, *params)
        except DelugeError as exc:
            if exc.deluge_code != NOT_AUTHENTICATED:
                raise
        # The session ran out: log in again, once.
        self._logged_in = False
        self._login()
        return self._raw(method, *params)

    def version(self) -> str:
        found = _VERSION.match(text(self._rpc("daemon.get_version"), 64))
        if found is None:
            raise wrong_kind()
        return found.group(1)

    def categories(self) -> list[str]:
        plugins = self._rpc("core.get_enabled_plugins")
        if not isinstance(plugins, list) or LABEL_PLUGIN not in plugins:
            return []
        labels = self._rpc("label.get_labels")
        return [label for label in labels if isinstance(label, str)] if isinstance(labels, list) else []

    def create_category(self) -> None:
        try:
            self._rpc("core.enable_plugin", LABEL_PLUGIN)
            self._rpc("label.add", self.target.category)
        except DelugeError as exc:
            if "already exists" not in exc.deluge_message:
                raise category_failed(self.target.category) from exc

    def add_torrent(self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str,
                    urgent: bool | None = None, seed: SeedGoal | None = None) -> str:
        # ⚠️ Never removed by Deluge itself: a "remove at ratio" of its own would take it before the import.
        options = {"add_paused": False, "remove_at_ratio": False}
        try:
            if content is not None:
                added = self._rpc("core.add_torrent_file", file_name, base64.b64encode(content).decode(), options)
            elif magnet:
                added = self._rpc("core.add_torrent_magnet", magnet, options)
            else:
                raise refused()
        except DelugeError as exc:
            if "already in session" in exc.deluge_message:
                raise refused("duplicate") from exc
            raise refused() from exc
        except ClientError as exc:
            raise self.unsure_on_timeout(exc, info_hash.lower()) from exc
        found = text(added, 64).lower()
        if not _HASH.match(found):
            raise refused()
        try:
            self._rpc("label.set_torrent", found, self.target.category)
        except DelugeError as exc:
            # Without its label nexbooks would never find the torrent again: it goes out, the files with it.
            logger.warning("Deluge did not label the torrent; it is removed again")
            try:
                self._rpc("core.remove_torrent", found, True)
            except ClientError:
                logger.warning("Deluge did not remove the torrent again")
            raise category_failed(self.target.category) from exc
        if seed is not None and seed.ratio is not None:
            try:
                self._rpc("core.set_torrent_options", [found],
                          {"stop_ratio": seed.ratio, "stop_at_ratio": True, "remove_at_ratio": False})
            except DelugeError:
                logger.info("Deluge did not take the seed ratio")
        if urgent:
            try:
                self._rpc("core.queue_top", [found])
            except DelugeError:
                logger.info("Deluge did not move the torrent to the top")
        return found

    def download_folder(self) -> str | None:
        folder = text(self._rpc("core.get_config_value", "download_location"))
        return folder.rstrip("/\\") if folder.startswith("/") or (len(folder) > 2 and folder[1] == ":") else None

    # --- Following the jobs ------------------------------

    def _label_of(self, info_hash: str) -> str | None:
        status = self._rpc("core.get_torrents_status", {"id": [info_hash]}, ["label"])
        entry = status.get(info_hash) if isinstance(status, dict) else None
        return text(entry.get("label"), 64) if isinstance(entry, dict) else None

    def _torrents(self, ids: list[str] | None = None) -> dict[str, Any]:
        wanted: dict[str, Any] = {"label": self.target.category}
        if ids is not None:
            wanted["id"] = ids
        status = self._rpc("core.get_torrents_status", wanted, list(KEYS))
        if not isinstance(status, dict):
            raise wrong_kind()
        return status

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        wanted = sorted({ref.lower() for ref in refs if ref})
        if not wanted:
            return {}
        found: dict[str, Job] = {}
        for item in (self._torrents(wanted)).values():
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        return found

    def category_jobs(self) -> list[CategoryJob]:
        found: list[CategoryJob] = []
        for item in (self._torrents()).values():
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _job(item: dict[str, Any]) -> Job | None:
        ref = text(item.get("hash"), 64).lower()
        if not ref:
            return None
        state = text(item.get("state"), 64)
        lowered = state.casefold()
        progress = number(item.get("progress"))
        eta = whole(item.get("eta"))
        size = whole(item.get("total_wanted")) or whole(item.get("total_size"))
        finished = item.get("is_finished") is True
        common: dict[str, Any] = {
            "ref": ref,
            "client_state": state,
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(progress),
        }
        if lowered == "error":
            return Job(state="problem", problem="client_error", **common)
        if size is None or size <= 0:
            return Job(state="queued", **common)
        if lowered == "seeding" or (finished and lowered in ("paused", "queued")):
            folder, name = text(item.get("save_path")), text(item.get("name"), 1024)
            if not folder or not name:
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            separator = "\\" if "\\" in folder and "/" not in folder else "/"
            path = folder.rstrip("/\\") + separator + name
            return Job(state="completed", remaining_seconds=0, path=path, **common)
        remaining = eta if eta is not None and eta > 0 else None
        if lowered in QUEUED_STATES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if lowered == "paused":
            return Job(state="paused", **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    def holds(self, ref: str) -> bool:
        # Deluge keeps labels in lower case.
        label = self._label_of(ref.lower())
        return label is not None and label.casefold() == self.target.category.casefold()

    def remove(self, ref: str, *, delete_files: bool) -> None:
        try:
            self._rpc("core.remove_torrent", ref.lower(), delete_files)
        except DelugeError as exc:
            if "not in session" not in exc.deluge_message:
                raise
            logger.info("Deluge did not have torrent %s any more", ref[:8])

    def seeding(self, refs: list[str]) -> dict[str, Seeding]:
        wanted = sorted({ref.lower() for ref in refs if ref})
        if not wanted:
            return {}
        wanted_filter = {"label": self.target.category, "id": wanted}
        status = self._rpc("core.get_torrents_status", wanted_filter, list(SEED_KEYS))
        if not isinstance(status, dict):
            raise wrong_kind()
        found: dict[str, Seeding] = {}
        for item in status.values():
            if not isinstance(item, dict):
                continue
            ref = text(item.get("hash"), 64).lower()
            if ref not in wanted:
                continue
            done = item.get("is_finished") is True
            stopped = done and text(item.get("state"), 64).casefold() == "paused"
            ratio = number(item.get("ratio"))
            stop_ratio = number(item.get("stop_ratio"))
            at_ratio = item.get("stop_at_ratio") is True and ratio is not None and stop_ratio is not None
            folder, name = text(item.get("save_path")), text(item.get("name"), 1024)
            separator = "\\" if "\\" in folder and "/" not in folder else "/"
            found[ref] = Seeding(
                ref=ref,
                done=done,
                stopped=stopped,
                goal_reached=stopped and at_ratio and ratio >= stop_ratio,
                seeding_seconds=whole(item.get("seeding_time")),
                ratio=ratio,
                path=folder.rstrip("/\\") + separator + name if folder and name else None,
            )
        return found

    def stop(self, ref: str) -> None:
        self._rpc("core.pause_torrent", [ref.lower()])
