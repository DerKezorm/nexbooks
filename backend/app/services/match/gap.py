"""What stands between a book and "sure", said so that a person can act on it: how many points it has and how many it
needs,
what the file or the sources lack that would make it surer, and, when two proposals are about as good, what tells
them apart.

It is worked out from what the matching kept of the proposals (their points, the codes of what is missing, the candidate
as the source gave it), not from anything new: the list a person looks at shows it, the matching does not need it.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..readers.names import title_keys
from .score import AMBIGUITY, P_AUTHOR, P_CONFIRM, P_LANGUAGE, P_RUNTIME, P_VOLUME, P_YEAR, SURE

#: What a proposal may lack, as the code the score names it and as the word the sentence uses ("Es fehlt: ..."), in the
#: order of how much it helps: an ISBN settles it, a second source adds points, the rest are smaller.
FIXABLE = (
    ("no_isbn", "isbn"), ("one_source", "second_source"), ("language_unknown", "language"), ("no_author", "author"),
    ("no_runtime", "runtime"), ("no_volume", "volume"), ("no_year", "year"),
)
#: What each of them is worth in points when the file or the source gives it (an ISBN is worth the whole book: the match
#: is certain then).
WORTH = {"second_source": P_CONFIRM, "language": P_LANGUAGE, "author": P_AUTHOR, "runtime": P_RUNTIME,
         "volume": P_VOLUME, "year": P_YEAR}
#: Reasons a proposal is held back from "sure" whatever its points (the score names them in ``why``).
CAPS = ("author_weak", "author_unknown", "language_missing")
#: At most this many are named.
NAMED = 3


class Lacks(NamedTuple):
    """What would make a proposal sure: ``words`` (as in ``FIXABLE``), each of them enough alone when ``join`` is ``or``
    and all of them needed together when it is ``and``; ``cap`` is what holds it back whatever its points (``""`` for
    nothing)."""

    words: list[str]
    join: str
    cap: str


def _usable(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in proposals if isinstance(p, dict) and not p.get("fatal") and isinstance(p.get("c"), dict)]


def _differences(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[str | None, str | None]]:
    """How two proposals differ: the volume, the title, the year (two years or more apart), the languages, the kind of
    recording; each with the two values."""
    ca, cb = a["c"], b["c"]
    found: dict[str, tuple[str | None, str | None]] = {}
    pa, pb = ca.get("series_pos"), cb.get("series_pos")
    if isinstance(pa, int | float) and isinstance(pb, int | float) and pa != pb:
        found["volume"] = (_show(pa), _show(pb))
    if title_keys(str(ca.get("title", "")))[0] != title_keys(str(cb.get("title", "")))[0]:
        found["title"] = (str(ca.get("title", ""))[:80], str(cb.get("title", ""))[:80])
    ya, yb = ca.get("year"), cb.get("year")
    if isinstance(ya, int) and isinstance(yb, int) and abs(ya - yb) >= 2:
        found["year"] = (str(ya), str(yb))
    if ca.get("languages") and cb.get("languages") and set(ca["languages"]) != set(cb["languages"]):
        found["language"] = (", ".join(ca["languages"][:2]), ", ".join(cb["languages"][:2]))
    if ca.get("variant") and cb.get("variant") and ca["variant"] != cb["variant"]:
        found["variant"] = (str(ca["variant"]), str(cb["variant"]))
    return found


def _tie(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What tells two proposals apart, the most telling first: the volume, the title, the year, the language, the kind
    of recording. ``nothing`` when the file gives no way to tell them apart."""
    found = _differences(a, b)
    for name in ("volume", "title", "year", "language", "variant"):
        if name in found:
            return {"difference": name, "a": found[name][0], "b": found[name][1]}
    return {"difference": "nothing", "a": None, "b": None}


def _show(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def lack_info(proposal: dict[str, Any]) -> Lacks:
    """What one proposal lacks that would make it sure, worked out in points: only the lacks that would really do it are
    named. Alone when one of them is enough (``or``); else the fewest that are enough together (``and``); none when
    nothing the file or the source could still give reaches the points. A proposal that is held back by a weak or
    missing author or an unknown language names that, and says what lifts it (an ISBN settles anything)."""
    codes = proposal.get("missing") if isinstance(proposal.get("missing"), list) else []
    why = proposal.get("why") if isinstance(proposal.get("why"), list) else []
    candidate = proposal.get("c") if isinstance(proposal.get("c"), dict) else {}
    points = int(proposal.get("score") or 0)
    against = proposal.get("against") if isinstance(proposal.get("against"), list) else []
    cap = next((code for code in CAPS if code in why), "") or (
        "subtitle_differs" if "subtitle_differs" in against else "series_only" if "series_only" in against else "")
    lacking = [word for code, word in FIXABLE if code in codes]
    # What the candidate could still earn from what the file lacks.
    available = {
        "second_source": "second_source" in lacking,
        "language": "language" in lacking and bool(candidate.get("languages")),
        "author": "author" in lacking and bool(candidate.get("authors")),
        "runtime": "runtime" in lacking and bool(candidate.get("runtime_min")),
        "volume": "volume" in lacking and isinstance(candidate.get("series_pos"), int | float),
        "year": "year" in lacking and isinstance(candidate.get("year"), int),
    }
    # An ISBN settles the book; an author lifts a book that has none, and earns points too.
    lifts = {"isbn": "isbn" in lacking, "author": available["author"] and cap == "author_unknown"}
    need = SURE - points
    if cap:
        words = []
        if lifts["isbn"]:
            words.append("isbn")
        if lifts["author"] and points + WORTH["author"] >= SURE:
            words.append("author")
        return Lacks(words, "or", cap)
    if need <= 0:
        return Lacks([], "or", "")
    worth = {word: WORTH[word] for word, ok in available.items() if ok}
    if lifts["isbn"]:
        worth = {"isbn": SURE + 100, **worth}
    alone = [word for word, value in worth.items() if value >= need]
    if alone:
        return Lacks(sorted(alone, key=[w for _c, w in FIXABLE].index)[:NAMED], "or", "")
    together: list[str] = []
    total = 0
    for word, value in sorted(worth.items(), key=lambda item: -item[1]):
        together.append(word)
        total += value
        if total >= need:
            return Lacks(sorted(together, key=[w for _c, w in FIXABLE].index)[:NAMED], "and", "")
    return Lacks([], "or", "")


def lacks_of(proposal: dict[str, Any]) -> list[str]:
    """The words of ``lack_info`` (at most ``NAMED``)."""
    return lack_info(proposal).words[:NAMED]


def gap_of(reason: str, proposals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``{"sure_at": 80, "points": 75, "missing": ["isbn", "second_source"], "join": "or", "cap": "", "tie": None}`` for
    a book with proposals,
    ``None`` for one without a proposal that could be the book. ``tie`` is set when two proposals are about as good."""
    usable = _usable(proposals)
    if not usable:
        return None
    best = usable[0]
    info = lack_info(best)
    if reason == "one_source":
        # An e-book with the points of a book that is sure, told by one catalogue alone: an ISBN in the file settles it,
        # a second source that tells the same book does too.
        info = Lacks(["isbn", "second_source"], "or", "")
    elif reason == "subtitle_differs":
        # The same core, another subtitle: another volume, unless the ISBN or the volume says it is this one.
        info = Lacks(["isbn", "volume"], "or", "subtitle_differs")
    if reason == "runtime_unproven":
        # A recording with the points of a book that is sure lacks what proves *which recording* it is: a second source
        # that tells the same book, or a running time at the source (when it gives none).
        codes = best.get("missing") if isinstance(best.get("missing"), list) else []
        info = Lacks(["second_source", *(["runtime"] if "no_runtime" in codes else [])], "or", "")
    tie = None
    if len(usable) >= 2:
        said = reason in ("ambiguous", "sources_disagree")
        # The other book is the first proposal that is another book by its volume or its title; failing that, for a tie
        # the matching called, the next one (what tells them apart may then be the year, the language or nothing).
        partner = next((p for p in usable[1:] if {"volume", "title"} & set(_differences(best, p))), None)
        if partner is not None:
            close = abs(int(best.get("score") or 0) - int(partner.get("score") or 0)) <= AMBIGUITY
            if said or close:
                tie = _tie(best, partner)
        elif said:
            tie = _tie(best, usable[1])
    return {"sure_at": SURE, "points": int(best.get("score") or 0), "missing": info.words[:NAMED], "join": info.join,
            "cap": info.cap, "tie": tie}
