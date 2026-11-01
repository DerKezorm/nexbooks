"""Titles a file carries that are no title of the book: the name of a series with a number
(``Lantern Saga 2``, ``Lantern Saga Band 02``, ``LS 2``), and the title with the name of whoever put the file up
(``The Lantern Road by someone``, ``[group] The Lantern Road``).

nexbooks reads the title of a book from its files. When a book was fetched for a wish, the wish knows the title, and a
file that says only one of these shapes is not to be believed: the book goes in under the title wished. A title that is
the title of another book, or of another volume, is never taken for one of these (that is another book).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .readers.text import fold

#: The words that stand between a series and its number ("Band 2", "Vol. 2", "Book 2", "#2").
VOLUME_WORDS = frozenset({"band", "bd", "teil", "tl", "vol", "volume", "book", "buch", "tome", "tomo", "deel", "del",
                          "nr", "no", "part", "folge", "episode", "livre", "libro", "bok"})
#: Words that say who put a file up or where it comes from, not what the book is called.
UPLOAD_WORDS = frozenset({"by", "upload", "uploaded", "uploader", "scan", "scanned", "scanner", "ripped", "rip",
                          "www", "com", "net", "org", "retail", "ebook", "epub", "mobi", "azw3", "pdf"})
_NUMBER = re.compile(r"^0*(\d{1,4})$")
_BRACKETED = re.compile(r"[\[\{][^\]\}]*[\]\}]")


def _words(text: str) -> list[str]:
    return fold(text or "").split()


def _initials(words: list[str]) -> str:
    return "".join(word[0] for word in words if word)


def series_shorthand(title: str, *, series: Iterable[str] = (), position: float | None = None,
                     wished: str = "") -> bool:
    """Whether ``title`` is only a series and a number: the words before the number are the series (one of ``series``),
    its initials, or the first words of the title wished (``wished``, the longer title of the same book). With a known
    ``position`` the number has to be it (another number is another volume, not a shorthand)."""
    found = _words(title)
    if not found:
        return False
    number = _NUMBER.match(found[-1])
    if number is None:
        # "Lantern Saga 2a" or no number at all: no shorthand.
        return False
    if position is not None and abs(int(number.group(1)) - float(position)) > 0.01:
        return False
    head = found[:-1]
    while head and head[-1] in VOLUME_WORDS:
        head.pop()
    named = [words for words in (_words(name) for name in series) if words]
    if not head:
        return bool(named) or bool(wished)
    for words in named:
        if head == words or (len(head) == 1 and len(words) > 1 and head[0] == _initials(words)):
            return True
    longer = _words(wished)
    return bool(longer) and len(longer) > len(head) and longer[:len(head)] == head and longer != found


def names_more_than_a_number(title: str) -> bool:
    """Whether a title says more than a word of a volume and a number (``Nebelchor 2``, not ``Teil 2`` or ``Band 2``):
    only such a shorthand names a book by itself."""
    found = _words(title)
    if not found or _NUMBER.match(found[-1]) is None:
        return False
    head = found[:-1]
    while head and head[-1] in VOLUME_WORDS:
        head.pop()
    return bool(head)


def with_uploader(title: str, wished: str, authors: Iterable[str] = ()) -> bool:
    """Whether ``title`` is the title wished with the name of whoever put the file up: bracketed marks
    (``[group]``), an address, ``by …`` or ``scanned by …``. The title wished has to be in it word for word."""
    longer, core = _words(title), _words(wished)
    if not core or len(longer) <= len(core):
        return False
    starts = [index for index in range(len(longer) - len(core) + 1) if longer[index:index + len(core)] == core]
    if not starts:
        return False
    if _BRACKETED.search(title or "") or "@" in (title or ""):
        return True
    rest = longer[:starts[0]] + longer[starts[0] + len(core):]
    names = {word for name in authors for word in _words(name)}
    if rest and all(word in names for word in rest):
        # The title and the author: the author has no business in the title.
        return True
    return any(word in UPLOAD_WORDS for word in rest)


__all__ = ["UPLOAD_WORDS", "VOLUME_WORDS", "names_more_than_a_number", "series_shorthand", "with_uploader"]
