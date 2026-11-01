"""From files to works: the files of one unit become an edition, and the edition finds its work.

A *unit* is what belongs together on the disk: the files of one e-book (same folder, same name: ``Book.epub``,
``Book.azw3``, ``Book.mobi``) or the files of one recording (same folder, same album: the parts, also in ``CD 1`` and
``CD 2`` folders). The scan reads the files (``services/probe.py``) and keeps what it read in the file's row; this
module puts a unit together from those rows and the files beside them, then:

1. finds the **edition** of the unit: the one its files already belong to (so a book keeps its identity when a file
   is added or changes), else a new one, an e-book joining the edition of the same language in the same library
   (formats are parallel: *the edition is the content, the format is the file*), a recording always its own;
2. finds the **work** of a new edition: the same ISBN, else the same title and the same author (last name *and* first
   name or initial, never the last name alone), and a different volume number is a different work; a translation joins
   its original only on a sure proof (the original title the file names); in doubt it is a work of its own, which
   the matching with the sources may join;
3. lets the work take over what its editions say (``summarize``: the **lead edition**, the first e-book else the first
   recording, speaks, the others fill what it leaves empty). That is the summary the server keeps. A person is shown the
   same rule over the editions *they* may see (``services/works.py``), never this summary.

What a unit says about itself, in this order: its own files (the best format first), the files beside it
(``metadata.opf``, ``metadata.json``, ``cover.jpg``), and last the names of the files and folders.

**The parts of a recording** lie in the folder of the book, also in folders that only hold parts (``Audio``,
``Hörbuch``, ``MP3``, ``CD 1``, ``Disc 2``, ``Teil 1``): they are all one unit, on one timeline in the order of the
discs. The title of a recording is the album tag without what a carrier or a form adds to it (``Todesrache MP3CD``,
``(ungekürzt)``), if it fits the name of the book's folder; a folder named the way a tool names it (``Titel - Reihe 6
(2020)``, a year, a number in a collection) is worth more than a tag that says something else, and a text of a shop or
a series where a title should be is no title. The number of a part in a file name (``Titel-1v6``, ``001-Titel``) is
never the title. A folder of a series with a numbered folder for every book (``1.-.Titel``, ``2.-.Titel``) is one book
for each. The author is the top folder of the library when the tags do not say or say "Various".
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    ABRIDGED,
    AUTHOR,
    DRAMATIZED,
    EDITION_AUDIO,
    EDITION_EBOOK,
    NARRATOR,
    ROLE_BOOK,
    TRANSLATOR,
    UNABRIDGED,
    BookFile,
    Chapter,
    Credit,
    Edition,
    ExternalRef,
    Library,
    Person,
    PersonAlias,
    Series,
    Work,
)
from . import aliases, book_words, chapter_groups, genres, merge, search_index, series_names, spelling, title_shape
from .match import overlay as match_overlay
from .readers import AUDIO_FORMATS
from .readers.audio import unnamed_cast
from .readers.names import (
    FolderBook,
    NameParts,
    album_is_blurb,
    album_is_generic,
    article_in_front,
    author_beside_title,
    authors_compatible,
    authors_in_path,
    clean_album,
    clean_authors,
    collapse_repeats,
    core_title,
    disc_book,
    disc_in_path,
    display_name,
    folder_book,
    is_disc_folder,
    is_generic_series,
    is_label,
    is_shelf_name,
    looks_like_author,
    ordinal_series,
    parse_name,
    part_count_of,
    part_number_of,
    person_key,
    sort_name,
    split_form_suffix,
    split_prefix_series,
    split_series_title,
    split_suffix_series,
    strip_blurb,
    strip_part_folders,
    strip_part_marks,
    strip_recording_note,
    subtitle_key,
    subtitles_differ,
    title_fits,
    title_keys,
    top_folder_author,
    year_in_path,
)
from .readers.text import clean, date_of, fold, key_fold, release_text, valid_year

logger = logging.getLogger("nexbooks.catalog")

#: The better the format for reading, the earlier it speaks for the edition.
EBOOK_ORDER = {"epub": 0, "azw3": 1, "mobi": 2, "pdf": 3}
#: Minutes of a recording per printed page, to give an audio-only book a width on the shelf.
MINUTES_PER_PAGE = 1.55
MAX_CHAPTERS = 5000

_NATURAL = re.compile(r"(\d+)")


def natural(text: str) -> list[Any]:
    """A sort key that puts ``Part 2`` before ``Part 10``."""
    return [int(piece) if piece.isdigit() else piece.casefold() for piece in _NATURAL.split(text)]


@dataclass
class Meta:
    """Everything an edition is made of, put together from the files of its unit."""

    kind: str
    title: str = ""
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    language: str = ""
    isbn: str = ""
    asin: str = ""
    publisher: str = ""
    published: str = ""
    year: int | None = None
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    genre: str = ""
    #: What the voices of the file say about the genre (``genres.tally``): kept so that a source can add its own votes
    #: later (``match/overlay.py``) and the answer is the majority of all of them.
    genre_votes: genres.Votes = field(default_factory=dict)
    #: The year the folder or file name puts in brackets ("Auris (2019)"): a sign for the matching, whatever the file
    #: says.
    name_year: int | None = None
    series: str = ""
    series_pos: float | None = None
    original_title: str = ""
    original_language: str = ""
    variant: str = ""
    uncertain: bool = False
    #: The recording is called a play by the genre in its tags and by nothing else.
    play_by_genre: bool = False
    pages: int = 0
    duration: int = 0
    chapters: list[dict[str, Any]] = field(default_factory=list)
    cover: str = ""
    color: str = ""
    dir: str = ""
    #: Pairs of names the tags say are one person (``A schreibt als B``): the proof of a pen name.
    pen_names: list[tuple[str, str]] = field(default_factory=list)
    #: The title carried its series (in front of it or after it) and lost it: said once, the other kind is not
    #: looked for.
    series_in_title: bool = False
    #: The description is only the comment tag of a recording: a source's description goes before it.
    description_weak: bool = False
    #: The title comes from the name of a file or folder, no tag or package says it.
    title_from_names: bool = False


def _probe(row: BookFile) -> dict[str, Any]:
    return row.probe if isinstance(row.probe, dict) else {}


def _pick(sources: list[dict[str, Any]], key: str) -> Any:
    """The first non-empty value of ``key`` among the sources (best source first)."""
    for source in sources:
        value = source.get(key)
        if value not in (None, "", [], 0):
            return value
    return None


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _unit_dir(path: str, *, release: bool = False) -> str:
    """The folder of a recording's parts: the folder of the file without the folders that only hold parts (``CD 1``,
    ``Disc 2``, ``Teil 1``, ``Audio``, ``Hörbuch``, ``MP3``), so that all of them are one book: ``Autor/Titel/Audio``
    and ``Autor/Titel/CD 2`` are both ``Autor/Titel``. ``release``: the path is relative to a release, whose own folder
    is the book (``CD 1/01.mp3`` is a part of it), and folders that name their book as well keep that book apart from
    the next one on discs (``Der Hafen CD1`` and ``Die Nebelbahn CD1``): ``die nebelbahn`` is no folder on the disk."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    book = strip_part_folders(folder, release=release)
    if not release:
        return book
    named = next((name for name in map(disc_book, folder[len(book):].split("/")) if name), "")
    return f"{book}/{named}" if book and named else (named or book)


#: A recording whose author field names this many people or more is taken for a play with a cast.
CAST_MIN = 6
#: A recording read by this many people or more is taken for a play as well (a reading has one reader, seldom two or
#: three).
CAST_READERS = 4
#: ``Folge 12`` in the names of a recording (folded): an episode of a play, when the recording is as short as one.
_EPISODE = re.compile(r"(?<![a-z])(?:folge|episode)\s*0*\d{1,4}(?![\d])")
#: The longest an episode of a play runs, in seconds (most run an hour): a reading of a book runs longer.
EPISODE_MAX = 9000

#: What the parts of a unit key are joined with: a control character, which no name that is kept can hold (the walk
#: lists such a name as a problem and leaves it out), so that nothing in a folder name can be mistaken for the joint.
UNIT_JOINT = chr(31)
#: The unit of all the recordings of a release when a person said they are one book.
WHOLE_UNIT = UNIT_JOINT.join(("a", "", ""))


def album_key(probe: dict[str, Any] | None, path: str, fmt: str) -> str:
    """What the albums of the parts of one recording have in common: the album without what a disc or a carrier adds
    (``Todesrache MP3CD`` and ``Todesrache CD 2`` are both ``todesrache``). An album that names no book counts as
    none; a single container file (M4B, M4A) without one stands alone by its name."""
    album = clean_album((probe or {}).get("album"))
    key = "" if album_is_generic(album) else fold(album)
    # The parts of a recording named "Titel-1v6", "Titel-2v6": one recording whatever the albums of the parts say (a
    # tool writes the number of the part into the album too, and the albums then differ).
    if part_count_of(_stem(path)):
        base = fold(strip_part_marks(_stem(path)))
        if base:
            return "~" + base
    if not key and fmt in ("m4b", "m4a"):
        key = "~" + fold(_stem(path))
    return key


def unit_key(path: str, fmt: str, probe: dict[str, Any] | None, *, release: bool = False) -> str:
    """What the files of one unit have in common. E-books: the folder and the name without the extension. Recordings:
    the folder (the folders of parts count as the book's folder) and the album; without an album a single container
    file (M4B, M4A) stands alone and loose parts (MP3, FLAC) belong to their folder. The files on a disc of a folder
    (``CD 2``) are parts of that folder's recording whatever their albums say. ``release``: the path is relative to a
    release (``_unit_dir``)."""
    if fmt in AUDIO_FORMATS.values():
        album = "" if on_a_disc(path, release=release) else album_key(probe, path, fmt)
        return UNIT_JOINT.join(("a", _unit_dir(path, release=release), album))[:1500]
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    return UNIT_JOINT.join(("e", folder, fold(_stem(path))))[:1500]


def on_a_disc(path: str, *, release: bool = False) -> bool:
    """Whether a file lies in a folder named for a disc (``CD 2``, ``Disc 01``), or for a disc of the book its folder
    stands for (``Die Nebelbahn CD 2``)."""
    parts = path.split("/")
    if len(parts) < 2:
        return False
    folder = "/".join(parts[:-1])
    return is_disc_folder(parts[-2]) or (
        bool(disc_book(parts[-2])) and strip_part_folders(folder, release=release) != folder)


def unit_folder(unit: str) -> str:
    """The folder a unit key names."""
    return unit.split(UNIT_JOINT, 2)[1]


def unit_group(path: str, fmt: str) -> str:
    """What the files that are read before their units can be put together have in common, known *before* a file is
    read (the album of a recording is not): an e-book's unit key, and for a recording the folder of its parts (the
    units in it are told by the album after it). A unit can be put together when every file of its group is read."""
    if fmt in AUDIO_FORMATS.values():
        return UNIT_JOINT.join(("a", _unit_dir(path)))[:1500]
    return unit_key(path, fmt, None)


#: The languages ``text.sample_language`` has short words for, and pairs of them it may mix up in a short sample.
_COUNTED_LANGUAGES = frozenset({"de", "en", "fr", "es", "it", "nl"})
LANGUAGE_NEIGHBOURS = frozenset({frozenset(("de", "nl")), frozenset(("es", "it"))})


def _detect_variant(meta_text: str, abridged: bool | None) -> tuple[str, bool]:
    """``(variant, uncertain)`` of a recording from a sidecar's flag or the words in its names."""
    if abridged is True:
        return ABRIDGED, False
    if abridged is False:
        return UNABRIDGED, False
    text = fold(meta_text)
    if book_words.dramatized_word(text):
        return DRAMATIZED, False
    if re.search(r"\b(ungekurzt\w*|unabridged|ungekuerzt\w*)\b", text):
        return UNABRIDGED, False
    if book_words.ABRIDGED.search(f" {text} "):
        return ABRIDGED, False
    return UNABRIDGED, True


def _real_date(value: Any, this_year: int) -> str:
    """The date of a file when its year is a real one and the date exists, else an empty string (``0101-01-01``,
    ``2015-13-45``)."""
    text = date_of(value)
    return text if text and valid_year(int(text[:4]), this_year) else ""


#: A title that names no book: what a program writes when it has nothing to say ("Microsoft Word - document1.doc").
_JUNK_TITLE = re.compile(r"^(?:microsoft word|untitled|unbenannt|document\d*|dokument\d*|kapitel \d+|layout \d+|"
                         r"adobe|pdfmaker|acrobat)\b|\.(?:docx?|indd|qxd|pmd|rtf|tmp)$", re.IGNORECASE)
_STOP_WORDS = frozenset({"der", "die", "das", "ein", "eine", "und", "the", "a", "an", "of", "and", "von", "in", "im",
                         "zu", "mit", "pdf", "ebook", "buch", "book"})


def _words(text: str) -> set[str]:
    return {word for word in fold(text).split() if len(word) > 2 and word not in _STOP_WORDS}


def _pdf_against_the_path(path: str, authors: Any, title: str) -> tuple[Any, str]:
    """A PDF's information is the weakest voice of a book (the tool that made the file, a name left from the template):
    when it contradicts the names the person gave the folders and the file, the names win. An author that no name in the
    path fits, while the path names one, is replaced by it; a title that shares no word with the name of the file or its
    folder, or is a program's placeholder, by the title of the name."""
    named = authors_in_path(path)
    if authors and named and not any(authors_compatible(a, b) for a in authors for b in named) or not authors and named:
        authors = named[:1]
    stem = _stem(path)
    folder = path.rsplit("/", 2)[-2] if path.count("/") >= 1 else ""
    from_name = parse_name(stem).title or clean(stem, 300)
    if title and (_JUNK_TITLE.search(title.strip()) or (
            _words(title) and _words(from_name) and not (_words(title) & (_words(from_name) | _words(folder))))):
        title = ""
    return authors, title or from_name


def audio_order(row: BookFile) -> tuple[Any, ...]:
    """The order of the parts of a recording: the disc (the tag, else the number of the folder it lies in), the track
    (the tag, else the number the file name gives), then the name with its numbers counted as numbers."""
    probe = _probe(row)
    disc = probe.get("disc") or disc_in_path(row.path) or 1
    track = probe.get("track") or part_number_of(_stem(row.path)) or 0
    return (disc, track, natural(row.path))


def fields_turned(title: str, authors: Any, path: str) -> bool:
    """Whether the title and the author of an e-book stand in each other's fields, by the names of its file: the field
    of the author holds what the name of the file or its folder calls the title and reads as no name of a person, and
    the field of the title holds a name of a person the path does not call the title. Whatever else is left as it is
    (a book of two names is called ``Anna Marten``)."""
    field = ", ".join(str(name) for name in authors or [])
    if not title or not field or not looks_like_author(title, several=False) or looks_like_author(field, several=False):
        return False
    folder = path.rsplit("/", 2)[-2] if "/" in path else ""
    called = [text for name in (_stem(path), folder) for text in (parse_name(name).title, name) if text]
    own = parse_name(_stem(path)).title
    return any(title_fits(field, name) for name in called) and not (own and title_fits(title, own))


def _roles_by_the_folder(own: list[dict[str, Any]], path: str, top: str | None,
                         narrators: list[str]) -> tuple[list[str], list[str]] | None:
    """``(authors, narrators)`` with the artist and the album artist of a recording turned around, when the album
    artist is the author a folder of the path names (``Ida Brandt/Der Spiegelsaal`` with the artist ``Mara Leser``
    and the album artist ``Ida Brandt``) and the artist is not: the folder and the album artist are two
    witnesses against one. None when nothing is to be turned (a tag that says who the author is, one name in both, the
    artist a name of the folders as well)."""
    if _pick(own, "authors_field"):
        return None
    artist, album = str(_pick(own, "artist") or ""), str(_pick(own, "albumartist") or "")
    if not artist or not album or person_key(artist) == person_key(album):
        return None
    folders = [name for name in {top or "", top_folder_author(path), *path.split("/")[:-1]} if name]

    def named(name: str) -> bool:
        return any(authors_compatible(name, folder) or person_key(name) == person_key(folder) for folder in folders)

    if not named(album) or named(artist):
        return None
    readers = [artist] + [name for name in narrators if person_key(name) not in (person_key(album), person_key(artist))]
    return [album], readers


def _narrator_beside_the_author(own: list[dict[str, Any]], path: str, top: str | None, authors: list[str],
                                narrators: list[str]) -> tuple[list[str], list[str]] | None:
    """``(authors, narrators)`` when the artist of a recording holds the narrator beside the author (``Ida Brenner, Ada
    Feldkamp``) and nothing but the artist says who the author is (no author tag, no album artist of its own): a name a
    folder of the path names as well is an author, the others read. None when no folder names one of them: two
    authors stay two."""
    if _pick(own, "authors_field") or len(authors) < 2:
        return None
    artist, album = str(_pick(own, "artist") or ""), str(_pick(own, "albumartist") or "")
    if album and person_key(album) != person_key(artist):
        return None
    folders = [name for name in {top or "", top_folder_author(path), *path.split("/")[:-1]} if name]
    named = [name for name in authors
             if any(authors_compatible(name, folder) or person_key(name) == person_key(folder) for folder in folders)]
    if not named:
        return None
    keys = {person_key(name) for name in authors}
    readers = [name for name in authors if name not in named] + [
        name for name in narrators if person_key(name) not in keys]
    return named, readers


_NUMBER_BEHIND = re.compile(r"\s+\d{1,2}$")


def _without_disc_marks(authors: list[str]) -> list[str]:
    """The names a tag of a recording gives, without what a disc adds to them: ``VORL 1`` names nobody, and
    ``Rune Falkner 2`` is Rune Falkner on the second disc."""
    return [_NUMBER_BEHIND.sub("", name) for name in authors if not is_label(name)]


def _authors_against_the_path(authors: list[str], path: str, title: str, series: str, top: str) -> list[str]:
    """The authors of a recording as its tags give them, unless they are no person. A tag that holds the name of the
    series (``Kommissar Kluftinger``) or of the book (``Rauhnacht``) where an author belongs, while the top folder of
    the library names a person who is none of them (``top``, see ``top_author``), is replaced by that person; a tag
    that names another person is left (the folder may be wrong)."""
    authors = _without_disc_marks(authors)
    if not top or (series and fold(top) == fold(series)):
        return authors
    if authors and any(authors_compatible(name, top) for name in authors):
        return authors
    # Names of the book as the path gives them: the folders below the top one, and the file name without the number of
    # its part. A tag that is the name of the book or of its series, or stands in the file name beside the title
    # (``10. Kommissar Kluftinger - Kluftinger.mp3``), names no person.
    stem = fold(strip_part_marks(_stem(path)) or _stem(path))
    folders = path.split("/")[1:-1]
    book_names = ({fold(title), fold(series)} | {fold(part) for part in folders}
                  | {fold(re.sub(r"\s*\(\s*\d{4}\s*\)", "", part)) for part in folders}) - {""}

    def is_no_person(name: str) -> bool:
        key = fold(name)
        return key in book_names or (len(key) >= 3 and key in stem and fold(title) in stem)

    if not authors or all(is_no_person(name) for name in authors):
        return [top]
    return authors


def _folder_of(directory: str) -> FolderBook:
    """What the folder of a recording's parts says (``folder_book``). A folder called like a shelf or a disc says
    nothing."""
    segments = [part for part in directory.split("/") if part]
    if not segments or is_shelf_name(segments[-1]):
        return FolderBook()
    return folder_book(segments[-1], depth=len(segments), parent=segments[-2] if len(segments) >= 2 else "")


def _best_album(own: list[dict[str, Any]]) -> str:
    """The album of a recording that names its book: the one most parts have, cleaned (``Todesrache MP3CD`` is
    ``Todesrache``), without the generic ones (``Audiobook``) and the texts of a shop (``Ein ...-Thriller, Band 6``)."""
    counts: Counter[str] = Counter()
    for probe in own:
        album = clean_album(probe.get("album"))
        if album and not album_is_generic(album) and not album_is_blurb(album):
            counts[album] += 1
    return max(counts, key=lambda name: counts[name]) if counts else ""


def _blurb_of(own: list[dict[str, Any]]) -> str:
    """The text of a shop in the albums, the last thing a title is taken from."""
    for probe in own:
        album = clean_album(probe.get("album"))
        if album and not album_is_generic(album):
            return core_title(album)
    return ""


def _description_of(kind: str, own: list[dict[str, Any]], beside: list[dict[str, Any]]) -> tuple[str, bool]:
    """``(description, weak)``. An e-book's own package speaks first, then the file beside it. A recording's comment
    tag is the last word (``weak``: a source's description goes before it), after a ``metadata.json`` or
    ``metadata.opf`` beside it. Text that is the note of whoever copied the files (``upped by``, ``encoded with``, an
    address, a short line without a sentence) is never a description."""
    package = [p for p in own if p.get("format") != "pdf"] if kind == EDITION_EBOOK else []
    for probe in package:
        text = probe.get("description") or ""
        if text and not release_text(text):
            return text, False
    for probe in beside:
        text = probe.get("description") or ""
        if text and not release_text(text, strict=True):
            return text, False
    if kind == EDITION_AUDIO:
        for probe in own:
            text = probe.get("description") or ""
            if text and not release_text(text, strict=True):
                return text, True
    return "", False


#: Whether the top folder of a library names its author, by library and folder: asked once and kept until the next
#: run of the scan ends (``forget_top_authors``).
_TOP_AUTHOR: dict[tuple[int, str], bool] = {}
_TOP_LOOKED_AT = 500


def forget_top_authors(library_id: int | None = None) -> None:
    for key in [key for key in _TOP_AUTHOR if library_id is None or key[0] == library_id]:
        _TOP_AUTHOR.pop(key, None)


def top_author(db: Session, library_id: int, path: str) -> str:
    """The author the top folder of a library names for a book (``top_folder_author``), unless the other books below
    that folder say it is none: books whose tags name other people as their authors and never this one, or name the
    folder as their series (a folder of a series or a box looks like a name: ``Star Wars``, ``Perry Rhodan``). In
    doubt, nobody."""
    top = top_folder_author(path)
    if not top:
        return ""
    folder = path.split("/", 1)[0]
    known = _TOP_AUTHOR.get((library_id, folder))
    if known is None:
        known = _top_holds_its_author(db, library_id, folder, top)
        if len(_TOP_AUTHOR) > 20_000:
            _TOP_AUTHOR.clear()
        _TOP_AUTHOR[(library_id, folder)] = known
    return top if known else ""


def _top_holds_its_author(db: Session, library_id: int, folder: str, top: str) -> bool:
    like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    rows = db.execute(select(BookFile.path, func.json_extract(BookFile.probe, "$.authors"),
                             func.json_extract(BookFile.probe, "$.series"),
                             func.json_extract(BookFile.probe, "$.album"))
                      .where(BookFile.library_id == library_id, BookFile.role == ROLE_BOOK,
                             BookFile.missing_at.is_(None), BookFile.path.like(like, escape="\\"))
                      .limit(_TOP_LOOKED_AT)).all()
    folded = fold(folder.replace(".", " ").replace("_", " "))
    for path, authors, series, album in rows:
        if series and fold(str(series)) in (folded, fold(top)):
            return False
        try:
            names = json.loads(authors) if isinstance(authors, str) else authors
        except ValueError:
            names = []
        people = clean_authors([name for name in names if isinstance(name, str)]) if isinstance(names, list) else []
        people = _without_disc_marks(people)
        # A tag that is the name of its own book (its folder, its album, its series, the name of its file) names no
        # person: it says nothing about the folder above.
        stem = fold(strip_part_marks(_stem(path)) or _stem(path))
        own = {fold(part) for part in path.split("/")[1:-1]} | {
            fold(re.sub(r"\s*\(\s*\d{4}\s*\)", "", part)) for part in path.split("/")[1:-1]} | {
            fold(clean_album(album or "")), fold(str(series or ""))}
        people = [name for name in people
                  if fold(name) not in own and not (len(fold(name)) >= 3 and fold(name) in stem)]
        if people and not any(authors_compatible(name, top) or person_key(name) == person_key(top) for name in people):
            return False
    return True


def build_meta(kind: str, rows: list[BookFile], sidecars: list[BookFile], top: str | None = None) -> Meta:
    """Puts the metadata of a unit together (see the module text for the order). ``top`` is the author the top folder of
    the library names (``top_author``, which asks the other books below it); None takes the name of the folder as it
    is."""
    meta = Meta(kind=kind)
    if kind == EDITION_EBOOK:
        rows = sorted(rows, key=lambda r: (EBOOK_ORDER.get(r.format, 9), r.path))
    else:
        rows = sorted(rows, key=audio_order)
    own = [_probe(r) for r in rows]
    beside = sorted((_probe(r) for r in sidecars if r.format in ("opf", "json")), key=lambda p: p.get("format") != "opf"
                    if kind == EDITION_EBOOK else p.get("format") != "json")
    sources = own + beside
    first_path = rows[0].path
    if top is None:
        top = top_folder_author(first_path)
    meta.dir = _unit_dir(first_path) if kind == EDITION_AUDIO else _dir_of(first_path)

    folder = FolderBook()
    ordinal: tuple[str, float] | None = None
    if kind == EDITION_AUDIO:
        folder = _folder_of(meta.dir)
        album = _best_album(own)
        # An album that only says which case of a series it is ("Kommissar Dupins zwölfter Fall") names no book.
        ordinal = ordinal_series(album) if album else None
        if ordinal is not None:
            album = ""
        side_title = next((source["title"] for source in beside if source.get("title")), "")
        # The album tag, if it is a title and fits the folder it lies in. A folder written the way a tool names the
        # folder of a book (a year, a series, a number) is worth more than a tag that says something else, and the file
        # beside the book (``metadata.json``) is worth more than the folder.
        if album and (not folder.structured or not folder.title or folder.title.isdigit()
                      or title_fits(album, folder.title)):
            meta.title = album
        elif side_title:
            meta.title = ""
        elif folder.structured and folder.title:
            meta.title = folder.title
            meta.title_from_names = True
        else:
            meta.title = album
        authors = _pick(own, "authors")
        subjects = [text for text in (_pick(own, "genre"),) if text]
        meta.narrators = list(_pick(own, "narrators") or [])
    else:
        meta.title = _pick(own, "title") or ""
        authors = _pick(own, "authors")
        subjects = list(_pick(own, "subjects") or [])
        meta.translators = list(_pick(own, "translators") or [])
        if all(row.format == "pdf" for row in rows):
            authors, meta.title = _pdf_against_the_path(rows[0].path, authors, meta.title)
        elif fields_turned(meta.title, authors, first_path):
            authors, meta.title = [meta.title], ", ".join(authors)
    for source in beside:
        if not meta.title and source.get("title"):
            meta.title = source["title"]
        if not authors and source.get("authors"):
            authors = source["authors"]
        if not subjects and source.get("subjects"):
            subjects = list(source["subjects"])
        if not meta.narrators and source.get("narrators"):
            meta.narrators = list(source["narrators"])
        if not meta.translators and source.get("translators"):
            meta.translators = list(source["translators"])
    meta.authors = list(authors or [])
    if kind == EDITION_AUDIO:
        swapped = _roles_by_the_folder(own, first_path, top, meta.narrators)
        if swapped is not None:
            meta.authors, meta.narrators = swapped
        else:
            beside_the_author = _narrator_beside_the_author(own, first_path, top, meta.authors, meta.narrators)
            if beside_the_author is not None:
                meta.authors, meta.narrators = beside_the_author
        meta.authors = _authors_against_the_path(meta.authors, first_path, meta.title, meta.series, top)
        meta.pen_names = [tuple(pair) for pair in (_pick(own, "pen_names") or []) if len(pair) == 2]
    else:
        # The package of an e-book (not a PDF's: its author is whoever made the file) and the file beside it say a
        # name is a pen name: ``Feldkamp, Mara (als Mara Lind)``.
        meta.pen_names = [tuple(pair) for pair in (_pick([p for p in own if p.get("format") != "pdf"] + beside,
                                                         "pen_names") or []) if len(pair) == 2]
    meta.subtitle = clean(_pick(sources, "subtitle"), 300)
    if meta.subtitle and fold(meta.subtitle) == fold(meta.title):
        meta.subtitle = ""
    for key in ("language", "isbn", "asin", "publisher", "published", "original_title"):
        setattr(meta, key, _pick(sources, key) or "")
    meta.description, meta.description_weak = _description_of(kind, own, beside)
    this_year = clock.now().year
    meta.published = _real_date(meta.published, this_year)
    meta.name_year = year_in_path(first_path)
    year = _pick(sources, "year")
    # A year no book can have ("0101-01-01" is what Calibre writes for a date it does not know) is no year: the year in
    # the name of the folder is next, then a source (the overlay), else the book shows none.
    meta.year = year if valid_year(year, this_year) else None
    if meta.year is None and meta.published:
        meta.year = int(meta.published[:4])
    if meta.year is None:
        meta.year = meta.name_year
    meta.series = _pick(sources, "series") or ""
    meta.series_pos = _pick(sources, "series_pos") if meta.series else None

    # The names of the files and folders: last, and only for what nothing else said. A single file speaks with its own
    # name; the parts of a recording with the name of their folder, and the number of a part is never a title.
    folder_name = meta.dir.rsplit("/", 1)[-1] if kind == EDITION_AUDIO else (
        first_path.rsplit("/", 1)[0].rsplit("/", 1)[-1] if "/" in first_path else "")
    if kind == EDITION_AUDIO:
        part_free = strip_part_marks(_stem(first_path))
        file_names = parse_name(part_free) if part_free else NameParts()
        folder_names = NameParts(title=folder.title, author=folder.author, series=folder.series, pos=folder.pos)
        single = clean_album(_pick(own, "title")) if len(rows) == 1 else ""
        if not meta.title and single and not album_is_generic(single):
            meta.title = single
        if not meta.title:
            meta.title = folder_names.title or file_names.title or part_free
            meta.title_from_names = True
        if not meta.title:
            meta.title = _blurb_of(own) or clean(_stem(first_path), 300)
        names_author = folder_names.author or file_names.author
        if not meta.authors and not names_author and not (meta.series and fold(top) == fold(meta.series)):
            names_author = top
        name_series = folder_names.series or file_names.series
        name_pos = folder_names.pos if folder_names.series else file_names.pos
    else:
        file_names = parse_name(_stem(first_path))
        folder_names = parse_name(folder_name) if folder_name else NameParts()
        names = file_names
        names_author = names.author or file_names.author or folder_names.author
        name_series = names.series or file_names.series or folder_names.series
        name_pos = names.pos if names.series else (file_names.pos if file_names.series else folder_names.pos)
        if not meta.title:
            meta.title = names.title or clean(_stem(first_path), 300)
            meta.title_from_names = True
    if not meta.authors and names_author:
        meta.authors = [display_name(names_author)]
    # The note a tool or a shop puts at the end of a title (``(gelesen von Ida Leser)``, ``- read by …``,
    # ``(ungekürzt)``, ``(Hörbuch)``) is no part of the title; the reader it names is the narrator of a recording whose
    # tags name none.
    meta.title, readers = strip_recording_note(meta.title)
    meta.title = series_names.without_line(meta.title)
    if kind == EDITION_AUDIO and readers and not meta.narrators:
        meta.narrators = readers
    # The kind of book at the end of a title ("Der Beobachter - Kriminalroman", "Ahnenland | Thriller") is no part of
    # the title: it is the subtitle (when there is none) and a sign of the genre. (Before the series is taken off: that
    # cuts a bar and what follows.)
    # What a shop adds to sell the book (``| Auftakt der Serie von Nr.1-Bestsellerautor ...``, ``(German Edition)``) is
    # no part of the title, and cut off before anything else is read out of it.
    meta.title = strip_blurb(meta.title) or meta.title
    meta.subtitle = strip_blurb(meta.subtitle)
    meta.title, form = split_form_suffix(meta.title)
    parts = split_series_title(meta.title, meta.series, meta.series_pos, authors=meta.authors)
    meta.title = parts.title or meta.title
    if parts.series and not meta.series:
        meta.series, meta.series_pos = parts.series, parts.pos
    if not parts.series:
        # ``Walter Pulaski 04 - Rachefrühling``: the series with its number in front of the title, when the folder
        # or the
        # file bears the title out (or the number is written with a zero or a word).
        again = split_prefix_series(meta.title, f"{folder_name} {folder.title} {_stem(first_path)}")
        if again is not None:
            meta.title = again.title
            meta.series_in_title = True
            if not meta.series:
                meta.series, meta.series_pos = again.series, again.pos
            elif meta.series_pos is None and fold(meta.series) == fold(again.series):
                meta.series_pos = again.pos
    if not parts.series and not meta.series_in_title:
        # ``Erbarmen (Carl Mørck 1)``, ``Titel - Untertitel - Reihe 1``: the series with its number after the title.
        behind = split_suffix_series(meta.title)
        if behind is not None:
            meta.title = behind.title
            meta.series_in_title = True
            if not meta.series:
                meta.series, meta.series_pos = behind.series, behind.pos
            elif meta.series_pos is None and fold(meta.series) == fold(behind.series):
                meta.series_pos = behind.pos
    if ordinal is not None and not meta.series:
        meta.series, meta.series_pos = ordinal
    if not meta.series and name_series:
        meta.series, meta.series_pos = name_series, name_pos
    elif meta.series and meta.series_pos is None and name_series and fold(name_series) == fold(meta.series):
        meta.series_pos = name_pos
    if is_generic_series(meta.series):
        # ``Teil``, ``Band``, ``CD``: a word for a part, never the name of a series.
        meta.series, meta.series_pos = "", None
    elif meta.series and series_names.is_line(meta.series, None, authors=meta.authors):
        # A publisher's line (``Fischer Taschenbuch 1983``, ``Klassiker im Insel Verlag``) or the author's own name in
        # the field of the series: no series of the book. Its number says nothing either (a series of plays counts
        # past a hundred, so the number alone keeps nothing out).
        meta.series, meta.series_pos = "", None
    meta.title, again = split_form_suffix(meta.title)
    form = form or again
    if form and not meta.subtitle:
        meta.subtitle = form
    if meta.subtitle and fold(meta.subtitle) in fold(meta.title):
        meta.subtitle = ""
    meta.genre_votes = genres.add_votes(genres.tally(subjects, genres.WEIGHTS["file"]),
                                        genres.tally([form] if form else [], genres.WEIGHTS["title"]))
    meta.genre = genres.winner(meta.genre_votes)
    meta.subjects = subjects

    if kind == EDITION_EBOOK:
        meta.pages = int(_pick(own, "pages") or 0)
        meta.chapters = [{"title": c.get("title", ""), "target": c.get("target", ""), "depth": int(c.get("depth", 0)),
                          "start_ms": 0} for c in (_pick(own, "chapters") or [])][:MAX_CHAPTERS]
        meta.variant = ""
    else:
        offset = 0
        chapters: list[dict[str, Any]] = []
        # Consecutive files without chapters of their own: one chapter each, unless their titles prove that a chapter
        # runs over several of them (``chapter_groups``). A file with chapters inside ends such a run.
        run: list[tuple[int, str]] = []
        # What names the book itself says nothing about a chapter (a title tag that holds the title of the book on every
        # file, parts of the book numbered as ``Titel (1/12)``).
        book_titles = [meta.title, folder.title, folder_name, *(str(p.get("album") or "") for p in own),
                       # A tag that holds the author, a reader or the series on each file names no chapter either.
                       *meta.authors, *meta.narrators, meta.series]

        def close_run() -> None:
            for first, _count, title in chapter_groups.group([title for _start, title in run], book_titles):
                chapters.append({"title": title, "target": "", "depth": 0, "start_ms": run[first][0]})
            run.clear()

        for row in rows:
            seconds = (row.duration_ms or 0) / 1000
            inner = _probe(row).get("chapters") or []
            if inner:
                close_run()
                chapters.extend({"title": c.get("title") or "", "target": "", "depth": 0,
                                 "start_ms": offset + round(float(c.get("start", 0)) * 1000)} for c in inner)
            else:
                run.append((offset, clean(_probe(row).get("title"), 300) or clean(_stem(row.path), 300)))
            offset += round(seconds * 1000)
        close_run()
        if len(chapters) == 1 and len(rows) == 1:
            sidecar_chapters = _pick(beside, "chapters")
            if sidecar_chapters:
                chapters = [{"title": c.get("title") or "", "target": "", "depth": 0,
                             "start_ms": round(float(c.get("start", 0)) * 1000)} for c in sidecar_chapters]
        meta.chapters = chapters[:MAX_CHAPTERS]
        meta.duration = round(sum(r.duration_ms or 0 for r in rows) / 1000)
        abridged = next((s.get("abridged") for s in beside if isinstance(s.get("abridged"), bool)), None)
        albums = " ".join(str(p.get("album") or "") for p in own[:3])
        # The genre tags count too: "Hörspiel" there is a play whatever the title says.
        named = " ".join([meta.title, folder_name, first_path.rsplit("/", 1)[-1], str(_pick(own, "title") or ""),
                          albums])
        text = " ".join([named, *subjects])
        meta.variant, meta.uncertain = _detect_variant(text, abridged)
        meta.play_by_genre = meta.variant == DRAMATIZED and not book_words.dramatized_word(fold(named))
        if meta.uncertain and (len(meta.authors) >= CAST_MIN or len(meta.narrators) >= CAST_READERS):
            # A cast in the field of the authors (six names and more) or of the readers (four and more): a play, as
            # far as the files can tell. Still not proven.
            meta.variant = DRAMATIZED
        elif meta.uncertain and (any(probe.get("cast") for probe in own) or unnamed_cast(
                str(_pick(own, "artist") or ""), str(_pick(own, "albumartist") or "")) or (
                _EPISODE.search(fold(text)) and 0 < meta.duration <= EPISODE_MAX)):
            # A cast the tags do not name (``div.``), or an episode of a series (``Folge 12``) with the length of one: a
            # play. A shop calls the recording of a play "unabridged" too: that does not change it.
            meta.variant, meta.uncertain = DRAMATIZED, False

    # The cover: of the unit's own files first, then the picture beside them.
    for source in own + [_probe(r) for r in sidecars if r.format == "cover"]:
        if source.get("cover_file"):
            meta.cover, meta.color = source["cover_file"], source.get("color", "")
            break
    meta.title = article_in_front(clean(meta.title, 300))
    meta.language = (meta.language or "")[:8]
    if kind == EDITION_EBOOK:
        # The text says its language more surely than the package (tools write ``en`` into German books, retail files
        # come with ``es``), but only when it says it beyond doubt (``text.clear_sample_language``: a language the short
        # words tell from its neighbours, by far ahead) and the package names nothing, or a language the words tell
        # apart, and not one next to it (``nl`` and ``de``). ``pt``, ``ca``, ``gl`` or ``af`` in a package stay: the
        # short words read them as Spanish or Dutch. Else the package stays, and the check after a download holds both.
        spoken = str(_pick(own, "text_language_clear") or "")
        package = meta.language[:2].casefold()
        if spoken and spoken != package and (not package or (
                package in _COUNTED_LANGUAGES and frozenset((package, spoken)) not in LANGUAGE_NEIGHBOURS)):
            meta.language = spoken
    # A tag that holds the title of this book and its author (``Der Leuchtturm (ungekürzt) - Mara Feldkamp``) names the
    # author: the title is known only now (the folder may have given it).
    book_titles = [meta.title, folder.title, folder_name]
    authors_now = [author_beside_title(name, book_titles) or name for name in meta.authors]
    if authors_now != meta.authors:
        meta.authors = list(dict.fromkeys(authors_now))
    return meta


# ---- People and series ------------------------------------------------------------------------------------------


def get_person(db: Session, name: str) -> Person:
    key = person_key(name)[:200]
    row = db.scalar(select(Person).where(Person.key == key))
    if row is None:
        # A name that is proved to be another name of a person leads to that person (a pen name).
        alias = db.scalar(select(PersonAlias).where(PersonAlias.key == key))
        row = db.get(Person, alias.person_id) if alias is not None else None
    if row is None:
        row = Person(name=display_name(name)[:200], sort_name=sort_name(name)[:200], key=key)
        db.add(row)
        db.flush()
    return row


def merge_collapsed_people(db: Session) -> int:
    """People made twice by a rule that wrote every repeated word once, initials too (``J. R. Falkner`` beside
    ``J. R. R. Falkner``, ``Mara R. Lindqvist`` beside ``Mara R. R. Lindqvist``): the one with the repeated initials is
    the person, the other one's credits go to it and it is gone. Returns how many people were merged. The caller
    commits."""
    people = db.execute(select(Person.id, Person.name, Person.key)).all()
    by_key = {key: ident for ident, _name, key in people}
    merged = 0
    for ident, name, _key in people:
        shorter = collapse_repeats(name)
        if shorter == name:
            continue
        other = by_key.get(person_key(shorter)[:200])
        if other is None or other == ident:
            continue
        taken = {(c.edition_id, c.work_id, c.role) for c in db.scalars(select(Credit).where(Credit.person_id == ident))}
        for credit in db.scalars(select(Credit).where(Credit.person_id == other)):
            if (credit.edition_id, credit.work_id, credit.role) in taken:
                db.delete(credit)
            else:
                credit.person_id = ident
        db.flush()
        editions = list(db.scalars(select(Credit.edition_id).where(Credit.person_id == ident,
                                                                    Credit.edition_id.is_not(None))))
        from . import people_info

        people_info.take_over(db, ident, other)
        db.execute(delete(Person).where(Person.id == other))
        search_index.update(db, editions)
        by_key.pop(person_key(shorter)[:200], None)
        merged += 1
    return merged


def rekey_people(db: Session) -> int:
    """People whose key is not the one the present rule makes of their name (``Mueller`` and ``Müller`` are one key
    now): the key is put right, and two people who have the same key now are one (the one with more credits stays).
    Returns how many people were changed. The caller commits."""
    people = db.execute(select(Person.id, Person.name, Person.key)).all()
    by_key = {key: ident for ident, _name, key in people}
    gone: set[int] = set()
    changed = 0
    for ident, name, key in people:
        wanted = person_key(name)[:200]
        if wanted == key or ident in gone:
            continue
        other = by_key.get(wanted)
        if other is not None and other != ident:
            mine, theirs = db.get(Person, ident), db.get(Person, other)
            if mine is None or theirs is None:
                continue
            keep, drop = (mine, theirs) if aliases.weight(db, mine) > aliases.weight(db, theirs) else (theirs, mine)
            aliases.merge_people(db, keep, drop)
            gone.add(drop.id)
            by_key.pop(drop.key, None)
            if keep.key != wanted:
                db.execute(update(Person).where(Person.id == keep.id).values(key=wanted))
                by_key.pop(keep.key, None)
                by_key[wanted] = keep.id
        else:
            db.execute(update(Person).where(Person.id == ident).values(key=wanted))
            by_key.pop(key, None)
            by_key[wanted] = ident
        db.flush()
        changed += 1
    return changed


def rekey_series(db: Session) -> int:
    """The same for series: ``Quist-Saga`` written with an umlaut or without is one series. Their books go to the
    one that stays. Returns how many were changed. The caller commits."""
    rows = db.execute(select(Series.id, Series.name, Series.key)).all()
    by_key = {key: ident for ident, _name, key in rows}
    changed = 0
    for ident, name, key in rows:
        wanted = key_fold(name)[:200]
        if wanted == key:
            continue
        other = by_key.get(wanted)
        if other is not None and other != ident:
            db.execute(update(Edition).where(Edition.series_id == ident).values(series_id=other))
            db.execute(update(Work).where(Work.series_id == ident).values(series_id=other))
            db.execute(delete(Series).where(Series.id == ident))
            by_key.pop(key, None)
        else:
            db.execute(update(Series).where(Series.id == ident).values(key=wanted))
            by_key.pop(key, None)
            by_key[wanted] = ident
        changed += 1
    return changed


def get_series(db: Session, name: str) -> Series | None:
    key = key_fold(name)[:200]
    if not key:
        return None
    row = db.scalar(select(Series).where(Series.key == key))
    if row is None:
        row = Series(name=clean(name, 200), key=key)
        db.add(row)
        db.flush()
    return row


def _set_credits(db: Session, *, edition: Edition | None = None, work: Work | None = None,
                 people: list[tuple[str, list[str]]]) -> None:
    """Replaces the credits of an edition or a work by ``(role, [names])`` lists."""
    if edition is not None:
        db.execute(delete(Credit).where(Credit.edition_id == edition.id))
    if work is not None:
        db.execute(delete(Credit).where(Credit.work_id == work.id, Credit.edition_id.is_(None)))
    seen: set[tuple[str, int]] = set()
    for role, names in people:
        for position, name in enumerate(names):
            person = get_person(db, name)
            if (role, person.id) in seen:
                continue
            seen.add((role, person.id))
            db.add(Credit(person_id=person.id, role=role, position=position,
                          edition_id=edition.id if edition is not None else None,
                          work_id=work.id if work is not None and edition is None else None))


# ---- Finding the work -------------------------------------------------------------------------------------------


def _authors_of_work(db: Session, work_id: int) -> list[str]:
    return list(db.scalars(
        select(Person.name).join(Credit, Credit.person_id == Person.id)
        .where(Credit.work_id == work_id, Credit.role == AUTHOR).order_by(Credit.position)))


def _authors_fit(db: Session, work_id: int, authors: list[str]) -> bool:
    if not authors:
        return False
    authors = aliases.canonical_names(db, authors)
    theirs = _authors_of_work(db, work_id)
    return any(authors_compatible(a, b) for a in authors for b in theirs)


_NUMBERS = re.compile(r"\d+")


def volumes_differ(series_id: int | None, position: float | None, other_id: int | None,
                   other_position: float | None) -> bool:
    """Whether two places in a series say that two books are two volumes. Only places in **one** series (or in a series
    nobody named) can: volume 1 of one series and volume 2 of another say nothing about each other."""
    if position is None or other_position is None or position == other_position:
        return False
    return not (series_id is not None and other_id is not None and series_id != other_id)


def same_core(title: str, other: str, *, strict: bool = False, series: Iterable[str] = (),
              position: float | None = None) -> bool:
    """Whether two titles are the title of one book: the same title, or the same up to a subtitle (the core title, so
    ``Das Skript: Thriller`` and ``Das Skript: Roman`` are one book); not when both name a number and the numbers differ
    (``Teil 1`` and ``Teil 2``), not when both have a subtitle and the two name two books (``Die Tribute von Panem:
    Tödliche Spiele`` and ``…: Flammender Zorn``), and never when one of them is an adaptation, a book about the book
    or an edition of the film (``Stolz und Vorurteil - Die Graphic Novel``, ``Nebelchor: Das Filmbuch``). ``strict``:
    the subtitle may only be there on one side. ``series`` and ``position`` (the series of the books and the volume):
    ``Nebelchor 2`` is the title of that volume (``title_shape.series_shorthand``)."""
    key, main = title_keys(title)
    okey, omain = title_keys(other)
    if not key or not okey:
        return False
    if key == okey:
        return True
    if book_words.not_the_work(title, other) or book_words.not_the_work(other, title):
        return False
    named = [name for name in series if name]
    if named and (title_shape.series_shorthand(title, series=named, position=position, wished=other)
                  or title_shape.series_shorthand(other, series=named, position=position, wished=title)):
        return True
    if main != omain:
        return False
    if strict:
        return key == main or okey == omain
    if key != main and okey != omain and (subtitles_differ(subtitle_key(title), subtitle_key(other))
                                          or _rests_differ(key[len(main):], okey[len(omain):])):
        return False
    mine, theirs = _NUMBERS.findall(key), _NUMBERS.findall(okey)
    return not (mine and theirs and mine != theirs)


#: Words of a subtitle that name the kind of book, not which book it is.
_KIND_ONLY = frozenset({"roman", "romane", "krimi", "kriminalroman", "thriller", "psychothriller", "novel", "mystery",
                        "erzahlung", "erzaehlung", "erzahlungen", "erzaehlungen", "story", "stories", "geschichte",
                        "saga", "romance", "fantasy", "liebesroman", "jugendbuch", "kinderbuch", "ein", "eine", "a",
                        "an", "der", "die", "das", "the", "neuer", "neue", "historischer", "spannender"})


def _rests_differ(rest: str, other: str) -> bool:
    """Whether what follows the core of two titles (folded) names two books: both say something that is not the kind
    of book (``es`` and ``carrie``, not ``thriller`` and ``roman``), and they are not one spelled twice."""
    rest, other = rest.strip(), other.strip()
    if not rest or not other or rest == other or _NUMBERS.search(rest) or _NUMBERS.search(other):
        return False
    if all(word in _KIND_ONLY for word in rest.split()) or all(word in _KIND_ONLY for word in other.split()):
        return False
    return rest not in other and other not in rest


def _series_id_of(db: Session, meta: Any) -> int | None:
    """The series a meta (or something shaped like it) is in, looked up and never made. A series that is named and not
    there yet is ``-1``: no series of any work, so no work is in the same series."""
    ident = getattr(meta, "series_id", None)
    if ident is not None:
        return int(ident)
    name = getattr(meta, "series", "")
    key = key_fold(name)[:200] if name else ""
    if not key:
        return None
    found = db.scalar(select(Series.id).where(Series.key == key))
    return int(found) if found is not None else -1


def series_like(db: Session, main: str, names: Iterable[str] = (),
                incoming: Iterable[tuple[str, str]] = ()) -> bool:
    """Whether the core title ``main`` (a title as far as its first subtitle) is the name of a series and not a title:
    ``Sucher - Die Spur der Kinder`` and ``Sucher - Stimme der Angst`` are two books of the series ``Sucher``. It is
    when
    a book says so (``names``: the series of the books asked about), when a series of that name is known, or when at
    least two different subtitled titles of one kind begin with it (``incoming``: the ``(kind, whole key)`` of the
    books asked about, counted even when they are not in the database yet)."""
    if not main:
        return False
    if any(name and key_fold(name)[:200] == main[:200] for name in names):
        return True
    if db.scalar(select(Series.id).where(Series.key == main[:200])) is not None:
        return True
    seen: dict[str, set[str]] = {}
    for kind, whole in incoming:
        if whole != main:
            seen.setdefault(kind, set()).add(whole)
    for kind, title in db.execute(select(Edition.kind, Edition.title).where(
            Edition.title_key.startswith(main, autoescape=True), Edition.title_key != main,
            Edition.missing_at.is_(None)).limit(300)):
        whole, head = title_keys(title)
        if head == main and whole != main:
            seen.setdefault(kind, set()).add(whole)
    return any(len(titles) >= 2 for titles in seen.values())


def same_book_title(db: Session, one: Any, other: Any, *, strict: bool = False) -> bool:
    """Whether the titles of two books (anything with ``title``, ``kind`` and perhaps ``series_name``) are the title of
    one book: ``same_core``, and **not when the core is the name of a series** (``series_like``) and the whole titles
    differ: then what follows the name of the series is the title (``Sucher - Die Spur der Kinder`` is not
    ``Sucher - Stimme der Angst``, nor the book called ``Sucher``). Two books of a series are one book only by the proof
    of an ISBN or a source, never by their titles."""
    if not same_core(one.title, other.title, strict=strict):
        return False
    key, main = title_keys(one.title)
    okey = title_keys(other.title)[0]
    if key == okey:
        return True
    names = [getattr(one, "series_name", "") or "", getattr(other, "series_name", "") or ""]
    return not series_like(db, main, names, [(one.kind, key), (other.kind, okey)])


def find_work(db: Session, meta: Meta, skip: int | None = None) -> Work | None:
    """The work a new edition belongs to, or None. See the module text for the rules. ``skip`` is a work that does not
    count (the one an edition is leaving)."""
    if meta.isbn:
        work_id = db.scalar(select(Edition.work_id).where(
            Edition.isbn == meta.isbn, Edition.work_id != (skip or 0)).order_by(Edition.id).limit(1))
        if work_id:
            return db.get(Work, work_id)
    key, main = title_keys(meta.title)
    if not key:
        return None
    candidates = db.scalars(select(Work).where((Work.key == key) | (Work.main_key == main), Work.id != (skip or 0))
                            .order_by(Work.id)).all()
    series_id = _series_id_of(db, meta) if meta.series_pos is not None else None
    for work in candidates:
        same = work.key == key or (work.main_key == main and (work.key == work.main_key or key == main))
        if not same:
            continue
        if work.key != key and (book_words.not_the_work(meta.title, work.title)
                                or book_words.not_the_work(work.title, meta.title)):
            # ``Stolz und Vorurteil - Die Graphic Novel`` is no edition of ``Stolz und Vorurteil``.
            continue
        if volumes_differ(series_id, meta.series_pos, work.series_id, work.series_pos):
            continue
        if work.key != key and series_like(db, main, [getattr(meta, "series", "") or ""],
                                           [(getattr(meta, "kind", EDITION_EBOOK), key)]):
            # The title begins with the name of a series: the books told apart by what follows are not one book.
            continue
        if _authors_fit(db, work.id, meta.authors):
            return work
    return None


def same_title(work: Work, other: Work) -> bool:
    """Whether two works have the title of one book (the rule of ``find_work``, for works that are there already):
    the same title, or the same up to a subtitle, and not two different volumes of a series."""
    if not work.key or not other.key:
        return False
    same = work.key == other.key or (work.main_key == other.main_key and (
        work.key == work.main_key or other.key == other.main_key))
    if not same:
        return False
    return not volumes_differ(work.series_id, work.series_pos, other.series_id, other.series_pos)


def find_original(db: Session, meta: Meta, skip: int | None = None) -> Work | None:
    """The work a translation belongs to, on the proof the file gives: its original title is the title of the work (or
    the
    work's original title is this title), by the same author. Nothing else counts."""
    if not meta.authors:
        return None
    if meta.original_title:
        key = title_keys(meta.original_title)[0]
        for work in db.scalars(select(Work).where(Work.key == key, Work.id != (skip or 0)).order_by(Work.id)):
            if _authors_fit(db, work.id, meta.authors):
                return work
    own = title_keys(meta.title)[0]
    if own:
        for work in db.scalars(select(Work).where(Work.original_key == own, Work.id != (skip or 0))
                               .order_by(Work.id)):
            if _authors_fit(db, work.id, meta.authors):
                return work
    return None


def _edition_authors(db: Session, edition_id: int) -> list[str]:
    return list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id == edition_id, Credit.role == AUTHOR).order_by(Credit.position)))


def _authors_fit_names(db: Session, edition_id: int, authors: list[str]) -> bool:
    theirs = _edition_authors(db, edition_id)
    return any(authors_compatible(a, b) for a in authors for b in theirs)


def _same_book(db: Session, edition: Edition, other: Edition, refs: set[tuple[str, str]],
               others: set[tuple[str, str]], authors: list[str]) -> bool:
    """Whether two editions are of one book by what they say now: the same ISBN, a source that calls both by the name of
    one work, the core of the title (the same title, or the same up to a subtitle, not two volumes of a series), or one
    is the translation of the other (its original title is the other's title, by one author). **Only the lack of all
    of this makes two editions two books**: that two editions come from different sources, with different ids there,
    says nothing."""
    if edition.isbn and edition.isbn == other.isbn:
        return True
    if refs & others:
        return True
    key = title_keys(edition.title)[0]
    okey = title_keys(other.title)[0]
    # The same title is enough to stay (the author of a file may be uncertain, and a book is not taken from its twin
    # because a reading of it names another author); only a different title is a reason to go.
    if same_book_title(db, edition, other):
        return not volumes_differ(edition.series_id, edition.series_pos, other.series_id, other.series_pos)
    if not authors or not _authors_fit_names(db, other.id, authors):
        return False
    mine = title_keys(edition.original_title)[0] if edition.original_title else ""
    theirs = title_keys(other.original_title)[0] if other.original_title else ""
    return bool((mine and mine == okey) or (theirs and theirs == key))


def find_home(db: Session, edition: Edition, authors: list[str], skip: int) -> Work | None:
    """The work an edition that leaves ``skip`` goes to: the work that holds the same book, found as when a book is read
    for the first time (``find_work``, ``find_original``), else one that holds an edition with the same core title by
    the same author, **whatever that work is called now** (a work is named after the edition that speaks first, and it
    may be about to lose that one in this very run). None when there is none; the oldest work wins."""
    meta = SimpleNamespace(isbn=edition.isbn, title=edition.title, series_pos=edition.series_pos,
                           series_id=edition.series_id, authors=authors, original_title=edition.original_title)
    work = find_work(db, meta, skip=skip)  # type: ignore[arg-type]
    if work is None and edition.kind == EDITION_EBOOK:
        work = find_original(db, meta, skip=skip)  # type: ignore[arg-type]
    if work is not None or not authors:
        return work
    main = title_keys(edition.title)[1]
    if not main:
        return None
    for other in db.scalars(select(Edition).where(
            Edition.work_id != skip, Edition.id != edition.id, Edition.missing_at.is_(None),
            Edition.title_key.startswith(main, autoescape=True)).order_by(Edition.work_id, Edition.id)):
        if not same_book_title(db, edition, other) or volumes_differ(
                edition.series_id, edition.series_pos, other.series_id, other.series_pos):
            continue
        if _authors_fit_names(db, other.id, authors):
            return db.get(Work, other.work_id)
    return None


#: How an edition came to stand in its work on purpose: a person put it there (``hand``), or a wish filed it after the
#: check said it is the book wished (``wish``). No grouping by the scan takes such an edition away again.
PLACED = ("hand", "wish")


def leave_foreign_work(db: Session, edition: Edition, work_ids: set[int]) -> bool:
    """An edition that is no longer the book of the work it stands in leaves it: by what the files, the sources and the
    names say now it is the same book as none of the other editions of the work (a match that was wrong once put it
    there, and the right one has changed what it is). **It takes a positive proof that it is another book** (another
    title, another volume of the series); two editions of one core title stay together whatever sources they come from.
    It goes to the work it fits (``find_home``), else to a work of its own. What hangs on the edition (the place in the
    book, bookmarks, the history) hangs on the edition and goes with it. **Never an edition a person placed**
    (``grouped_by`` ``hand``) **or a wish filed** (``wish``: the files were checked as the book wished), and never one
    that stands alone. The works it touches are added to ``work_ids``. Whether it moved."""
    if edition.grouped_by in PLACED:
        return False
    # The sessions of the scan do not flush by themselves: the credits just made must be there to be read.
    db.flush()
    members = list(db.scalars(select(Edition).where(
        Edition.work_id == edition.work_id, Edition.missing_at.is_(None)).order_by(Edition.id)))
    if len(members) < 2:
        return False
    authors = {m.id: aliases.canonical_names(db, _edition_authors(db, m.id)) for m in members}
    refs: dict[int, set[tuple[str, str]]] = {m.id: set() for m in members}
    for ref in db.scalars(select(ExternalRef).where(
            ExternalRef.edition_id.in_(list(refs)), ExternalRef.kind == "work")):
        refs[int(ref.edition_id or 0)].add((ref.source, ref.key))
    # The editions that are one book (by pairs, and what is one with one is one with all): the work holds as many books
    # as there are such groups.
    group = {m.id: m.id for m in members}

    def root(ident: int) -> int:
        while group[ident] != ident:
            group[ident] = group[group[ident]]
            ident = group[ident]
        return ident

    for index, one in enumerate(members):
        for other in members[index + 1:]:
            if _same_book(db, one, other, refs[one.id], refs[other.id], authors[one.id]) or _same_book(
                    db, other, one, refs[other.id], refs[one.id], authors[other.id]):
                group[root(other.id)] = root(one.id)
    books: dict[int, list[Edition]] = {}
    for m in members:
        books.setdefault(root(m.id), []).append(m)
    if len(books) < 2:
        return False
    work = db.get(Work, edition.work_id)
    work_key = work.key if work is not None else ""
    work_title = work.title if work is not None else ""

    def named_after(m: Edition) -> bool:
        return bool(work_key) and (title_keys(m.title)[0] == work_key or same_core(m.title, work_title))

    def weight(items: list[Edition]) -> tuple[bool, int, bool, int]:
        # The book a person placed stays, then the one with most editions, then the one the work is called after, then
        # the oldest.
        return (any(m.grouped_by in PLACED for m in items), len(items), any(named_after(m) for m in items),
                -min(m.id for m in items))

    keeps = max(books.values(), key=weight)
    if edition in keeps:
        return False
    old = edition.work_id
    target = find_home(db, edition, authors[edition.id], old)
    if target is None:
        now = clock.now()
        target = Work(title=edition.title[:300], added_at=now, updated_at=now)
        db.add(target)
        db.flush()
        target.pattern, target.tall, target.gold = genres.style_of(target.id)
    edition.work_id = target.id
    edition.grouped_by = ""
    work_ids.update((old, target.id))
    db.flush()
    logger.info("Edition %s left work %s for work %s (it is not the book of the others)", edition.id, old, target.id)
    return True


def _compatible_edition(db: Session, work: Work, library_id: int, meta: Meta) -> Edition | None:
    """The edition of an e-book in the same library and language (formats are parallel). Two different ISBNs are two
    editions; an unknown language fits any."""
    if meta.kind != EDITION_EBOOK:
        return None
    for edition in db.scalars(select(Edition).where(Edition.work_id == work.id, Edition.library_id == library_id,
                                                    Edition.kind == EDITION_EBOOK).order_by(Edition.id)):
        if edition.language and meta.language and edition.language != meta.language:
            continue
        if edition.isbn and meta.isbn and edition.isbn != meta.isbn:
            continue
        return edition
    return None


# ---- Putting it together ----------------------------------------------------------------------------------------


def _apply(db: Session, edition: Edition, meta: Meta, now: datetime) -> None:
    edition.kind = meta.kind
    edition.variant = meta.variant
    edition.variant_uncertain = meta.uncertain if meta.kind == EDITION_AUDIO else False
    edition.language = meta.language
    edition.title = meta.title[:300]
    edition.title_key = title_keys(meta.title)[0][:300]
    edition.subtitle = meta.subtitle[:300]
    edition.isbn = meta.isbn
    edition.asin = meta.asin
    edition.publisher = meta.publisher[:200]
    edition.published = (meta.published or (str(meta.year) if meta.year else ""))[:10]
    edition.description = meta.description
    edition.genre = meta.genre
    # A guess is made again after the books are in (``settle_guesses``); what the files and the sources say is no guess.
    edition.genre_guess = False
    edition.series_name = meta.series[:200]
    series = get_series(db, meta.series) if meta.series else None
    edition.series_id = series.id if series else None
    edition.series_pos = meta.series_pos
    author_sort = sort_name(meta.authors[0]) if meta.authors else ""
    edition.shelf_key = "|".join([
        f"{genres.rank(meta.genre):02d}", fold(author_sort) or "~", fold(meta.series) if series else "~",
        f"{meta.series_pos if meta.series_pos is not None else 0.0:09.3f}", fold(meta.title)])[:500]
    edition.original_title = meta.original_title[:300]
    edition.original_language = (meta.original_language or "")[:8]
    edition.pages = meta.pages
    edition.duration = meta.duration
    edition.cover = meta.cover
    edition.color = meta.color
    edition.missing_at = None
    edition.updated_at = now
    if edition.id is not None and meta.kind == EDITION_AUDIO:
        before = [(c.start_ms, c.title) for c in db.scalars(
            select(Chapter).where(Chapter.edition_id == edition.id).order_by(Chapter.number))]
        after = [(int(c["start_ms"]), clean(c["title"], 300)) for c in meta.chapters]
        if before and before != after:
            # The chapters were cut another way (files that are one chapter are one now): the places people saved keep
            # their time and only learn which chapter that is.
            merge.relabel_places(db, edition, meta.chapters)
    db.execute(delete(Chapter).where(Chapter.edition_id == edition.id))
    if meta.chapters:
        db.execute(Chapter.__table__.insert(), [
            {"edition_id": edition.id, "number": number, "title": clean(c["title"], 300),
             "start_ms": int(c["start_ms"]),
             "target": str(c.get("target") or "")[:500], "depth": int(c.get("depth") or 0)}
            for number, c in enumerate(meta.chapters, start=1)])
    _set_credits(db, edition=edition, people=[(AUTHOR, meta.authors), (NARRATOR, meta.narrators),
                                              (TRANSLATOR, meta.translators)])


def edition_order(edition: Edition) -> tuple[int, int]:
    """The order editions speak in: e-books before recordings, each by when it was made."""
    return (0 if edition.kind == EDITION_EBOOK else 1, edition.id)


@dataclass
class Shown:
    """What a work shows of itself, worked out from a set of its editions."""

    lead: Edition
    title: str
    description: str
    genre: str
    original_title: str
    original_language: str
    series_id: int | None
    series_pos: float | None
    year: int | None
    pages: int
    cover: str
    #: The colour of the cover; empty without one (the caller takes a colour of the genre's palette).
    color: str
    added: datetime
    #: The genre is a guess (no edition has a genre of its own).
    genre_guess: bool = False


def _no_title(title: str, other: str, series: list[str], position: float | None) -> bool:
    return (title_shape.series_shorthand(title, series=series, position=position, wished=other)
            or title_shape.with_uploader(title, other))


def _best_title(lead: Edition, ordered: list[Edition]) -> str:
    """The title the work goes by: the lead edition's, unless its files carry no title of the book (``Lantern Saga 2``,
    the title with an uploader's name) and another edition has the real one (two editions joined by a person or a
    wish: the work is not called after the shortest file title)."""
    series = [e.series_name for e in ordered if e.series_name]
    for other in ordered:
        if other is lead or not other.title or _no_title(other.title, "", series, other.series_pos):
            continue
        if _no_title(lead.title, other.title, series, lead.series_pos if lead.series_pos is not None
                     else other.series_pos):
            return other.title
    return lead.title


def summarize(editions: list[Edition]) -> Shown | None:
    """What a work shows, taken from ``editions``: the lead edition (the first e-book, else the first recording) speaks,
    the others fill what it leaves empty. **The one rule for the work as the server keeps it (all its editions) and as a
    person sees it (only the editions of their libraries).** None without editions."""
    ordered = sorted(editions, key=edition_order)
    if not ordered:
        return None
    lead = ordered[0]
    ebooks = [e for e in ordered if e.kind == EDITION_EBOOK]

    def first(attribute: str) -> Any:
        for edition in ordered:
            value = getattr(edition, attribute)
            if value not in (None, ""):
                return value
        return None

    # A genre somebody said (the files, a source, a person) beats a guess, whatever the order of the editions.
    genre_edition = next((e for e in ordered if e.genre and not e.genre_guess), None) or next(
        (e for e in ordered if e.genre), None)
    in_series = next((e for e in ordered if e.series_id is not None), None)
    this_year = clock.now().year
    year = next((int(e.published[:4]) for e in ordered
                 if e.published[:4].isdigit() and valid_year(int(e.published[:4]), this_year)), None)
    if lead.kind == EDITION_EBOOK and lead.pages:
        pages = lead.pages
    else:
        pages = next((e.pages for e in ebooks if e.pages), 0) or (
            round(lead.duration / 60 / MINUTES_PER_PAGE) if lead.duration else 0)
    cover = next((e for e in ordered if e.cover), None)
    return Shown(
        lead=lead, title=_best_title(lead, ordered), description=first("description") or "",
        genre=genre_edition.genre if genre_edition else "",
        genre_guess=bool(genre_edition and genre_edition.genre_guess),
        original_title=first("original_title") or "", original_language=first("original_language") or "",
        series_id=in_series.series_id if in_series else None,
        series_pos=in_series.series_pos if in_series else None, year=year, pages=pages,
        cover=cover.cover if cover else "", color=cover.color if cover and cover.color else "",
        added=min(e.created_at for e in ordered))


def refresh_work(db: Session, work: Work) -> None:
    """Lets a work take over what its editions say (``summarize``, over all of them): the summary the server keeps. What
    a person sees is worked out again from the editions they may see (``services/works.py``)."""
    editions = db.scalars(select(Edition).where(Edition.work_id == work.id, Edition.missing_at.is_(None))
                          .order_by(Edition.id)).all()
    shown = summarize(list(editions))
    if shown is None:
        return
    ordered = sorted(editions, key=edition_order)
    work.title = shown.title
    work.description = shown.description
    work.genre = shown.genre
    work.series_id = shown.series_id
    work.series_pos = shown.series_pos
    work.original_title = shown.original_title
    work.original_language = shown.original_language
    key, main = title_keys(work.title)
    work.key, work.main_key = key[:300], main[:300]
    work.original_key = title_keys(work.original_title)[0][:300] if work.original_title else ""
    work.year = shown.year or work.year
    work.pages = shown.pages
    work.cover = shown.cover
    work.color = shown.color or genres.palette_color(work.genre, work.key or str(work.id))
    by_edition: dict[int, list[Credit]] = {}
    for credit in db.scalars(select(Credit).where(Credit.edition_id.in_([e.id for e in ordered]), Credit.role == AUTHOR)
                             .order_by(Credit.edition_id, Credit.position)):
        by_edition.setdefault(int(credit.edition_id or 0), []).append(credit)
    # The authors of the lead edition; if it names none, those of the first edition that does.
    lead_authors = next((by_edition[e.id] for e in ordered if by_edition.get(e.id)), [])
    seen: set[int] = set()
    db.execute(delete(Credit).where(Credit.work_id == work.id, Credit.edition_id.is_(None)))
    for position, credit in enumerate(lead_authors):
        if credit.person_id in seen:
            continue
        seen.add(credit.person_id)
        db.add(Credit(person_id=credit.person_id, work_id=work.id, role=AUTHOR, position=position))
    work.updated_at = clock.now()


def _held_by(db: Session, rows: list[BookFile]) -> dict[int, int]:
    """The editions the files of a unit are in now, with how many of the unit's files each holds. An edition that more
    of its files lie in another unit than in this one belongs to that unit, not to this one (a unit that was split, or
    whose files were moved): two units never share an edition."""
    held = Counter(row.edition_id for row in rows if row.edition_id is not None)
    unit = rows[0].unit
    mine: dict[int, int] = {}
    for ident, count in held.items():
        by_unit = dict(db.execute(select(BookFile.unit, func.count(BookFile.id)).where(
            BookFile.edition_id == ident, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))
            .group_by(BookFile.unit)).all())
        owner = min(by_unit, key=lambda name: (-by_unit[name], name)) if by_unit else unit
        if owner == unit:
            mine[ident] = count
    return mine


def _shifts_of(rows: list[BookFile], kind: str) -> dict[int, int]:
    """For a recording put together out of the files of several editions: by how many milliseconds the running time
    before the first part of each of them has grown (the places kept in it move by as much). Empty for an e-book."""
    if kind != EDITION_AUDIO:
        return {}
    shifts: dict[int, int] = {}
    running = 0
    for row in sorted(rows, key=audio_order):
        if row.edition_id is not None and row.edition_id not in shifts:
            shifts[row.edition_id] = running
        running += int(row.duration_ms or 0)
    return shifts


def _merge_into(db: Session, edition: Edition, absorbed: list[Edition], shifts: dict[int, int], meta: Meta,
                work_ids: set[int]) -> None:
    """Editions that are one with ``edition`` now give up what hangs on them (``services/merge.py``); one that still
    holds files of another unit stays as it is."""
    survivor_id = edition.id
    chapters = meta.chapters
    merge.shift_places(db, edition, shifts.get(survivor_id, 0), chapters)
    for other in absorbed:
        if merge.files_left(db, other.id):
            continue
        work_ids.add(other.work_id)
        merge.absorb(db, edition, other, shifts.get(other.id, 0), chapters)
    db.flush()


def assign(db: Session, library: Library, rows: list[BookFile], sidecars: list[BookFile]) -> Edition | None:
    """Puts the unit made of ``rows`` (files of one unit, none missing) together and returns its edition. Writes to the
    session; the caller commits."""
    if not rows:
        return None
    now = clock.now()
    kind = EDITION_AUDIO if rows[0].format in AUDIO_FORMATS.values() else EDITION_EBOOK
    meta = build_meta(kind, rows, sidecars, top=top_author(db, library.id, rows[0].path))
    if not meta.title:
        return None
    # What the files say, before a source or a person is laid over it: a changed file is a new look at the book.
    from .match import run as matching

    files_say = matching.fingerprint(meta)
    held = _held_by(db, rows)
    edition: Edition | None = None
    absorbed: list[Edition] = []
    if held:
        edition = db.get(Edition, merge.survivor_of(db, held))
        absorbed = [other for ident in sorted(held) if ident != getattr(edition, "id", None)
                    and (other := db.get(Edition, ident)) is not None and other.kind == kind
                    and other.library_id == library.id]
        if edition is not None and absorbed:
            merge.take_over_by_hand(db, edition, absorbed)
    created = False
    if edition is None:
        work = find_work(db, meta)
        if work is None and kind == EDITION_EBOOK:
            work = find_original(db, meta)
        if work is None:
            work = Work(title=meta.title[:300], added_at=now, updated_at=now)
            db.add(work)
            db.flush()
            work.pattern, work.tall, work.gold = genres.style_of(work.id)
            edition = None
        else:
            edition = _compatible_edition(db, work, library.id, meta)
        if edition is None:
            edition = Edition(work_id=work.id, library_id=library.id, kind=kind, title=meta.title[:300],
                              created_at=now, updated_at=now)
            db.add(edition)
            db.flush()
            created = True
    match_overlay.apply_to(meta, edition.overlay)
    _apply(db, edition, meta, now)
    # The tags say one person writes as another: that is evidence of this edition, made anew with every reading (it
    # is carried out when the library is settled).
    aliases.note_tags(db, edition, list(meta.pen_names))
    work_ids = {edition.work_id}
    if not created and edition.grouped_by == "":
        # A book read again by newer rules may now be the same work as another (the title lost the kind of book at its
        # end): the one that stands alone joins the older work it is the same as. Never an edition a person or a source
        # placed, and never one that has twins of its own (they would be left behind).
        target = find_work(db, meta)
        if target is not None and target.id != edition.work_id and not db.scalar(
                select(func.count(Edition.id)).where(Edition.work_id == edition.work_id, Edition.id != edition.id)):
            work_ids.add(target.id)
            edition.work_id = target.id
    if not created:
        leave_foreign_work(db, edition, work_ids)
    # What the files and the work say now, as far as matching cares: another fingerprint puts the book in the queue
    # again.
    matching.note(db, edition, matching.in_place(files_say, len(rows), edition.work_id))
    shifts = _shifts_of(rows, kind)
    for position, row in enumerate(sorted(rows, key=lambda r: (EBOOK_ORDER.get(r.format, 9), r.path)
                                          if kind == EDITION_EBOOK else audio_order(r)),
                                   start=1):
        if row.edition_id not in (None, edition.id):
            previous = db.get(Edition, row.edition_id)
            if previous is not None:
                work_ids.add(previous.work_id)
        row.edition_id = edition.id
        row.part = position if kind == EDITION_AUDIO else 1
        row.dirty = False
    db.flush()
    if absorbed:
        _merge_into(db, edition, absorbed, shifts, meta, work_ids)
    search_index.update(db, [edition.id])
    for work_id in work_ids:
        work = db.get(Work, work_id)
        if work is None:
            continue
        if db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id)):
            refresh_work(db, work)
        else:
            # The work an edition left, and nothing else is in it.
            db.delete(work)
            db.flush()
    # The sessions of the scan do not flush by themselves: the next unit looks for its work in the database.
    db.flush()
    if created:
        logger.debug("Edition made id=%s work=%s kind=%s", edition.id, edition.work_id, kind)
    return edition


def separate_mixed_works(db: Session) -> int:
    """Looks at every work that holds editions with different titles once more (``leave_foreign_work``): what a book
    read earlier was taken for may be clear only now that its neighbours are in (the second book of a series told apart
    by its title makes the name of the series no title). Returns how many editions moved."""
    work_ids = list(db.scalars(select(Edition.work_id).where(Edition.missing_at.is_(None)).group_by(
        Edition.work_id).having(func.count(func.distinct(Edition.title_key)) > 1)))
    touched: set[int] = set()
    moved = 0
    for work_id in work_ids:
        for edition in list(db.scalars(select(Edition).where(
                Edition.work_id == work_id, Edition.missing_at.is_(None)).order_by(Edition.id))):
            if edition.work_id == work_id and leave_foreign_work(db, edition, touched):
                moved += 1
    for ident in sorted(touched):
        work = db.get(Work, ident)
        if work is None:
            continue
        if db.scalar(select(func.count(Edition.id)).where(Edition.work_id == ident)):
            refresh_work(db, work)
        else:
            db.delete(work)
    db.flush()
    return moved


def join_twin_works(db: Session, since: datetime | None = None) -> dict[str, int]:
    """Works that hold the one book are one work: the editions of the younger go into the older, and what is left of it
    is deleted. A work without an edition is deleted as well. It is what a run that put a recording and its e-book into
    two works leaves behind (a source that named them by different ids, a title that was read another way before), and
    it makes good the way back for such a state.

    Two works are one when an edition of the one and an edition of the other have the same core title (``same_core``;
    a subtitle on both sides only for an e-book and a recording), by one author, and do not say that they are two
    volumes of one series. An e-book and a recording that lie in folders of the same name in their libraries
    (``Autor/Titel (2015)`` beside ``Autor/Titel (2015)``), each the only book of its kind there, are one book as well,
    whatever their files call them, unless they say two volumes, two authors, or one of them is no edition of the work
    (``_folder_twins``). **Never a work that holds an edition a person placed** (``grouped_by`` ``hand``): a person
    split or joined it on purpose, and nothing joins it or is joined to it. Progress, bookmarks and history hang on the
    editions and go with them. Returns how many works went into another and how many empty ones were deleted.

    Two works that were both there before the run (``since``) and become one now (newer rules after an update, a file
    that changed) are told: a line in the history of the downloads and a note for the people who look after the
    library, both with the way back ("Trennen", ``POST /editions/{id}/split``)."""
    counts = {"works_joined": 0, "works_empty": 0}
    rows = db.execute(select(
        Edition.id, Edition.work_id, Edition.title, Edition.kind, Edition.series_id, Edition.series_pos,
        Edition.series_name, Edition.library_id,
    ).where(Edition.missing_at.is_(None)).order_by(Edition.work_id, Edition.id)).all()
    by_core: dict[str, dict[int, list[Any]]] = {}
    for row in rows:
        main = title_keys(row.title)[1]
        if main:
            by_core.setdefault(main, {}).setdefault(row.work_id, []).append(row)
    candidates = {core: works for core, works in by_core.items() if len(works) > 1}
    folder_pairs = _folder_twins(db, rows)
    if candidates or folder_pairs:
        placed = set(db.scalars(select(Edition.work_id).where(Edition.grouped_by == "hand")))
        authors: dict[int, list[str]] = {}

        def authors_of(ident: int) -> list[str]:
            if ident not in authors:
                authors[ident] = aliases.canonical_names(db, _edition_authors(db, ident))
            return authors[ident]

        def one_book(left: list[Any], right: list[Any]) -> bool:
            for one in left:
                for other in right:
                    if not same_book_title(db, one, other, strict=one.kind == other.kind):
                        continue
                    if volumes_differ(one.series_id, one.series_pos, other.series_id, other.series_pos):
                        continue
                    if any(authors_compatible(a, b) for a in authors_of(one.id) for b in authors_of(other.id)):
                        return True
            return False

        moved: dict[int, int] = {}
        for works in candidates.values():
            kept: list[int] = []
            for work_id in sorted(works):
                if work_id in placed or work_id in moved:
                    continue
                target = next((k for k in kept if one_book(works[k], works[work_id])), None)
                if target is None:
                    kept.append(work_id)
                    continue
                # What the target holds now is more than its rows say: the editions that came into it count too.
                works[target] = works[target] + works[work_id]
                moved[work_id] = target

        def home(ident: int) -> int:
            while ident in moved:
                ident = moved[ident]
            return ident

        for book, recording in folder_pairs:
            left, right = home(book.work_id), home(recording.work_id)
            if left == right or left in placed or right in placed:
                continue
            if volumes_differ(book.series_id, book.series_pos, recording.series_id, recording.series_pos):
                continue
            mine, theirs = authors_of(book.id), authors_of(recording.id)
            if mine and theirs and not any(authors_compatible(a, b) for a in mine for b in theirs):
                continue
            older, younger = sorted((left, right))
            moved[younger] = older
            logger.info("Works %s and %s lie in folders of one name: one book", older, younger)
        joined: dict[int, set[int]] = {}
        for old, new in moved.items():
            while new in moved:
                new = moved[new]
            joined.setdefault(new, set()).add(old)
        for new, olds in sorted(joined.items()):
            for old in sorted(olds):
                if since is not None:
                    _tell_joined(db, old, new, since)
                db.execute(update(Edition).where(Edition.work_id == old).values(work_id=new))
                logger.info("Work %s was the book of work %s and went into it", old, new)
                counts["works_joined"] += 1
            db.flush()
            target_work = db.get(Work, new)
            if target_work is not None:
                refresh_work(db, target_work)
            for old in olds:
                work = db.get(Work, old)
                if work is not None:
                    db.delete(work)
            db.flush()
    empty = list(db.scalars(select(Work.id).where(Work.id.not_in(select(Edition.work_id)))))
    for ident in empty:
        work = db.get(Work, ident)
        if work is not None:
            db.delete(work)
            counts["works_empty"] += 1
    db.flush()
    return counts


def _tell_joined(db: Session, old: int, new: int, since: datetime) -> None:
    """The line in the history and the notes for the managers when work ``old`` goes into ``new`` and both were there
    before ``since`` (see ``join_twin_works``)."""
    from ..models import LibraryMember, ProcureHistory, ProcureNotice

    source, target = db.get(Work, old), db.get(Work, new)
    if source is None or target is None or not (source.added_at < since and target.added_at < since):
        return
    for edition in db.scalars(select(Edition).where(Edition.work_id == old, Edition.missing_at.is_(None))):
        note = {"edition_id": edition.id, "title": (edition.title or source.title)[:300], "into": target.title[:300]}
        db.add(ProcureHistory(user_id=None, library_id=edition.library_id, work_id=new, kind=edition.kind,
                              book_title=target.title[:300], book_author="", release="", result="joined", note=note,
                              created_at=clock.now()))
        for user_id in db.scalars(select(LibraryMember.user_id).where(LibraryMember.library_id == edition.library_id,
                                                                      LibraryMember.right == "manage")):
            db.add(ProcureNotice(user_id=user_id, edition_id=edition.id, code="works_joined",
                                 detail={"title": note["title"], "into": note["into"]}, created_at=clock.now()))
    logger.info("Work %s went into work %s, both there before: told in the history", old, new)


#: Words of a folder name that say nothing of the book in it.
_FOLDER_NOISE = frozenset({"ungekurzt", "ungekuerzt", "gekurzt", "gekuerzt", "unabridged", "abridged", "horbuch",
                           "hoerbuch", "audiobook", "ebook", "epub", "german", "deutsch", "english", "lesung"})


def _folder_twins(db: Session, rows: list[Any]) -> list[tuple[Any, Any]]:
    """Pairs of an e-book and a recording (rows of ``join_twin_works``) whose book folders have the same name, folded,
    with an author folder above (``Autor/Titel``), where each is the only edition of its kind under that name, and whose
    titles both hold a word of the folder's name: the shape a library of e-books beside one of recordings has.
    Words of a book that is no edition of the work (``book_words.not_the_work``) keep the two apart."""
    ids = [row.id for row in rows if row.kind in (EDITION_EBOOK, EDITION_AUDIO)]
    if not ids:
        return []
    folder_of: dict[int, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for edition_id, path, fmt in db.execute(select(BookFile.edition_id, BookFile.path, BookFile.format).where(
                BookFile.edition_id.in_(chunk), BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))):
            if edition_id in folder_of:
                continue
            folder_of[edition_id] = _unit_dir(path) if fmt in AUDIO_FORMATS.values() else _dir_of(path)
    by_name: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        folder = folder_of.get(row.id, "")
        if "/" not in folder:
            continue
        key = "/".join(" ".join(fold(part).split()) for part in folder.split("/")[-2:])
        by_name.setdefault((row.kind, key), []).append(row)
    pairs: list[tuple[Any, Any]] = []
    for (kind, key), found in by_name.items():
        recordings = by_name.get((EDITION_AUDIO, key), [])
        if kind != EDITION_EBOOK or len(found) != 1 or len(recordings) != 1:
            continue
        book, recording = found[0], recordings[0]
        if book.work_id == recording.work_id:
            continue
        named = {word for word in key.rsplit("/", 1)[-1].split() if len(word) >= 4 and not word.isdigit()}
        named -= _FOLDER_NOISE
        if not all(named & set(fold(row.title or "").split()) for row in (book, recording)):
            # A title that has nothing of the folder's name says another book: no proof of one.
            continue
        if book_words.not_the_work(book.title or "", recording.title or "") or book_words.not_the_work(
                recording.title or "", book.title or ""):
            continue
        if not _one_book_in_the_folder(key.rsplit("/", 1)[-1], book, recording,
                                       _edition_authors(db, book.id) + _edition_authors(db, recording.id)):
            continue
        pairs.append((book, recording))
    return pairs


#: Words of a title that say nothing of which book it is, beside the words of the folder (folded).
_TITLE_FILL = frozenset({"der", "die", "das", "des", "dem", "den", "ein", "eine", "und", "von", "the", "a", "an", "of",
                         "and", "le", "la", "les", "de", "et", "roman", "krimi", "thriller", "novel", "band", "teil",
                         "buch", "book", "folge", "kriminalroman", "psychothriller", "erzahlung", "erzaehlung",
                         "lesung", "ungekurzte", "ungekuerzte", "mystery", "audible", "retail"}) | _FOLDER_NOISE


def _title_rest(title: str, folder: str, people: list[str]) -> list[str]:
    known = set(folder.split()) | {word for name in people for word in fold(name).split()}
    return [word for word in fold(title or "").split() if word not in known and word not in _TITLE_FILL
            and not word.isdigit()]


def _one_book_in_the_folder(folder: str, book: Any, recording: Any, people: list[str]) -> bool:
    """Whether a folder that holds an e-book and a recording is the folder of one book, not of a series with a volume
    of each in it (``Spiegelwelt`` with the second volume as an e-book and the fifth as a recording): the folder is no
    name of the series of either, unless both say the same volume; the titles name no two volumes; and what the titles
    say beside the words of the folder (``Die Kammer der Uhren``, ``Der Orden der Glocken``) is no two different
    books."""
    for row in (book, recording):
        if row.series_name and fold(row.series_name) == folder and not (
                book.series_pos is not None and book.series_pos == recording.series_pos):
            return False
    one, other = book_words.title_volume(book.title or ""), book_words.title_volume(recording.title or "")
    if one is not None and other is not None and one != other:
        return False
    mine, theirs = _title_rest(book.title, folder, people), _title_rest(recording.title, folder, people)
    if mine and theirs:
        return bool({word for word in mine if len(word) >= 4} & {word for word in theirs if len(word) >= 4})
    # A subtitle on one side only (``Raul`` beside ``Raul – Ein Fall für …``) is the shape of one book.
    return True


def settle_library(db: Session, library_id: int, since: datetime | None = None) -> dict[str, int]:
    """After a run: editions without any file row are gone, editions whose files are all missing are marked, editions
    with a file again are not; works that lost or regained an edition take over what they show anew, and works without
    editions are gone. Commits as it goes (the editions first, then each work by itself). Returns the counts."""
    now = clock.now()
    forget_top_authors(library_id)
    counts = {"editions_removed": 0, "works_removed": 0, "editions_missing": 0}
    live = select(BookFile.edition_id).where(BookFile.library_id == library_id, BookFile.role == ROLE_BOOK,
                                             BookFile.missing_at.is_(None), BookFile.edition_id.is_not(None))
    anyfile = select(BookFile.edition_id).where(BookFile.library_id == library_id, BookFile.role == ROLE_BOOK,
                                                BookFile.edition_id.is_not(None))
    touched: set[int] = set()
    for ident, work_id in db.execute(select(Edition.id, Edition.work_id).where(
            Edition.library_id == library_id, Edition.id.not_in(anyfile))):
        touched.add(work_id)
        counts["editions_removed"] += 1
        db.execute(delete(Edition).where(Edition.id == ident))
    for ident, work_id in db.execute(select(Edition.id, Edition.work_id).where(
            Edition.library_id == library_id, Edition.id.not_in(live), Edition.missing_at.is_(None))):
        touched.add(work_id)
        counts["editions_missing"] += 1
        db.execute(update(Edition).where(Edition.id == ident).values(missing_at=now))
    for ident, work_id in db.execute(select(Edition.id, Edition.work_id).where(
            Edition.library_id == library_id, Edition.id.in_(live), Edition.missing_at.is_not(None))):
        touched.add(work_id)
        db.execute(update(Edition).where(Edition.id == ident).values(missing_at=None))
    db.commit()
    for work_id in sorted(touched):
        # One work that cannot take over its editions' data must not stop the others: it is undone and logged.
        try:
            work = db.get(Work, work_id)
            if work is None:
                continue
            if not db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id)):
                db.delete(work)
                db.flush()
                counts["works_removed"] += 1
            else:
                refresh_work(db, work)
                db.flush()
            db.commit()
        except OperationalError:
            raise
        except Exception:
            logger.exception("Settling a work failed work=%s", work_id)
            db.rollback()
            counts["works_failed"] = counts.get("works_failed", 0) + 1
    try:
        counts["people_merged"] = merge_collapsed_people(db)
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Merging people failed")
        db.rollback()
    try:
        counts["people_rekeyed"] = rekey_people(db) + rekey_series(db)
        counts["spelling"] = int(spelling.note_spelling(db))
        counts["claims"] = int(aliases.recompute(db))
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Putting people in order failed")
        db.rollback()
    try:
        counts["languages"] = derive_languages(db, library_id)
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Deriving languages failed library=%s", library_id)
        db.rollback()
    try:
        from . import duplicates

        counts["copies"] = duplicates.tidy(db, library_id)
        counts["duplicates"] = duplicates.tell(db, library_id)
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Looking for editions held twice failed library=%s", library_id)
        db.rollback()
    try:
        counts["separated"] = separate_mixed_works(db)
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Separating the books of mixed works failed library=%s", library_id)
        db.rollback()
    try:
        counts.update(join_twin_works(db, since))
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Joining the works of one book failed library=%s", library_id)
        db.rollback()
    try:
        counts["people_removed"] = drop_unused_people(db)
        db.commit()
    except OperationalError:
        raise
    except Exception:
        logger.exception("Removing people without books failed")
        db.rollback()
    counts["guesses"] = refresh_guesses(db, library_id)
    return counts


def drop_unused_people(db: Session) -> int:
    """People no book names any more go: a name an older rule made of a field (``Nele (als Mara Lind) Feldkamp``,
    ``Ben Hollister liest Mara Feldkamp``), an author whose last book went. Their other names, photo and text go with
    them; what a person decided stays, because it hangs on books and on names, never on a person without one (a split of
    two names is kept by their names, ``AliasClaim``). Returns how many went. The caller commits."""
    used = select(Credit.person_id).distinct()
    gone = list(db.scalars(select(Person.id).where(Person.id.not_in(used))))
    for start in range(0, len(gone), 500):
        db.execute(delete(Person).where(Person.id.in_(gone[start:start + 500])))
    if gone:
        db.flush()
        logger.info("People without books removed count=%s", len(gone))
    return len(gone)


# ---- A genre nobody said: the genre of the author's other books --------------------------------------------------

#: A genre is guessed for a book only from at least this many other books of the same author in the same library, of
#: which at least this share have it.
GUESS_MIN_BOOKS = 2
GUESS_MIN_SHARE = 0.6


def settle_guesses(db: Session, library_id: int) -> int:
    """Gives a book of a library that has no genre the genre most of the *other* books of its author have, marked as a
    guess (``Edition.genre_guess``). **Only books of this library count**: everybody who sees the book sees all the
    others, so the guess tells nobody anything about a book of a library they are not in. A book whose work has a genre
    from another edition (the recording of it) needs no guess. A guess that no longer holds (the books changed) is taken
    back. Returns how many editions changed; the caller commits."""
    rows = db.execute(select(Edition.id, Edition.work_id, Edition.kind, Edition.genre, Edition.genre_guess,
                             Edition.shelf_key).where(Edition.library_id == library_id, Edition.missing_at.is_(None))
                      ).all()
    if not rows:
        return 0
    own: dict[int, str] = {}
    for _ident, work_id, _kind, genre, guess, _shelf in sorted(rows, key=lambda r: (r[2] != EDITION_EBOOK, r[0])):
        if genre and not guess:
            own.setdefault(work_id, genre)
    editions_of_person: dict[int, set[int]] = {}
    people_of: dict[int, list[int]] = {}
    work_of = {ident: work_id for ident, work_id, *_rest in rows}
    in_library = select(Edition.id).where(Edition.library_id == library_id)
    credits = select(Credit.edition_id, Credit.person_id).where(Credit.role == AUTHOR,
                                                                Credit.edition_id.in_(in_library))
    for edition_id, person_id in db.execute(credits):
        if edition_id in work_of:
            people_of.setdefault(edition_id, []).append(person_id)
            editions_of_person.setdefault(person_id, set()).add(work_of[edition_id])
    changed: set[int] = set()
    for ident, work_id, _kind, genre, guess, shelf in rows:
        if genre and not guess:
            continue
        wanted = ""
        if work_id not in own:
            # A work counts once, also when two of its authors are authors of this book.
            others = {other for person_id in people_of.get(ident, []) for other in editions_of_person.get(person_id, ())
                      if other != work_id and other in own}
            votes = Counter(own[other] for other in others)
            total = sum(votes.values())
            if total >= GUESS_MIN_BOOKS:
                top, count = min(votes.items(), key=lambda item: (-item[1], genres.rank(item[0])))
                if count >= GUESS_MIN_BOOKS and count / total >= GUESS_MIN_SHARE:
                    wanted = top
        if wanted != (genre if guess else ""):
            db.execute(update(Edition).where(Edition.id == ident).values(
                genre=wanted, genre_guess=bool(wanted), shelf_key=f"{genres.rank(wanted):02d}" + shelf[2:]))
            changed.add(work_id)
    for work_id in sorted(changed):
        work = db.get(Work, work_id)
        if work is not None:
            db.expire(work)
            refresh_work(db, work)
    db.flush()
    return len(changed)


def refresh_guesses(db: Session, library_id: int) -> int:
    """``settle_guesses`` and ``derive_languages`` (a match taken back or replaced may leave a recording without the
    language it had), committed, and no fault of it stops what called it (a guess is a nicety)."""
    try:
        done = settle_guesses(db, library_id) + derive_languages(db, library_id)
        db.commit()
        return done
    except OperationalError:
        raise
    except Exception:
        logger.exception("Guessing genres failed library=%s", library_id)
        db.rollback()
        return 0


# ---- A language nobody said: the language of the e-book of the work, else of most of the library -------------------

#: A language is taken from the library only when it is the language of at least this many editions, and of more than
#: this share of the editions that have one.
LANGUAGE_MIN_BOOKS = 3
LANGUAGE_MIN_SHARE = 0.5


def derive_languages(db: Session, library_id: int) -> int:
    """A recording whose tags, file beside it and sources say no language (``language_unknown`` weighs on every one of
    them) gets the language of the e-book of its work, else the language most editions of the library have. It is
    kept as a *derived* value (``overlay.GUESS``): a file or a source or a person that says a language replaces it, and
    the interface marks it. A recording whose language was derived and that now has no ground for it loses it. Returns
    how many editions changed."""
    from .match import apply as match_apply
    from .match import overlay as match_overlay

    rows = db.execute(select(Edition.id, Edition.work_id, Edition.language, Edition.kind, Edition.overlay).where(
        Edition.library_id == library_id, Edition.missing_at.is_(None))).all()
    counts: Counter[str] = Counter()
    for _ident, _work, language, _kind, overlay in rows:
        if language and not match_overlay.is_guess(overlay, "language"):
            counts[language] += 1
    top = counts.most_common(1)
    library_language = ""
    if top and top[0][1] >= LANGUAGE_MIN_BOOKS and top[0][1] / sum(counts.values()) > LANGUAGE_MIN_SHARE:
        library_language = top[0][0]
    book_language: dict[int, str] = {}
    for _ident, work_id, language, kind, overlay in rows:
        if kind == EDITION_EBOOK and language and not match_overlay.is_guess(overlay, "language"):
            book_language.setdefault(work_id, language)
    changed = 0
    for ident, work_id, language, kind, overlay in rows:
        if kind != EDITION_AUDIO:
            continue
        guessed = match_overlay.is_guess(overlay, "language")
        if language and not guessed:
            continue
        wanted = book_language.get(work_id) or library_language
        if (wanted or "") == (language if guessed else ""):
            continue
        edition = db.get(Edition, ident)
        if edition is None:
            continue
        edition.overlay = match_overlay.with_guess(edition.overlay, "language", wanted) or None
        match_apply.rebuild(db, edition)
        changed += 1
        if changed % 50 == 0:
            # A write that is held open for a whole library keeps every other writer waiting.
            db.commit()
    return changed
