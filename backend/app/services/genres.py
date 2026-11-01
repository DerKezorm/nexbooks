"""The eight genres of the shelf, and how the subjects a book carries ("Roman-Thriller", "Science Fiction") map onto
them.

The shelf of the design has eight genres, in this order. The keywords are German and English; the first genre in
``GENRES`` that has a keyword in any subject of the book wins, so "Roman-Thriller" is a crime novel (a thriller is the
more telling word), "Kinderkrimi" a children's book, and a novel with no other word is a novel. A book none of this
fits has no genre and stands at the end of the shelf. The names people read ("Romane", "Krimi & Thriller") are the
interface's.
"""

from __future__ import annotations

import zlib
from fractions import Fraction
from typing import NamedTuple

from .readers.text import fold, unescape_deep

#: The keys in the order of the shelf.
GENRES = ("roman", "krimi", "fantasy", "scifi", "klassiker", "sachbuch", "kinder", "lyrik")

#: (genre, keywords). A keyword matches whole words of the folded subject, or its start when it ends in ``*``.
#: Checked in this order (most telling first), not in the order of the shelf.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lyrik", ("lyrik", "gedicht*", "poetry", "poem*", "verse", "poesie", "lieder")),
    ("kinder", ("kinder*", "jugend*", "children*", "juvenile", "young adult", "ya", "bilderbuch", "kids", "teen*",
                "vorlese*", "erstleser", "madchen*", "maedchen*", "*jugendbuch", "*kinderbuch", "*madchenbuch",
                "*maedchenbuch")),
    ("scifi", ("science fiction", "sciencefiction", "science-fiction", "scifi", "sci fi", "sf", "dystop*", "utopi*",
               "weltraum", "space opera", "cyberpunk", "zukunft")),
    ("fantasy", ("fantasy", "fantastik", "fantastische*", "urban fantasy", "epic fantasy", "drachen", "magie",
                 "paranormal*")),
    ("krimi", ("krimi*", "thriller*", "kriminal*", "crime", "mystery", "detective", "detektiv*", "suspense",
               "spannung", "mord", "psychothriller", "roman thriller", "whodunit", "polizei*", "noir", "*thriller",
               "*krimi", "*kriminalroman", "*detektivroman")),
    ("klassiker", ("klassiker", "classic", "classics", "weltliteratur", "literary classics")),
    ("sachbuch", ("sachbuch*", "non fiction", "nonfiction", "biograph*", "biografi*", "autobiograph*", "memoir*",
                  "ratgeber", "wissenschaft*", "geschichte", "history", "politik", "politics", "psychologie",
                  "philosophie", "philosophy", "kochen", "cooking", "reise*", "travel", "gesundheit", "health",
                  "wirtschaft*", "business", "self help", "selbsthilfe", "essay*", "sport", "religion", "natur")),
    ("roman", ("roman*", "*roman", "novel*", "fiction", "belletristik", "*literatur", "romance", "erzaehlung*",
               "erzahlung*", "short stories", "kurzgeschichten", "familiensaga", "saga", "drama", "humor")),
)

#: The colours of the spines of each genre when a book has no cover to take one from (the palette of the design).
_PALETTE: dict[str, tuple[str, ...]] = {
    "roman": ("#5e2027", "#d9cba9", "#7c8c6b", "#4f2b47", "#e7ddc8", "#8b4b2b", "#a8616a", "#215258"),
    "krimi": ("#1d1c1f", "#5e2027", "#3d4752", "#1f304b", "#bf9234", "#7d2f2b"),
    "fantasy": ("#4f2b47", "#215258", "#2f4b3b", "#1f304b", "#a3762c", "#4a5a32"),
    "scifi": ("#3d4752", "#5b7c9c", "#1d1c1f", "#215258", "#e7ddc8", "#1f304b"),
    "klassiker": ("#7d2f2b", "#2f4b3b", "#1f304b", "#5e2027", "#5d5b30"),
    "sachbuch": ("#e7ddc8", "#bf9234", "#5b7c9c", "#7c8c6b", "#8b4b2b", "#d9cba9"),
    "kinder": ("#bf9234", "#5b7c9c", "#8b4b2b", "#7c8c6b", "#215258", "#a8616a"),
    "lyrik": ("#d9cba9", "#4f2b47", "#7c8c6b", "#e7ddc8"),
}


def _matches(folded: str, keyword: str) -> bool:
    """``word`` is a whole word, ``word*`` a word that starts so, ``*word`` one that ends so (Liebesroman)."""
    if keyword.endswith("*"):
        return any(word.startswith(keyword[:-1]) for word in folded.split())
    if keyword.startswith("*"):
        return any(word.endswith(keyword[1:]) for word in folded.split())
    return f" {keyword} " in f" {folded} "


#: Words that stand in a subject of a story as readily as of a book about the thing: "Geschichte 1802-1830" and "Die
#: Geschichte einer Faelscherin" head a novel, "Zukunft" a novel of the present, "Natur" a nature poem. Such a word
#: counts as a vote only with a second sign for the same genre in the same voice (another word of the genre that is
#: not one of these, or a second one of them).
_BROAD = frozenset({
    "geschichte", "history", "politik", "politics", "reise*", "travel", "gesundheit", "health", "wirtschaft*",
    "business", "sport", "religion", "natur", "zukunft", "weltraum", "magie", "drachen", "spannung", "mord", "kochen",
    "cooking", "wissenschaft*",
})


class Hit(NamedTuple):
    """What a subject stands for: the genre, whether it says so by a word that means it (``strong``), and the broad
    words that said it when not."""

    genre: str
    strong: bool
    broad: frozenset[str]


def classify(subject: str) -> Hit | None:
    """The genre one subject stands for (``Psychothriller``, ``Krimis &amp;amp; Thriller``), or None. A genre whose only
    words in the subject are broad ones is a weak hit; a later genre with a word that means it wins over it."""
    folded = fold(unescape_deep(subject or ""))
    if not folded:
        return None
    weak: Hit | None = None
    for genre, keywords in _KEYWORDS:
        hits = [keyword for keyword in keywords if _matches(folded, keyword)]
        if not hits:
            continue
        if any(keyword not in _BROAD for keyword in hits):
            return Hit(genre, True, frozenset())
        if weak is None:
            weak = Hit(genre, False, frozenset(hits))
    return weak


def genre_of(subjects: list[str]) -> str:
    """The genre of the shelf for a book's subjects, or an empty string: the most telling genre any of them has."""
    hits = [hit for hit in (classify(subject) for subject in subjects if subject) if hit is not None]
    for genre, _words in _KEYWORDS:
        if any(hit.genre == genre and hit.strong for hit in hits):
            return genre
    for genre, _words in _KEYWORDS:
        if any(hit.genre == genre for hit in hits):
            return genre
    return ""


# ---- Votes: a genre from several voices
# -------------------------------------------------------------------------------

#: The genre that says little: a "novel" is the answer of every file, shop and catalogue for any story. It wins only
#: when nothing more telling has a vote worth counting.
GENERIC = "roman"
#: What a genre needs, from all the voices together, to win against "novel": about one whole voice of the library or a
#: catalogue, or the file itself saying so in part. A thin voice (one subject of many, one source of three) does not
#: make a novel a crime novel.
SPECIFIC_MIN = Fraction(1)
#: And its share of all the votes: a genre that is three tenths of what was said or more, not one that stands beside a
#: stronger "novel" with a few whispers.
SPECIFIC_SHARE = Fraction(3, 10)
#: The weight of each voice: the file itself says the most, the national library and the catalogues of recordings and
#: books about the same, Open Library least (its subjects are many and noisy), the kind of book a title names in
#: between. Whole numbers and fractions: a vote is added up the same way whatever the order it came in.
WEIGHTS = {"file": Fraction(3), "dnb": Fraction(2), "audible": Fraction(2), "hardcover": Fraction(2),
           "openlibrary": Fraction(1), "title": Fraction(3, 2)}
#: The order a tie is decided in: the genre whose keywords are the more telling.
_ORDER = {genre: index for index, (genre, _words) in enumerate(_KEYWORDS)}

Votes = dict[str, Fraction]


def genre_of_one(subject: str) -> str:
    """The genre one subject stands for, or an empty string (a weak hit counts: this tells only what a subject says)."""
    hit = classify(subject)
    return hit.genre if hit else ""


def to_fraction(value: float | Fraction) -> Fraction:
    """A vote that was kept as a number (thousandths) as the exact fraction it was."""
    if isinstance(value, Fraction):
        return value
    return Fraction(round(float(value) * 1000), 1000)


def tally(subjects: list[str], weight: float | Fraction) -> Votes:
    """The votes of one voice with the weight it has: its subjects count for the genre each stands for (a telling genre
    twice as much as "novel"), and the voice casts its weight in the proportion they came out. A subject that says its
    genre by a broad word only (``Geschichte``) counts only with a second sign for that genre in the same voice. A voice
    whose subjects mean nothing casts nothing."""
    cast = to_fraction(weight)
    counts: dict[str, int] = {}
    weak: dict[str, list[frozenset[str]]] = {}
    for subject in subjects[:60]:
        hit = classify(subject)
        if hit is None:
            continue
        if hit.strong:
            counts[hit.genre] = counts.get(hit.genre, 0) + (1 if hit.genre == GENERIC else 2)
        else:
            weak.setdefault(hit.genre, []).append(hit.broad)
    for genre, groups in weak.items():
        # A second sign: a word of the genre that means it, or a second broad word (the same word twice is one).
        if counts.get(genre) or len(frozenset().union(*groups)) >= 2:
            counts[genre] = counts.get(genre, 0) + (1 if genre == GENERIC else 2) * len(groups)
    total = sum(counts.values())
    return {genre: cast * count / total for genre, count in counts.items()} if total else {}


def add_votes(*parts: Votes) -> Votes:
    total: Votes = {}
    for part in parts:
        for genre, value in part.items():
            total[genre] = total.get(genre, Fraction(0)) + to_fraction(value)
    return total


def winner(votes: Votes) -> str:
    """The genre the votes decide for, or an empty string when nobody voted. A telling genre beats "novel" when it has
    enough weight and a fair share of all that was said; "novel" is the answer when that is all anybody said or what was
    said is thin; a thin telling genre alone is still better than none. A tie goes to the more telling genre, always the
    same one (the votes are exact fractions, so the order they were added in does not matter)."""
    exact = {genre: to_fraction(value) for genre, value in votes.items()}
    specific = {genre: value for genre, value in exact.items() if genre != GENERIC and value > 0}
    best = min(specific, key=lambda genre: (-specific[genre], _ORDER.get(genre, 99))) if specific else ""
    generic = exact.get(GENERIC, Fraction(0))
    if best:
        total = sum((value for value in exact.values() if value > 0), Fraction(0))
        if generic <= 0 or (specific[best] >= SPECIFIC_MIN and specific[best] / total >= SPECIFIC_SHARE):
            return best
    if generic > 0:
        return GENERIC
    return best


def palette_color(genre: str, seed: str) -> str:
    """A spine colour for a book without a cover: one of the genre's palette, fixed for the same ``seed``."""
    colors = _PALETTE.get(genre) or _PALETTE["roman"]
    return colors[zlib.crc32(seed.encode("utf-8")) % len(colors)]


def rank(genre: str) -> int:
    """The place of a genre on the shelf (a book with none comes last)."""
    return GENRES.index(genre) if genre in GENRES else len(GENRES)


def style_of(work_id: int) -> tuple[int, float, bool]:
    """How the spine of a work looks, fixed by its id: ``(pattern 0..4, height 0..1, gilt lines)``. The same numbers
    every time, so that a book keeps its look."""

    def stream(seed: int):  # type: ignore[no-untyped-def]
        state = seed & 0xFFFFFFFF

        def draw() -> float:
            nonlocal state
            state = (state + 0x6D2B79F5) & 0xFFFFFFFF
            t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
            t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) ^ t) & 0xFFFFFFFF
            return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

        return draw

    draw = stream(work_id * 2654435761 + 7)
    return int(draw() * 5) % 5, draw(), draw() < 0.18

