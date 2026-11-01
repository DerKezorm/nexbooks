"""What the download programs have in common: the target, the errors, and the test that saving runs.

* Every call goes through the gate (``gate.Session``): the address is the operator's, the host is the only
  one asked, the answer is read up to 10 MB and for 20 seconds. SABnzbd's key travels as ``apikey`` in the
  query, which the log masks; the password of the others only in a header or in the login body, and no
  request body or answer body is ever logged.
* A program that cannot be reached, or does not answer in time, is ``client_unreachable``
  (``client_timeout``). Credentials it refuses are ``client_auth_failed`` with ``reason`` (``wrong_key``,
  ``nzb_key``, ``wrong_password``); a SABnzbd that refuses nexbooks's host name ``client_host_refused``, a
  Transmission whose whitelist refuses nexbooks's address ``client_address_refused``; something else at the
  address ``client_wrong_kind``. A program's own sentences never reach an answer or the log.
* ⚠️ What a program answers is never trusted: names and numbers are read tolerantly and checked where they
  are used.
* The category is nexbooks's own (``nexbooks``). Saving creates it when it is missing and reads it back; a
  program that would move the files of that category behind nexbooks's back (SABnzbd with sorting on, job
  folders off) is refused.

Handing a release over: ``add_nzb`` and ``add_torrent`` give the program's name for the job (SABnzbd's
``nzo_id``, NZBGet's number, the info hash of a torrent). ⚠️ A hand-over that went out but got no answer in time is
``HandOverUnsure``: the program may have taken it, and only a look into its category tells.

Following the jobs: ``jobs`` gives the state of the jobs nexbooks knows by their names,
``category_jobs`` every job of nexbooks's category whoever put it there, ``remove`` takes a job away (with its files),
``seeding`` and ``stop`` are for torrents that are filed and seed on, ``usenet_retention`` tells how long the news
servers keep articles. A job is read in nexbooks's words (``Job``); ⚠️ what a program says about a job is never trusted.
"""

from __future__ import annotations

import logging
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any, ClassVar, Self

import httpx

from .. import gate

logger = logging.getLogger("nexbooks.procure")

DEFAULT_CATEGORY = "nexbooks"
KINDS = ("sabnzbd", "nzbget", "qbittorrent", "transmission", "deluge")
#: The kinds that log in with a user name and a password; SABnzbd takes one key, Deluge one password.
WITH_USERNAME = frozenset({"qbittorrent", "nzbget", "transmission"})
#: Every call to a program has its own 20 seconds; connecting gives up after 10.
SECONDS = 20.0


class ClientError(Exception):
    """An answer of a download program nexbooks cannot use. ``code`` is what the interface words."""

    def __init__(self, code: str, status: int = 502, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.values = values


def unreachable() -> ClientError:
    return ClientError("client_unreachable")


def auth_failed(reason: str) -> ClientError:
    return ClientError("client_auth_failed", reason=reason)


def host_refused() -> ClientError:
    return ClientError("client_host_refused")


def address_refused() -> ClientError:
    return ClientError("client_address_refused")


def wrong_kind() -> ClientError:
    return ClientError("client_wrong_kind")


def category_failed(category: str) -> ClientError:
    return ClientError("client_category_failed", category=category)


def category_missing(category: str) -> ClientError:
    return ClientError("client_category_missing", category=category)


def category_unusable(reason: str) -> ClientError:
    return ClientError("client_category_unusable", reason=reason)


def refused(reason: str | None = None) -> ClientError:
    return ClientError("client_refused", **({"reason": reason} if reason else {}))


class HandOverUnsure(ClientError):
    """The program did not answer a hand-over in time, but the request may have gone out: it may have the release.
    ``ref`` is the job's name when nexbooks knows it beforehand (the info hash of a torrent), else empty."""

    def __init__(self, ref: str = "") -> None:
        super().__init__("client_timeout", 504)
        self.ref = ref


@dataclass(frozen=True)
class SeedGoal:
    """What a torrent is to seed: a ratio and seconds of seeding; None for either leaves the program's own."""

    ratio: float | None = None
    seconds: int | None = None

    @property
    def empty(self) -> bool:
        return self.ratio is None and self.seconds is None


#: For the OpenAPI ``responses`` of every route that tests a program.
ERRORS = (
    (502, "client_unreachable"), (502, "client_auth_failed"), (502, "client_host_refused"),
    (502, "client_address_refused"), (502, "client_wrong_kind"), (502, "client_category_failed"),
    (502, "client_category_missing"), (502, "client_category_unusable"), (502, "client_refused"),
    (502, "client_blocked"), (504, "client_timeout"),
)


@dataclass(frozen=True)
class Target:
    #: sabnzbd, nzbget, qbittorrent, transmission or deluge.
    kind: str
    #: The base address, without user info or query.
    url: str
    #: The user name of qBittorrent, NZBGet or Transmission; empty for SABnzbd and Deluge.
    username: str = ""
    #: ⚠️ SABnzbd's API key or the password, in the clear, in memory only while the program is asked.
    secret: str = field(default="", repr=False)
    category: str = DEFAULT_CATEGORY


#: What a job can be for following it.
JOB_STATES = ("queued", "downloading", "paused", "completed", "failed", "problem")


@dataclass(frozen=True)
class Job:
    """One download as a program reports it, in nexbooks's words."""

    #: The program's name for the job (the info hash of a torrent in lower case).
    ref: str
    #: One of ``JOB_STATES``.
    state: str
    #: The program's own state, for the log only.
    client_state: str
    #: 0 to 100.
    progress: float | None = None
    remaining_seconds: int | None = None
    size_bytes: int | None = None
    #: The path the program reports for a finished job, as it reports it; None while unknown.
    path: str | None = None
    #: A problem or a hint: ``client_error``, ``no_space``, ``path_not_found``, ``stalled``.
    problem: str | None = None
    #: For ``failed``: ``client_failed`` or ``encrypted``.
    reason: str | None = None
    #: For ``failed``: what the program said, as a code (``FAILED_DETAILS``).
    detail: str | None = None
    #: For ``failed``: the folders the program may have left behind, as it names them.
    leftovers: tuple[str, ...] = ()
    #: For ``paused``: why the program holds the job, in the words it gives (SABnzbd's labels such as ``DUPLICATE`` or
    #: ``TOO LARGE``, as it writes them) and ``queue_paused`` when the whole queue stands; empty when it names nothing.
    why: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryJob:
    """A job in nexbooks's category with the program's name for it, to find a hand-over that got no answer and the jobs
    nobody asked nexbooks for."""

    name: str
    job: Job


@dataclass(frozen=True)
class Seeding:
    """A finished torrent as the program reports it after it was filed, for its seed goal."""

    ref: str
    #: The download is complete.
    done: bool
    #: The program has the torrent stopped.
    stopped: bool
    #: The program stopped it at a goal of its own (qBittorrent's share limits, Transmission's ``isFinished``, Deluge's
    #: stop ratio).
    goal_reached: bool
    #: Started by force (qBittorrent): never counted as at its goal.
    forced: bool = False
    seeding_seconds: int | None = None
    ratio: float | None = None
    #: The torrent's files as the program names them.
    path: str | None = None


#: The ways a failed job is told to nexbooks's users (the words are in the interface).
FAILED_DETAILS = ("repair_failed", "unpack_failed", "incomplete", "not_on_server", "password", "encrypted",
                  "unwanted_extension", "duplicate", "aborted", "other")


@dataclass(frozen=True)
class Checked:
    version: str
    #: Whether the category was there before the test (it is made when it was not).
    category_existed: bool
    #: Where the program puts finished jobs of the category, as it names the folder; None when it does not tell.
    folder: str | None
    #: False for a program that has no categories at all (Transmission): nexbooks puts its jobs in a folder of its own.
    has_categories: bool = True


# --- Tolerant reading ------------------------------------------------------------------------------------------------


def number(value: Any) -> float | None:
    """A finite number from a number or a numeric text (SABnzbd sends numbers as texts), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(100.0, max(0.0, value)), 1)


def nfc(value: str) -> str:
    """A name in Unicode NFC: a file name as a Mac writes it and as nexbooks writes it are the same name."""
    return unicodedata.normalize("NFC", value)


def longest_retention(values: list[int | None]) -> int | None:
    """How long several news servers keep articles together: the longest, and no limit as soon as one has none (0 or
    unknown), or when there is none at all. A release is there while any server still has it."""
    if not values or any(value is None or value <= 0 for value in values):
        return None
    return max(value for value in values if value is not None)


def whole(value: Any) -> int | None:
    found = number(value)
    return int(found) if found is not None else None


def text(value: Any, limit: int = 4096) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def join(folder: str, name: str) -> str:
    """A path in the program's own spelling: a Windows folder with backslashes, every other one with slashes."""
    separator = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("/\\") + separator + name


def looks_absolute(folder: str) -> bool:
    return folder.startswith("/") or (len(folder) > 2 and folder[1] == ":" and folder[2] in "\\/")


@dataclass(frozen=True)
class Answer:
    status: int
    headers: httpx.Headers
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace").strip()


class ClientBase:
    """Use as ``with open_client(target) as client: ...``."""

    kind: ClassVar[str] = ""
    #: Whether the program knows categories (or labels) a job can be given. Transmission does not.
    has_categories: ClassVar[bool] = True

    def __init__(self, target: Target) -> None:
        self.target = target
        self._session: gate.Session | None = None

    def __enter__(self) -> Self:
        self._session = gate.Session(self.target.url, label=self.kind, seconds=SECONDS)
        self._session.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session is not None:
            self._session.__exit__(*exc)
            self._session = None

    def address(self, path: str) -> str:
        return self.target.url.rstrip("/") + path

    def call(self, method: str, url: str, **kwargs: Any) -> Answer:
        """One request; no answer at all is ``client_unreachable``."""
        if self._session is None:
            raise RuntimeError("A download client is used outside of 'with'")
        try:
            answer = self._session.request(method, url, **kwargs)
        except gate.GateError as exc:
            if exc.code == "timeout":
                raise ClientError("client_timeout", 504) from exc
            if exc.code in ("refused", "host_not_allowed", "redirect", "bad_address"):
                raise ClientError("client_blocked") from exc
            if exc.code == "too_large":
                raise wrong_kind() from exc
            raise unreachable() from exc
        return Answer(answer.status_code, answer.headers, answer.content)

    # The operations every kind has. ------------------------------------------------------------------------------

    def version(self) -> str:
        """The program's version; tells the kind."""
        raise NotImplementedError

    def categories(self) -> list[str]:
        raise NotImplementedError

    def has_category(self, names: list[str]) -> bool:
        return self.target.category in names

    def create_category(self) -> None:
        raise NotImplementedError

    def check_category(self) -> None:
        """Raises ``client_category_unusable`` when the program would move files behind nexbooks's back."""

    def download_folder(self) -> str | None:
        """Where the program puts finished jobs of the category, as it names the folder; None when unknown."""
        return None

    # Handing a release over. ------------------------------------------------------------------------------------

    def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        """An NZB into the category; the program's name for the job. ``urgent``: True for a book that is missing (to
        the front of the queue), False for an upgrade (to the back), None for neither."""
        raise refused()

    def add_torrent(self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str,
                    urgent: bool | None = None, seed: SeedGoal | None = None) -> str:
        """A torrent file or a magnet link into the category; the info hash. ``seed``: the goal the tracker asks
        for, handed to the program where it takes it."""
        raise refused()

    # Following the jobs. ----------------------------------------------------------------------------------------

    def jobs(self, refs: list[str]) -> dict[str, Job]:
        """The jobs among ``refs`` in nexbooks's category, by name. A job the program no longer has is missing."""
        raise NotImplementedError

    def category_jobs(self) -> list[CategoryJob]:
        """Every job in nexbooks's category, whoever put it there."""
        raise NotImplementedError

    def remove(self, ref: str, *, delete_files: bool) -> None:
        """Takes a job away. A job that is gone already is no failure. ⚠️ Only through ``remove_own``: the program
        removes by name, whatever category the job is in."""
        raise NotImplementedError

    def holds(self, ref: str) -> bool:
        """Whether the program has the job in nexbooks's category now. A torrent is known by its info hash, and the
        same torrent may be another's (the operator seeds it himself, another tool put it there): only what is in
        nexbooks's category is nexbooks's to take away."""
        found = self.jobs([ref])
        return ref in found or ref.lower() in found

    def remove_imported(self, ref: str) -> None:
        """After a book was filed. SABnzbd and NZBGet forget the job; a torrent stays and seeds until its goal."""

    def seeding(self, refs: list[str]) -> dict[str, Seeding]:
        """The finished torrents among ``refs`` in nexbooks's category; nothing for Usenet."""
        return {}

    def stop(self, ref: str) -> None:
        """Stops a torrent that reached its seed goal (Transmission and Deluge have no seed time of their own)."""
        raise refused()

    def usenet_retention(self) -> int | None:
        """How many days the program's active news servers keep articles (``longest_retention``); None for no limit."""
        return None

    def unsure_on_timeout(self, exc: ClientError, ref: str = "") -> ClientError:
        """A hand-over that timed out may have reached the program: unsure, not failed."""
        if exc.code == "client_timeout":
            return HandOverUnsure(ref)
        return exc


def remove_own(client: ClientBase, ref: str, *, delete_files: bool) -> bool:
    """Takes the job away only when it is in nexbooks's category (``holds``). Returns whether it was taken away; a job
    that is elsewhere now (or gone) is left as it is, and that is no failure."""
    if not ref or not client.holds(ref):
        logger.info("The %s job is not in nexbooks's category; it is left alone", client.kind)
        return False
    client.remove(ref, delete_files=delete_files)
    return True


def remove_imported_own(client: ClientBase, ref: str) -> bool:
    """``remove_imported`` for a job in nexbooks's category only."""
    if not ref or not client.holds(ref):
        logger.info("The %s job is not in nexbooks's category; it is left alone", client.kind)
        return False
    client.remove_imported(ref)
    return True


def ensure_category(client: ClientBase) -> bool:
    """Whether the category existed. A missing one is created and read back. A program without categories has none to
    look for."""
    if not client.has_categories:
        return False
    if client.has_category(client.categories()):
        return True
    client.create_category()
    if not client.has_category(client.categories()):
        logger.warning("The %s category was not there after creating it", client.kind)
        raise category_failed(client.target.category)
    logger.info("Category created in %s", client.kind)
    return False
