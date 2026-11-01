"""What a source or a person said about an edition, kept apart from what the files say, and laid over it by the scan.

**The rank, from the top: a person (hand), then the file, then a source.**

* A *hand* value always stands, also when it is empty (a person who clears a series meant it).
* A *file* value (the book's own package file, the file beside it, its name) is never replaced by a source: a source
  that only writes the same thing differently ("Rune Falkner" and "Falkner, Rune") changes nothing. A source fills only
  what the files leave empty.
* A *source* value is replaced the next time the matching runs; a hand value only by a person.

The overlay lives on the edition (``Edition.overlay``) and is applied to the ``Meta`` the scan puts together, before
it is
written (``catalog.assign``). So the scan of a changed file does not lose it, and the work shows what the editions a
person
may see say, by the same rule as ever (``catalog.summarize``).
"""

from __future__ import annotations

from typing import Any

from .. import genres
from ..readers.text import fold

HAND = "hand"
SOURCE = "source"
#: The origin of a value nexbooks worked out itself (the language of a recording from its e-book twin or from the
#: library): a source entry that no match replaces, and that the interface marks as derived.
GUESS = "guess"

#: The fields an overlay may hold, with the kind of value each takes.
TEXT_FIELDS = ("title", "subtitle", "language", "isbn", "asin", "description", "genre", "series", "original_title",
               "original_language", "publisher", "cover")
LIST_FIELDS = ("authors", "narrators")
NUMBER_FIELDS = ("series_pos", "year")
FIELDS = TEXT_FIELDS + LIST_FIELDS + NUMBER_FIELDS + ("variant",)


def entry(value: Any, by: str, **extra: Any) -> dict[str, Any]:
    return {"v": value, "by": by, **extra}


def _empty(value: Any) -> bool:
    return value in (None, "", [], 0)


def _valid(overlay: Any) -> dict[str, dict[str, Any]]:
    """The overlay as a dict of entries with a known field, a value and a rank; anything else is dropped."""
    if not isinstance(overlay, dict):
        return {}
    return {name: item for name, item in overlay.items()
            if name in FIELDS and isinstance(item, dict) and "v" in item and item.get("by") in (HAND, SOURCE)}


def with_source(overlay: Any, values: dict[str, Any], *, origins: dict[str, str] | None = None,
                keep: frozenset[str] | set[str] = frozenset(), **extras: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The overlay with the source's entries replaced by ``values`` (field -> value); the person's entries stay.

    ``origins`` (field -> name of the source that said it) is written into the entries. A source named in ``keep`` is
    one that took part in an earlier match and could not be asked this time: what it said stays, except where
    ``values`` brings a new answer for the field (only what a new answer delivers is replaced). A field in ``extras``
    (field -> extra keys) carries them (the colour of a cover)."""
    old = _valid(overlay)
    kept = {name: item for name, item in old.items() if item["by"] == HAND}
    for name, value in values.items():
        if name in FIELDS and name not in kept and not _empty(value):
            origin = {"src": origins[name]} if origins and name in origins else {}
            kept[name] = entry(value, SOURCE, **origin, **extras.get(name, {}))
    for name, item in old.items():
        if item["by"] == SOURCE and (item.get("src") in keep or item.get("src") == GUESS) and name not in kept:
            kept[name] = item
    # A place in a series belongs to the series it was said with: a new series takes the old place away.
    old_series, series = old.get("series"), kept.get("series")
    if "series_pos" in kept and kept["series_pos"]["by"] == SOURCE and "series_pos" not in values and (
            series is not None and old_series is not None and fold(str(old_series["v"])) != fold(str(series["v"]))):
        del kept["series_pos"]
    return kept


def with_guess(overlay: Any, name: str, value: Any) -> dict[str, dict[str, Any]]:
    """The overlay with a worked-out value for ``name`` (nothing a person, the file or a source said is touched)."""
    result = _valid(overlay)
    existing = result.get(name)
    if existing is not None and existing.get("src") != GUESS:
        return result
    if _empty(value):
        result.pop(name, None)
    else:
        result[name] = entry(value, SOURCE, src=GUESS)
    return result


def is_guess(overlay: Any, name: str) -> bool:
    item = _valid(overlay).get(name)
    return item is not None and item["by"] == SOURCE and item.get("src") == GUESS


def without_source(overlay: Any) -> dict[str, dict[str, Any]]:
    return {name: item for name, item in _valid(overlay).items() if item["by"] == HAND}


def without_match(overlay: Any) -> dict[str, dict[str, Any]]:
    """The overlay without what a match gave: a person's entries stay, and so do the values nexbooks worked out itself
    (``GUESS``), which no match gave."""
    return {name: item for name, item in _valid(overlay).items()
            if item["by"] == HAND or item.get("src") == GUESS}


def with_hand(overlay: Any, changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The overlay with a person's values put in (a value of ``None`` takes a correction back)."""
    result = _valid(overlay)
    for name, value in changes.items():
        if name not in FIELDS:
            continue
        if value is None:
            result.pop(name, None)
        else:
            result[name] = entry(value, HAND)
    return result


def hand_fields(overlay: Any) -> list[str]:
    """The names of the fields a person corrected by hand."""
    return sorted(name for name, item in _valid(overlay).items() if item["by"] == HAND)


def has_source(overlay: Any) -> bool:
    return any(item["by"] == SOURCE for item in _valid(overlay).values())


def source_entry(overlay: Any, name: str) -> dict[str, Any] | None:
    item = _valid(overlay).get(name)
    return item if item is not None and item["by"] == SOURCE else None


def source_values(overlay: Any) -> dict[str, Any]:
    return {name: item["v"] for name, item in _valid(overlay).items() if item["by"] == SOURCE}


def votes_of(item: Any) -> genres.Votes:
    """The votes a source's genre carries (``genres.tally``), as far as they are what they should be: genres of the
    shelf with a weight. A source's genre without votes (written by an older version) has none."""
    raw = item.get("votes") if isinstance(item, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {genre: genres.to_fraction(value) for genre, value in raw.items()
            if genre in genres.GENRES and isinstance(value, int | float) and not isinstance(value, bool)
            and 0 < value < 100}


def apply_to(meta: Any, overlay: Any) -> None:
    """Lays the overlay over ``meta`` (``catalog.Meta``), by the rank in the top of this file. Changes ``meta``."""
    items = _valid(overlay)
    if not items:
        return
    # The genre is the majority of all the voices: what the files said (their votes are in ``meta``) and what the
    # sources said. A person's word is the only one that overrules them.
    genre = items.get("genre")
    if genre is not None:
        if genre["by"] == HAND:
            meta.genre = str(genre["v"] or "")
        elif votes_of(genre):
            meta.genre = genres.winner(genres.add_votes(getattr(meta, "genre_votes", {}), votes_of(genre)))
        elif _empty(getattr(meta, "genre", "")):
            meta.genre = str(genre["v"] or "")
    for name in TEXT_FIELDS:
        item = items.get(name)
        if item is None or name in ("cover", "series", "genre"):
            continue
        # A recording's comment tag is the last word on the description: a source's goes before it.
        weak = name == "description" and bool(getattr(meta, "description_weak", False))
        if item["by"] == HAND or weak or _empty(getattr(meta, name, "")):
            setattr(meta, name, str(item["v"] or ""))
    for name in LIST_FIELDS:
        item = items.get(name)
        if item is not None and (item["by"] == HAND or _empty(getattr(meta, name, []))):
            setattr(meta, name, [str(value) for value in item["v"] if isinstance(value, str)] if isinstance(
                item["v"], list) else [])
    year = items.get("year")
    if year is not None and (year["by"] == HAND or meta.year is None):
        meta.year = int(year["v"]) if isinstance(year["v"], int) and not isinstance(year["v"], bool) else None
        if year["by"] == HAND or not meta.published:
            meta.published = str(meta.year or "")
    # A series is its name and its place in it, and they belong together.
    series, pos = items.get("series"), items.get("series_pos")
    from_overlay = False
    if series is not None and (series["by"] == HAND or not meta.series):
        meta.series, from_overlay = str(series["v"] or ""), True
        if series["by"] == HAND or meta.series_pos is None:
            meta.series_pos = None
    if pos is not None and meta.series:
        same = fold(meta.series) == fold(str(series["v"])) if series is not None else False
        if pos["by"] == HAND or (meta.series_pos is None and (from_overlay or same)):
            meta.series_pos = float(pos["v"]) if isinstance(pos["v"], int | float) else None
    elif series is not None and series["by"] == HAND and not meta.series:
        meta.series_pos = None
    variant = items.get("variant")
    if variant is not None and meta.kind == "audio" and (variant["by"] == HAND or variant.get("src") == GUESS
                                                         or meta.uncertain):
        meta.variant, meta.uncertain = str(variant["v"]), False
    cover = items.get("cover")
    if cover is not None and (cover["by"] == HAND or not meta.cover):
        meta.cover, meta.color = str(cover["v"]), str(cover.get("color", ""))
