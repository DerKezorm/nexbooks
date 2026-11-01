"""Which series a source names is a series of the author's books, and when two names are one series.

The catalogues write the *publisher's* series into the field of the series: the paperback line with its number
(``Bastei-Lübbe-Taschenbuch ; 1750``, ``rororo 4281``, ``btb 7427``), a collection of classics (``Klassiker im Insel
Verlag``, ``Svenska Ljud Classica`` 9293), the line of a book club or a newspaper (``Reader's Digest Hörbuch``,
``Bild am Sonntag präsentiert``). Those are lines of many authors' books, no series of the work: they are dropped, both
for the catalogue of an author's works and for the books of the libraries (``match.score.junk_series``).

* **A publisher or a line**: the name is (or begins with) the name of a publisher, or names a kind of line (paperback,
  classics, a book club, a newspaper);
* **a number no volume has**: from ``IMPRINT_NUMBER`` on in a library's record (an imprint number), from
  ``LINE_NUMBER`` on anywhere;
* **the author's name** (a catalogue's list of an author's books).

Two spellings are one series: ``Anselm Quist`` and ``Anselm Quist Series``, ``Kai Lorn`` and
``Kai-Lorn-Reihe``, ``Reihe (gekürzt)`` (``key``).
"""

from __future__ import annotations

import re

from .readers.names import person_key, strip_noise
from .readers.text import fold

#: The names of the publishers whose imprint numbers come as series from the catalogues ("Fischer 1983", "Ullstein
#: 2829").
PUBLISHERS = frozenset({
    "fischer", "ullstein", "blanvalet", "dtv", "heyne", "goldmann", "rowohlt", "penguin", "piper", "droemer", "knaur",
    "suhrkamp", "diogenes", "btb", "aufbau", "luebbe", "lubbe", "bastei", "bastei luebbe", "bastei lubbe", "lyx", "cbj",
    "carlsen", "oetinger", "loewe", "random house", "harpercollins", "hachette", "macmillan", "vintage", "bloomsbury",
    "orion", "pan", "ravensburger", "fischer taschenbuch", "ullstein taschenbuch", "list", "knaur taschenbuch",
    "heyne verlag", "rororo", "reclam", "kiwi", "kiepenheuer", "hanser", "atlantik", "folio", "livre de poche",
    "le livre de poche", "j ai lu", "gallimard", "albin michel", "norstedts", "bonnier", "bonniers", "wahlstrom",
    "pocketforlaget", "lind co", "svenska ljud", "cappelen", "aschehoug", "gyldendal", "bruna", "de bezige bij",
    "mondadori", "feltrinelli", "einaudi", "debolsillo", "plaza janes", "hodder", "picador", "argon",
    "der audio verlag", "horbuch hamburg", "hoerbuch hamburg", "lubbe audio", "random house audio", "audible studios",
    "weltbild", "bertelsmann",
    # Platforms that publish or sell the books of many authors (their name in the field of the series is the platform's,
    # not a series): self-publishing, print on demand, the shops of audiobooks.
    "neobooks", "epubli", "tredition", "books on demand", "bod", "createspace", "kindle direct publishing",
    "independently published", "smashwords", "draft2digital", "wattpad", "storytel", "storytel original",
    "bookbeat", "nextory", "audible", "audible originals", "saga egmont", "egmont"})
#: Words that name a line of a publisher, a club or a newspaper, not a series of books of one author (folded: ``ü`` is
#: ``u``).
_LINE = re.compile(
    r"\b(?:taschenbuch\w*|taschenbucher|paperbacks?|classics|classica|klassiker\w*|weltliteratur|"
    r"horbuch\w*|hoerbuch\w*|audiobooks?|ljudbok\w*|luisterboek\w*|lydbok\w*|readers? s digest|readers digest|"
    r"club taschenbuch|buchclub|bokklubb\w*|insel bucherei|bibliothek suhrkamp|bild am sonntag|suddeutsche|"
    r"brigitte|spiegel edition|zeit edition|prasentiert|pasentiert|bestseller\w*|bestseler\w*|best sellers?|"
    r"meilleures? ventes|bastsaljare|storsaljare|sonderausgabe|jubilaumsausgabe|"
    # (Lines of pocket books in other languages: a Slovenian ``Žepna knjiga``, a Croatian ``Džepna knjiga``, an
    # Italian ``Tascabili``.)
    r"d?zepn[aeiu] knjig\w*|tascabil\w*|de bolsillo|kieszonkow\w*|kapesni \w+|pocket ?(?:books?|bok|boeken)|"
    # (A recording made for a shop as a play, and the names of who reads: ``Die Hörspieltrilogie``, ``Gesprochen von
    # ...``, ``... liest ...``: no series of the book.)
    r"h(?:o|oe)rspiel\w*|(?:gesprochen|gelesen|vorgelesen) von|(?:read|narrated|performed) by|liest|"
    r"schullekture\w*|schulausgabe\w*|lekture\w*|lernausgabe|easy readers?|graded readers?|cornelsen|klett|"
    r"\w*lesemethode|\w*lernmethode|sprachen lernen|"
    r"\w*edition|edition\w*|editions?|\w*presse|grammophon|bvt|lieblingsbucher|"
    # (A publisher's edition of the works and a collection of classics: ``Thomas Mann, Werke``, ``Die Originale``,
    # ``Literarisches Gold``, ``Meister des Schreckens``, ``Classic Mindful Literature Series``, a Korean ``segye munhak
    # chŏnjip``: the volumes of those are no volumes of one work.)
    r"werke|gesammelte \w+|samtliche \w+|literarisches gold|originale|\w*literatur(?:en)?|literature|"
    r"meister de[rs] \w+|"
    r"munhak|chonjip|collected works|complete works|oeuvres)\b"
    r"|\w*bibliothek$|\bbibliotheque$"
    # (A collection named after someone, ``A. B. Morrows Bibliothek des Grauens``, ``Morrow's Library of ...``: books of
    # many authors under one name.)
    r"|\b\w+ ?s (?:bibliothek|library|bibliotheque)\b")
#: A volume number from this on is the number of an imprint in a library's record, from ``LINE_NUMBER`` on anywhere
#: (a line of a publisher numbers thousands of books).
IMPRINT_NUMBER = 100
LINE_NUMBER = 1000
#: The words a name of a series adds that do not change the series.
_SERIES_WORDS = frozenset({"series", "serie", "reihe", "serien", "serier", "saga", "sequence", "cycle", "zyklus",
                           "the", "a", "die", "der", "das", "la", "le", "les", "de", "het", "en"})
#: The sources whose records are one edition in a library's catalogue: their series are often a publisher's line.
LIBRARIES = ("dnb", "bnf", "libris", "kb", "nb")


def key(name: str) -> str:
    """A series name as it is compared: folded, without what names the form of a recording (``Reihe (gekürzt)``) and
    without the words ``series``, ``Reihe`` and the articles (``Anselm Quist Series`` is ``anselm quist``)."""
    words = fold(strip_noise(name or "")).split()
    kept = [word for word in words if word not in _SERIES_WORDS]
    return " ".join(kept or words)


_BRACKET_AT_END = re.compile(r"\s*\(([^()]+)\)\s*$")
_COLON_AT_END = re.compile(r":\s+(.+)$")


def without_line(title: str) -> str:
    """A title without the publisher's line at its end, in brackets or after a colon: only what begins with the name of
    a publisher. ``(Hörspiel)`` and ``Ein Klassiker neu erzählt`` say something about the book."""
    for found in (_BRACKET_AT_END.search(title), _COLON_AT_END.search(title)):
        if found and title[:found.start()].strip() and (fold(found.group(1)).split() or [""])[0] in PUBLISHERS:
            return title[:found.start()].strip()
    return title


def is_line(name: str, position: float | None = None, *, authors: list[str] | tuple[str, ...] = (),
            library: bool = False) -> bool:
    """Whether a series a source names is a publisher's line (or none at all): see the top of this module."""
    # (The words of the form are read too: ``Club-Taschenbuch`` is a line, not the series ``Club``.)
    whole = fold(name or "")
    folded = fold(strip_noise(name or ""))
    if not folded:
        return True
    for text in {whole, folded}:
        if text in PUBLISHERS or any(text.startswith(f"{publisher} ") for publisher in PUBLISHERS):
            return True
        if _LINE.search(text):
            return True
    if any(person_key(author) and person_key(author) == person_key(name) for author in authors):
        return True
    # The author's name in front of what a shop calls its products (``Lena Vorlage - Die Trilogie``): a list of the
    # author's books, no series of them.
    head = re.split(r"\s+[-–—:]\s+|:\s+", name or "", maxsplit=1)
    if len(head) == 2 and any(person_key(author) and person_key(author) == person_key(head[0]) for author in authors):
        return True
    return position is not None and position >= (IMPRINT_NUMBER if library else LINE_NUMBER)


__all__ = ["IMPRINT_NUMBER", "LIBRARIES", "LINE_NUMBER", "PUBLISHERS", "is_line", "key"]
