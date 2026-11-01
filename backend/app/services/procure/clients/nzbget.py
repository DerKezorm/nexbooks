"""NZBGet through its JSON-RPC API (``<url>/jsonrpc``), user and password by HTTP Basic. (Measured on NZBGet 26.)

* ``version`` answers a text such as ``26.3``; without the right user or password NZBGet answers 401.
* **Categories** live in NZBGet's configuration (``CategoryN.Name``) and are read from there. A job with a category
  NZBGet does not know is still taken and filed below ``DestDir/<category>`` (``AppendCategoryDir`` on, the default),
  but nexbooks does not count that as a category: it cannot make one, because writing NZBGet's configuration would
  mean a restart of NZBGet, and ⚠️ ``saveconfig`` takes the whole list: saved with a single option, every other one
  was gone. So the test fails with ``client_category_missing`` until the operator has made the category in NZBGet; it
  never says "made" where nothing was made. A category with a ``DestDir`` of its own is where the jobs end.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from .. import gate
from .base import (
    CategoryJob,
    ClientBase,
    ClientError,
    Job,
    auth_failed,
    category_missing,
    join,
    longest_retention,
    looks_absolute,
    nfc,
    percent,
    refused,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexbooks.procure")

RPC_PATH = "/jsonrpc"
PRIORITY_HIGH = 50
PRIORITY_LOW = -50
_VERSION = re.compile(r"^\d+(\.\d+){0,3}(-[\w.]+)?$")


QUEUED_STATUSES = frozenset({"QUEUED"})


PAUSED_STATUSES = frozenset({"PAUSED"})


#: The text after ``FAILURE/`` or ``DELETED/`` as nexbooks's code.
_FAIL_CODES = {
    "PAR": "repair_failed",
    "UNPACK": "unpack_failed",
    "HEALTH": "incomplete",
    "MOVE": "other",
    "BAD": "other",
    "SCRIPT": "other",
    "MANUAL": "aborted",
    "COPY": "duplicate",
    "DUPE": "duplicate",
    "GOOD": "duplicate",
    "PASSWORD": "password",
    "DAMAGED": "repair_failed",
    "REPAIRABLE": "repair_failed",
}


def _size(item: dict[str, Any], low: str, high: str) -> int | None:
    lo, hi = whole(item.get(low)), whole(item.get(high))
    if lo is None:
        return None
    return (hi or 0) * (1 << 32) + lo


class Nzbget(ClientBase):
    kind = "nzbget"

    def _rpc(self, method: str, *params: Any) -> Any:
        answer = self.call(
            "POST", self.address(RPC_PATH),
            content=json.dumps({"method": method, "params": list(params), "id": 1}),
            headers={"Authorization": gate.basic_auth(self.target.username, self.target.secret),
                     "Content-Type": "application/json"},
        )
        if answer.status in (401, 403):
            raise auth_failed("wrong_password")
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except (ValueError, RecursionError) as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data:
            raise wrong_kind()
        if data.get("error"):
            raise refused()
        return data["result"]

    def _config(self) -> dict[str, str]:
        entries = self._rpc("config")
        if not isinstance(entries, list):
            raise wrong_kind()
        return {
            text(entry.get("Name"), 128): text(entry.get("Value"))
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("Name"), str)
        }

    def version(self) -> str:
        version = text(self._rpc("version"), 40)
        if not _VERSION.match(version):
            raise wrong_kind()
        return version

    def categories(self) -> list[str]:
        return [value for name, value in self._config().items() if re.fullmatch(r"Category\d+\.Name", name) and value]

    def create_category(self) -> None:
        """NZBGet's categories are made in its configuration, which nexbooks does not write (see above)."""
        raise category_missing(self.target.category)

    def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        priority = 0 if urgent is None else (PRIORITY_HIGH if urgent else PRIORITY_LOW)
        encoded = base64.b64encode(content).decode()
        try:
            # NZBFilename, Content, Category, Priority, AddToTop, AddPaused, DupeKey, DupeScore, DupeMode, PPParameters
            number = self._rpc("append", file_name, encoded, self.target.category, priority, False, False, "", 0,
                               "FORCE", [])
        except ClientError as exc:
            raise self.unsure_on_timeout(exc) from exc
        found = whole(number)
        if found is None or found <= 0:
            raise refused()
        return str(found)

    def download_folder(self) -> str | None:
        config = self._config()
        for name, value in config.items():
            found = re.fullmatch(r"Category(\d+)\.Name", name)
            if found and value == self.target.category:
                own = config.get(f"Category{found.group(1)}.DestDir", "")
                if looks_absolute(own):
                    return own.rstrip("/\\")
        folder = config.get("DestDir", "")
        if not looks_absolute(folder):
            return None
        append = config.get("AppendCategoryDir", "yes").casefold() not in ("no", "false", "0")
        return join(folder, self.target.category) if append else folder.rstrip("/\\")

    # --- Following the jobs ------------------------------

    def _queue_job(self, item: dict[str, Any], held: bool) -> Job | None:
        number = whole(item.get("NZBID"))
        if number is None:
            return None
        status = text(item.get("Status"), 64).upper()
        size = _size(item, "FileSizeLo", "FileSizeHi")
        left = _size(item, "RemainingSizeLo", "RemainingSizeHi")
        progress = percent((size - left) / size * 100) if size and left is not None else None
        common: dict[str, Any] = {
            "ref": str(number), "client_state": status, "size_bytes": size, "progress": progress,
        }
        if status in PAUSED_STATUSES or (held and status in QUEUED_STATUSES | {"DOWNLOADING"}):
            return Job(state="paused", **common)
        if status in QUEUED_STATUSES:
            return Job(state="queued", **common)
        return Job(state="downloading", **common)

    @staticmethod
    def _history_job(item: dict[str, Any]) -> Job | None:
        number = whole(item.get("NZBID"))
        if number is None or text(item.get("Kind"), 16).upper() not in ("", "NZB"):
            return None
        status = text(item.get("Status"), 64).upper()
        head, _slash, tail = status.partition("/")
        common: dict[str, Any] = {
            "ref": str(number), "client_state": status, "size_bytes": _size(item, "FileSizeLo", "FileSizeHi"),
        }
        folder = text(item.get("FinalDir")) or text(item.get("DestDir"))
        if head == "SUCCESS":
            return Job(state="completed", progress=100.0, remaining_seconds=0, path=folder or None, **common)
        if head == "WARNING" and tail == "SPACE":
            return Job(state="problem", problem="no_space", **common)
        if head in ("FAILURE", "WARNING", "DELETED"):
            return Job(
                state="failed", reason="client_failed", detail=_FAIL_CODES.get(tail, "other"),
                leftovers=(folder,) if folder else (), **common,
            )
        return Job(state="downloading", progress=100.0, **common)

    def _both(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        groups = self._rpc("listgroups", 0)
        history = self._rpc("history", False)
        status = self._rpc("status")
        if not isinstance(groups, list) or not isinstance(history, list):
            raise wrong_kind()
        held = isinstance(status, dict) and bool(status.get("DownloadPaused"))
        return (
            [item for item in groups if isinstance(item, dict)],
            [item for item in history if isinstance(item, dict)],
            held,
        )

    def _ours(self, item: dict[str, Any]) -> bool:
        return text(item.get("Category"), 64).casefold() == self.target.category.casefold()

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        wanted = {ref for ref in refs if ref}
        if not wanted:
            return {}
        groups, history, held = self._both()
        found: dict[str, Job] = {}
        for item in groups:
            job = self._queue_job(item, held) if self._ours(item) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        for item in history:
            job = self._history_job(item) if self._ours(item) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        return found

    def category_jobs(self) -> list[CategoryJob]:
        groups, history, held = self._both()
        found: list[CategoryJob] = []
        for item in groups:
            job = self._queue_job(item, held) if self._ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("NZBName"), 1024)), job=job))
        for item in history:
            job = self._history_job(item) if self._ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("Name"), 1024)), job=job))
        return found

    def usenet_retention(self) -> int | None:
        """``ServerN.Retention`` of every server with ``ServerN.Active`` set to yes, in days, 0 for unlimited."""
        config = self._config()
        values: list[int | None] = []
        for name, value in config.items():
            match = re.fullmatch(r"(Server\d+)\.Active", name)
            if match and value.casefold() == "yes":
                values.append(whole(config.get(f"{match.group(1)}.Retention")))
        return longest_retention(values)

    def holds(self, ref: str) -> bool:
        number = whole(ref)
        if number is None:
            return False
        groups, history, _held = self._both()
        return any(whole(item.get("NZBID")) == number and self._ours(item) for item in [*groups, *history])

    def remove(self, ref: str, *, delete_files: bool) -> None:
        number = whole(ref)
        if number is None:
            return
        for command in (
            "GroupFinalDelete" if delete_files else "GroupDelete",
            "HistoryFinalDelete" if delete_files else "HistoryDelete",
        ):
            try:
                self._rpc("editqueue", command, "", [number])
            except ClientError as exc:
                if exc.code != "client_refused":
                    raise
                logger.info("NZBGet did not %s a job; it may be gone already", command)

    def remove_imported(self, ref: str) -> None:
        number = whole(ref)
        if number is None:
            return
        try:
            self._rpc("editqueue", "HistoryDelete", "", [number])
        except ClientError as exc:
            if exc.code != "client_refused":
                raise
            logger.info("NZBGet did not hide an imported job in its history")
