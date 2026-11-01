"""Operator settings in the database, with defaults."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Setting

DEFAULTS: dict[str, Any] = {
    "log_mode": "normal",
    "log_mode_until": None,
    #: off | daily | weekly. Daily in the night unless the operator decides otherwise.
    "backup_schedule": "daily",
    "backup_keep": 7,
    #: The address people use to reach nexbooks: invitation links, the redirect address of the sign-in providers.
    #: Empty means ``NEXBOOKS_PUBLIC_URL``, and without that the request's own.
    "public_url": "",
    #: Members may sign in with a password. The operator always may: the emergency exit when OIDC fails.
    "password_login": True,
    #: Every account needs a second factor (a code from an app or a passkey), on from the start: right after the
    #: password, an account without one sets it up before it gets anywhere. Turning it off asks for the password.
    "two_factor_required": True,
    #: Keys for programs (``/api/v1``): closed until the operator opens it.
    "api_keys_allowed": False,
    #: E-readers (``services/ereaders.py``, ``services/kindle.py``): every person makes connections for their reading
    #: apps and sends books to their Kindle. On from the start; the operator may switch it off and block connections.
    "ereaders_allowed": True,
    #: Ask GitHub once a day whether a newer nexbooks is out (services/updates). Only the question goes out, so on.
    "update_check": True,
    #: Invitation mail: without a host nothing is sent, the link to copy is enough.
    "smtp_host": "",
    "smtp_port": 587,
    #: starttls | tls | none
    "smtp_security": "starttls",
    "smtp_user": "",
    "smtp_password_enc": "",
    "smtp_from": "",
    #: Web Push (``services/webpush.py``, ``services/push.py``): nexbooks's own key pair, made at the first need and
    #: sealed with the server's secret; who the push services may contact (empty: the public https address, else a
    #: placeholder); and push services beyond the known ones, as host names.
    "push_key_enc": "",
    "push_contact": "",
    "push_hosts": [],
    #: How often the libraries are read again by themselves, in minutes (0: never by itself; "Scan now" always works).
    "scan_minutes": 60,
    #: The sources (``services/sources``): the main switch "nexbooks may ask sources on the internet", and one switch
    # per
    #: source. From the start Open Library and the German National Library are on (they need nothing from the
    #: operator; the German National Library is asked about German books only), Hardcover is on but asks for a token of
    #: the operator first. What goes out is a title, an author and an ISBN, never a file. An operator who switched the
    #: German National Library off has the switch saved as off, and it stays so.
    #: Audible is off until the operator turns it on: nexbooks asks once (``sources.config.audible_decided``: the
    #: switch has been saved, in either way), and an instance that never chose is asked at the next visit of the
    #: settings.
    "sources_enabled": True,
    "source_openlibrary": True,
    "source_audible": False,
    "source_dnb": True,
    "source_hardcover": True,
    #: Apple's free chart of the best-selling e-books (``services/sources/apple.py``) for Discover: it needs nothing
    #: from the operator and is asked about nothing but a country.
    "source_apple": True,
    #: Wikidata and Wikimedia Commons (``services/sources/wikidata.py``) for the photos and short biographies of authors
    #: Nothing from the operator is needed; what goes out is an author's name and the titles of their books.
    "source_wikidata": True,
    #: The national libraries of France, Sweden, the Netherlands and Norway, for the catalogue of an author's works
    #: Open data, nothing from the operator is needed; what goes out is an author's name.
    "source_bnf": True,
    "source_libris": True,
    "source_kb": True,
    "source_nb": True,
    #: Whether nexbooks looks up photos and short biographies of
    #: authors on the internet at all (the sources above decide
    #: where it may look). The photos that lie in the authors' folders are read either way.
    "author_info": True,
    #: Tidying the files of a library: off until the
    #: operator turns it on. The libraries are read-only from the
    #: start; this switch is what lets the interface offer renaming and moving.
    "organize_enabled": False,
    #: The markets of the catalogue of Audible that are asked (the market
    #: of an edition's language first, when it is one of these).
    "source_markets": ["de", "us"],
    #: The read-only token of Hardcover, sealed with the server's secret (never read back).
    "hardcover_token_enc": "",
    #: The version of the rules the books were last matched by (``services/match/run.py``): when the code has a newer
    #: one, every book no person decided on is matched anew, once.
    "match_rev": 0,
    #: Fetching books (``services/procure``): the formats and places the operator chose (``formats.py``),
    #: and the settings of the automatic work (``automation.py``). Null until saved: the code holds the defaults.
    "procure_formats": None,
    "procure_auto": None,
    #: What the news servers of the Usenet programs keep (services/procure/retention): days, the programs, when read.
    "procure_retention": None,
    #: The version of the rules the books were last put together by out of what was read (``services/scanner.py``):
    #: when the code has a newer one, every book is put together anew, once.
    "assemble_rev": 0,
    #: The languages of the indexers' categories were looked at once (``indexer_admin.adopt_stated_languages``).
    "indexer_langs_rev": 0,
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def get_all(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.scalars(select(Setting)):
        values[row.key] = row.value
    return values


def save(db: Session, changes: dict[str, Any], *, commit: bool = True) -> None:
    """Writes the settings; ``commit=False`` leaves them in the transaction of the caller. Safe to run at the same
    moment as another writer of the same key."""
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        row = db.get(Setting, key)
        if row is None:
            # A key that is not there yet: two writers at the same moment (two starts on an empty folder, two
            # presses on "save") both see it missing, so the insert itself decides, and the later one wins.
            db.execute(sqlite_insert(Setting).values(key=key, value=value)
                       .on_conflict_do_update(index_elements=[Setting.key], set_={"value": value}))
        else:
            row.value = value
    if commit:
        db.commit()
    else:
        db.flush()


def normalize_public_url(value: str) -> str:
    """``scheme://host[:port]`` and nothing else, or empty. ``ValueError`` for anything people could not type into
    a browser: no other schemes, no credentials, no path, no query."""
    text = value.strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("scheme or host")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("path, query or credentials")
    try:
        parts.port  # noqa: B018 - raises ValueError when the port is not a number
    except ValueError as exc:
        raise ValueError("port") from exc
    return f"{parts.scheme.lower()}://{parts.netloc}"


def public_url(db: Session) -> str:
    """The public address without a trailing slash: the setting, otherwise the environment, otherwise empty."""
    stored = str(get(db, "public_url") or "").strip().rstrip("/")
    return stored or get_settings().public_url.strip().rstrip("/")
