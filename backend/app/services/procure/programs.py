"""The download programs of the operator: making, changing, testing and removing them, and the path mappings.

Saving tests: a program whose address, kind, user, key or category changed is asked first (version, login, the category
made and read back) and saved only when that went through, so that every saved program has been seen working once. What
is saved is kept as the test found it (version, when). The key or password is sealed (``security.encrypt_secret``) and
never read back.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import CLIENT_PROTOCOL, DownloadClient, PathMapping
from ...security import decrypt_secret, encrypt_secret
from . import gate
from .clients import DEFAULT_CATEGORY, KINDS, WITH_USERNAME, Checked, ClientError, Target, check

logger = logging.getLogger("nexbooks.procure")

NAME_MAX = 100
CATEGORY_MAX = 64
SECRET_MAX = 512
USER_MAX = 256
MAX_CLIENTS = 20
SECRET_CONTEXT = "client-secret"
_CATEGORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
DEFAULT_NAMES = {"sabnzbd": "SABnzbd", "nzbget": "NZBGet", "qbittorrent": "qBittorrent", "transmission": "Transmission",
                 "deluge": "Deluge"}


def protocol_of(row: DownloadClient) -> str:
    return CLIENT_PROTOCOL[row.kind]


def valid_kind(kind: str) -> str:
    if kind not in KINDS:
        raise error("invalid_input", "That kind of program is not known.", 422, field="kind")
    return kind


def valid_address(url: str) -> str:
    try:
        return gate.normalize_address(url)
    except gate.GateError as exc:
        raise error("address_invalid", "This address cannot be used.", 422, field="url") from exc


def valid_name(db: Session, name: str, *, ignore_id: int | None = None) -> str:
    text = " ".join((name or "").split())
    if not text or len(text) > NAME_MAX:
        raise error("invalid_input", "The name is not valid.", 422, field="name")
    taken = db.scalar(select(DownloadClient.id).where(func.lower(DownloadClient.name) == text.lower(),
                                                      DownloadClient.id != (ignore_id or 0)))
    if taken is not None:
        raise error("name_taken", "There is a program with this name.", 409, field="name")
    return text


def valid_category(category: str) -> str:
    text = (category or "").strip()
    if not text or len(text) > CATEGORY_MAX or not _CATEGORY.match(text):
        raise error("invalid_input", "The category is not valid.", 422, field="category")
    return text


def valid_priority(value: int) -> int:
    if not 1 <= value <= 50:
        raise error("invalid_input", "The priority is not valid.", 422, field="priority")
    return value


def valid_credentials(kind: str, username: str, secret: str | None) -> tuple[str, str | None]:
    user = (username or "").strip() if kind in WITH_USERNAME else ""
    if len(user) > USER_MAX or any(ord(char) < 32 for char in user):
        raise error("invalid_input", "The user name is not valid.", 422, field="username")
    if secret is not None and (len(secret) > SECRET_MAX or any(ord(char) < 32 and char not in "\t" for char in secret)):
        raise error("invalid_input", "The key or password is not valid.", 422, field="secret")
    return user, secret


def secret_of(row: DownloadClient) -> str:
    return decrypt_secret(row.secret_enc, SECRET_CONTEXT)


def target_of(row: DownloadClient) -> Target:
    return Target(kind=row.kind, url=row.url, username=row.username, secret=secret_of(row), category=row.category)


def run_check(target: Target) -> Checked:
    """The test of a program; ``ClientError`` says what is wrong."""
    return check(target)


def create(db: Session, *, kind: str, name: str | None, url: str, username: str, secret: str | None, category: str,
           priority: int, enabled: bool) -> tuple[DownloadClient, Checked]:
    valid_kind(kind)
    if (db.scalar(select(func.count(DownloadClient.id))) or 0) >= MAX_CLIENTS:
        raise error("too_many_clients", "nexbooks holds at most 20 download programs.", 409)
    address = valid_address(url)
    user, secret = valid_credentials(kind, username, secret)
    chosen = valid_category(category or DEFAULT_CATEGORY)
    label = valid_name(db, name or DEFAULT_NAMES[kind])
    rank = valid_priority(priority)
    checked = run_check(Target(kind=kind, url=address, username=user, secret=secret or "", category=chosen))
    moment = clock.now()
    row = DownloadClient(name=label, kind=kind, url=address, username=user,
                         secret_enc=encrypt_secret(secret or "", SECRET_CONTEXT), category=chosen,
                         priority=rank, enabled=enabled, version=checked.version, last_error="",
                         checked_at=moment, created_at=moment, updated_at=moment)
    db.add(row)
    db.commit()
    logger.info("Download program added kind=%s", kind)
    return row, checked


def update(db: Session, row: DownloadClient, changes: dict[str, Any]) -> tuple[DownloadClient, Checked | None]:
    """Changes a program. ``changes`` holds only what the operator sent; a key or password that is missing (or None)
    stays. Whatever touches the connection is tested first."""
    kind = valid_kind(changes["kind"]) if changes.get("kind") is not None else row.kind
    url = valid_address(changes["url"]) if changes.get("url") is not None else row.url
    user, secret = valid_credentials(kind, changes["username"] if changes.get("username") is not None else row.username,
                                     changes.get("secret"))
    if changes.get("secret") == "":
        secret = None
    category = valid_category(changes["category"]) if changes.get("category") is not None else row.category
    name = valid_name(db, changes["name"], ignore_id=row.id) if changes.get("name") is not None else row.name
    priority = valid_priority(changes["priority"]) if changes.get("priority") is not None else row.priority
    connection_changed = (kind, url, user, category) != (row.kind, row.url, row.username, row.category) \
        or secret is not None
    checked: Checked | None = None
    if connection_changed:
        # The key stays the old one unless a new one came; a kind that changed brings its own key or password.
        if secret is None and not gate.key_follows(row.url, url):
            raise error("key_required", "The address changed; enter the key or password again.", 422, field="secret")
        used = secret if secret is not None else secret_of(row)
        checked = run_check(Target(kind=kind, url=url, username=user, secret=used, category=category))
        row.version, row.last_error, row.checked_at = checked.version, "", clock.now()
        if secret is not None:
            row.secret_enc = encrypt_secret(secret, SECRET_CONTEXT)
    row.kind, row.url, row.username, row.category = kind, url, user, category
    row.name, row.priority = name, priority
    if changes.get("enabled") is not None:
        row.enabled = bool(changes["enabled"])
    row.updated_at = clock.now()
    db.commit()
    return row, checked


def test(db: Session, row: DownloadClient) -> Checked:
    """The test of a saved program; what it found (the version, or the code of what failed) is kept."""
    try:
        checked = run_check(target_of(row))
    except ClientError as exc:
        row.last_error, row.checked_at = exc.code, clock.now()
        db.commit()
        raise
    row.version, row.last_error, row.checked_at = checked.version, "", clock.now()
    db.commit()
    return checked


def remove(db: Session, row: DownloadClient) -> None:
    db.delete(row)
    db.commit()
    logger.info("Download program removed")


def mappings(db: Session) -> list[PathMapping]:
    return list(db.scalars(select(PathMapping).order_by(PathMapping.id)))
