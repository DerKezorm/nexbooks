"""Download programs: SABnzbd, NZBGet, qBittorrent, Transmission and Deluge behind one shape.

``open_client(target)`` gives the client of a kind; ``check`` is the test that saving runs: the kind (a
version call), the credentials and the category (created and read back), and whether the category is usable.
"""

from __future__ import annotations

from .base import (
    DEFAULT_CATEGORY,
    ERRORS,
    FAILED_DETAILS,
    JOB_STATES,
    KINDS,
    WITH_USERNAME,
    CategoryJob,
    Checked,
    ClientBase,
    ClientError,
    HandOverUnsure,
    Job,
    SeedGoal,
    Seeding,
    Target,
    ensure_category,
    longest_retention,
    remove_imported_own,
    remove_own,
)
from .deluge import Deluge
from .nzbget import Nzbget
from .qbittorrent import Qbittorrent
from .sabnzbd import Sabnzbd
from .transmission import Transmission


def open_client(target: Target) -> ClientBase:
    """The client of the target's kind; use it as ``with``."""
    if target.kind == "sabnzbd":
        return Sabnzbd(target)
    if target.kind == "qbittorrent":
        return Qbittorrent(target)
    if target.kind == "nzbget":
        return Nzbget(target)
    if target.kind == "transmission":
        return Transmission(target)
    if target.kind == "deluge":
        return Deluge(target)
    raise ValueError(f"no download client of the kind {target.kind!r}")


def check(target: Target) -> Checked:
    """The test of a program. The category is made when it is missing (and read back); the finished folder is asked
    for on the way, a program that does not tell leaves it out."""
    with open_client(target) as client:
        version = client.version()
        existed = ensure_category(client)
        client.check_category()
        try:
            folder = client.download_folder()
        except ClientError:
            folder = None
        has_categories = client.has_categories
    return Checked(version=version, category_existed=existed, folder=folder, has_categories=has_categories)


__all__ = [
    "DEFAULT_CATEGORY", "ERRORS", "FAILED_DETAILS", "JOB_STATES", "KINDS", "WITH_USERNAME", "CategoryJob", "Checked",
    "ClientBase", "ClientError", "Deluge", "HandOverUnsure", "Job", "Nzbget", "Qbittorrent", "Sabnzbd", "SeedGoal",
    "Seeding", "Target", "Transmission", "check", "ensure_category", "longest_retention", "open_client",
    "remove_imported_own", "remove_own",
]
