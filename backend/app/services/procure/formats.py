"""Formats and places (the operator's, for the whole instance): which formats are fetched and in which order, which
languages, which version of an audiobook, how big a release may be, into which library the books go and how they are
named.

**No profiles.** Per kind one ranking with a tick for each format (the top line is taken first; a format that is not
ticked is never fetched) and a goal: from the goal on nexbooks looks for nothing better. Saved here, put to use by the
search, the rating and the filing (they read ``config``).

The ranking holds every format of its kind once. The goal is a ticked format, so a kind keeps at least one ticked.
"""

from __future__ import annotations

import copy
import errno
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...errors import error
from ...models import Library
from .. import libraries as library_service
from .. import settings_service
from ..organize import plan as planning
from ..organize import scheme as schemes
from . import newznab

logger = logging.getLogger("nexbooks.procure")

KEY = "procure_formats"
EBOOK_FORMATS = ("epub", "azw3", "mobi", "pdf")
#: The audiobook formats in the order of quality: lossless first, then AAC from 128 kbit/s, then MP3 by its rate.
AUDIO_FORMATS = ("flac", "m4b_aac", "mp3_320", "mp3_192", "mp3_128", "mp3_64")
FORMATS = {"ebook": EBOOK_FORMATS, "audio": AUDIO_FORMATS}
#: What is off from the start: a PDF is fixed pages, hard to read on a phone and often a scan; 64 kbit/s sounds thin.
OFF_AT_START = frozenset({"pdf", "mp3_64"})
VARIANTS = ("unabridged", "prefer")
SIZE_MAX_MB = 100_000.0
#: How long what an upgrade replaced lies in the trash of its library (days), from the start and at most.
TRASH_DAYS = 30
TRASH_DAYS_MAX = 365
#: The books a person may look at when previewing a scheme: invented, so that the preview names nobody's books.
SAMPLES = (
    {"author": "Ada Example", "title": "The Lantern Road", "series": "Lantern Saga", "position": 2.0, "year": "2019"},
    {"author": "Ben Sample", "title": "Standalone Tale", "series": "", "position": None, "year": "2021"},
    {"author": "Cleo Placeholder", "title": "The Long Quiet", "series": "Harbour Books", "position": 1.0, "year": ""},
    {"author": "Dov Specimen", "title": "Colours of the Valley: A Novel", "series": "", "position": None,
     "year": "2016"},
)


#: The place of "Originalsprache" in the ranking of the languages: the language the book first came out in.
ORIGINAL = "original"


def default_config() -> dict[str, Any]:
    return {
        "ebook": [{"f": name, "on": name not in OFF_AT_START} for name in EBOOK_FORMATS],
        "audio": [{"f": name, "on": name not in OFF_AT_START} for name in AUDIO_FORMATS],
        "target": {"ebook": "epub", "audio": "flac"},
        "languages": ["de", "en", ORIGINAL],
        "original": True,
        "variant": "unabridged",
        "abridged": False,
        "dramatized": False,
        "size": {"ebook": [0.05, 80.0], "audio": [20.0, 20000.0]},
        "library": {"ebook": None, "audio": None},
        "schema": schemes.DEFAULT,
        "trash_days": TRASH_DAYS,
    }


def config(db: Session) -> dict[str, Any]:
    """The saved choice, completed with what a later version added (a stored value that is not valid is replaced by the
    default for that part, so that the app never stops on a bad row)."""
    stored = settings_service.get(db, KEY)
    result = default_config()
    if not isinstance(stored, dict):
        return result
    try:
        merged = {**result, **{key: value for key, value in stored.items() if key in result}}
        return validate(db, merged, check_libraries=False)
    except ValueError:
        return result


def accepted_languages(db: Session) -> tuple[str, ...]:
    """The languages the operator ticked, in his order (without the place of "Originalsprache")."""
    return tuple(code for code in config(db)["languages"] if code != ORIGINAL)


def allowed_languages(db: Session, original: str = "") -> list[str]:
    """The languages a book may be fetched in (settings, "Sprachen"), **the preferred one first**: the ones the
    operator ticked, in his order, with the language the book first came out in (``original``) at the place of
    "Originalsprache" when it is ticked. The account's own language plays no part: it is the language of the
    interface, not of the books."""
    code = (original or "").strip().lower()[:2]
    found: list[str] = []
    for item in config(db)["languages"]:
        item = code if item == ORIGINAL else item
        if item in newznab.LANGUAGE_WORDS and item not in found:
            found.append(item)
    return found


def _fail(field: str) -> ValueError:
    return ValueError(field)


def _ranking(kind: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _fail(kind)
    names = [item.get("f") if isinstance(item, dict) else None for item in value]
    if sorted(map(str, names)) != sorted(FORMATS[kind]) or len(set(names)) != len(FORMATS[kind]):
        raise _fail(kind)
    ranking = [{"f": str(item["f"]), "on": item.get("on") is True} for item in value]
    return ranking


def _size(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise _fail("size")
    low, high = value
    if any(isinstance(item, bool) or not isinstance(item, int | float) for item in (low, high)):
        raise _fail("size")
    if not 0 <= low < high <= SIZE_MAX_MB:
        raise _fail("size")
    return [float(low), float(high)]


def validate(db: Session, value: dict[str, Any], *, check_libraries: bool = True) -> dict[str, Any]:
    """The choice made valid, or ``ValueError(field)`` for the first part that is not."""
    base = default_config()
    result: dict[str, Any] = {}
    for kind in ("ebook", "audio"):
        result[kind] = _ranking(kind, value.get(kind))
    target = value.get("target")
    if not isinstance(target, dict):
        raise _fail("target")
    result["target"] = {}
    for kind in ("ebook", "audio"):
        chosen = target.get(kind)
        allowed = {item["f"] for item in result[kind] if item["on"]}
        if chosen not in allowed:
            raise _fail("target")
        result["target"][kind] = chosen
    languages = value.get("languages")
    if not isinstance(languages, list) or len(languages) > len(newznab.LANGUAGE_WORDS) + 1 \
            or any(item not in newznab.LANGUAGE_WORDS and item != ORIGINAL for item in languages) \
            or len(set(languages)) != len(languages):
        raise _fail("languages")
    for name in ("original", "abridged", "dramatized"):
        if not isinstance(value.get(name), bool):
            raise _fail(name)
        result[name] = value[name]
    # The ranking holds the place of "Originalsprache"; ``original`` says whether it is ticked. Ticked without a place
    # (a saved choice of an older version, a client that only sends the switch): the last place.
    ranked = [item for item in languages if item != ORIGINAL or result["original"]]
    if result["original"] and ORIGINAL not in ranked:
        ranked.append(ORIGINAL)
    if not ranked:
        # Without a language nothing could be fetched (and a search would fall back to one nobody chose).
        raise _fail("languages")
    result["languages"] = ranked
    if value.get("variant") not in VARIANTS:
        raise _fail("variant")
    result["variant"] = value["variant"]
    if result["variant"] == "unabridged":
        # Only the unabridged version: nothing abridged is allowed beside it.
        result["abridged"] = False
    sizes = value.get("size")
    if not isinstance(sizes, dict):
        raise _fail("size")
    result["size"] = {kind: _size(sizes.get(kind)) for kind in ("ebook", "audio")}
    libraries = value.get("library")
    if not isinstance(libraries, dict):
        raise _fail("library")
    result["library"] = {}
    for kind in ("ebook", "audio"):
        library_id = libraries.get(kind)
        if library_id is None:
            result["library"][kind] = None
            continue
        if isinstance(library_id, bool) or not isinstance(library_id, int):
            raise _fail("library")
        if check_libraries:
            row = db.get(Library, library_id)
            if row is None or not library_service.kind_allows(row.kind, kind):
                raise _fail("library")
        result["library"][kind] = library_id
    trash_days = value.get("trash_days", base["trash_days"])
    if isinstance(trash_days, bool) or not isinstance(trash_days, int) or not 1 <= trash_days <= TRASH_DAYS_MAX:
        raise _fail("trash_days")
    result["trash_days"] = trash_days
    schema = value.get("schema", base["schema"])
    if not isinstance(schema, str):
        raise _fail("schema")
    try:
        result["schema"] = schemes.parse(schema).text
    except schemes.SchemeError as exc:
        raise _fail("schema") from exc
    return result


def save(db: Session, value: dict[str, Any]) -> dict[str, Any]:
    try:
        checked = validate(db, value)
    except ValueError as exc:
        raise error("invalid_input", "The formats are not valid.", 422, field=str(exc)) from exc
    settings_service.save(db, {KEY: checked})
    return copy.deepcopy(checked)


# --- Naming: the preview ---------------------------------------------------------------------------------------------


def preview(db: Session, schema: str, sample: int) -> dict[str, Any]:
    """Where a sample book would be put by ``schema``, for each kind, under the library chosen for it (or just relative
    to it when none is chosen). ``SchemeError`` for a scheme that cannot be used."""
    parsed = schemes.parse(schema)
    book = SAMPLES[sample % len(SAMPLES)]
    series = book["series"]
    values = {
        "Author": book["author"], "AuthorSort": book["author"], "Series": series,
        "Volume": schemes.volume_text(book["position"]) if series else "", "Title": book["title"],
        "Year": book["year"], "Language": "en", "Narrator": "Eli Voice",
    }
    parts = schemes.render(parsed, values)
    chosen = config(db)["library"]
    paths: dict[str, str] = {}
    for kind in ("ebook", "audio"):
        row = db.get(Library, chosen[kind]) if chosen.get(kind) else None
        root = row.path.rstrip("/\\") + "/" if row is not None else ""
        if kind == "ebook":
            ending = next((item["f"] for item in config(db)["ebook"] if item["on"]), "epub")
            paths[kind] = root + "/".join(parts[:-1] + [f"{parts[-1]}.{ending}"])
        else:
            paths[kind] = root + "/".join(parts) + "/"
    return {"ebook": paths["ebook"], "audio": paths["audio"], "sample": sample % len(SAMPLES)}


def samples() -> list[dict[str, Any]]:
    return [dict(item) for item in SAMPLES]


# --- The place: can nexbooks put books there? ------------------------------------------------------------------------


def probe_library(path: str) -> dict[str, Any]:
    """Whether nexbooks may write in the folder of a library: a probe file is made, written and removed again, and a
    second name for it (a hard link) shows whether files can be linked there instead of copied. Nothing stays behind.

    ``writable`` says whether it went; ``reason`` is empty then, else ``missing``, ``read_only``, ``no_permission`` or
    ``write_failed``; ``hardlink`` is None when nothing could be written."""
    ok, why = planning.writable(path)
    if not ok:
        return {"writable": False, "reason": why, "hardlink": None}
    root = Path(path)
    stem = f".nexbooks-probe-{secrets.token_hex(6)}"
    first, second = root / stem, root / f"{stem}-link"
    linked: bool | None = None
    try:
        with open(first, "xb") as handle:
            handle.write(b"nexbooks")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(first, second)
            linked = True
        except OSError as exc:
            linked = False
            logger.info("Hard links are not possible in a library folder errno=%s", errno.errorcode.get(exc.errno or 0))
    except PermissionError:
        return {"writable": False, "reason": "no_permission", "hardlink": None}
    except OSError as exc:
        if exc.errno == errno.EROFS:
            return {"writable": False, "reason": "read_only", "hardlink": None}
        return {"writable": False, "reason": "write_failed", "hardlink": None}
    finally:
        for leftover in (first, second):
            try:
                leftover.unlink()
            except OSError:
                pass
    return {"writable": True, "reason": "", "hardlink": linked}


def write_state(path: str) -> tuple[bool, str]:
    """Whether nexbooks may write in the folder of a library, the cheap way (as "Ordnen" asks it, nothing is written):
    ``(True, "")``, or ``(False, reason)`` with ``missing``, ``read_only`` or ``no_permission``."""
    return planning.writable(path)


def same_filesystem(first: str, second: str) -> bool | None:
    """Whether two folders lie on one file system (so that a file can be linked from one to the other); None when
    either cannot be looked at from here."""
    try:
        return os.stat(first).st_dev == os.stat(second).st_dev
    except OSError:
        return None
