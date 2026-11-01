"""Settings from the environment, prefix ``NEXBOOKS_``.

What the operator changes at runtime (log level and, later, sign-in rules and backups) lives in the database, see
``services/settings_service.py``. Only what must be known before the first start is here.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


def _real(path: Path) -> Path:
    """The path with every link followed, for a folder that may not exist (yet)."""
    try:
        return Path(os.path.realpath(path))
    except (OSError, ValueError):
        return Path(os.path.abspath(path))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXBOOKS_",
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    #: Database, logs, backups and the operator's own language files. The books are not kept here: nexbooks only reads
    #: the folders of its libraries.
    data_dir: Path = PROJECT_DIR / "data"
    #: Extra languages as JSON files, one per language (``es.json``). Empty: ``<data_dir>/locales``.
    locales_dir: Path | None = None
    disable_background: bool = False
    #: Where the update check asks for the newest release. Empty: GitHub's API. The tests point it elsewhere.
    update_url: str = ""
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    #: Overrides the stored log level; the emergency exit when the app does not even start.
    log_level: str = ""
    #: Signs the sign-in attempt cookie of OIDC and encrypts what the server must read on its own (the OIDC client
    #: secret, the mail password). Empty: a random key in ``<data_dir>/secret.key``, made at the first start.
    secret_key: str = ""
    #: A session with "stay signed in on this device" ends after this many days without use (each use moves the end).
    session_days: int = 30
    #: Argon2id for passwords. The tests lower the cost.
    argon2_time: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 2
    #: ``auto``: the session cookie is ``Secure`` when the request came over HTTPS (or a proxy says so).
    cookie_secure: str = "auto"
    #: The address people reach nexbooks under, for links in invitations and the OIDC return. Empty: the address of
    #: the request. The setting in the interface wins over this.
    public_url: str = ""
    #: Appended to every cookie name. Two instances on one host under different ports share their cookies (a browser
    #: does not tell ports apart); a suffix keeps their sign-ins apart. Letters, digits and "_" only.
    cookie_suffix: str = ""
    #: Addresses or networks of reverse proxies whose ``X-Forwarded-For`` may be believed, comma separated. Empty:
    #: the header is ignored, so that nobody dodges the sign-in brake with made-up addresses.
    trusted_proxies: str = ""
    #: The code the first account must bring. Empty: nexbooks makes one at every start until set up and writes it to
    #: the log, so that whoever reaches a fresh instance first does not become its operator.
    setup_token: str = ""
    #: Addresses or networks the operator's settings may be changed from, comma separated (e.g. 192.168.0.0/16).
    #: Empty: from anywhere. Behind a reverse proxy only with ``trusted_proxies`` set.
    operator_networks: str = ""
    #: How many pictures are unpacked at the same moment (profile pictures, later the covers of books). Each one may
    #: take some 150 MB while it is open; one is enough for a household, a larger server may give more.
    decode_slots: int = 1
    #: Where the libraries may lie, comma separated: a library is a folder at or below one of these. Nothing else on
    #: the server can be read, whatever the operator types as a path. The container mounts the books read-only at
    #: ``/books``.
    library_roots: str = "/books"
    #: Where nexbooks may look for what the download programs finished, comma separated: the folder of a program, as
    #: nexbooks has it mounted. A path a program reports is read only below one of these (or of a library root), however
    #: a mapping is written. Nothing else on the server is read for it. The data folder is never one of them, nor
    #: anything that holds it (the start refuses), and it is never read for a download whatever is typed here.
    download_roots: str = "/downloads"
    #: How many files may be sent to one person at the same moment (all their browsers and devices together). A player
    #: holds one or two; the rest answers ``429`` with ``Retry-After``.
    max_streams: int = 12
    #: Brakes per person and day on fetching books (the operator has none): new wishes, and searches a person starts
    #: anew ("Jetzt suchen", "Neu suchen"). Above them ``429`` with ``Retry-After``; 0 switches a brake off.
    wishes_per_day: int = 100
    searches_per_day: int = 60
    #: An indexer with a limit of requests per day gives each person with the right to acquire a fair share: the limit
    #: divided by the people who used it today plus one, so that a place is kept for whoever comes next, but never less
    #: than this many requests (and never more than the limit leaves). The operator is not held to a share.
    indexer_share_min: int = 10

    @field_validator("data_dir", "frontend_dist", "locales_dir")
    @classmethod
    def _relative_to_project(cls, value: Path | None) -> Path | None:
        # A relative path means the project, not whatever directory the process was started from.
        if value is None or value == Path(""):
            return None
        return value if value.is_absolute() else PROJECT_DIR / value

    @model_validator(mode="after")
    def _defaults_inside_data(self) -> Settings:
        if self.locales_dir is None:
            self.locales_dir = self.data_dir / "locales"
        return self

    @model_validator(mode="after")
    def _download_roots_keep_off_the_data(self) -> Settings:
        data = _real(self.data_dir)
        for entry in self.download_root_list():
            root = _real(entry)
            if root == data or root.is_relative_to(data) or data.is_relative_to(root):
                raise ValueError(
                    "NEXBOOKS_DOWNLOAD_ROOTS may not name the data folder, a folder in it, or a folder that holds it: "
                    "the database and the keys lie there. Mount the folder of your download program somewhere else, "
                    "for example at /downloads.")
        return self

    _remembered_key: str = PrivateAttr(default="")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nexbooks.db"

    def library_root_list(self) -> list[Path]:
        """The folders a library may lie in, as typed (empty entries dropped); a relative one means the project."""
        found = []
        for entry in self.library_roots.split(","):
            entry = entry.strip()
            if entry:
                path = Path(entry)
                found.append(path if path.is_absolute() else PROJECT_DIR / path)
        return found

    def download_root_list(self) -> list[Path]:
        """The folders the finished jobs of the download programs may be seen in (empty entries dropped)."""
        found = []
        for entry in self.download_roots.split(","):
            entry = entry.strip()
            if entry:
                path = Path(entry)
                found.append(path if path.is_absolute() else PROJECT_DIR / path)
        return found

    def cookie_name_suffix(self) -> str:
        """The suffix, with anything but letters, digits and "_" taken out (a cookie name allows little)."""
        return "".join(ch for ch in self.cookie_suffix if ch.isascii() and (ch.isalnum() or ch == "_"))

    def resolved_secret_key(self) -> str:
        """``NEXBOOKS_SECRET_KEY``, else ``secret.key`` in the data folder, made once with a random value."""
        if self.secret_key:
            return self.secret_key
        if self._remembered_key:
            return self._remembered_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            self._remembered_key = key_file.read_text(encoding="utf-8").strip()
        else:
            self._remembered_key = secrets.token_urlsafe(48)
            # Made only the owner's from the first byte: written first and narrowed after, it stood open a moment.
            descriptor = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self._remembered_key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return self._remembered_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
