"""The scheme of tidying: where a book goes, written like ``{Author}/{Series}/{Volume} - {Title} ({Year})``.

A scheme is a path of parts separated by ``/``; in a part, ``{Name}`` stands for a value of the book. The names exist in
English and in German (``{Author}`` / ``{Autor}``), in any case.

What is left of a part when a value is missing is tidied:
empty brackets and dangling separators go, so ``{Volume} - {Title}
({Year})`` of a book without a volume and a year is just
the title; a part that is nothing at all is left out (a book of no series stands in the author's folder).

**What becomes a name on the disk is made safe for every file system at once**, so that a library can be moved from one
system to another: no ``<>:"/\\|?*``, no control character and no mark that turns the direction of the text,
no dot or space at the end, no name Windows keeps for devices,
and no part longer than ``MAX_PART`` characters or ``MAX_BYTES`` bytes. A scheme can never climb out of the library: no
``..``, no absolute path, no drive, no backslash, and a value can never add a ``/``.

The last part names the file of an e-book (the ending is added) and the folder of a recording.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: The names a scheme may use, by the name they have for nexbooks, with the names people may write instead.
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "Author": ("author", "autor"),
    "AuthorSort": ("authorsort", "autorsort"),
    "Series": ("series", "reihe"),
    "Volume": ("volume", "band"),
    "Title": ("title", "titel"),
    "Year": ("year", "jahr"),
    "Language": ("language", "sprache"),
    "Narrator": ("narrator", "sprecher"),
}
_BY_NAME = {alias: name for name, aliases in PLACEHOLDERS.items() for alias in aliases}

DEFAULT = "{Author}/{Series}/{Volume} - {Title} ({Year})"
MAX_SCHEME = 300
MAX_PARTS = 6
MAX_PART = 120
MAX_BYTES = 200
#: A whole path (relative to the library) longer than this is not made: the longest path Windows takes by default is 260
#: and the library's own folder has to fit in front of it.
MAX_PATH = 240

_TOKEN = re.compile(r"\{([^{}]*)\}")
_RESERVED = re.compile(
    r"^(?:con|prn|aux|nul|conin\$|conout\$|com[0-9\u00b9\u00b2\u00b3]"
    r"|lpt[0-9\u00b9\u00b2\u00b3])(?:\..*)?$",
    re.IGNORECASE,
)
#: Control characters (the C1 ones too), the marks that turn the direction of the text (a name that reads backwards
#: on the screen) and lone surrogates (no file system can write them).
_ILLEGAL = re.compile(
    r'[<>"|?*\x00-\x1f\x7f-\x9f'
    r'\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ud800-\udfff]'
)
_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
_SEPARATORS = re.compile(r"(?:\s*[-–—_,;]\s*){2,}")


class SchemeError(ValueError):
    """A scheme that cannot be used. ``code`` says why (the interface
    words it); ``detail`` names the part or the name that is wrong."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Scheme:
    text: str
    #: The parts, each a list of pieces: a text, or a ``Name`` of ``PLACEHOLDERS`` as ``("name", Name)``.
    parts: tuple[tuple[tuple[str, str], ...], ...]


def parse(text: str) -> Scheme:
    """The scheme in ``text``, or ``SchemeError``."""
    raw = unicodedata.normalize("NFC", text or "").strip()
    if not raw:
        raise SchemeError("empty")
    if len(raw) > MAX_SCHEME:
        raise SchemeError("too_long")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise SchemeError("bad_character")
    if "\\" in raw:
        raise SchemeError("backslash")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw):
        raise SchemeError("absolute")
    pieces = raw.split("/")
    if len(pieces) > MAX_PARTS:
        raise SchemeError("too_deep")
    parts: list[tuple[tuple[str, str], ...]] = []
    for piece in pieces:
        if not piece.strip():
            raise SchemeError("empty_part")
        if piece.strip() in (".", "..") or piece.strip(". ") == "":
            raise SchemeError("dots", piece.strip())
        tokens: list[tuple[str, str]] = []
        position = 0
        for match in _TOKEN.finditer(piece):
            if match.start() > position:
                tokens.append(("text", piece[position:match.start()]))
            name = _BY_NAME.get(match.group(1).strip().casefold())
            if name is None:
                raise SchemeError("unknown_placeholder", match.group(1).strip()[:40])
            tokens.append(("name", name))
            position = match.end()
        rest = piece[position:]
        if "{" in rest or "}" in rest:
            raise SchemeError("unbalanced")
        if rest:
            tokens.append(("text", rest))
        parts.append(tuple(tokens))
    last = parts[-1]
    if not any(kind == "name" and name == "Title" for kind, name in last):
        raise SchemeError("no_title")
    return Scheme(text=raw, parts=tuple(parts))


def clean_part(text: str, *, max_chars: int = MAX_PART, max_bytes: int = MAX_BYTES) -> str:
    """A name of one part made safe for any file system (see the top of this module); empty when nothing is left."""
    value = re.sub(r"[\t\n\r\f\v]", " ", text)
    value = unicodedata.normalize("NFC", _ILLEGAL.sub("", value))
    value = value.replace("/", "-").replace("\\", "-").replace(":", " -")
    value = _BRACKETS.sub("", value)
    value = re.sub(r"\s+", " ", value)
    value = _SEPARATORS.sub(" - ", value)
    value = value.strip(" -–—_,;.")
    if len(value) > max_chars:
        value = value[:max_chars].rsplit(" ", 1)[0] if " " in value[:max_chars] else value[:max_chars]
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1]
    value = value.strip(" -–—_,;.")
    if _RESERVED.match(value):
        value = "_" + value
    return value


_ENDING = re.compile(r"^[A-Za-z0-9]{1,8}$")


def clean_file(name: str) -> str:
    """The name of a file made safe like ``clean_part`` does it, **with its ending kept**: it is the stem that is
    shortened (so that the whole name stays inside ``MAX_PART`` characters and ``MAX_BYTES`` bytes), never the end.
    A name without an ending is a part. Empty when nothing is left of the stem."""
    stem, dot, ending = name.rpartition(".")
    if not dot or not stem.strip() or not _ENDING.match(ending):
        return clean_part(name)
    tail = "." + ending
    cleaned = clean_part(stem, max_chars=MAX_PART - len(tail), max_bytes=MAX_BYTES - len(tail.encode("utf-8")))
    if not cleaned:
        return ""
    full = cleaned + tail
    return "_" + full if _RESERVED.match(full) else full


def volume_text(position: float | None) -> str:
    """``2`` is ``02``, ``2.5`` is ``02.5``; nothing for a book that has no place in a series."""
    if position is None or position < 0:
        return ""
    whole, fraction = divmod(round(position * 100), 100)
    text = f"{int(whole):02d}"
    return text if fraction == 0 else f"{text}.{str(int(fraction)).rjust(2, '0').rstrip('0')}"


def render(scheme: Scheme, values: dict[str, str]) -> list[str]:
    """The parts of the path for one book. A part with nothing
    left is left out - but the last one, which always names the
    book: ``SchemeError("no_title")`` when it is empty. Every part is safe for the disk (``clean_part``)."""
    parts: list[str] = []
    for index, tokens in enumerate(scheme.parts):
        text = "".join(values.get(value, "") if kind == "name" else value for kind, value in tokens)
        cleaned = clean_part(text)
        if cleaned:
            parts.append(cleaned)
        elif index == len(scheme.parts) - 1:
            raise SchemeError("no_title")
    return parts
