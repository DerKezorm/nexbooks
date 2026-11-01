"""The check after the download: is what came the book that was wished, in the language, kind, format and
version wished? **Every book file goes through the readers and the assembly of the scan**: the EPUB's OPF
(``dc:language``, title, author, ISBN, series), the tags and the running time of a recording, the parts of a recording
put together as the scan puts them (``catalog.unit_key``, ``catalog.build_meta``). Then each book of the release (a
*unit*) is held against the wish:

* **The book**: the author as a set (one of the file's authors fits one of the book's, never by the last name alone),
  the core of the title (``names.title_fits``), the volume of a series. A file that names nobody and no title is taken
  by the release name, which the search judged already. A file of another author is ``author_mismatch``; one of the
  right author and another title is a question (``files_unassigned``); another volume is ``other_volume``.
* **The language from the file**: an e-book's ``dc:language`` (or a recording's tag) that is not the language wished
  turns the release down (``language``); a file that does not say is no reason.
* **The kind** (e-book or recording) and **the format** by the operator's ranking: a format that is not ticked never
  goes into a library (an e-book release with EPUB and PDF files puts the EPUB in and leaves the PDF out; one with only
  a PDF is ``format_not_wanted``). A recording's place in the ranking comes from its codec and the bit rate its size
  over its running time comes to (known only now, the release name only guessed it).
* **The version of a recording by its running time** against the catalogue (the recordings of the book there are, the
  forms of a book of the sources, Audible): from 0.95 to 1.05 of the unabridged running time it is that; well below,
  and near an abridged running time, it is abridged. Only a source that says which version it lists proves one: a
  running time of a library catalogue (``UNSAID``) proves nothing, and a length that fits an unabridged and an abridged
  recording both proves neither. A recording of under a tenth of the unabridged running time is a piece of the book, not
  a version of it: it is turned down (``runtime_part``), not asked about (a hand upload is asked as before). A version
  the operator does not take is a question (``runtime``): the person keeps it
  as abridged or blocks it. **Without any proof** (no word in the names, no running time to compare with, as when
  Audible is not used) and with abridged recordings not taken, the question is ``runtime`` with the reason
  ``unproven``: the person keeps it as the unabridged one, or blocks it.
* **An upgrade** has to be better than what is there (``not_better`` else).
* **A bundle** (several books in one release): the book wished is taken, others only when they are wished too, the
  rest stays out; when it is not clear which one is wished, the person is asked (``bundle``).

The answer is a ``Verdict``: take (which units, for which wish), turn down (a code), or ask (a code and what the
words need). Nothing here touches a file or the database.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ...models import ABRIDGED, DRAMATIZED, EDITION_AUDIO, EDITION_EBOOK, UNABRIDGED, BookFile
from .. import book_words, catalog, title_shape
from ..readers import AUDIO_FORMATS, names
from ..readers.text import fold
from . import judge, release_match, release_name

logger = logging.getLogger("nexbooks.procure")

#: The generation of the rules of the check after the download: raised when a rule changes in a way a question that is
#: open should get (how the files of a release are put together into books, how a running time is held against the
#: catalogue, what is turned down). Every download keeps the generation of its last judgement (``Download.check_rev``);
#: one that waits under "Braucht dich" with a question and an older number is judged again, from the files it has, by
#: the same check as a fresh download (``recheck``). 1: the first generation, so that every question that is open when
#: it comes is judged once more. 2: a recording between two running times of the catalogue.
CHECK_REV = 2
#: The formats of an e-book the reader of nexbooks opens.
OPENABLE = ("epub", "azw3", "mobi", "pdf")
#: The running time of the file against the unabridged one of the catalogue: inside this it is that recording.
RUNTIME_PROOF = (0.95, 1.05)
#: Below this share of the unabridged running time a recording is taken for an abridged one.
RUNTIME_ABRIDGED = 0.80
#: Above this it is no recording of the one book (a bundle, another book).
RUNTIME_TOO_LONG = 1.30
#: Below this share of the unabridged running time a recording is no abridged reading of the book but a piece of it:
#: a part of a release (``52 minutes`` of a reading of 50 hours). The shortest real abridged readings measured come to
#: about 14 percent of the full reading, so nothing that is asked about lies under this. It is turned down, not asked.
RUNTIME_PART = 0.10
#: How near two recordings must come in length to be copies of one book (the shorter by the longer one).
COPY_LENGTH = 0.90
#: How alike a title in a file and the title wished must be to be one with a slip of the keys, where the name of the
#: release named the book.
TITLE_SLIP = 0.93
#: The running times of a source that does not say which version they are.
UNSAID = "unsaid"
#: Between ``RUNTIME_ABRIDGED`` and ``RUNTIME_PROOF``: too short for the unabridged reading the catalogue knows, too
#: long to call it abridged. A person decides; one recording of a book lands within a minute of the catalogue as a rule.
SHORTER = "shorter"
#: Under ``RUNTIME_PART`` of the unabridged running time: a piece of the book, not a version of it.
PART = "part"


@dataclass
class Unit:
    """One book of a release: its files (``PlaceFile`` rows or anything with path, format, probe, size), what the scan
    makes of them, and how it fits the wish."""

    key: str
    kind: str
    files: list[Any]
    meta: catalog.Meta
    #: The format in the words of the ranking.
    fmt: str | None = None
    author: str = "unknown"
    title: str = "unknown"
    #: The author is not the one wished, but one first name away from that (``names.first_name_near``).
    near_author: bool = False
    other_volume: bool = False
    #: The title says more than the book wished: ``sample`` (a Leseprobe), ``companion`` (a Begleitbuch, a Making-of),
    #: ``adaptation`` (a graphic novel, a retelling for children), ``edition`` (a school edition with materials, the
    #: book of the film), ``partial`` (``Teil 1``, ``Band 2 von 4``); empty when it says nothing of the kind.
    extra: str = ""
    #: Another wish this unit fills (a bundle).
    wish_id: int | None = None
    #: The language of the book when its witnesses outweigh what its package or tags say (``_wrong_package``).
    spoken: str = ""

    @property
    def fits(self) -> bool:
        return self.author != "no" and self.title not in ("no", "other") and not self.other_volume \
            and self.extra not in ("companion", "adaptation")

    def brief(self) -> dict[str, Any]:
        return {"unit": self.key, "title": self.meta.title, "authors": self.meta.authors[:3],
                "volume": self.meta.series_pos, "series": self.meta.series, "format": self.fmt or "",
                "files": len(self.files)}


@dataclass
class Other:
    """Another open wish of the same library and kind a volume of a bundle may fill."""

    wish_id: int
    subject: dict[str, Any]


@dataclass
class Have:
    """An edition the library has of the book: what decides whether a book that comes is the same recording or another
    version of the book (and so may better it, or must stand beside it)."""

    #: Its best format, in the words of the ranking.
    fmt: str
    #: Of a recording: unabridged, abridged, dramatized.
    variant: str = ""
    language: str = ""
    narrators: list[str] = field(default_factory=list)
    seconds: float = 0.0
    #: Of an e-book: what tells one edition of the text from another (a translation, a school edition).
    isbn: str = ""
    translators: list[str] = field(default_factory=list)
    pages: int = 0
    publisher: str = ""
    #: Of an e-book: its title and the year it was published (``2015``), to tell whether a book that comes is the same
    #: edition when the old one says nothing else.
    title: str = ""
    year: str = ""


#: Pages of two files of one e-book edition differ by this share at most from one publisher (another layout).
PAGES_PROOF = 0.15
#: Pages that differ by more than this share are two texts whoever published them (a school edition, a retelling).
PAGES_APART = 0.4


def _isbn(value: str) -> str:
    return "".join(character for character in (value or "") if character.isdigit() or character in "Xx").upper()


def _features(have: Have) -> bool:
    """Whether an e-book says anything that tells its edition from another: an ISBN, a translator, a publisher, the
    pages counted from its text (a PDF has the pages of its print)."""
    return bool(_isbn(have.isbn) or have.translators or have.publisher.strip()
                or (have.pages > 0 and have.fmt != "pdf"))


def text_against(other: Have, mine: Have) -> str:
    """How an e-book that comes (``mine``) stands to an edition the library has (``other``, the book the check took
    for the one wished, in its language): ``same`` (its better format replaces the old one), ``apart`` (another edition:
    it stands beside it) or ``unsure`` (a person decides).

    * **The same** by the same ISBN or translator, or the same publisher with pages close (``PAGES_PROOF``); or when
      the old one says nothing of its edition (no ISBN, translator, publisher, pages of its text: a PDF, an old MOBI)
      and the one that comes fits it: the same title, and the same year when both say one.
    * **Apart** by two ISBNs, two translators, pages far apart (``PAGES_APART``), or two publishers with pages apart:
      another translation or a school edition.
    * **Unsure** else: the old one says something, and nothing proves the same edition."""
    ours, theirs = _isbn(mine.isbn), _isbn(other.isbn)
    if mine.pages > 0 and mine.pages == other.pages and mine.fmt == other.fmt and mine.title.strip() \
            and fold(mine.title) == fold(other.title):
        # The same title, format and length of text, to the page: the same file, whatever else it does not say.
        return "same"
    if ours and ours == theirs:
        return "same"
    if mine.translators and other.translators and any(
            names.authors_compatible(one, two) for one in mine.translators for two in other.translators):
        return "same"
    if ours and theirs:
        return "apart"
    if mine.translators and other.translators:
        return "apart"
    publishers = bool(mine.publisher.strip() and other.publisher.strip())
    same_house = publishers and names.person_key(mine.publisher) == names.person_key(other.publisher)
    if mine.pages > 0 and other.pages > 0 and "pdf" not in (mine.fmt, other.fmt):
        apart = abs(mine.pages - other.pages) / max(mine.pages, other.pages)
        if apart > PAGES_APART or (apart > PAGES_PROOF and publishers and not same_house):
            return "apart"
        if same_house:
            return "same"
    if _features(other):
        return "unsure"
    if mine.title.strip() and other.title.strip() and not names.title_fits(mine.title, other.title):
        return "unsure"
    if mine.year[:4].isdigit() and other.year[:4].isdigit() and mine.year[:4] != other.year[:4]:
        return "unsure"
    return "same"


def same_text(other: Have, *, isbn: str = "", translators: list[str] | None = None, pages: int = 0,
              publisher: str = "", fmt: str = "", title: str = "", year: str = "") -> bool:
    """Whether an e-book that comes is the same edition of the text as ``other``, so that its better format replaces
    the old one (``text_against`` says ``same``)."""
    return text_against(other, Have(fmt=fmt, isbn=isbn, translators=translators or [], pages=pages,
                                    publisher=publisher, title=title, year=year)) == "same"


def _language(code: str) -> str:
    return (code or "").casefold().replace("_", "-").split("-")[0][:2]


def same_version(kind: str, other: Have, *, variant: str = "", language: str = "", narrators: list[str] | None = None,
                 seconds: float = 0.0, text: Have | None = None, assume: bool | None = None) -> bool:
    """Whether ``other`` (an edition the library has) is the same version as a book that comes, so that the better
    format replaces it. **In doubt it is not**: the book then stands beside the other (a gekürzt beside an ungekürzt, a
    Hörspiel beside a Lesung; another reader's recording is never thrown away for a better codec).

    * the language, when both are known;
    * an e-book: the same edition of the text (``text_against``, with what ``text`` says of the book that comes);
      where that is unsure, what a person said (``assume``: the same edition, or another one);
    * a recording: the same variant, the same reader when both are known, and the same running time (inside
      ``RUNTIME_PROOF``; a running time that is not known is no proof).
    """
    if _language(language) and _language(other.language) and _language(language) != _language(other.language):
        return False
    if kind != EDITION_AUDIO:
        found = text_against(other, text or Have(fmt=""))
        return found == "same" or (found == "unsure" and bool(assume))
    if (variant or UNABRIDGED) != (other.variant or UNABRIDGED):
        return False
    if narrators and other.narrators and not any(
            names.authors_compatible(mine, theirs) or names.person_key(mine) == names.person_key(theirs)
            for mine in narrators for theirs in other.narrators):
        return False
    if seconds <= 0 or other.seconds <= 0:
        return False
    return RUNTIME_PROOF[0] <= seconds / other.seconds <= RUNTIME_PROOF[1]


@dataclass
class Wanted:
    kind: str
    #: The language wished (empty: any the operator accepts).
    language: str
    accepted: list[str]
    #: The book (``search.subject_of_work`` / ``subject_of_foreign``); None for an upload of a book nexbooks does not
    #: know.
    subject: dict[str, Any] | None
    config: dict[str, Any]
    upload: bool = False
    #: What the library has of the book in this kind and language, one entry per edition (see ``same_version``).
    haves: list[Have] = field(default_factory=list)
    #: The wish was for a better format than the one there is.
    upgrade: bool = False
    others: list[Other] = field(default_factory=list)
    #: The running times the catalogue knows, in seconds: ``{"unabridged": [..], "abridged": [..], "unsaid": [..]}``.
    runtimes: dict[str, list[int]] = field(default_factory=dict)
    #: What a person said about an earlier question.
    decision: dict[str, Any] = field(default_factory=dict)
    #: The language the name of the release states by a mark or its words (``GERMAN``, ``Hörbuch``); empty when it
    #: says none.
    named_language: str = ""
    #: The operator allows more than one language (a book nothing says the language of is then a question).
    several_allowed: bool = False
    #: The language the indexer's category gave the release (``7120`` under ``Books - DE``); a witness only when no
    #: other says a language: indexers fill their categories with books of every language.
    category_language: str = ""
    #: The name of the release named an author of the book: a file that names another one is a question then (a pen
    #: name), not the release of another book.
    named_author: bool = False
    #: The language the title of the release is known in (``loading.TITLE_KEY``): the weakest witness.
    title_language: str = ""
    #: The language the book first came out in.
    original: str = ""
    #: The name of the release named the title and an author of the book.
    named_book: bool = False


@dataclass
class Verdict:
    outcome: str
    code: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    #: The units that go into the library, by key, with the wish each fills (None: the download's own).
    take: dict[str, int | None] = field(default_factory=dict)
    #: Files that stay out, by path (relative, with ``origin`` in front: ``source:a/b.pdf``), with the reason.
    drop: dict[str, str] = field(default_factory=dict)
    #: The version of a recording: ``unabridged``, ``abridged``, ``dramatized``; empty for an e-book.
    variant: str = ""
    variant_proven: bool = False
    #: The format of the book taken, in the words of the ranking.
    fmt: str = ""
    #: The book replaces what the library has (an upgrade).
    replaces: bool = False
    #: The language of the book where the check did not follow its package or tags.
    language: str = ""


def take(**values: Any) -> Verdict:
    return Verdict("take", **values)


def reject(code: str, **values: Any) -> Verdict:
    return Verdict("reject", code=code, values=values)


def ask(code: str, **values: Any) -> Verdict:
    return Verdict("ask", code=code, values=values)


def file_key(item: Any) -> str:
    return f"{getattr(item, 'origin', 'source')}:{item.path}"


def is_protected(unit: Unit) -> bool:
    """Whether the book is copy-protected: it has files nexbooks could open (an e-book in a format its reader knows) and
    every one of them is protected (the scan marks it, ``readers``). One format that opens makes it a book."""
    probes = [item.probe for item in unit.files if item.format in OPENABLE]
    locked = all(isinstance(probe, dict) and probe.get("drm") for probe in probes)
    return unit.kind == EDITION_EBOOK and bool(probes) and locked


def kind_of_format(fmt: str) -> str:
    return EDITION_AUDIO if fmt in AUDIO_FORMATS.values() else EDITION_EBOOK


# --- Units ---------------------------------------------------------------------------------------------------------


def _transient(item: Any) -> BookFile:
    probe = item.probe if isinstance(item.probe, dict) else {}
    return BookFile(path=item.path, format=item.format, probe=probe, size=item.size,
                    duration_ms=round(float(probe.get("duration") or 0) * 1000))


def units_of(books: list[Any], sidecars: list[Any] = ()) -> list[Unit]:  # type: ignore[assignment]
    """The books of a release as the scan would put them together: one unit per e-book (its formats) and per recording
    (its parts). Files read from different folders (the download's own, the unpacked one) never share a unit."""
    groups: dict[str, list[Any]] = {}
    for item in books:
        if not isinstance(item.probe, dict):
            continue
        unit = item.unit or catalog.unit_key(item.path, item.format, item.probe, release=True)
        key = f"{getattr(item, 'origin', 'source')}|{unit}"
        groups.setdefault(key, []).append(item)
    found: list[Unit] = []
    for key, items in sorted(groups.items()):
        kind = kind_of_format(items[0].format)
        rows = [_transient(item) for item in items]
        folders = (catalog.unit_folder(key.split("|", 1)[1]), _real_folder(items[0].path)) if sidecars else ()
        beside = [_transient(item) for item in sidecars
                  if isinstance(item.probe, dict) and getattr(item, "origin", "source") == key.split("|", 1)[0]
                  and (catalog._unit_dir(item.path, release=True) in folders or _real_folder(item.path) in folders)]
        meta = catalog.build_meta(kind, rows, beside if kind == EDITION_AUDIO else [], top="")
        found.append(Unit(key=key, kind=kind, files=items, meta=meta))
    return found


def _real_folder(path: str) -> str:
    """The folder of the book a file of a release lies in, on the disk (``catalog._unit_dir`` may name a book there)."""
    return names.strip_part_folders(path.rsplit("/", 1)[0] if "/" in path else "", release=True)


def audio_format(unit: Unit) -> str | None:
    """The place of a recording in the ranking: the codec most of its parts have, at the bit rate all of them come
    to."""
    codecs = Counter(item.format for item in unit.files)
    codec = codecs.most_common(1)[0][0] if codecs else None
    return judge.audio_format("m4b" if codec in ("m4a", "m4b") else codec, measured_rate(unit.files))


def measured_rate(files: list[Any]) -> int | None:
    """The bit rate of a recording in kbit/s: what the reading of its parts measured of the sound (``audio.read``), by
    the running time of each; for parts read before it did, their size over their running time."""
    timed = [(float((item.probe or {}).get("duration") or 0), int((item.probe or {}).get("bitrate") or 0))
             for item in files]
    seconds = sum(length for length, _rate in timed)
    if seconds > 0 and all(rate > 0 for _length, rate in timed):
        return round(sum(length * rate for length, rate in timed) / seconds)
    size = sum(int(item.size or 0) for item in files)
    return int(size * 8 / seconds / 1000) if seconds > 0 and size > 0 else None


def ebook_format(unit: Unit, ticked: list[str]) -> str | None:
    formats = [item.format for item in unit.files]
    for fmt in ticked:
        if fmt in formats:
            return fmt
    order = list(catalog.EBOOK_ORDER)
    return min(formats, key=lambda f: order.index(f) if f in order else 9) if formats else None


# --- Holding a unit against a book ----------------------------------------------------------------------------------


def _authors(meta: catalog.Meta, wanted: list[str]) -> str:
    """``yes`` when one of the file's authors is one of the book's, ``no`` when it names others, ``unknown`` when it
    names nobody: a name that is no person (an uploader, a program, a publisher: ``names.not_a_person``,
    ``names.is_company``) names nobody."""
    named = [name for name in meta.authors
             if name and not names.not_a_person(name) and not names.is_company(name) and not names.is_label(name)]
    if not named or not wanted:
        return "unknown"
    for name in named:
        for other in wanted:
            if names.authors_compatible(name, other) or names.person_key(name) == names.person_key(other) \
                    or names.same_person_as_written(name, other):
                return "yes"
    if all(any(names.part_of_a_name(name, other) for other in wanted) for name in named):
        # One word of the name in the tag (``Dale``): it says who it is no more than an empty tag does.
        return "unknown"
    return "no"


def book_of(subject: dict[str, Any]) -> release_match.Book:
    """The book wished as the title rule holds it (``release_match``): every title it is known by, the authors, the
    series, and the titles of the author's other books."""
    titles = [release_match.Title(names.article_in_front(str(item.get("text") or "")), str(item.get("language") or ""))
              for item in subject.get("titles") or [] if isinstance(item, dict) and item.get("text")]
    if subject.get("title"):
        titles.append(release_match.Title(names.article_in_front(str(subject["title"])), ""))
    return release_match.Book(titles=tuple(titles),
                              authors=tuple(str(name) for name in subject.get("authors") or [] if name),
                              series=str(subject.get("series") or ""),
                              others=tuple(str(text) for text in subject.get("others") or [] if text))


def title_against(title: str, subject: dict[str, Any]) -> str:
    """How a title (of a file, of a catalogue entry) stands to the book wished, by the rule the releases are judged by
    (``release_match.title_is``): ``yes`` when it is the title as a whole, ``other`` when it is the title of another
    book of the author as a whole (``Wir wollen Ruß sehen`` for ``Ruß``: a book nexbooks knows), else ``no``. A short
    title never fits as a part of a longer one: ``Wir wollen Ruß sehen`` is no ``Ruß``."""
    book = book_of(subject)
    if not book.titles or not title.strip():
        return "no"
    if release_match.title_is(title, book):
        return "yes"
    for other in book.others:
        if release_match.title_is(title, release_match.Book(titles=(release_match.Title(other),),
                                                            authors=book.authors)):
            return "other"
    return "no"


def _titles(meta: catalog.Meta, subject: dict[str, Any], named: bool = False) -> str:
    return titles_against(meta.title or "", meta.original_title or "", subject, named=named,
                          from_names=meta.title_from_names)


def _no_book_named(title: str, subject: dict[str, Any], from_names: bool) -> str:
    """What a title that is no title of the book wished says, where the name of the release named the book (title and
    author): ``yes`` for the title wished without the name of its series (``und der Stein der Wege``) or with a slip of
    the keys (``Das letze Licht``), ``unknown`` for what names no book at all: a number, a series and a number (an
    album ``Der lange Turm 1``), the mark of a disc, the name of a file without a tag behind it. A title that holds the
    title wished and goes on is another book, and stays ``no``."""
    wished = [fold(item.text) for item in book_of(subject).titles]
    key = fold(title)
    if any(other.endswith(f" {key}") and len(key.split()) >= 3 for other in wished) or any(
            SequenceMatcher(None, key, other).ratio() >= TITLE_SLIP for other in wished):
        return "yes"
    if key.replace(" ", "").isdigit() or title_shape.names_more_than_a_number(title) \
            or book_words.disc_mark(title):
        return "unknown"
    if from_names and not any(key.startswith(f"{other} ") for other in wished):
        return "unknown"
    return "no"


def titles_against(title: str, original_title: str, subject: dict[str, Any], *, named: bool = False,
                   from_names: bool = False) -> str:
    """``title_against`` for the title of a file and its original title, as the check holds them: ``unknown`` without a
    title, and a title of the series with the number of the volume wished is that volume. With ``named`` the name of the
    release named the book, and a title that names none says nothing (``_no_book_named``; ``from_names``: it comes from
    the name of a file). The filing asks the same (``importing.fits_wish``), so the two never part over a book."""
    if not title:
        return "unknown"
    found = title_against(title, subject)
    if found == "no" and subject.get("series") and subject.get("position") is not None and \
            title_shape.names_more_than_a_number(title) and \
            title_shape.series_shorthand(title, series=[str(subject["series"])], position=subject["position"],
                                         wished=str(subject.get("title") or "")):
        # ``Nebelchor 2``: the series and the number of the volume wished, the release was that book (it passed the
        # check of its name); filed under the title wished (``importing._name_after_wish``).
        return "yes"
    if found != "yes" and original_title and title_against(original_title, subject) == "yes":
        return "yes"
    return _no_book_named(title, subject, from_names) if found == "no" and named else found


def _turn_fields_round(meta: catalog.Meta, subject: dict[str, Any]) -> None:
    """A file whose field of the title holds the author wished and whose field of the author the title wished (the names
    of the file said nothing of it, ``catalog.fields_turned``): the two change places for the check."""
    field = ", ".join(meta.authors)
    wished = [str(name) for name in subject.get("authors") or []]
    if not meta.title or not field:
        return
    if title_against(field, subject) == "yes" and any(
            names.authors_compatible(meta.title, name) or names.same_person_as_written(meta.title, name)
            for name in wished):
        meta.title, meta.authors = field, [meta.title]


def hold(unit: Unit, subject: dict[str, Any], named: bool = False) -> None:
    """How the unit fits the book: author, title, volume. ``named``: the name of the release named the book."""
    _turn_fields_round(unit.meta, subject)
    wished_authors = [str(name) for name in subject.get("authors") or []]
    unit.author = _authors(unit.meta, wished_authors)
    unit.near_author = unit.author == "no" and any(
        names.first_name_near(name, other) for name in unit.meta.authors for other in wished_authors)
    unit.title = _titles(unit.meta, subject, named)
    position = subject.get("position")
    mine = unit.meta.series_pos
    unit.other_volume = (position is not None and mine is not None and abs(float(position) - float(mine)) > 0.01
                         and not (unit.title == "yes" and not unit.meta.series))
    # A volume the title names in words (``Das zweite Buch der Träume``, ``Krieg und Frieden, zweiter Band``, ``Teil
    # II``) against the volume wished: the place in the series, else the volume the title wished names itself.
    wished = float(position) if position is not None else release_match.titles_volume(book_of(subject))
    spoken = book_words.title_volume(unit.meta.title or "", parts=False)
    if wished is not None and spoken is not None and abs(wished - spoken) > 0.01:
        unit.other_volume = True
    # (A title that says nothing of the book says nothing of a part of it either.)
    unit.extra = "" if unit.title == "unknown" and unit.meta.title else _extra(unit.meta, subject)


def _extra(meta: catalog.Meta, subject: dict[str, Any]) -> str:
    """What the title of the file says that the book wished does not: a sample, a book about the book, an adaptation,
    an edition with additions, a part (``book_words``). The words are only held against the file when the title wished
    does not hold them itself. A part counts for a volume of a series only beside the volume (``Band 3, Teil 1``)."""
    own = " ".join([meta.title or "", getattr(meta, "subtitle", "") or ""])
    wished = " ".join([str(item.get("text") or "") for item in subject.get("titles") or []]
                      + [str(subject.get("title") or "")])
    if release_name.sample_word(own) and not release_name.sample_word(wished):
        return "sample"
    other = book_words.not_the_work(own, wished)
    if other == "companion" or (release_name.companion_word(own) and not release_name.companion_word(wished)):
        return "companion"
    if other:
        return other
    if book_words.box_word(own) and not book_words.box_word(wished):
        # Several books in one, or the whole of a work a person may wish in parts.
        return "edition"
    if meta.kind == EDITION_EBOOK and book_words.dramatized_word(own) and not book_words.dramatized_word(wished):
        # The text of a play on a book: a recording says so by its version, an e-book only by its title.
        return "edition"
    position = subject.get("position")
    part = book_words.part_of(own, volumes=position is None)
    if part is not None and position is not None and not book_words.names_volume(own):
        # Beside a volume of a series only what never names a volume is a part: ``Teil 1 von 2``, ``Part 2``, ``CD 3``.
        part = book_words.sure_part(own)
    if part is None:
        return ""
    wished_part = book_words.part_of(wished, volumes=True)
    if (wished_part is not None and wished_part[0] == part[0]) or \
            release_match.titles_volume(book_of(subject)) == part[0]:
        # The title wished names that part itself (``Faust. Der Tragödie erster Teil``).
        return ""
    return "partial"


def _strength(unit: Unit) -> int:
    return (2 if unit.title == "yes" else 0) + (1 if unit.author == "yes" else 0)


# --- The running time ------------------------------------------------------------------------------------------------


def _nearest(seconds: float, values: list[int]) -> int:
    """The running time closest to ``seconds`` by their ratio: the longest of a list may be a bundle of books."""
    return min(values, key=lambda value: abs(math.log(seconds / value))) if seconds > 0 else values[-1]


def runtime_variant(seconds: float, runtimes: dict[str, list[int]]) -> tuple[str, bool, dict[str, int]]:
    """``(variant, proven, values)`` of a recording of ``seconds`` against the catalogue's running times: empty when the
    catalogue says nothing, or when a length fits two versions; ``too_long`` for one far longer than the unabridged
    one. Only a time of a source that says its version proves it (``UNSAID`` proves nothing)."""
    full = sorted(runtimes.get(UNABRIDGED) or [])
    short = sorted(runtimes.get(ABRIDGED) or [])
    values: dict[str, int] = {"loaded": int(seconds)}
    if full:
        values["unabridged"] = _nearest(seconds, full)
    if short:
        values["abridged"] = _nearest(seconds, short)
    if seconds <= 0:
        return "", False, values
    is_full = any(RUNTIME_PROOF[0] <= seconds / value <= RUNTIME_PROOF[1] for value in full)
    is_short = any(RUNTIME_PROOF[0] <= seconds / value <= RUNTIME_PROOF[1] for value in short)
    if is_full and is_short:
        return "", False, values
    if is_full:
        return UNABRIDGED, True, values
    if is_short:
        return ABRIDGED, True, values
    if any(RUNTIME_PROOF[0] <= seconds / value <= RUNTIME_PROOF[1] for value in runtimes.get(DRAMATIZED) or []):
        return DRAMATIZED, True, values
    if full:
        if seconds / values["unabridged"] > RUNTIME_PROOF[1]:
            # The catalogue holds several times and one of them is far off (a sample, one part of a set): a recording
            # between two of them is held against the longer one, or it would pass for a long reading of the short one.
            values["unabridged"] = next((value for value in full if value > seconds), values["unabridged"])
        ratio = seconds / values["unabridged"]
        if ratio < RUNTIME_PART:
            return PART, False, values
        if ratio < RUNTIME_ABRIDGED:
            return ABRIDGED, False, values
        if seconds / full[-1] > RUNTIME_TOO_LONG:
            return "too_long", False, values
        if ratio < RUNTIME_PROOF[0]:
            return SHORTER, False, values
        return UNABRIDGED, False, values
    return "", False, values


def _version(unit: Unit, wanted: Wanted) -> tuple[str, bool, Verdict | None]:
    """The version of a recording and what it leads to: nothing (it is taken), a question, or turning it down."""
    config = wanted.config
    meta = unit.meta
    named = "" if meta.uncertain else (meta.variant or "")
    seconds = float(meta.duration or 0)
    by_time, proven, values = runtime_variant(seconds, wanted.runtimes)
    chosen = (wanted.decision or {}).get("variant")
    if chosen in (UNABRIDGED, ABRIDGED, DRAMATIZED):
        return chosen, True, None
    if meta.play_by_genre and by_time == UNABRIDGED and proven and not wanted.upload:
        # The genre in the tags says a play, the running time proves the reading: a tag is the weaker voice, but no
        # voice is left out. A person says.
        return UNABRIDGED, False, ask("runtime", reason="genre", **values)
    if named == DRAMATIZED:
        if not config.get("dramatized"):
            return DRAMATIZED, True, reject("dramatized")
        if wanted.upload or not wanted.runtimes.get(UNABRIDGED):
            return DRAMATIZED, True, None
        # Plays are allowed, and a reading of the book is known to exist: the wish is for the book, a play tells another
        # one in a fraction of the time. A person says whether it stands in for the reading.
        return DRAMATIZED, True, ask("runtime", reason="dramatized", **values)
    # A cast in the tags, or a play of the catalogue of that length: a sign, no word.
    played = meta.variant == DRAMATIZED or by_time == DRAMATIZED
    if played and not wanted.upload:
        if config.get("dramatized") and not wanted.runtimes.get(UNABRIDGED):
            return DRAMATIZED, by_time == DRAMATIZED, None
        return DRAMATIZED, False, ask("runtime", reason="dramatized", **values)
    if wanted.upload:
        # (A person who files a recording by hand is asked as before; a piece of the book is an abridged one to them.)
        shown = {SHORTER: "", PART: ABRIDGED}.get(by_time, by_time)
        return named or shown or "", bool(named) or proven, None
    if by_time == PART:
        # Far too short to be any reading of this book, whatever the names say: a part of the release. Not a question.
        return named or ABRIDGED, False, reject("runtime_part", **values)
    if by_time == "too_long" and named != ABRIDGED:
        return named or UNABRIDGED, False, ask("runtime", reason="too_long", **values)
    if by_time == SHORTER and not named and not config.get("abridged"):
        # 10 to 20 percent short of the unabridged reading: a cut, a part missing, or another reading. Nothing proves
        # which, and a word in the names would have.
        return UNABRIDGED, False, ask("runtime", reason="shorter", **values)
    variant = named or ("" if by_time == SHORTER else by_time)
    if not variant and seconds > 0 and not config.get("abridged"):
        # Nothing says which reading this is: the file's names do not, and the catalogue (Audible, when the operator
        # uses it) has no running time to hold it against. An abridged recording must not go in as the unabridged one
        # on a guess, so a person decides.
        return UNABRIDGED, False, ask("runtime", reason="unproven", **values)
    if variant == ABRIDGED and not config.get("abridged"):
        return ABRIDGED, bool(named) or proven, ask("runtime", reason="abridged", **values)
    if named and by_time and by_time != "too_long" and named != by_time and proven:
        # The names say one thing and the running time proves another: the time wins.
        variant = by_time
        if variant == ABRIDGED and not config.get("abridged"):
            return ABRIDGED, True, ask("runtime", reason="abridged", **values)
    return variant or UNABRIDGED, bool(named) or proven, None


# --- The verdict -----------------------------------------------------------------------------------------------------


def _ticked(config: dict[str, Any], kind: str) -> list[str]:
    return [item["f"] for item in config[kind] if item["on"]]


def _rank(config: dict[str, Any], kind: str, fmt: str | None) -> int:
    order = [item["f"] for item in config[kind]]
    return order.index(fmt) if fmt in order else len(order)


def package_language(unit: Unit) -> str:
    """The language the files themselves state (``dc:language`` of an e-book, the tag of a recording), as a witness of
    its own: the edition may take the language of its text instead (``catalog.build_meta``), the check holds both."""
    if unit.kind == EDITION_EBOOK:
        for item in unit.files:
            probe = getattr(item, "probe", None)
            if isinstance(probe, dict) and isinstance(probe.get("language"), str) and probe["language"]:
                return str(probe["language"])[:8]
    return unit.meta.language or ""


def clear_text_language(unit: Unit) -> str:
    """The language of the text of an e-book when its sample says it beyond doubt (``text.clear_sample_language``)."""
    for item in unit.files:
        probe = getattr(item, "probe", None)
        if isinstance(probe, dict) and isinstance(probe.get("text_language_clear"), str) \
                and probe["text_language_clear"]:
            return str(probe["text_language_clear"])
    return ""


def _title_abroad(unit: Unit, wanted: Wanted, fits: list[str]) -> bool:
    """Whether the title of the file is the title of the book in another language as well: then it says no language."""
    own = fold(unit.meta.title or "")
    return any(fold(str(item.get("text") or "")) == own and item.get("language") not in ("", None, *fits)
               for item in (wanted.subject or {}).get("titles") or [])


def _wrong_package(unit: Unit, wanted: Wanted, fits: list[str], said: set[str]) -> str:
    """The language of a book whose package or tag alone says another one, when what speaks for it outweighs that, else
    empty. Of an e-book the text, beyond doubt, in a language the short words tell from the one of the package; of a
    recording the name of the release and a title of the book in that language: a tool writes ``eng`` into a tag
    whatever is read, and ``en`` into an EPUB."""
    package = package_language(unit)
    rest = said - {package}
    only = next(iter(rest)) if len(rest) == 1 else ""
    if only not in fits:
        return ""
    if unit.kind == EDITION_EBOOK:
        near = frozenset((_language(package), only)) in catalog.LANGUAGE_NEIGHBOURS
        return only if clear_text_language(unit) == only and not near else ""
    return only if wanted.named_language == only and unit.title == "yes" and not _title_abroad(unit, wanted, fits) \
        else ""


def text_language(unit: Unit) -> str:
    """The language the text of an e-book is written in, as its reading found it (``epub._text_language``)."""
    for item in unit.files:
        probe = getattr(item, "probe", None)
        if isinstance(probe, dict) and isinstance(probe.get("text_language"), str) and probe["text_language"]:
            return str(probe["text_language"])
    return ""


def folder_language(unit: Unit) -> str:
    """The language the name of the folder a book lies in states by a mark or its words (``… - Hoerbuch - DE``), as a
    release name would; empty when it says none."""
    for item in unit.files:
        parts = str(getattr(item, "path", "") or "").replace("\\", "/").split("/")[:-1]
        for part in reversed(parts):
            name = release_name.parse(part)
            if name.language and name.language_how in ("mark", "words"):
                return str(name.language)
    return ""


def _language_verdict(unit: Unit, wanted: Wanted) -> Verdict | None:
    """The language of a book against the languages wished, from every witness there is: the package of an e-book or
    the tags of a recording (``file``), a sample of the text of an e-book (``text``), the name of the release
    (``name``) and the name of the folder the book lies in (``folder``).

    * All that say a language say the same one: a language wished is taken; another one turns the release down
      (``other_language``) when the package or the tags are among them, and is a question when only the text says it
      (``language_unclear``: a text sample can be wrong, a package that says nothing is no proof).
    * They say different languages (an English text in a package that says German, a release named German with an
      English book in it): a person decides (``language_unclear``); nothing is taken or turned down for it. Unless the
      package or the tag alone stands against the rest and they outweigh it (``_wrong_package``): a German text beyond
      doubt in a package that says ``en``.
    * A text with none of the short words of any language nexbooks knows (a comic, a list) is no witness when the
      package and the name of the release both say a language wished.
    * None says a language: a person decides (``language_unknown``), also with one language allowed; a missing word is
      no yes. The weak witnesses count when nothing else speaks: the category of the indexer, and for an e-book the
      language its title is known in. A book wished in the language it first came out in, whose files carry its title
      in that language, needs none.

    A person who said which language the files are in took them as they are. "Übernehmen" for a question of the title
    (``take``) answers that question only: the language is still held against the wish."""
    if (wanted.decision or {}).get("language"):
        return None
    fits = [wanted.language] if wanted.language else list(wanted.accepted)
    if not fits:
        return None
    package = package_language(unit)
    listed = [code for item in unit.files if isinstance(getattr(item, "probe", None), dict)
              for code in (item.probe.get("languages") or []) if isinstance(code, str) and code]
    text = text_language(unit) if unit.kind == EDITION_EBOOK else ""
    name, folder = wanted.named_language, folder_language(unit)
    values = {"file": package, "text": text, "name": name, "folder": folder, "wanted": wanted.language or fits[0]}
    if text == "other" and _language(package) in fits and name in fits:
        text = ""
    if text in ("mixed", "other"):
        # Two languages in the text, or one nexbooks has no words for: no yes for any language wished.
        return ask("language_unclear", **values)
    said = {code for code in (package, text, name, folder, *listed) if code}
    if not said:
        # Weak witnesses, only when nothing else speaks: the category of the indexer (``3130`` set to German), then the
        # language the title of the release is known in (not for a recording: a title of one language says nothing of
        # the language it is read in).
        titled = wanted.title_language if unit.kind == EDITION_EBOOK else ""
        weak = [code for code in (wanted.category_language, titled) if code and code != "mixed"]
        if weak:
            said = {weak[0]}
            values["category"] = weak[0]
    if not said:
        if wanted.original and wanted.original in fits and unit.title == "yes" \
                and not _title_abroad(unit, wanted, fits):
            return None
        # Nothing says the language, not even with one language allowed: looked for in that language is no proof that
        # it is in it (a recording without tags, a release name without a mark). A person decides.
        return ask("language_unknown", **values)
    if len(said) > 1:
        unit.spoken = _wrong_package(unit, wanted, fits, said)
        return None if unit.spoken else ask("language_unclear", **values)
    only = next(iter(said))
    if only in fits:
        return None
    if not package and not listed:
        return ask("language_unclear", **values)
    return reject("other_language", file=only, wanted=wanted.language or fits[0])


def judge_units(units: list[Unit], wanted: Wanted) -> Verdict:
    """The verdict on the units of a release (see the top of this module). A question says what stays out of the
    library when the answer takes the book it asks about."""
    left: list[Unit] = []
    verdict = _judge(units, wanted, left)
    if verdict.outcome == "ask" and left and verdict.code != "bundle":
        verdict.values["left_out"] = [{"title": unit.meta.title, "files": len(unit.files)} for unit in left]
    return verdict


def _judge(units: list[Unit], wanted: Wanted, left: list[Unit]) -> Verdict:
    """The verdict; ``left`` receives the books of the release that stay out."""
    kind = wanted.kind
    mine = [unit for unit in units if unit.kind == kind]
    if not mine:
        return reject("other_kind" if units else "no_book")
    drop: dict[str, str] = {}
    for unit in units:
        if unit.kind != kind:
            for item in unit.files:
                drop[file_key(item)] = "other_kind"
    # A book nexbooks could not open is no book to bring in: it is turned down and the release is blocked, unless the
    # release holds another book that opens (the protected one stays out). A person's own upload is theirs to bring.
    locked = [unit for unit in mine if is_protected(unit)]
    if locked and not wanted.upload:
        if len(locked) == len(mine):
            return reject("protected")
        for unit in locked:
            for item in unit.files:
                drop[file_key(item)] = "protected"
        mine = [unit for unit in mine if unit not in locked]
    if not wanted.upload and not any(_has_a_ticked_format(unit, wanted) for unit in mine):
        # Nothing in the release could go in, whoever wrote it: no question about its author or its title.
        return reject("format_not_wanted", found=sorted({found for unit in mine for found in _formats_of(unit)}))
    subject = wanted.subject
    chosen_key = (wanted.decision or {}).get("unit")
    if subject is None:
        # An upload of books nexbooks does not know: every book in it is a book of its own.
        chosen = mine
    else:
        for unit in mine:
            hold(unit, subject, wanted.named_book)
        if chosen_key:
            chosen = [unit for unit in mine if unit.key == chosen_key]
            if not chosen:
                return ask("bundle", units=[unit.brief() for unit in mine], wanted=subject.get("position"))
        elif (wanted.decision or {}).get("take"):
            chosen = [unit for unit in mine if unit.author != "no"] or mine
        else:
            fitting = [unit for unit in mine if unit.fits]
            if fitting:
                best = max(_strength(unit) for unit in fitting)
                fitting = [unit for unit in fitting if _strength(unit) == best]
            if kind == EDITION_AUDIO and len(fitting) > 1 and (
                    any(_on_numbered_folder(unit) for unit in fitting) or not _copies(fitting)):
                # ``01/`` and ``Teil 2/`` with albums of their own, folders nothing marks as discs: the parts of one
                # recording or books of a box. The better one alone would leave the rest of a recording behind, so a
                # person looks.
                return ask("bundle", units=[unit.brief() for unit in mine], wanted=subject.get("position"), whole=True)
            if len(mine) > 1 and kind == EDITION_AUDIO and len(fitting) > 1:
                # Two recordings of the one book (MP3 and M4B): the better format is taken.
                fitting = [min(fitting, key=lambda unit: _rank(wanted.config, kind, audio_format(unit)))]
            if len(mine) > 1 and not fitting:
                if all(unit.author == "no" and not _worth_asking(unit) for unit in mine) and not wanted.upload:
                    return reject("author_mismatch", found=mine[0].meta.authors[:3])
                if all(unit.title == "other" for unit in mine) and not wanted.upload:
                    return reject("title_mismatch", title=mine[0].meta.title)
                return ask("bundle", units=[unit.brief() for unit in mine], wanted=subject.get("position"))
            if not fitting:
                unit = mine[0]
                if unit.extra == "adaptation" and not wanted.upload:
                    return reject("adaptation", title=unit.meta.title)
                if unit.author == "no" and not wanted.upload:
                    if (wanted.named_author and unit.title == "yes") or _worth_asking(unit):
                        # The release named the author wished, the file names another with the title wished: a pen
                        # name, a translator or a reader where the author belongs. Or the file names one first name
                        # away from the author wished. Two voices: a person decides.
                        return ask("pen_name", found=unit.meta.authors[:3],
                                   wanted=[str(name) for name in (subject.get("authors") or [])][:3],
                                   title=unit.meta.title)
                    return reject("author_mismatch", found=unit.meta.authors[:3])
                if unit.other_volume and not wanted.upload:
                    return reject("other_volume", found=unit.meta.series_pos, wanted=subject.get("position"))
                if unit.title == "other" and not wanted.upload:
                    # The title of another book of the author nexbooks knows: clearly not the book wished.
                    return reject("title_mismatch", title=unit.meta.title)
                return ask("files_unassigned", title=unit.meta.title, authors=unit.meta.authors[:3],
                           volume=unit.meta.series_pos)
            chosen = fitting
        rest = [unit for unit in mine if unit not in chosen]
        for unit in rest:
            other = next((item for item in wanted.others if _fills(unit, item.subject)), None)
            if other is not None and not wanted.upload:
                unit.wish_id = other.wish_id
                chosen.append(unit)
            elif wanted.upload and unit.author != "no" and not chosen_key:
                # An upload's other books go in as books of their own.
                chosen.append(unit)
            else:
                left.append(unit)
                for item in unit.files:
                    drop[file_key(item)] = "bundle_rest"
    # A sample is no book (unless a person said to take it), nor is an adaptation; an edition with additions or a part
    # is a question.
    taken_as_is = bool((wanted.decision or {}).get("take"))
    if subject is not None and not wanted.upload and not taken_as_is:
        lead_unit = next((unit for unit in chosen if unit.wish_id is None), None)
        if any(unit.extra == "sample" and unit.wish_id is None for unit in chosen):
            return reject("sample")
        if lead_unit is not None and lead_unit.extra == "adaptation":
            return reject("adaptation", title=lead_unit.meta.title)
        if lead_unit is not None and lead_unit.extra in ("edition", "partial"):
            return ask(lead_unit.extra, title=lead_unit.meta.title, authors=lead_unit.meta.authors[:3])
    # The language from the files.
    if not wanted.upload:
        for unit in chosen:
            if unit.wish_id is not None:
                continue
            found = _language_verdict(unit, wanted)
            if found is not None:
                return found
    # The format.
    config = wanted.config
    ticked = _ticked(config, kind)
    for unit in chosen:
        if kind == EDITION_EBOOK:
            if not wanted.upload:
                for item in unit.files:
                    if item.format not in ticked:
                        drop[file_key(item)] = "format_not_wanted"
                unit.files = [item for item in unit.files if file_key(item) not in drop]
            unit.fmt = ebook_format(unit, ticked) if unit.files else None
        else:
            unit.fmt = audio_format(unit)
    if not wanted.upload:
        main = [unit for unit in chosen if unit.wish_id is None]
        if any(not unit.files for unit in main):
            found = sorted({item.format for unit in main for item in unit.files} or
                           {item.format for unit in mine for item in unit.files})
            return reject("format_not_wanted", found=found)
        if kind == EDITION_AUDIO:
            for unit in main:
                if unit.fmt not in ticked:
                    return reject("format_not_wanted", found=[unit.fmt or ""])
        chosen = [unit for unit in chosen if unit.files and (kind == EDITION_EBOOK or unit.fmt in ticked)]
    lead = next((unit for unit in chosen if unit.wish_id is None), chosen[0] if chosen else None)
    if lead is None:
        return reject("no_book")
    variant, proven = "", False
    if kind == EDITION_AUDIO:
        variant, proven, verdict = _version(lead, wanted)
        if verdict is not None:
            return verdict
    replaces = False
    have = None if wanted.upload else best_have(wanted, kind, lead, variant)
    unsure = None if wanted.upload or have is not None else unsure_have(wanted, kind, lead)
    if unsure is not None:
        better = _rank(config, kind, lead.fmt) < _rank(config, kind, unsure)
        if wanted.upgrade and not better:
            return reject("not_better", found=lead.fmt or "", have=unsure)
        if better:
            # A better format of an e-book the library has, and nothing proves it the same edition (the old one says
            # something of its own, the new one does not match it): replace it, or keep both? A person decides.
            return ask("same_edition", have=unsure, found=lead.fmt or "", title=lead.meta.title,
                       authors=lead.meta.authors[:3])
    if have is not None:
        better = _rank(config, kind, lead.fmt) < _rank(config, kind, have)
        if not better:
            # The library has this version of the book, in this format or a better one: the file would stand beside it
            # as a copy.
            return reject("not_better", found=lead.fmt or "", have=have)
        replaces = better
    if kind == EDITION_AUDIO and left and not proven and not wanted.upload and not chosen_key \
            and not (wanted.decision or {}).get("take"):
        # Nothing proves that what goes in is the whole book: what stays out may be a part of it.
        return ask("bundle", units=[unit.brief() for unit in mine], wanted=(subject or {}).get("position"), whole=True)
    return take(take={unit.key: unit.wish_id for unit in chosen}, drop=drop, variant=variant, variant_proven=proven,
                fmt=lead.fmt or "", replaces=replaces, language=lead.spoken)


def _formats_of(unit: Unit) -> list[str]:
    """The formats of a book in the words of the ranking."""
    if unit.kind == EDITION_EBOOK:
        return [item.format for item in unit.files]
    return [audio_format(unit) or ""]


def _has_a_ticked_format(unit: Unit, wanted: Wanted) -> bool:
    ticked = _ticked(wanted.config, unit.kind)
    return any(found in ticked or not found for found in _formats_of(unit))


def _text_of(lead: Unit) -> Have:
    return Have(fmt=lead.fmt or "", isbn=lead.meta.isbn, translators=lead.meta.translators, pages=lead.meta.pages,
                publisher=lead.meta.publisher, title=lead.meta.title or "",
                year=str(lead.meta.year) if lead.meta.year else "")


def best_have(wanted: Wanted, kind: str, lead: Unit, variant: str) -> str | None:
    """The best format of what the library has that is the same version as the book that came (None: nothing of it).
    An e-book whose edition is unsure counts as the same when a person said so (``Wanted.decision`` ``replace``)."""
    ranking = [item["f"] for item in wanted.config[kind]]
    assume = (wanted.decision or {}).get("replace")
    fits = [item.fmt for item in wanted.haves
            if item.fmt and same_version(kind, item, variant=variant, language=lead.meta.language or wanted.language,
                                         narrators=lead.meta.narrators, seconds=float(lead.meta.duration or 0),
                                         text=_text_of(lead), assume=assume is True)]
    return min(fits, key=lambda fmt: ranking.index(fmt) if fmt in ranking else len(ranking), default=None)


def unsure_have(wanted: Wanted, kind: str, lead: Unit) -> str | None:
    """The best format of an e-book the library has in the language whose edition is unsure against the one that came
    (``text_against``), when nobody answered the question yet; None else."""
    if kind != EDITION_EBOOK or (wanted.decision or {}).get("replace") is not None:
        return None
    ranking = [item["f"] for item in wanted.config[kind]]
    language = _language(lead.meta.language or wanted.language)
    found = [item.fmt for item in wanted.haves if item.fmt and not (
        language and _language(item.language) and _language(item.language) != language)
        and text_against(item, _text_of(lead)) == "unsure"]
    return min(found, key=lambda fmt: ranking.index(fmt) if fmt in ranking else len(ranking), default=None)


def _copies(units: list[Unit]) -> bool:
    """Whether recordings are copies of one book, and not its parts: each in a format of its own, and of one length."""
    lengths = [float(unit.meta.duration or 0) for unit in units]
    shapes = [audio_format(unit) for unit in units]
    return len(set(shapes)) == len(shapes) and min(lengths) >= COPY_LENGTH * max(lengths)


def _worth_asking(unit: Unit) -> bool:
    """Whether a file that names another author is nearly the author wished, and says nothing against the title."""
    return unit.near_author and unit.title in ("yes", "unknown")


def _on_numbered_folder(unit: Unit) -> bool:
    """Whether the files of a recording lie in a folder of a numbered part (``names.disc_in_path``)."""
    return any(names.disc_in_path(item.path) is not None for item in unit.files)


def _fills(unit: Unit, subject: dict[str, Any]) -> bool:
    probe = Unit(key=unit.key, kind=unit.kind, files=unit.files, meta=unit.meta)
    hold(probe, subject)
    return probe.fits and probe.title == "yes"


__all__ = ["Have", "Other", "Unit", "Verdict", "Wanted", "audio_format", "best_have", "file_key", "hold", "judge_units",
           "runtime_variant", "same_version", "units_of"]
