"""Where a finished job lies, as nexbooks sees it: the path a program reports, turned
into a path of this machine through the mappings the operator confirmed, and the checks that a finished download can
go on.

* **A path nexbooks may read** lies at or below a library root or a download root (``NEXBOOKS_LIBRARY_ROOTS``,
  ``NEXBOOKS_DOWNLOAD_ROOTS``), after every link is followed. Nothing else is read for a download, whatever a program
  reports or a mapping says.
* **A mapping** turns the front of a reported path into a local one (the longest match wins). A mapping is never made
  up from what a program says: it is confirmed by the operator, and only as one of the suggestions ``look_for`` makes
  itself, by looking for the name of the job below the roots (one place is a suggestion; more are a choice; none is
  said so).
* **A finished download goes on** when its files are seen and the library it goes into takes a file (a probe file is
  made and removed). Otherwise it is a problem with a code and the values the interface needs: ``path_not_found``
  (with what ``look_for`` found) or ``no_write``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...config import get_settings

logger = logging.getLogger("nexbooks.procure")

#: The folders ``look_for`` goes into below a root, how many in all, and how many places it names.
PROPOSE_DEPTH = 6
PROPOSE_MAX_FOLDERS = 20_000
PROPOSE_SHOWN = 5
PROPOSE_SHOWN_ROOTS = 10
WORKING_PREFIXES = ("_UNPACK_", "_FAILED_")


# --- Paths -------------------------------------------------------------------------------------------------------


def resolved(path: str | Path) -> Path | None:
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def inside(path: Path, folder: Path) -> bool:
    """Whether ``path`` is ``folder`` or lies in it, both resolved. A path that does not exist is not inside."""
    first, second = resolved(path), resolved(folder)
    return first is not None and second is not None and (first == second or first.is_relative_to(second))


def strictly_inside(path: Path, folder: Path) -> bool:
    first, second = resolved(path), resolved(folder)
    return first is not None and second is not None and first != second and first.is_relative_to(second)


def data_folder() -> Path:
    """The data folder (the database, the keys, the backups), with every link followed. Never read for a download."""
    folder = get_settings().data_dir
    return resolved(folder) or Path(os.path.abspath(folder))


def in_data(path: Path) -> bool:
    """Whether a resolved path is the data folder or lies in it."""
    data = data_folder()
    return path == data or path.is_relative_to(data)


def roots() -> list[Path]:
    """The download roots and the library roots that exist, resolved, the download ones first. A root in the data folder
    is none (the start refuses one, and a library may not lie there)."""
    settings = get_settings()
    found: list[Path] = []
    for entry in [*settings.download_root_list(), *settings.library_root_list()]:
        path = resolved(entry)
        if path is not None and path.is_dir() and path not in found and not in_data(path):
            found.append(path)
    return found


def download_roots() -> list[Path]:
    found: list[Path] = []
    for entry in get_settings().download_root_list():
        path = resolved(entry)
        if path is not None and path.is_dir() and path not in found:
            found.append(path)
    return found


def library_folders() -> list[Path]:
    """The folders of the libraries, resolved. A download is never read in one: what a program reports there is a book
    of a library, no release, and filing it would move it."""
    from ...db import SessionLocal
    from ...models import Library

    try:
        with SessionLocal() as db:
            paths = list(db.scalars(select(Library.path)))
    except Exception:  # noqa: BLE001 - no database to ask (the start): no library known
        return []
    return [found for found in (resolved(entry) for entry in paths) if found is not None]


def in_a_library(path: Path) -> bool:
    """Whether a resolved path is a folder of a library or lies in one."""
    return any(path == folder or path.is_relative_to(folder) for folder in library_folders())


def holds_a_library(path: Path) -> bool:
    """Whether a library or the data folder lies in a resolved path, however deep: taking away what is in it would take
    them with it."""
    return any(folder != path and folder.is_relative_to(path) for folder in [*library_folders(), data_folder()])


def visible(path: str | Path) -> Path | None:
    """The resolved path when it exists and lies at or below a root nexbooks may read, else None. The data folder and
    all below it is never visible, whatever root holds it and whatever link leads there."""
    found = resolved(path)
    if found is None or in_data(found):
        return None
    return found if any(found == root or found.is_relative_to(root) for root in roots()) else None


def remote_parts(path: str) -> tuple[str, list[str]] | None:
    """A program's path as its root (``/``, ``C:`` or empty) and its names; None with ``..`` or a NUL in it."""
    if not path or "\x00" in path:
        return None
    text = path.strip()
    root = ""
    drive = re.match(r"^([A-Za-z]:)[\\/]", text)
    if drive:
        root, text = drive.group(1), text[3:]
    elif text.startswith(("/", "\\")):
        root = "/"
    names = [name for name in re.split(r"[\\/]", text) if name and name != "."]
    if any(name == ".." for name in names):
        return None
    return root, names


def join_remote(root: str, names: list[str]) -> str:
    if root and root != "/":
        return root + "\\" + "\\".join(names)
    return root + "/".join(names)


def map_remote(reported: str, mappings: list[tuple[str, str]]) -> Path | None:
    """The local path of a reported path through the longest matching mapping (remote, local), or None."""
    parts = remote_parts(reported)
    if parts is None:
        return None
    best: tuple[int, Path] | None = None
    for remote_text, local in mappings:
        remote = remote_parts(remote_text)
        if remote is None or not local or remote[0] != parts[0]:
            continue
        length = len(remote[1])
        if parts[1][:length] == remote[1] and (best is None or length > best[0]):
            best = (length, Path(local, *parts[1][length:]))
    return best[1] if best is not None else None


def below_working_folder(path: Path) -> bool:
    """A folder of the path starts with ``_UNPACK_`` or ``_FAILED_``: the program still works on it."""
    return any(part.upper().startswith(WORKING_PREFIXES) for part in path.parts)


def local_path(reported: str | None, mappings: list[tuple[str, str]]) -> Path | None:
    """Where nexbooks sees a reported path: through a mapping, else at the same path; None while it sees nothing."""
    if not reported:
        return None
    candidates: list[str | Path] = []
    mapped = map_remote(reported, mappings)
    if mapped is not None:
        candidates.append(mapped)
    if remote_parts(reported) is not None:
        candidates.append(reported)
    for candidate in candidates:
        found = visible(candidate)
        if found is not None and not in_a_library(found):
            return found
    return None


# --- Looking for a job that is not where the program says ------------------------------------------------------------


@dataclass(frozen=True)
class Lookout:
    """What the search below the roots found for a reported path nexbooks does not see.

    ``reason``: ``proposed`` (one place: a mapping to confirm), ``twice`` (more than one: the operator picks one),
    ``cut`` (the search stopped at its limits), ``nowhere`` (looked everywhere: the folder is not mounted) or
    ``no_path`` (the program named no path)."""

    reason: str
    proposal: dict[str, str] | None = None
    matches: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()

    def values(self, reported: str | None) -> dict[str, Any]:
        choices = []
        for place in self.matches:
            pair = mapping_for(reported or "", Path(place))
            if pair is not None:
                choices.append({**pair, "place": place})
        return {"proposal": self.proposal, "reported": reported or None, "search": self.reason,
                "matches": list(self.matches), "choices": choices, "roots": list(self.roots)}


def look_for(reported: str | None, search_roots: list[Path] | None = None) -> Lookout:
    """Looks for the last name of the reported path below the roots; one match is a proposal, the longest pair of
    prefixes that differ with at least one name left in the remote one."""
    below = search_roots if search_roots is not None else roots()
    shown = tuple(str(root) for root in below[:PROPOSE_SHOWN_ROOTS])
    parts = remote_parts(reported or "")
    if parts is None or not parts[1]:
        return Lookout("no_path", roots=shown)
    root, names = parts
    wanted = names[-1]
    matches: list[Path] = []
    visited = 0
    cut = False
    # The data folder is never walked into: a root that holds it meets it as a folder of its own, and as links are not
    # followed the path of an entry is the path it is under, so the text is all the comparison needs.
    data_text = os.path.normcase(str(data_folder()))
    # Nor into the folders of the libraries: a download is none of their books.
    library_texts = {os.path.normcase(str(folder)) for folder in library_folders()}
    for base in below:
        if os.path.normcase(str(base)) in library_texts:
            continue
        stack: list[tuple[Path, int]] = [(base, 0)]
        while stack and len(matches) < PROPOSE_SHOWN:
            if visited >= PROPOSE_MAX_FOLDERS:
                cut = True
                break
            folder, depth = stack.pop()
            visited += 1
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False) and os.path.normcase(entry.path) in {data_text, *library_texts}:
                    continue
                path = Path(entry.path)
                if entry.name == wanted and path not in matches:
                    matches.append(path)
                if entry.is_dir(follow_symlinks=False):
                    if depth < PROPOSE_DEPTH:
                        stack.append((path, depth + 1))
                    else:
                        cut = True
    if len(matches) == 1:
        return Lookout("proposed", _pair(root, names, list(matches[0].parts), _depth_of_root(matches[0], below)),
                       roots=shown)
    if matches:
        return Lookout("twice", matches=tuple(str(path) for path in matches[:PROPOSE_SHOWN]), roots=shown)
    return Lookout("cut" if cut else "nowhere", roots=shown)


#: How long the answer of a search for a path is kept: a folder that is not mounted yet does not turn up in a minute,
#: and a search over a big tree costs seconds. A change of the roots (the download folder) asks again at once.
LOOKOUT_KEEP_SECONDS = 600.0
_lookouts: dict[str, tuple[float, tuple[str, ...], Lookout]] = {}
_lookout_lock = threading.Lock()


def clock() -> float:
    return time.monotonic()


def forget_lookouts() -> None:
    with _lookout_lock:
        _lookouts.clear()


def kept_lookout(reported: str | None) -> Lookout | None:
    """The answer kept for this path, when it is not older than ``LOOKOUT_KEEP_SECONDS`` and the roots are the same."""
    signature = tuple(str(root) for root in roots())
    with _lookout_lock:
        found = _lookouts.get(reported or "")
    if found is None:
        return None
    when, kept_roots, lookout = found
    return lookout if clock() - when < LOOKOUT_KEEP_SECONDS and kept_roots == signature else None


def lookout_for(reported: str | None, *, may_search: bool = True) -> Lookout | None:
    """``look_for``, kept per path: a path that was searched a short while ago, with the same roots, is not searched
    again. None when there is no kept answer and ``may_search`` is false."""
    kept = kept_lookout(reported)
    if kept is not None or not may_search:
        return kept
    signature = tuple(str(root) for root in roots())
    lookout = look_for(reported)
    with _lookout_lock:
        if len(_lookouts) > 500:
            _lookouts.clear()
        _lookouts[reported or ""] = (clock(), signature, lookout)
    return lookout


def mapping_for(reported: str, local: Path) -> dict[str, str] | None:
    """The mapping that turns a reported path into a folder that was chosen, or None. Only when the chosen folder
    carries the reported name: a mapping swaps the front of a path and keeps its end, so a folder with another name
    would be a mapping for this one download only, and wrong for every later one."""
    parts = remote_parts(reported)
    local_parts = list(local.parts)
    if parts is None or not parts[1] or not local_parts or parts[1][-1] != local_parts[-1]:
        return None
    return _pair(parts[0], parts[1], local_parts, _depth_of_root(local, roots()))


def _depth_of_root(place: Path, below: list[Path]) -> int:
    """How many parts the deepest root that holds ``place`` has (``/downloads`` has two): the local side of a mapping
    reaches at least that far. 0 when no root holds it."""
    depth = 0
    for base in below:
        if place == base or place.is_relative_to(base):
            depth = max(depth, len(base.parts))
    return depth


def _pair(root: str, names: list[str], local_parts: list[str], least: int = 0) -> dict[str, str]:
    """The two fronts that differ: the names both paths end in alike are taken off, at least one name left in the
    remote one. The local front is never shorter than the root of nexbooks that holds the place (``least`` parts): a
    program that reports ``/data/downloads/complete/x`` for what nexbooks sees as ``/downloads/complete/x`` makes
    ``/data/downloads`` to ``/downloads``, not ``/data`` to ``/``, which reads like a way to the top of the disk. Both
    fronts grow by the same names, so the mapping stays right for this place and only gets narrower."""
    same = 0
    while (same < len(names) - 1 and same < len(local_parts) - 1
           and names[len(names) - 1 - same] == local_parts[len(local_parts) - 1 - same]):
        same += 1
    while same > 0 and len(local_parts) - same < least:
        same -= 1
    return {"remote": join_remote(root, names[: len(names) - same]),
            "local": str(Path(*local_parts[: len(local_parts) - same]))}


# --- Can the download go on? -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Arrival:
    """The answer for a finished download: ``path`` is where nexbooks sees the files; ``problem`` and ``values`` say why
    it cannot go on."""

    path: Path | None = None
    problem: str = ""
    values: dict[str, Any] | None = None


def arrive(reported: str | None, mappings: list[tuple[str, str]], library: Any) -> Arrival:
    """Whether a finished job can be filed: its files are seen, and the library (``id`` and ``path``; None when it is
    gone) takes a file."""
    path = local_path(reported, mappings)
    if path is None or below_working_folder(path):
        lookout = lookout_for(reported) or look_for(reported)
        return Arrival(problem="path_not_found", values=lookout.values(reported))
    if library is None:
        return Arrival(path=path, problem="no_write", values={"library_id": None, "reason": "library_gone"})
    from . import formats

    result = formats.probe_library(library.path)
    if not result["writable"]:
        return Arrival(path=path, problem="no_write", values={"library_id": library.id, "reason": result["reason"]})
    return Arrival(path=path)
