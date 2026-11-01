"""What a person typed, read the way a librarian reads it: the words, and a volume of a series when the words end with
one (``lantern saga 2``, ``Laternen-Saga Band 3``, ``tome 2``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..readers.text import clean, key_fold

#: The longest text that goes out.
MAX_TEXT = 200
#: Words in front of a volume number (folded).
VOLUME_WORDS = frozenset({"band", "bd", "teil", "book", "buch", "vol", "volume", "tome", "tomo", "livre", "nr", "no",
                          "folge", "part", "deel", "del", "bind"})
_NUMBER = re.compile(r"^(\d{1,3})$")


@dataclass
class Asked:
    #: The text as it goes out to the sources.
    text: str
    #: Every word, folded (``key_fold``: an umlaut is its spelling with two letters).
    words: list[str] = field(default_factory=list)
    #: The volume the words end with, and the words without it (and without the word in front of it).
    volume: float | None = None
    base: list[str] = field(default_factory=list)
    #: The text without the volume, as typed (for a source that is asked for the volume apart).
    base_text: str = ""


def read(raw: str) -> Asked:
    """The words of a search, and the volume they end with: a number of one to three digits that is not the only word
    (``2048`` is a title, ``2`` alone is nothing to go by), with or without ``Band``, ``Teil``, ``Book`` in front."""
    text = " ".join(clean(raw, MAX_TEXT).replace("#", " ").split())
    words = key_fold(text).split()
    asked = Asked(text=text, words=words, base=list(words), base_text=text)
    if len(words) < 2:
        return asked
    found = _NUMBER.match(words[-1])
    if found is None:
        return asked
    rest = words[:-1]
    typed = text.split()
    if typed and key_fold(typed[-1]) == words[-1]:
        typed = typed[:-1]
    if rest and rest[-1] in VOLUME_WORDS:
        if len(rest) == 1:
            # (``Band 2`` alone names no series.)
            return asked
        rest = rest[:-1]
        if typed and key_fold(typed[-1]) in VOLUME_WORDS:
            typed = typed[:-1]
    if not rest:
        return asked
    asked.volume = float(found.group(1))
    asked.base = rest
    asked.base_text = " ".join(typed)
    return asked


__all__ = ["Asked", "read"]
