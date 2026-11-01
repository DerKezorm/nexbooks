"""SABnzbd through its API (``<url>/api``), JSON only. (Measured on SABnzbd 5.1.)

* ``mode=version`` answers without a key and tells the kind. Every other call needs the full API key:
  without a key or with a wrong one SABnzbd answers 403 with a plain text, and the NZB key gets 403 on the
  queue and the categories. A 403 ``Access denied - Hostname verification failed`` means nexbooks's host
  name is not on SABnzbd's whitelist (``client_host_refused``). For any other 403, ``mode=auth`` tells
  whether the given key is the NZB key.
* Categories: names are stored in lower case. A missing one is created with ``set_config`` (``keyword`` and
  ``dir`` the name, ``pp`` 3, ``script`` None, ``priority`` -100) and must then be in ``get_cats``. A
  category folder ending in ``*`` switches job folders off, and sorting renames and moves files: both make
  it unusable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .base import (
    Answer,
    CategoryJob,
    ClientBase,
    ClientError,
    Job,
    auth_failed,
    category_failed,
    category_unusable,
    host_refused,
    join,
    longest_retention,
    looks_absolute,
    nfc,
    number,
    percent,
    refused,
    text,
    unreachable,
    whole,
    wrong_kind,
)

API_PATH = "/api"
PRIORITY_HIGH = "1"
PRIORITY_LOW = "-1"
HOST_REFUSED_TEXT = "hostname verification failed"
logger = logging.getLogger("nexbooks.procure")
_VERSION = re.compile(r"^\d+\.\d+")


class _Rejected(Exception):
    """SABnzbd answered with an error that is not about the key. Its text is never logged or answered."""


class _KeyRefused(Exception):
    """SABnzbd refused the key."""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on")


#: Labels of a job taken from the program, and how long one may be.
MAX_LABELS = 6
LABEL_LENGTH = 40


def _labels(value: Any) -> tuple[str, ...]:
    """What SABnzbd writes in ``labels`` of a queue entry (``DUPLICATE``, ``ENCRYPTED``, ``TOO LARGE`` ...), as it
    writes it: never trusted, so only short texts, once each, a few of them."""
    found: list[str] = []
    for item in value if isinstance(value, list) else []:
        label = " ".join(text(item, 200).split())[:LABEL_LENGTH]
        if label and label not in found and label != "queue_paused":
            found.append(label)
    return tuple(found[:MAX_LABELS])


def _names(values: Any) -> set[str]:
    return {text(name, 64).casefold() for name in values or [] if isinstance(name, str)}


MIB = 1024 * 1024


#: The history is filtered by nexbooks's job ids; this only caps a broken answer.
HISTORY_LIMIT = 500


QUEUED_STATUSES = frozenset({"queued", "grabbing", "propagating"})


NO_SPACE_MESSAGE = "unpacking failed, write error or disk is full?"


ENCRYPTED_PREFIX = "ENCRYPTED /"
ENCRYPTED_LABEL = "encrypted"


_TIME_LEFT = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2}):(\d{1,2})$")


def seconds_left(value: Any) -> int | None:
    """``0:10:32`` or ``1:02:03:04`` (days first) as seconds."""
    found = _TIME_LEFT.match(text(value, 32))
    if found is None:
        return None
    days, hours, minutes, seconds = (int(part) if part else 0 for part in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def job_path(storage: str, name: str) -> str:
    """The finished path: ``storage``, or the nearest folder above it named like the job."""
    if not storage:
        return storage
    separator = "\\" if "\\" in storage and "/" not in storage else "/"
    parts = storage.rstrip("/\\").split(separator)
    wanted = nfc(name)
    for end in range(len(parts), 1, -1):
        if parts[end - 1] and nfc(parts[end - 1]) == wanted:
            return separator.join(parts[:end])
    return storage


#: SABnzbd's ``fail_message`` as a code, by the start of its sentence (SABnzbd 4 and 5). The sentence itself is
#: never kept, logged or answered; the interface says it in the owner's language.
_FAIL_CODES = (
    ("repair failed", "repair_failed"),
    ("not enough repair", "repair_failed"),
    ("aborted, cannot be completed", "incomplete"),
    ("download might fail", "incomplete"),
    ("download failed - not on your server", "not_on_server"),
    ("unpacking failed, archive requires a password", "password"),
    ("aborted, encryption detected", "encrypted"),
    ("aborted, unwanted extension", "unwanted_extension"),
    ("duplicate", "duplicate"),
    ("unpacking failed", "unpack_failed"),
    ("post-processing was aborted", "aborted"),
    ("aborted", "aborted"),
)


def fail_code(message: str) -> str | None:
    """A code for SABnzbd's ``fail_message``; ``other`` for one nexbooks does not know, None for none."""
    lowered = message.strip().casefold()
    if not lowered:
        return None
    return next((code for start, code in _FAIL_CODES if lowered.startswith(start)), "other")


class Sabnzbd(ClientBase):
    kind = "sabnzbd"

    def _read(self, answer: Answer) -> Any:
        body = answer.text
        lowered = body.casefold()
        if answer.status in (401, 403):
            if HOST_REFUSED_TEXT in lowered:
                raise host_refused()
            raise _KeyRefused
        if answer.status == 404 or 300 <= answer.status < 400:
            raise wrong_kind()
        if answer.status >= 500:
            raise unreachable()
        if lowered.startswith("error"):
            if "api key" in lowered:
                raise _KeyRefused
            raise _Rejected
        try:
            data = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise wrong_kind() from exc
        if isinstance(data, dict) and data.get("status") is False:
            if "api key" in text(data.get("error"), 500).casefold():
                raise _KeyRefused
            raise _Rejected
        if not 200 <= answer.status < 300:
            raise wrong_kind()
        return data

    def _key_reason(self) -> str:
        """``nzb_key`` when SABnzbd calls the given key its NZB key, else ``wrong_key``."""
        if not self.target.secret:
            return "wrong_key"
        try:
            answer = self.call("GET", self.address(API_PATH),
                               params={"mode": "auth", "key": self.target.secret, "output": "json"})
            data = json.loads(answer.body)
        except (ValueError, RecursionError):
            return "wrong_key"
        return "nzb_key" if isinstance(data, dict) and data.get("auth") == "nzbkey" else "wrong_key"

    def _call(self, mode: str, *, key: bool = True, **params: Any) -> Any:
        query: dict[str, Any] = {"mode": mode, **params, "output": "json"}
        if key and self.target.secret:
            query["apikey"] = self.target.secret
        answer = self.call("GET", self.address(API_PATH), params=query)
        try:
            return self._read(answer)
        except _KeyRefused:
            raise auth_failed(self._key_reason()) from None

    def _config(self, section: str) -> Any:
        try:
            data = self._call("get_config", section=section)
        except _Rejected:
            return None
        config = data.get("config") if isinstance(data, dict) else None
        return config.get(section) if isinstance(config, dict) else None

    def version(self) -> str:
        try:
            data = self._call("version", key=False)
        except _Rejected as exc:
            raise wrong_kind() from exc
        version = text(data.get("version"), 40) if isinstance(data, dict) else ""
        if not _VERSION.match(version):
            raise wrong_kind()
        return version

    def categories(self) -> list[str]:
        try:
            data = self._call("get_cats")
        except _Rejected as exc:
            raise wrong_kind() from exc
        names = data.get("categories") if isinstance(data, dict) else data
        if not isinstance(names, list):
            raise wrong_kind()
        return [name.strip() for name in names if isinstance(name, str)]

    def has_category(self, names: list[str]) -> bool:
        wanted = self.target.category.casefold()
        return any(name.casefold() == wanted for name in names)

    def create_category(self) -> None:
        name = self.target.category
        try:
            self._call("set_config", section="categories", keyword=name, dir=name, pp="3", script="None",
                       priority="-100")
        except _Rejected as exc:
            raise category_failed(name) from exc

    def _category_entry(self, entries: Any) -> dict[str, Any] | None:
        wanted = self.target.category.casefold()
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and text(entry.get("name"), 64).casefold() == wanted:
                return entry
        return None

    def check_category(self) -> None:
        entry = self._category_entry(self._config("categories"))
        if entry is not None and text(entry.get("dir")).endswith("*"):
            raise category_unusable("no_job_folders")
        wanted = self.target.category.casefold()
        # SABnzbd 4.1 and later: sorters with the categories they apply to. Unknown shapes count as no sorting.
        sorters = self._config("sorters")
        for sorter in sorters if isinstance(sorters, list) else []:
            if isinstance(sorter, dict) and _truthy(sorter.get("is_active")):
                names = _names(sorter.get("sort_cats"))
                if wanted in names or "*" in names:
                    raise category_unusable("sorting")
        misc = self._config("misc")
        if isinstance(misc, dict):
            for kind in ("movie", "tv", "date"):
                names = _names(misc.get(f"{kind}_categories"))
                if _truthy(misc.get(f"enable_{kind}_sorting")) and (wanted in names or "*" in names):
                    raise category_unusable("sorting")

    def _job_category(self, job: str) -> str | None:
        """The category SABnzbd gave a job (from the queue, else the history); None when the job is not found."""
        for mode, name in (("queue", "cat"), ("history", "category")):
            try:
                data = self._call(mode, nzo_ids=job, start=0, limit=50)
            except _Rejected:
                continue
            part = data.get(mode) if isinstance(data, dict) else None
            slots = part.get("slots") if isinstance(part, dict) else None
            for slot in slots if isinstance(slots, list) else []:
                if isinstance(slot, dict) and text(slot.get("nzo_id"), 128) == job:
                    return text(slot.get(name), 64)
        return None

    def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        category = self.target.category
        query: dict[str, Any] = {"mode": "addfile", "cat": category, "output": "json"}
        if urgent is not None:
            query["priority"] = PRIORITY_HIGH if urgent else PRIORITY_LOW
        if self.target.secret:
            query["apikey"] = self.target.secret
        try:
            answer = self.call("POST", self.address(API_PATH), params=query,
                               files={"name": (file_name, content, "application/x-nzb")})
        except ClientError as exc:
            # ⚠️ Sent, but no answer in time: SABnzbd may have the job. Tracking looks for it by its name.
            raise self.unsure_on_timeout(exc) from exc
        try:
            data = self._read(answer)
        except _KeyRefused:
            raise auth_failed(self._key_reason()) from None
        except _Rejected:
            raise refused() from None
        ids = data.get("nzo_ids") if isinstance(data, dict) else None
        first = ids[0] if isinstance(ids, list) and ids else None
        if not isinstance(first, str) or not first.strip() or len(first) > 128:
            raise refused()
        job = first.strip()
        # ⚠️ SABnzbd takes a category it does not know without a word and puts the job into "*".
        given = self._job_category(job)
        if given is not None and given.casefold() != category.casefold():
            logger.warning("SABnzbd put a new job outside nexbooks's category; the job is removed again")
            try:
                self._call("queue", name="delete", value=job, del_files=1)
            except (ClientError, _Rejected):
                logger.warning("SABnzbd did not remove the job again")
            raise category_failed(category)
        return job

    def download_folder(self) -> str | None:
        """``complete_dir`` joined with the category's ``dir`` (or its name). None when either is missing or
        ``complete_dir`` is relative to SABnzbd's own folder, which nexbooks does not know."""
        misc = self._config("misc")
        complete = text(misc.get("complete_dir")) if isinstance(misc, dict) else ""
        entry = self._category_entry(self._config("categories"))
        folder = (text(entry.get("dir")) if entry is not None else "") or self.target.category
        if looks_absolute(folder):
            return folder.rstrip("/\\")
        if not looks_absolute(complete):
            return None
        return join(complete, folder.strip("/\\"))

    # --- Following the jobs ------------------------------

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        wanted = {ref for ref in refs if ref}
        if not wanted:
            return {}
        ids = ",".join(sorted(wanted))
        try:
            queue = self._call("queue", start=0, limit=0, category=self.target.category, nzo_ids=ids)
            history = self._call(
                "history", start=0, limit=HISTORY_LIMIT, category=self.target.category, nzo_ids=ids
            )
        except _Rejected as exc:
            raise wrong_kind() from exc
        found: dict[str, Job] = {}
        queue_part = queue.get("queue") if isinstance(queue, dict) else None
        queue_part = queue_part if isinstance(queue_part, dict) else {}
        queue_paused = _truthy(queue_part.get("paused"))
        for slot in queue_part.get("slots") or []:
            job = self._queue_job(slot, queue_paused) if isinstance(slot, dict) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        history_part = history.get("history") if isinstance(history, dict) else None
        history_part = history_part if isinstance(history_part, dict) else {}
        for slot in history_part.get("slots") or []:
            job = self._history_job(slot) if isinstance(slot, dict) else None
            if job is not None and job.ref in wanted:
                found[job.ref] = job
        return found

    def category_jobs(self) -> list[CategoryJob]:
        category = self.target.category
        try:
            queue = self._call("queue", start=0, limit=0, category=category)
            history = self._call("history", start=0, limit=HISTORY_LIMIT, category=category)
        except _Rejected as exc:
            raise wrong_kind() from exc
        found: list[CategoryJob] = []
        queue_part = queue.get("queue") if isinstance(queue, dict) else None
        queue_part = queue_part if isinstance(queue_part, dict) else {}
        queue_paused = _truthy(queue_part.get("paused"))
        wanted = category.casefold()
        for slot in queue_part.get("slots") or []:
            if not isinstance(slot, dict) or text(slot.get("cat"), 64).casefold() != wanted:
                continue
            job = self._queue_job(slot, queue_paused)
            if job is not None:
                found.append(CategoryJob(name=nfc(text(slot.get("filename"), 1024)), job=job))
        history_part = history.get("history") if isinstance(history, dict) else None
        history_part = history_part if isinstance(history_part, dict) else {}
        for slot in history_part.get("slots") or []:
            if not isinstance(slot, dict) or text(slot.get("category"), 64).casefold() != wanted:
                continue
            job = self._history_job(slot)
            if job is not None:
                found.append(CategoryJob(name=nfc(text(slot.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _queue_job(slot: dict[str, Any], queue_paused: bool) -> Job | None:
        ref = text(slot.get("nzo_id"), 128)
        status = text(slot.get("status"), 64)
        lowered = status.casefold()
        if not ref or lowered == "deleted":
            return None
        size_mb, left_mb = number(slot.get("mb")), number(slot.get("mbleft"))
        size = int(size_mb * MIB) if size_mb is not None else None
        common: dict[str, Any] = {"ref": ref, "client_state": status, "size_bytes": size}
        labels = _labels(slot.get("labels"))
        # (The older versions write it into the name of the job, the newer ones as a label. No password ever comes: the
        # job is over, whatever else its state says.)
        if text(slot.get("filename"), 1024).startswith(ENCRYPTED_PREFIX) or any(
                label.casefold() == ENCRYPTED_LABEL for label in labels):
            return Job(state="failed", reason="encrypted", **common)
        progress = number(slot.get("percentage"))
        if progress is None and size_mb and left_mb is not None:
            progress = (size_mb - left_mb) / size_mb * 100
        common["progress"] = percent(progress)
        forced = text(slot.get("priority"), 32).casefold() in ("force", "2")
        if lowered == "paused" or (queue_paused and not forced):
            why = (*labels, "queue_paused") if queue_paused and not forced else labels
            return Job(state="paused", why=why, **common)
        state = "queued" if lowered in QUEUED_STATUSES else "downloading"
        return Job(state=state, remaining_seconds=seconds_left(slot.get("timeleft")), **common)

    @staticmethod
    def _history_job(slot: dict[str, Any]) -> Job | None:
        ref = text(slot.get("nzo_id"), 128)
        if not ref:
            return None
        status = text(slot.get("status"), 64)
        lowered = status.casefold()
        name = text(slot.get("name"), 1024)
        common: dict[str, Any] = {
            "ref": ref,
            "client_state": status,
            "size_bytes": whole(slot.get("bytes")),
        }
        if name.startswith(ENCRYPTED_PREFIX):
            return Job(state="failed", reason="encrypted", **common)
        if lowered == "completed":
            storage = text(slot.get("storage"))
            if not storage:
                # ⚠️ Measured: at the first Completed the storage can still be empty for a second.
                return Job(state="downloading", progress=100.0, **common)
            return Job(state="completed", progress=100.0, remaining_seconds=0, path=job_path(storage, name), **common)
        if lowered == "failed":
            message = text(slot.get("fail_message"), 500)
            if message.casefold().startswith(NO_SPACE_MESSAGE):
                return Job(state="problem", problem="no_space", **common)
            leftovers = tuple(path for path in (text(slot.get("storage")), text(slot.get("path"))) if path)
            return Job(state="failed", reason="client_failed", detail=fail_code(message), leftovers=leftovers, **common)
        # Verifying, repairing, extracting, moving, running a script: still working on it.
        return Job(state="downloading", progress=100.0, **common)

    def usenet_retention(self) -> int | None:
        """``retention`` of every server with ``enable``, in days, 0 for unlimited (measured on SABnzbd 5.1.3). The
        section also holds the servers' passwords: only these two fields are read, nothing of it is logged."""
        servers = self._config("servers")
        listed = servers if isinstance(servers, list) else []
        active = [item for item in listed if isinstance(item, dict) and whole(item.get("enable"))]
        return longest_retention([whole(item.get("retention")) for item in active])

    def holds(self, ref: str) -> bool:
        given = self._job_category(ref)
        return given is not None and given.casefold() == self.target.category.casefold()

    def remove(self, ref: str, *, delete_files: bool) -> None:
        del_files = "1" if delete_files else "0"
        for mode, extra in (("queue", {}), ("history", {"archive": "0"})):
            try:
                self._call(mode, name="delete", value=ref, del_files=del_files, **extra)
            except _Rejected:
                logger.info("SABnzbd did not remove a job from its %s; it may be gone already", mode)

    def remove_imported(self, ref: str) -> None:
        try:
            self._call("history", name="delete", value=ref, archive="0")
        except _Rejected:
            logger.info("SABnzbd did not remove an imported job from its history")
