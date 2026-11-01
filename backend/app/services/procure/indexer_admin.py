"""Making, changing and removing indexers, and what they look like to the operator (``routers/procure.py``
speaks it).

An indexer saves only after it has said what it is: its caps are fetched (or taken from a test of the last
quarter of an hour) and the book categories are taken from them. The categories can be changed by the
operator afterwards; the language of each is their word (``mixed`` for "the indexer does not keep them
apart").
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import INDEXER_KINDS, Indexer
from . import budget, formats, gate, indexers, newznab
from .indexers import IndexerError, Target

logger = logging.getLogger("nexbooks.procure")


def view_categories(row: Indexer, allowed: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Every book category the indexer's caps list, each marked ``on`` when it is chosen, plus chosen numbers the
    caps do not list (no caps, or a number the operator typed). ``suggested`` by the languages books are fetched in
    (``allowed``); ``asked`` says whether a search may ask it at all (a category of a language not fetched is not)."""
    chosen = set(row.categories or [])
    langs = row.category_langs or {}
    found: dict[int, dict[str, Any]] = {}
    for item in newznab.book_categories(row.caps, allowed):
        found[item.id] = {"id": item.id, "name": item.name, "type": item.type,
                          "lang": langs.get(str(item.id), item.lang), "on": item.id in chosen,
                          "suggested": item.suggested, "proposed": item.lang}
    for number in sorted(chosen - set(found)):
        found[number] = {"id": number, "name": "", "type": "audio" if 3000 <= number < 4000 else "ebook",
                         "lang": langs.get(str(number), "mixed"), "on": True, "suggested": False,
                         "proposed": langs.get(str(number), "mixed")}
    for item in found.values():
        item["asked"] = newznab.asked_language(item["lang"], allowed) if allowed else True
    return sorted(found.values(), key=lambda item: (item["type"], item["id"]))


def fetch_languages(db: Session, fallback: str | None = None) -> tuple[str, ...]:
    """The languages books are fetched in (settings, "Sprachen", without the place of "Originalsprache"): the
    categories of these and the mixed ones are suggested. ``fallback`` (the interface language) only when none is
    ticked."""
    found = formats.accepted_languages(db)
    if found:
        return found
    return (fallback,) if fallback in newznab.LANGUAGE_WORDS else ()


#: The key of the setting that says the languages saved by older versions were looked at (``adopt_stated_languages``).
LANGS_REV_KEY = "indexer_langs_rev"
LANGS_REV = 1


def adopt_stated_languages(db: Session) -> int:
    """Once: an indexer whose saved languages are exactly what an older version saved by itself (each category by its
    own name, ``7120 Ebook`` as ``mixed``) was never changed by hand, so its categories take the languages the caps
    state (``7120`` under ``Books - DE`` is German) and only deviations are saved from now on. An indexer the operator
    changed keeps every language; its card shows the proposals and a button to take them. Commits. Returns how many
    indexers took the languages of the caps."""
    from .. import settings_service

    if int(settings_service.get(db, LANGS_REV_KEY) or 0) >= LANGS_REV:
        return 0
    changed = 0
    for row in db.scalars(select(Indexer)):
        saved = {str(key): value for key, value in (row.category_langs or {}).items()}
        chosen = list(row.categories or [])
        if not row.caps or not chosen:
            continue
        old = newznab.legacy_langs(row.caps, chosen)
        if {key: saved.get(key, "mixed") for key in old} == old and set(saved) <= set(old):
            row.category_langs = {}
            changed += 1
        else:
            row.category_langs = newznab.only_deviations(row.caps, saved)
    settings_service.save(db, {LANGS_REV_KEY: LANGS_REV})
    db.commit()
    if changed:
        logger.info("Indexers took the languages their caps state for their categories count=%d", changed)
    return changed


def take_proposals(db: Session, row: Indexer) -> Indexer:
    """"Vorschläge übernehmen": every chosen category takes the language the caps state; nothing is saved but what
    the caps do not state."""
    row.category_langs = {key: value for key, value in (row.category_langs or {}).items()
                          if not any(item.id == int(key) for item in newznab.book_categories(row.caps))} \
        if row.caps else dict(row.category_langs or {})
    row.updated_at = clock.now()
    db.commit()
    return row


def view(db: Session, row: Indexer) -> dict[str, Any]:
    rest = indexers.resting(row)
    caps = row.caps or {}
    return {
        "id": row.id, "name": row.name, "kind": row.kind, "url": row.url, "key_set": indexers.has_key(db, row),
        "enabled": row.enabled, "priority": row.priority, "daily_limit": row.daily_limit,
        "state": indexers.state_of(row), "used_today": indexers.used_today(row), "grabs": row.grabs,
        "grabs_today": budget.grabs_today(row), "rss_checked_at": row.rss_checked_at, "rss_error": row.rss_error,
        "day_ends_at": budget.midnight(),
        "via_prowlarr": row.prowlarr_indexer_id is not None,
        "categories": view_categories(row, fetch_languages(db)), "languages": list(fetch_languages(db)),
        "proposals": sum(1 for item in view_categories(row) if item["on"] and item["lang"] != item["proposed"]),
        "resting_until": rest[0] if rest else None, "resting_cause": rest[1] if rest else "",
        "last_error": row.last_error, "api_current": row.api_current, "api_max": row.api_max,
        "grab_current": row.grab_current, "grab_max": row.grab_max,
        "supported_params": {
            "search": caps.get("search_params") or [], "book": caps.get("book_params") or [],
            "audio": caps.get("audio_params") or [], "book_search": bool(caps.get("book_search")),
            "audio_search": bool(caps.get("audio_search")),
        },
        "caps_checked_at": row.caps_checked_at,
    }


def get(db: Session, indexer_id: int) -> Indexer:
    row = db.get(Indexer, indexer_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def listing(db: Session) -> list[Indexer]:
    return list(db.scalars(select(Indexer).order_by(Indexer.priority, Indexer.id)))


def create(db: Session, data: dict[str, Any]) -> Indexer:
    """A new indexer. ``data``: ``name``, ``kind``, ``url``, ``api_key`` (or None), ``priority``, ``daily_limit``,
    ``enabled``, ``categories`` and ``category_langs`` (or None: the suggestion of the caps in the languages books are
    fetched in), ``language`` (the interface language of the operator, for the suggestion when none is ticked)."""
    if (db.scalar(select(func.count(Indexer.id))) or 0) >= indexers.MAX_INDEXERS:
        raise error("too_many_indexers", "nexbooks holds at most 200 indexers.", 409)
    if data["kind"] not in INDEXER_KINDS:
        raise error("invalid_input", "That kind of indexer is not known.", 422, field="kind")
    name = indexers.valid_name(db, data["name"])
    url = indexers.valid_address(data["url"])
    key = indexers.valid_key(data.get("api_key") or "")
    priority = indexers.valid_priority(data.get("priority", indexers.PRIORITY_DEFAULT))
    limit = indexers.valid_limit(data.get("daily_limit", 0))
    categories, langs = indexers.valid_categories(data.get("categories"), data.get("category_langs"))
    caps = indexers.caps_for(db, Target(url=url, kind=data["kind"], api_key=key))
    if data.get("categories") is None:
        listed = newznab.book_categories(caps, fetch_languages(db, data.get("language")))
        categories = [item.id for item in listed if item.suggested] or list(newznab.DEFAULT_CATEGORIES)
        langs = {}
    langs = newznab.only_deviations(caps, langs)
    moment = clock.now()
    row = Indexer(name=name, kind=data["kind"], url=url, api_key_enc=indexers.seal(key),
                  enabled=bool(data.get("enabled", True)),
                  priority=priority, daily_limit=limit, categories=categories, category_langs=langs, caps=caps,
                  caps_checked_at=moment, created_at=moment, updated_at=moment)
    db.add(row)
    db.commit()
    logger.info("Indexer added kind=%s", row.kind)
    return row


def update(db: Session, row: Indexer, data: dict[str, Any]) -> Indexer:
    """Changes an indexer. ``data`` holds only what the operator sent. An indexer from Prowlarr keeps the name, the
    kind, the address and the key it came with: they follow Prowlarr."""
    via = row.prowlarr_indexer_id is not None
    if via and any(data.get(name) is not None and data[name] != getattr(row, name) for name in ("name", "kind", "url")):
        raise error("indexer_from_prowlarr", "This indexer comes from Prowlarr; change it there.", 409)
    if via and data.get("api_key") is not None:
        raise error("indexer_from_prowlarr", "This indexer comes from Prowlarr; change it there.", 409)
    kind = data["kind"] if data.get("kind") is not None else row.kind
    if kind not in INDEXER_KINDS:
        raise error("invalid_input", "That kind of indexer is not known.", 422, field="kind")
    name = indexers.valid_name(db, data["name"], ignore_id=row.id) if data.get("name") is not None else row.name
    url = indexers.valid_address(data["url"]) if data.get("url") is not None else row.url
    new_key = indexers.valid_key(data["api_key"]) if data.get("api_key") is not None else None
    priority = indexers.valid_priority(data["priority"]) if data.get("priority") is not None else row.priority
    limit = indexers.valid_limit(data["daily_limit"]) if data.get("daily_limit") is not None else row.daily_limit
    categories, langs = row.categories, row.category_langs
    if data.get("categories") is not None:
        categories, langs = indexers.valid_categories(data["categories"], data.get("category_langs"))
    elif data.get("category_langs") is not None:
        _unused, langs = indexers.valid_categories([], data["category_langs"])
    connection_changed = (kind, url) != (row.kind, row.url) or new_key is not None
    if connection_changed:
        if new_key is None and not gate.key_follows(row.url, url):
            raise error("key_required", "The address changed; enter the key again.", 422, field="api_key")
        key = new_key if new_key is not None else indexers.key_of(db, row)
        row.caps = indexers.caps_for(db, Target(url=url, kind=kind, api_key=key))
        row.caps_checked_at = clock.now()

        # A new address or key ends what the old ones brought on: a refused key, a rest after a request limit.
        row.last_error, row.paused_until, row.pause_cause = "", None, ""
        if new_key is not None:
            row.api_key_enc = indexers.seal(new_key)
    row.name, row.kind, row.url = name, kind, url
    row.priority, row.daily_limit = priority, limit
    # Saved are only the languages the operator set apart from what the caps state.
    row.categories, row.category_langs = categories, newznab.only_deviations(row.caps, dict(langs or {}))
    if data.get("enabled") is not None:
        row.enabled = bool(data["enabled"])
    row.updated_at = clock.now()
    db.commit()
    return row


def remove(db: Session, row: Indexer) -> None:
    if row.prowlarr_indexer_id is not None:
        raise error("indexer_from_prowlarr", "This indexer comes from Prowlarr; remove it there.", 409)
    db.delete(row)
    db.commit()
    logger.info("Indexer removed")


__all__ = ["IndexerError", "create", "get", "listing", "remove", "update", "view"]
