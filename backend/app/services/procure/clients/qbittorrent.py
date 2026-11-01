"""qBittorrent through its Web API v2. (Measured on qBittorrent 5.2, WebAPI 2.15.)

* Login with user name and password (``auth/login``): 204 with a session cookie named after the WebUI port,
  which the session keeps whatever its name. A 403 later logs in again, once. A 401 is a wrong password:
  nexbooks stops there, since five failures ban its address for an hour. Without a user name and a password
  nexbooks does not log in (qBittorrent's bypass for the local network).
* ⚠️ Never an ``Origin`` or ``Referer`` header: a foreign one is 401, even with the right password. The
  login body carries the password; no request body is ever logged.
* ``app/webapiVersion`` tells the kind and the version.
* A category is created with ``torrents/createCategory`` and an empty ``savePath`` and must then be in
  ``torrents/categories``; a second create answers 409, which is what nexbooks wants.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .base import (
    Answer,
    CategoryJob,
    ClientBase,
    ClientError,
    Job,
    SeedGoal,
    Seeding,
    auth_failed,
    category_failed,
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

LOGIN_OK_TEXTS = ("", "Ok.")
REFUSED_TEXT = "Fails."
_WEBAPI = re.compile(r"^\d+(?:\.\d+)+$")
#: From here ``torrents/add`` says ``stopped`` instead of ``paused`` (qBittorrent 5.0).
STOPPED_SINCE = (2, 11, 0)
#: From here ``torrents/add`` takes the share limits itself.
LIMITS_ON_ADD_SINCE = (2, 8, 1)
#: From here a torrent carries its own share limit action.
ACTION_SINCE = (2, 11, 2)


def _decimal(value: float) -> str:
    """A ratio as qBittorrent reads it: ``1``, ``0.5``, ``2.125``."""
    return format(value, ".3f").rstrip("0").rstrip(".") or "0"


def _webapi(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".")[:4] if part.isdigit())


#: How close a ratio counts as reached, as in Radarr.
RATIO_SLACK = 0.001


STOPPED_DONE_STATES = frozenset({"stoppedup", "pausedup"})


#: qBittorrent's ETA for "unknown".
ETA_UNKNOWN = 8_640_000


QUEUED_STATES = frozenset({"metadl", "forcedmetadl", "queueddl", "checkingdl", "checkingup", "checkingresumedata"})


PAUSED_STATES = frozenset({"stoppeddl", "pauseddl"})


COMPLETED_STATES = frozenset({"uploading", "stalledup", "queuedup", "forcedup", "stoppedup", "pausedup"})


ERROR_STATES = frozenset({"error", "missingfiles"})


STALLED_STATE = "stalleddl"


def _same_path(first: str, second: str) -> bool:
    return first.rstrip("/\\") == second.rstrip("/\\")


def _limit(item: dict[str, Any], resolved: str, own: str) -> float | None:
    """A share limit in force: the resolved one qBittorrent reports since 4.6, else the torrent's own; None for
    none (-1) or the default (-2)."""
    value = number(item.get(resolved)) if resolved in item else number(item.get(own))
    return value if value is not None and value >= 0 else None


def _seeding(item: dict[str, Any], moment: float) -> Seeding | None:
    ref = text(item.get("hash"), 64).lower()
    if not ref:
        return None
    state = text(item.get("state"), 64).casefold()
    progress = number(item.get("progress"))
    done = progress is not None and progress >= 1
    ratio = number(item.get("ratio"))
    if ratio is not None and ratio < 0:
        # -1 stands for a ratio beyond 9999.
        ratio = float("inf")
    seconds = whole(item.get("seeding_time"))
    reached = False
    ratio_limit = _limit(item, "max_ratio", "ratio_limit")
    if ratio_limit is not None and ratio is not None and ratio_limit - ratio <= RATIO_SLACK:
        reached = True
    time_limit = _limit(item, "max_seeding_time", "seeding_time_limit")
    if time_limit is not None and seconds is not None and seconds >= time_limit * 60:
        reached = True
    idle_limit = _limit(item, "max_inactive_seeding_time", "inactive_seeding_time_limit")
    last = whole(item.get("last_activity"))
    if idle_limit is not None and last is not None and last > 0 and moment - last >= idle_limit * 60:
        reached = True
    stopped = done and state in STOPPED_DONE_STATES
    forced = item.get("force_start") is True or state == "forcedup"
    path = text(item.get("content_path")) or None
    return Seeding(
        ref=ref,
        done=done,
        stopped=stopped,
        goal_reached=stopped and reached and not forced,
        forced=forced,
        seeding_seconds=seconds,
        ratio=ratio,
        path=path,
    )


class Qbittorrent(ClientBase):
    kind = "qbittorrent"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._logged_in = False

    def _login(self) -> None:
        if not self.target.username and not self.target.secret:
            self._logged_in = True
            return
        # ⚠️ No Origin, no Referer: qBittorrent answers a foreign one with 401 as if the password were wrong.
        answer = self.call("POST", self.address("/api/v2/auth/login"),
                           data={"username": self.target.username, "password": self.target.secret})
        if answer.status in (401, 403) or answer.text == REFUSED_TEXT:
            logger.warning("qBittorrent refused the login")
            raise auth_failed("wrong_password")
        if answer.status in (200, 204) and answer.text in LOGIN_OK_TEXTS:
            self._logged_in = True
            return
        raise wrong_kind()

    def _request(self, method: str, path: str, **kwargs: Any) -> Answer:
        if not self._logged_in:
            self._login()
        answer = self.call(method, self.address(path), **kwargs)
        if answer.status == 403:
            # The session ran out: log in again, once.
            self._logged_in = False
            self._login()
            answer = self.call(method, self.address(path), **kwargs)
        if answer.status in (401, 403):
            raise auth_failed("wrong_password")
        return answer

    def _json(self, path: str, **params: Any) -> Any:
        answer = self._request("GET", path, params=params or None)
        if answer.status != 200:
            raise wrong_kind()
        try:
            return json.loads(answer.body)
        except (ValueError, RecursionError) as exc:
            raise wrong_kind() from exc

    def version(self) -> str:
        answer = self._request("GET", "/api/v2/app/webapiVersion")
        version = answer.text
        if answer.status != 200 or len(version) > 20 or not _WEBAPI.match(version):
            raise wrong_kind()
        return version

    def categories(self) -> list[str]:
        data = self._json("/api/v2/torrents/categories")
        if not isinstance(data, dict):
            raise wrong_kind()
        return [name for name in data if isinstance(name, str)]

    def create_category(self) -> None:
        answer = self._request("POST", "/api/v2/torrents/createCategory",
                               data={"category": self.target.category, "savePath": ""})
        # 409: it exists already, which is what nexbooks wants.
        if answer.status not in (200, 409):
            raise category_failed(self.target.category)

    def add_torrent(self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str,
                    urgent: bool | None = None, seed: SeedGoal | None = None) -> str:
        webapi = _webapi(self.version())
        data: dict[str, str] = {"category": self.target.category,
                                ("stopped" if webapi >= STOPPED_SINCE else "paused"): "false"}
        goal = seed if seed is not None and not seed.empty else None
        if goal is not None and webapi >= LIMITS_ON_ADD_SINCE:
            if goal.ratio is not None:
                data["ratioLimit"] = _decimal(goal.ratio)
            if goal.seconds is not None:
                # qBittorrent counts the seeding time in minutes.
                data["seedingTimeLimit"] = str(max(1, goal.seconds // 60))
        if webapi >= ACTION_SINCE:
            data["shareLimitAction"] = "Stop"
        files: dict[str, Any] | None = None
        if content is not None:
            files = {"torrents": (file_name, content, "application/x-bittorrent")}
        elif magnet:
            data["urls"] = magnet
        else:
            raise refused()
        try:
            answer = self._request("POST", "/api/v2/torrents/add", data=data, files=files)
        except ClientError as exc:
            raise self.unsure_on_timeout(exc, info_hash.lower()) from exc
        if answer.status == 415:
            raise refused("file_invalid")
        if answer.status == 409:
            raise refused("duplicate")
        if answer.status != 200 or answer.text == REFUSED_TEXT or not info_hash:
            raise refused()
        if urgent:
            try:
                self._request("POST", "/api/v2/torrents/topPrio", data={"hashes": info_hash.lower()})
            except ClientError as exc:
                # With queueing off there is no queue: the torrent is in, that is what counts.
                logger.info("qBittorrent did not move the torrent to the top: %s", exc.code)
        return info_hash.lower()

    def download_folder(self) -> str | None:
        preferences = self._json("/api/v2/app/preferences")
        folder = text(preferences.get("save_path")) if isinstance(preferences, dict) else ""
        if not looks_absolute(folder):
            return None
        return join(folder, self.target.category)

    # --- Following the jobs ------------------------------

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        wanted = {ref.lower() for ref in refs if ref}
        if not wanted:
            return {}
        data = self._json("/api/v2/torrents/info", category=self.target.category, hashes="|".join(sorted(wanted)))
        if not isinstance(data, list):
            raise wrong_kind()
        found: dict[str, Job] = {}
        for item in data:
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        return found

    def category_jobs(self) -> list[CategoryJob]:
        data = self._json("/api/v2/torrents/info", category=self.target.category)
        if not isinstance(data, list):
            raise wrong_kind()
        found: list[CategoryJob] = []
        for item in data:
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
        remaining = eta if eta is not None and 0 <= eta < ETA_UNKNOWN else None
        size = whole(item.get("size"))
        if size is None or size <= 0:
            size = whole(item.get("total_size"))
        common: dict[str, Any] = {
            "ref": ref,
            "client_state": state,
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(progress * 100 if progress is not None else None),
        }
        if lowered in ERROR_STATES:
            return Job(state="problem", problem="client_error", **common)
        if lowered in COMPLETED_STATES and progress is not None and progress >= 1:
            content_path, save_path = text(item.get("content_path")), text(item.get("save_path"))
            if not content_path or (save_path and _same_path(content_path, save_path)):
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            return Job(state="completed", remaining_seconds=0, path=content_path, **common)
        if lowered in QUEUED_STATES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if lowered in PAUSED_STATES:
            return Job(state="paused", **common)
        if lowered == STALLED_STATE:
            return Job(state="downloading", problem="stalled", remaining_seconds=remaining, **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    def seeding(self, refs: list[str]) -> dict[str, Seeding]:
        wanted = {ref.lower() for ref in refs if ref}
        if not wanted:
            return {}
        data = self._json("/api/v2/torrents/info", category=self.target.category, hashes="|".join(sorted(wanted)))
        if not isinstance(data, list):
            raise wrong_kind()
        found: dict[str, Seeding] = {}
        moment = time.time()
        for item in data:
            seeding = _seeding(item, moment) if isinstance(item, dict) else None
            if seeding is not None and seeding.ref in wanted:
                found[seeding.ref] = seeding
        return found

    def holds(self, ref: str) -> bool:
        wanted = ref.lower()
        data = self._json("/api/v2/torrents/info", hashes=wanted)
        if not isinstance(data, list):
            raise wrong_kind()
        return any(isinstance(item, dict) and text(item.get("hash"), 64).lower() == wanted
                   and text(item.get("category"), 256) == self.target.category for item in data)

    def remove(self, ref: str, *, delete_files: bool) -> None:
        answer = self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": ref.lower(), "deleteFiles": "true" if delete_files else "false"},
        )
        if answer.status != 200:
            logger.info("qBittorrent did not remove a torrent (HTTP %d)", answer.status)
