"""What kind of book a work is: a novel, a book of stories, a biography, non-fiction, poems, a book for children, a box
of books, or unsure.

The rules were measured on a fresh set of authors (84 % right, 4 % of the novels wrongly set aside). Lessons that
shape them:

* **The libraries first.** The German library's subject group (``B`` fiction, ``K`` children, digits non-fiction), the
  French library's readers and literary form, the Dutch trade code: when two of them say "fiction for adults", a
  single other hint does not overturn it.
* **A single record must not tip a work** (a comic of the novel, a box, a reader with many voices hang on the same
  work): a hint counts when most records say it.
* **Subjects are no readers**: "children" stands on novels for adults; only words that name readers count
  (``juvenile fiction``, ``young adult``), and those of the tags only when most records say so.
* **The number of pages alone proves nothing** (some novels have 120 pages): a short work is ``unsure``, not stories.

Nothing is thrown away: what is not sure stands under "Nicht sicher zugeordnet" and is never fetched by itself.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from ..readers.names import distinct_title_key
from ..sources.model import Entry

KINDS = ("roman", "erz", "bio", "sach", "lyrik", "kinder", "box", "unsure")
#: The kinds a person may move a work to (everything but "unsure").
MOVABLE = ("roman", "erz", "bio", "sach", "lyrik", "kinder", "box")

#: Words that name readers (not subjects).
KIDS_WORDS = ("juvenile fiction", "juvenile literature", "children's fiction", "children's stories", "young adult",
              "middle grade", "picture book", "kinderbuch", "jugendbuch", "jeunesse", "kinder- und jugend")
SHORT_WORDS = ("short stories", "short-stories", "anthologies", "anthology", "erzählungen", "nouvelles")
COMIC_WORDS = ("comic", "graphic novel", "bande dessin", "bandes dessin", "manga")
POETRY_WORDS = ("poetry", "poems", "poésie", "gedichte", "lyrik", "verse")
OTHER_FORMS = ("drama", "plays", "letters", "correspondence", "essays", "literary criticism", "théâtre", "briefe")
BIO_WORDS = ("biography", "biographies", "autobiography", "memoir", "memoirs", "biografie", "biographie",
             "autobiografie", "lebenserinnerungen", "erinnerungen")

#: A title that says it is a box, a collection of books or of stories, a sample or a work of another form.
_BOX = re.compile(
    r"\b(box(ed)?\s*set|coffret|int[ée]grale|bundle|sammelband|omnibus|schuber|gesamtausgabe|sammelbox|"
    r"\d+\s*(books|novels|bücher|buecher|romane|romans)|x\s*\d+|complete edition|collected novels|"
    r"(band|bände|baende|books?|tome|tomes|vol\.?|volumes?)\s*\d+\s*(-|bis|to|à|a)\s*\d+|2in1|2-in-1|3-in-1|"
    r"works|complete|oeuvres|œuvres|werke|gesammelte|sämtliche|saemtliche|selected|trilog(?:y|ie|ia)|"
    r"tetralog(?:y|ie))\b", re.IGNORECASE)
#: A word in a title that says a set, beside a source that says so too (alone it is often a novel's subtitle).
_SET_HINT = re.compile(r"\b(collection|sammlung|edition complete)\b", re.IGNORECASE)
#: The box of a series (``Reihe: Gesamtausgabe``) under its own name.
BOX_TITLE = _BOX
_STORIES = re.compile(
    r"\b(stories|short story|nouvelles|erz[äa]hlungen?|kurzgeschichten?|geschichten|novellen|anthology|"
    r"anthologie|best .* of the year)\b", re.IGNORECASE)
_SAMPLE = re.compile(r"\b(leseprobe|preview|extrait|sample|excerpt)\b", re.IGNORECASE)
_LETTERS = re.compile(
    r"\b(letters|lettres|briefe|correspondance|carteggio|journal|tageb[uü]cher|quotes|zitate|wit and wisdom|"
    r"poems|poèmes|gedichte|po[eé]sies?|th[ée][aâ]tre|teatro|essais|essays|writings|juvenilia)\b", re.IGNORECASE)
_POEM_TITLE = re.compile(r"\b(poems|poèmes|gedichte|po[eé]sies?|verse)\b", re.IGNORECASE)


@dataclass
class Signals:
    pages: float | None = None
    runtime: float | None = None
    runtime_long: bool = False
    kids: int = 0
    adult: int = 0
    fic: int = 0
    nonfic: int = 0
    short_cat: int = 0
    book_cat: int = 0
    coll: float = 0.0
    comic: float = 0.0
    multi: float = 0.0
    forms: float = 0.0
    poetry: float = 0.0
    bio: float = 0.0
    compilation: float = 0.0
    words: list[str] = field(default_factory=list)


def _text(entry: Entry, name: str) -> str:
    value = entry.signals.get(name)
    if isinstance(value, list):
        return " ".join(str(item).lower() for item in value if isinstance(item, str))
    return str(value or "").lower()


def subject_group_says(groups: list[str]) -> str:
    """What the German library's subject groups of a record say: ``kids`` (``K``), ``fiction`` (``B``), ``nonfiction``
    (a Dewey number of a field of knowledge: ``150``, ``943``, ``323.49``), or nothing. The groups of language
    (``4xx``: a school edition of an English novel is filed under ``420``), of literature (``8xx``) and of comics
    (``741.5``: the comic of a novel), and the numbers of other schemes the records carry beside (``08A``, ``59``,
    ``23``, ``2303``, ``12.1A``) say nothing of the work."""
    if "K" in groups:
        return "kids"
    if "B" in groups:
        return "fiction"
    for group in groups:
        found = _DEWEY.fullmatch(str(group))
        if found and not found.group(1).startswith(("4", "8")) and not str(group).startswith("741.5"):
            return "nonfiction"
    return ""


#: A Dewey number (the subject groups of the German library are Dewey's): three digits, a point and more digits.
_DEWEY = re.compile(r"([0-9]{3})(?:\.[0-9]{1,8})?")
#: The sources whose records are one edition: an ISBN two of their records share is counted once.
_EDITION_SOURCES = ("dnb", "bnf", "kb", "libris", "nb")
#: The share of the records that must say a work is a box of books (one record of many does not tip it).
BOX_SHARE = 0.5
#: The unabridged recordings of novels run four hours and more; under this it is a short story or a novella.
SHORT_MINUTES = 120


def signals_of(entries: list[Entry]) -> Signals:
    """What the records of a work say, counted. The records of one ISBN count once (a school reader printed twenty
    times is one voice, not twenty)."""
    pages: list[int] = []
    runtime: list[int] = []
    out = Signals()
    weak_kids = tagged = counted = coll = comic = multi = forms = poetry = bio = boxed = 0
    numbers_seen: set[str] = set()
    for entry in entries:
        source = entry.source
        if source in _EDITION_SOURCES and entry.isbns:
            if numbers_seen & set(entry.isbns):
                continue
            numbers_seen.update(entry.isbns)
        if entry.pages:
            pages.append(entry.pages)
        if entry.compilation:
            boxed += 1
        tags = " ".join(_text(entry, name) for name in ("genres", "subjects", "keywords"))
        if source in ("openlibrary", "hardcover", "audible"):
            tagged += 1
            weak_kids += any(word in tags for word in KIDS_WORDS)
            coll += any(word in tags for word in SHORT_WORDS)
            comic += any(word in tags for word in COMIC_WORDS)
            forms += any(word in tags for word in OTHER_FORMS)
            poetry += any(word in tags for word in POETRY_WORDS)
            bio += any(word in tags for word in BIO_WORDS)
        if source in ("openlibrary", "audible"):
            counted += 1
            if entry.authors_count >= 4:
                multi += 1
        if source == "audible" and entry.runtime_min and entry.variant != "abridged":
            runtime.append(entry.runtime_min)
            if entry.runtime_min >= 300:
                out.runtime_long = True
        if source == "hardcover":
            lit, cat = entry.signals.get("lit"), entry.signals.get("cat")
            if lit == 2:
                out.nonfic += 1
            elif lit == 1:
                out.fic += 1
            if cat in (2, 3):
                out.short_cat += 1
            elif cat == 1:
                out.book_cat += 1
        if source == "dnb":
            groups = entry.signals.get("group") or []
            subjects = _text(entry, "subjects")
            said = subject_group_says([str(group) for group in groups])
            if said == "kids":
                out.kids += 1
            elif said == "fiction":
                out.adult += 1
                out.fic += 1
            elif said == "nonfiction":
                out.nonfic += 1
            bio += any(word in subjects for word in BIO_WORDS)
            poetry += any(word in subjects for word in POETRY_WORDS)
            if any(word in subjects for word in POETRY_WORDS + BIO_WORDS):
                tagged += 1
        if source == "bnf":
            audience = str(entry.signals.get("audience") or "").strip()
            litcode = str(entry.signals.get("litcode") or "")
            form = str(entry.signals.get("form") or "")
            if audience[:1] in ("a", "b", "c", "d", "e"):
                out.kids += 1
            elif audience[:1] in ("k", "m"):
                out.adult += 1
            if litcode == "y":
                out.nonfic += 1
            elif litcode in ("a", "f"):
                out.fic += 1
            if litcode == "f" or "nouvelle" in form:
                out.short_cat += 1
            if litcode in ("b", "c", "e", "g", "h", "i"):
                forms += 1
                tagged += 1
                if litcode in ("c", "e", "h"):
                    poetry += 1
        if source == "kb":
            for code in entry.signals.get("nur") or []:
                number = int(code) if str(code).isdigit() else 0
                if 200 <= number < 300:
                    out.kids += 1
                elif 300 <= number < 400:
                    out.adult += 1
                    out.fic += 1
                elif number >= 400:
                    out.nonfic += 1
                break
    out.pages = statistics.median(pages) if pages else None
    out.runtime = statistics.median(runtime) if runtime else None
    out.kids += 1 if tagged and weak_kids / tagged > 0.5 else 0
    out.coll = coll / tagged if tagged else 0.0
    out.comic = comic / tagged if tagged else 0.0
    out.forms = forms / tagged if tagged else 0.0
    out.poetry = poetry / tagged if tagged else 0.0
    out.bio = bio / tagged if tagged else 0.0
    out.multi = multi / counted if counted else 0.0
    out.compilation = boxed / len(entries) if entries else 0.0
    return out


def _most(entries: list[Entry], pattern: re.Pattern[str]) -> bool:
    """Whether the longest title and at least half of the titles fit the pattern."""
    titles = [entry.title for entry in entries if entry.title]
    if not titles:
        return False
    longest = max(titles, key=len)
    return bool(pattern.search(longest)) and sum(bool(pattern.search(t)) for t in titles) * 2 >= len(titles)


def _named_works(own_key: str, other_keys: list[str]) -> list[str]:
    """The other works of the author a title names: titles that say something by themselves (``Hercule Poirot``, not
    ``Poirot``, not ``Paris``), and of two that hold one another only the longer."""
    named = {key for key in other_keys if key and key != own_key and len(key) > 3 and distinct_title_key(key)
             and f" {key} " in f" {own_key} "}
    return [key for key in named if not any(key != other and key in other for other in named)]


def kind_of(entries: list[Entry], other_keys: list[str] | None = None, own_key: str = "",
            series_keys: set[str] | None = None) -> tuple[str, str]:
    """``(kind, why)``: the kind of the work, and for ``unsure`` the reason (``comic``, ``anthology``, ``forms``,
    ``short``, ``sample``). ``series_keys``: the names of the author's series (``series_names.key``)."""
    s = signals_of(entries)
    library_says_novel = s.adult >= 2 and s.fic >= 2
    if _most(entries, _SAMPLE):
        return "unsure", "sample"
    # (A box needs half of the records, as everything else: one record that is a set does not make a novel one. Or two
    # signs of it: a source says it is a set, and a title of it says so.)
    if s.compilation >= BOX_SHARE or _most(entries, _BOX) or (
            s.compilation > 0 and any(_BOX.search(entry.title) or _SET_HINT.search(entry.title) for entry in entries)):
        return "box", ""
    if own_key and len(_named_works(own_key, other_keys or [])) >= 2:
        # A title that names two other works of the author: a book of both.
        return "box", ""
    if _most(entries, _STORIES):
        return "erz", ""
    if _most(entries, _POEM_TITLE):
        return "lyrik", ""
    if _most(entries, _LETTERS):
        return "unsure", "forms"
    if s.multi >= 0.5 and not library_says_novel:
        return "unsure", "anthology"
    if s.comic >= 0.5 and not library_says_novel:
        return "unsure", "comic"
    if s.coll > 0.5 and not library_says_novel:
        return "erz", ""
    if s.poetry > 0.5 and not library_says_novel:
        return "lyrik", ""
    if s.forms > 0.5 and not library_says_novel:
        return "unsure", "forms"
    if s.kids > s.adult and s.kids >= 1:
        return "kinder", ""
    if s.nonfic > s.fic:
        return ("bio", "") if s.bio > 0 else ("sach", "")
    if s.short_cat > s.book_cat:
        return "erz", ""
    if s.runtime is not None and s.runtime < SHORT_MINUTES and (s.pages is None or s.pages < 130):
        # An unabridged recording of under two hours is no novel, whatever the library files it under (fiction is not
        # a novel: a short story is fiction too).
        return "unsure", "short"
    if s.pages is not None and s.pages < 130 and not s.runtime_long and not library_says_novel:
        return "unsure", "short"
    if s.pages is None and s.runtime is not None and s.runtime < 200:
        return "unsure", "short"
    return "roman", ""


__all__ = ["KINDS", "MOVABLE", "Signals", "kind_of", "signals_of"]
