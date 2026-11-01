"""Names: of people, of titles, of series, and what a file or folder name can tell when nothing else does.

Lessons of the measuring run on 167 e-books: the author is never matched by the last name alone ("Marlowe"
is not every Marlowe), a number beside the series name is the volume and decides ("Band 2" is not "Band 3"), and the
noise around a title ("(ungekürzt)", "Hörbuch", a genre after a dash) must not decide whether two titles are one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .. import book_words
from .text import clean, fold, key_fold

#: Words that belong to the last name when they stand before it ("Le Moor", "van Dorn", "von Aldenau").
_PARTICLES = frozenset({
    "von", "van", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "les", "ten", "ter", "zu", "zur",
    "dos", "das", "af", "el", "al", "bin", "ibn", "mac", "st", "saint",
})
#: What is not a person, though it stands in the author field of a badly kept collection.
_NOT_PEOPLE = frozenset({
    "unknown", "unbekannt", "unknown author", "unknown authors", "unbekannter autor", "autor unbekannt", "various",
    "various authors", "various artists", "diverse", "diverse autoren", "diverse autorinnen", "verschiedene",
    "verschiedene autoren", "div autoren", "n a", "na", "n n", "nn", "anonymous", "anonym", "anonyme", "no author",
    "kein autor", "ohne autor", "collective", "sammelband",
    # What a list puts for the rest of its people ("Mara Feldkamp; div.", "u. a.", "et al."): nobody.
    "div", "u a", "u a m", "et al", "and others", "und andere", "others", "andere",
})
#: A name that is an organisation, not a person: it has a legal form or a house in it. It is not turned around
#: ("Orchard Press, Inc." is not "Inc. Orchard") and not split at its "&" ("Kurt Mai & Co.").
_COMPANY = re.compile(
    r"(?<!\w)(?:inc|ltd|llc|plc|gmbh|kg|ohg|ag|corp|verlag|verlage|publishing|publishers|media|studios|records|s\.a|"
    r"b\.v|e\.v|co\.|&\s*co|und\s+s[oö]hne|and\s+sons|&\s+s[oö]hne|&\s+sons|audio|audiobooks|h[oö]rbuch|"
    r"h[oö]rb[uü]cher|hoerbuch|hoerbuecher|h[oö]rverlag|hoerverlag|verlagsgruppe|books|editions?|productions?)(?!\w)|"
    r"\w(?:verlag|verlage|h[oö]rverlag|hoerverlag)(?!\w)", re.IGNORECASE)


def is_company(name: str) -> bool:
    """Whether a name is an organisation (``Orchard Press, Inc.``, ``Müller & Söhne GmbH``, ``Kurt Mai & Co.``), a
    publisher or a house of audiobooks (``Hörbuch Beispielverlag``, ``Example House Audio``)."""
    return bool(_COMPANY.search(clean(name, 200)))


#: Programs that write their name where the author belongs.
_TOOLS = frozenset({"calibre", "adobe", "acrobat", "abbyy", "finereader", "indesign", "adobe indesign", "sigil",
                    "kindlegen", "pandoc", "openoffice", "libreoffice", "microsoft word", "word", "pdf", "scan",
                    "scanner", "ocr", "epub", "ebook", "mp3", "lame", "itunes", "audible", "librivox"})
#: One word and a number (``VORL 1``, ``cd2``): the label of a part, no person.
_LABEL = re.compile(r"^[^\W\d_]+[\s_-]*\d{1,3}$")
_RELEASE_NAME = re.compile(r"\b(?:ripped|encoded|scanned|uploaded|upped|converted|tagged|created|made)\s+(?:by|with)\b|"
                           r"https?://|\bwww\.|\.(?:com|net|org|de|to|info|ws|cc)\b", re.IGNORECASE)


def is_label(name: str) -> bool:
    """Whether a tag holds one word and a number (``VORL 1``, ``cd2``): the label of a part, where a name is known to be
    wished. A catalogue may well list a person so."""
    return bool(_LABEL.match(clean(name, 200).strip()))


def not_a_person(name: str) -> bool:
    """Whether what stands where an author belongs is nobody: the name of an uploader (letters and digits in one word,
    ``Sh4d0w``), a note of who copied the file (``Ripped by …``), an address, a program (``calibre``), or the name of
    a series with signs no name has (``Die drei ???``)."""
    text = clean(name, 200).strip()
    if not text:
        return True
    if _RELEASE_NAME.search(text) or fold(text) in _TOOLS:
        return True
    if re.search(r"[?!#@*=<>{}|\\_]", text):
        return True
    words = text.split()
    return len(words) == 1 and bool(re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{4,}", text))


#: Titles before a name that are no part of it: ``Sir Arthur Conan Doyle`` is ``Arthur Conan Doyle``.
_HONORIFICS = frozenset({"sir", "dame", "dr", "prof", "professor", "mr", "mrs", "ms", "lord", "lady"})
#: The word a pair or a group of siblings is named by in front of their last name (``Brüder Grimm``, ``Brothers
#: Grimm``): it fits every first name of that last name.
GROUP_WORDS = frozenset({"bruder", "brueder", "gebruder", "gebrueder", "brothers", "bros", "geschwister", "sisters",
                         "schwestern", "freres", "fratelli", "hermanos"})
#: First names written differently in other languages and alphabets: one person (``Lew``, ``Leo`` and ``Lev``
#: Tolstoi). Folded; the first of a group is the one the others are taken as.
_FIRST_NAME_GROUPS = (
    ("lev", "leo", "lew"), ("fjodor", "fyodor", "fedor", "fiodor", "feodor"), ("michail", "mikhail", "mihail"),
    ("nikolai", "nikolaj", "nikolay", "nicolai"), ("iwan", "ivan"), ("alexander", "aleksandr", "alexandr", "alexandre"),
    ("pjotr", "pyotr", "piotr"), ("maxim", "maksim"), ("wassili", "vasily", "vassily", "wassily", "vasilij"),
    ("jewgeni", "yevgeny", "evgeny", "evgenij"), ("juri", "yuri", "jurij", "iouri"), ("sergej", "sergei", "sergey"),
    ("dmitri", "dmitry", "dmitrij"), ("wladimir", "vladimir"), ("alexej", "alexei", "aleksei", "alexey"),
    ("jakob", "jacob", "jacques", "giacomo"), ("wilhelm", "william", "guillaume"), ("friedrich", "frederick"),
)
_FIRST_NAME_OF = {fold(name): group[0] for group in _FIRST_NAME_GROUPS for name in group}


def without_honorific(name: str) -> str:
    """``Sir Arthur Conan Doyle`` is ``Arthur Conan Doyle``; a name of two words after the title keeps it."""
    words = clean(name, 200).split()
    while len(words) > 2 and fold(words[0]).strip(".") in _HONORIFICS:
        words = words[1:]
    return " ".join(words)


def spelling_key(word: str) -> str:
    """One word in the spelling that old and foreign spellings share: ``Process`` and ``Prozess``, ``Tolstoy`` and
    ``Tolstoi``, ``Dostojewski`` and ``Dostojevski``, ``Philosophie`` and ``Filosofie`` (``c`` before e/i/y as ``z``,
    else as ``k``; ``ph`` as ``f``, ``th`` as ``t``, ``y`` as ``i``, ``w`` as ``v``). For words of four letters or more;
    a shorter one is itself. Only for comparing."""
    text = key_fold(word).replace(" ", "")
    if len(text) < 4:
        return text
    text = text.replace("ph", "f").replace("th", "t").replace("ck", "k")
    text = re.sub(r"c(?=[eiy])", "z", text).replace("c", "k")
    return text.replace("y", "i").replace("w", "v")


def first_name_key(word: str) -> str:
    """The first name as all its spellings are taken (``Leo`` and ``Lew`` are ``lev``), else its spelling key."""
    folded = fold(word).replace(" ", "")
    return _FIRST_NAME_OF.get(folded) or spelling_key(word)


def last_first(name: str) -> tuple[str, str]:
    """The last name and the first name(s) of a name as people write it, in the folded form: ``Falkner, Rune`` and
    ``Rune Falkner`` both give ``("falkner", "rune")``; ``Ulla K. Le Moor`` gives ``("le moor", "ulla k")``;
    ``Schirach, Ferdinand von`` and ``Ferdinand von Schirach`` give ``("von schirach", "ferdinand")``; a title before
    the name (``Sir``) is not part of it."""
    text = without_honorific(clean(name, 200))
    if is_company(text):
        # An organisation has no first name: its whole name is the last one.
        return key_fold(text), ""
    if "," in text:
        last, _, first = text.partition(",")
        # ``Schirach, Ferdinand von``: the particle after the first name belongs to the last name.
        firsts = first.split()
        moved: list[str] = []
        while len(firsts) > 1 and fold(firsts[-1]) in _PARTICLES:
            moved.insert(0, firsts.pop())
        return key_fold(" ".join([*moved, last.strip()])), key_fold(" ".join(firsts))
    words = text.split()
    if len(words) <= 1:
        return key_fold(text), ""
    cut = _last_name_at(words)
    return key_fold(" ".join(words[cut:])), key_fold(" ".join(words[:cut]))


#: Words that stand after a last name and belong to it (``Ben Hollister Jr.``, ``Rune Falkner III``): never a person of
#: their own, and never the last name alone.
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def _last_name_at(words: list[str]) -> int:
    """Where the last name begins in a name written first name first: the last word, with the particles before it
    (``van Dorn``) and with a word after it that only belongs to it (``Hollister Jr.``)."""
    cut = len(words) - 1
    if cut > 1 and fold(words[cut]) in _NAME_SUFFIXES:
        cut -= 1
    while cut > 1 and fold(words[cut - 1]) in _PARTICLES:
        cut -= 1
    return cut


def display_name(name: str) -> str:
    """The name the way it reads: first name first (``Falkner, Rune`` is ``Rune Falkner``)."""
    text = clean(name, 200)
    if "," in text and not is_company(text):
        last, _, first = text.partition(",")
        if first.strip() and last.strip() and "," not in first:
            return f"{first.strip()} {last.strip()}"
    return text


def sort_name(name: str) -> str:
    """``Last, First`` for the order by author."""
    text = clean(name, 200)
    if is_company(text):
        return text
    if "," in text:
        last, _, first = text.partition(",")
        return f"{last.strip()}, {first.strip()}".strip(", ")
    words = text.split()
    if len(words) <= 1:
        return text
    cut = _last_name_at(words)
    return f"{' '.join(words[cut:])}, {' '.join(words[:cut])}"


def person_key(name: str) -> str:
    """The key of a person: last and first name folded, the same for ``Falkner, Rune`` and ``Rune Falkner``."""
    last, first = last_first(name)
    return f"{first} {last}".strip()


def _initials(first: str) -> list[str]:
    return [word[0] for word in first.split()]


def _last_key(last: str) -> str:
    return " ".join(spelling_key(word) for word in last.split())


def _without_marks(name: str) -> str:
    """The name with the marks over its letters left out: ``Rüne Fälkner`` as a file without them writes it."""
    return "".join(mark for mark in unicodedata.normalize("NFKD", name) if not unicodedata.combining(mark))


def authors_compatible(a: str, b: str) -> bool:
    """Whether two names can be one author: the last names are equal and the first names agree, or one of them is only
    initials that fit the other. Never the last name alone. ``Rune Falkner`` and ``R. Falkner`` agree, ``Rune Falkner``
    and ``Ralf Falkner`` do not. Spellings of other languages and alphabets are one name (``Lew Tolstoi`` and ``Leo
    Tolstoy``), a title before the name is none of it (``Sir Arthur Conan Doyle``), the group word of siblings fits each
    of them (``Brüder Grimm`` and ``Jacob Grimm``), and two names of one word are one person when they are the same and
    no short word (``Homer``, ``Voltaire``). A name written without the marks over its letters is the same name
    (``Erik Fälkner`` and ``Erik Falkner``), while two names that differ in the letter under a mark are not."""
    return _compatible(a, b) or _compatible(_without_marks(a), _without_marks(b))


def _compatible(a: str, b: str) -> bool:
    last_a, first_a = last_first(a)
    last_b, first_b = last_first(b)
    if not last_a or (last_a != last_b and _last_key(last_a) != _last_key(last_b)):
        return False
    if not first_a and not first_b:
        # One word each, the same: a writer known by one name. Not a short word, which many people share.
        return len(last_a.replace(" ", "")) >= 5 and not is_company(a)
    if not first_a or not first_b:
        # A name with no first name at all ("Falkner") fits nobody: the lesson of "Silas Marlowe / Hedda Thorne".
        return False
    if first_a == first_b:
        return True
    if first_a in GROUP_WORDS or first_b in GROUP_WORDS:
        return True
    if [first_name_key(word) for word in first_a.split()] == [first_name_key(word) for word in first_b.split()]:
        return True
    words_a, words_b = first_a.split(), first_b.split()
    for short, long_ in ((words_a, words_b), (words_b, words_a)):
        if all(len(word) == 1 for word in short) and len(short) <= len(long_) and [
            word[0] for word in long_[: len(short)]] == short:
            return True
        if short and len(short) < len(long_) and long_[: len(short)] == short and all(
                len(word) == 1 for word in long_[len(short):]):
            # (A middle initial one source writes and another leaves out: ``Rune K. Falkner`` and ``Rune Falkner``,
            # ``R. K. K. Falkner`` and ``R. K. Falkner``.)
            return True
    return False


def last_name_buckets(name: str) -> set[str]:
    """What two names of one author share in their last name, for putting the names of a library in buckets before
    they are compared two by two: the last name in the spelling ``authors_compatible`` takes as one (also without the
    marks over its letters). Names that can be one author share a bucket. Empty for a name of no last name."""
    found: set[str] = set()
    for text in dict.fromkeys((name, _without_marks(name))):
        last = last_first(text)[0]
        if last:
            found.add(_last_key(last))
    words = without_honorific(name).split()
    if len(words) == 2 and "," not in name:
        # The two words of a name that may be written the other way round meet in one bucket too.
        found.add("~" + " ".join(sorted(key_fold(word) for word in words)))
    return found


def _swapped(a: str, b: str) -> bool:
    """Whether two names are the same two words the other way round (``Rune Falkner`` and ``Falkner Rune``). Only names
    of two words with no comma, none of them an initial: a name with a comma says which word is the last name, and an
    initial cannot be turned round."""
    words_a, words_b = without_honorific(a).split(), without_honorific(b).split()
    if len(words_a) != 2 or len(words_b) != 2:
        return False
    folded_a, folded_b = [key_fold(word) for word in words_a], [key_fold(word) for word in words_b]
    if any(len(word.replace(" ", "")) < 2 for word in folded_a + folded_b) or "," in a or "," in b:
        return False
    return folded_a[0] != folded_a[1] and folded_a[0] == folded_b[1] and folded_a[1] == folded_b[0]


def _relation(a: str, b: str) -> str:
    last_a, first_a = last_first(a)
    last_b, first_b = last_first(b)
    if not last_a or not first_a or not first_b or (last_a != last_b and _last_key(last_a) != _last_key(last_b)):
        return ""
    # (A group word such as ``Brüder Feldkamp`` fits each of the brothers for ``authors_compatible``; none of the rules
    # below says they are one, and so it is no relation here.)
    if first_a == first_b or [first_name_key(word) for word in first_a.split()] == [
            first_name_key(word) for word in first_b.split()]:
        return "peer"
    words_a, words_b = first_a.split(), first_b.split()
    for short, long_, relation in ((words_a, words_b, "a_shorter"), (words_b, words_a, "b_shorter")):
        if all(len(word) == 1 for word in short) and len(short) <= len(long_) and [
                word[0] for word in long_[: len(short)]] == short:
            return relation
        if short and len(short) < len(long_) and long_[: len(short)] == short and all(
                len(word) == 1 for word in long_[len(short):]):
            return relation
    return ""


def name_relation(a: str, b: str) -> str:
    """How two names of authors stand to each other, for putting people together by the spelling of their names alone.

    * ``swap``: the same two words the other way round (``Rune Falkner`` and ``Falkner Rune``).
    * ``peer``: the same name written differently (``Alexander Brandt`` and ``Alexandre Brandt``, ``Lew Falkner`` and
      ``Leo Falkner``): the first names are spellings of one name (``first_name_key``).
    * ``a_shorter`` / ``b_shorter``: one name gives less of the first name than the other and fits it (``A.K. Feldkamp``
      and ``Ada K. Feldkamp``, ``Rune Falkner`` and ``Rune K. Falkner``). Whether it fits one person or several is for
      the caller to see.
    * ``""``: none of these. Never a last name alone, and never the group word of siblings.

    A name that is no first-and-last name (one word, a company, a number in it) has no relation to anyone."""
    for name in (a, b):
        if is_company(name) or not_a_person(name) or is_label(name) or any(ch.isdigit() for ch in name):
            return ""
    if _swapped(a, b):
        return "swap"
    for text_a, text_b in dict.fromkeys(((a, b), (_without_marks(a), _without_marks(b)))):
        relation = _relation(text_a, text_b)
        if relation:
            return relation
    return ""


#: What a tag adds to a name that is no part of it: the number of a part or a volume, the language of the book.
_TAG_TAIL = re.compile(r"(?:[\s,;:._-]+(?:\d{1,3}|deutsch|german|english|englisch|francais|french))+$", re.IGNORECASE)
#: How alike two first names must be to be worth a question (``first_name_near``).
_FIRST_NAME_ALIKE = 0.85


def without_tag_tail(name: str) -> str:
    """A name in a tag without what the tag adds to it: ``Mara Falkner 2``, ``Mara Falkner Deutsch``. A name that is
    only such a word stays; ``Lena French`` stays too when it is the whole name: the caller holds both."""
    cut = _TAG_TAIL.sub("", name.strip())
    return cut or name.strip()


def _name_words(name: str) -> set[str]:
    """The words of a name without its particles, as ``first_name_key`` writes them."""
    return {first_name_key(word) for word in key_fold(name.replace(",", " ")).split() if word not in _PARTICLES}


def same_person_as_written(name: str, other: str) -> bool:
    """Whether a name in the tags of a file is that person, however careless the tag: the order of the words (``Falkner
    Rune``, ``Rune, Falkner``), a particle left out (``Odile Saint-Marceau``), a word added behind it (``Mara Falkner
    2``). A first name counts in the spellings that are known to be one name (``spelling_key``, ``Stefan`` and
    ``Stephan``; ``first_name_key``, ``Alexander`` and ``Alexandre``), never by how alike two names look: ``Julia`` and
    ``Julian`` are two people (``first_name_near``). Never a last name alone: all the words of two people must meet. For
    a check against a name that is known, not to tell two people apart in a catalogue (``authors_compatible``)."""
    for mine in dict.fromkeys((name, without_tag_tail(name))):
        words, others = _name_words(mine), _name_words(other)
        if len(words) >= 2 and len(others) >= 2 and words == others:
            return True
    return False


def first_name_near(name: str, other: str) -> bool:
    """Whether two names of one last name differ by a first name that looks nearly the same (``Julia`` and ``Julian``,
    ``Anna`` and ``Hanna``): another person as a rule, a slip of the keys now and then. Nothing tells the two apart, so
    this is a reason to ask a person, and no proof of anything."""
    for mine in dict.fromkeys((name, without_tag_tail(name))):
        last, first = last_first(mine), last_first(other)
        if not last[1] or not first[1] or last[1] == first[1] or _last_key(last[0]) != _last_key(first[0]):
            continue
        if SequenceMatcher(None, last[1], first[1]).ratio() >= _FIRST_NAME_ALIKE:
            return True
    return False


def part_of_a_name(name: str, other: str) -> bool:
    """Whether a tag holds one word of a name and no more (``Dale`` for ``Dale Falkner``): it names nobody else, and
    nobody by itself either."""
    words = _name_words(without_tag_tail(name))
    return len(words) == 1 and words <= _name_words(other)


#: A first name written twice by a careless source ("Jussi Jussi Adler-Olsen"). Only a word of at least three letters
#: counts as a repetition: an initial is repeated by right ("J. R. R. Falkner", "Mara R. R. Lindqvist").
def dedupe_words(name: str) -> str:
    """The name without a word that is repeated right after itself, unless it is the last word (the last name may
    really be a double word): ``Jussi Jussi Adler-Olsen`` is ``Jussi Adler-Olsen``, ``Bora Bora`` stays, and so do
    initials that come twice (``J. R. R. Falkner``)."""
    words = name.split()
    if len(words) < 3:
        return name
    kept: list[str] = []
    for index, word in enumerate(words):
        if kept and fold(kept[-1]) == fold(word) and index < len(words) - 1 and len(fold(word)) >= 3:
            continue
        kept.append(word)
    return " ".join(kept)


def collapse_repeats(name: str) -> str:
    """The name the way versions before this rule wrote it: every repeated word, also an initial, once. Used to find
    people who were made twice by that mistake (``J. R. Falkner`` for ``J. R. R. Falkner``)."""
    words = name.split()
    kept: list[str] = []
    for index, word in enumerate(words):
        if kept and fold(kept[-1]) == fold(word) and index < len(words) - 1 and fold(word):
            continue
        kept.append(word)
    return " ".join(kept)


_HARD_SEPARATORS = re.compile(r"\s*[;|]\s*|\s+/\s+")
_SOFT_SEPARATORS = re.compile(r"\s+&\s+|\s+(?:and|und|et)\s+")

#: What a name may carry for the part its person played: "Hans Müller (Hrsg.)", "Müller, Hans, Hg.", "Ed. Jane Doe".
#: Spelled out words count anywhere at the end; an abbreviation only with its full stop, so that a name that is an
#: abbreviation ("Ed Brandt") stays a name.
_ROLE_WORDS = {
    "editor": r"hrsg|hg|herausgeber(?:in)?|eds?|editors?|edited by|herausgegeben von",
    "translator": r"[uü]bersetzer(?:in)?|uebersetzer(?:in)?|[uü]bers|translators?|transl|trans|translated by|"
                  r"[uü]bersetzt von",
    "narrator": r"sprecher(?:in)?|narrators?|read by|gelesen von",
    "illustrator": r"illustrator(?:in)?|ill|illustriert von|illustrated by",
    # The mark of the author ("Mara Feldkamp [Autorin]", "Mara Feldkamp - Author"): the part every name of the field
    # plays anyway.
    "author": r"autor(?:in)?|authors?|verfasser(?:in)?",
}
_ROLE_ALL = "|".join(f"(?P<{role}>{words})" for role, words in _ROLE_WORDS.items())
_ROLE_BRACKET = re.compile(r"\s*[\(\[]\s*(?:" + _ROLE_ALL + r")\.?\s*[\)\]]", re.IGNORECASE)
_ROLE_TAIL = re.compile(r"(?:\s*,|\s+[-–—]|\s)\s*(?:" + _ROLE_ALL.replace("(?P<", "(?P<t_") + r")\.?\s*$",
                        re.IGNORECASE)
_ROLE_HEAD = re.compile(r"^(?:" + _ROLE_ALL.replace("(?P<", "(?P<h_") + r")\.?\s*:?\s+(?=\S)", re.IGNORECASE)


def _role_hit(match: re.Match[str], prefix: str = "") -> str:
    for role in _ROLE_WORDS:
        text = match.group(prefix + role)
        if text is not None:
            return "" if role == "author" else role
    return ""


def strip_role(piece: str) -> tuple[str, str]:
    """``(name, role)``: a name without the mark of the part its person played (``editor``, ``translator``, ``narrator``
    or ``illustrator``; empty for none). ``Müller, Hans (Hrsg.)`` is ``("Müller, Hans", "editor")``. A bare abbreviation
    counts only with its full stop (``Ed.``), never ``Ed Brandt``."""
    text = piece.strip()
    found = _ROLE_BRACKET.search(text)
    if found:
        return (text[: found.start()] + text[found.end():]).strip(" ,"), _role_hit(found)
    found = _ROLE_TAIL.search(text)
    if found:
        # A spelled-out word is a mark after a comma or a dash ("Hans Müller, Herausgeber", "Dietmar Schmidt -
        # Übersetzer", the way Audible lists a translator among the authors); after a space only an abbreviation with
        # its full stop is ("Hans Müller Hrsg."): "Real Narrator" is a name.
        matched = found.group(0).lstrip()
        if matched[:1] in (",", "-", "–", "—") or matched.lstrip(", ").rstrip().endswith("."):
            return text[: found.start()].strip(" ,-–—"), _role_hit(found, "t_")
    found = _ROLE_HEAD.search(text)
    if found:
        # At the front: with a full stop or a colon ("Hrsg. Hans Müller", "Hg.: Hans Müller") or as a phrase ("edited
        # by").
        matched = found.group(0).strip()
        if "." in matched or ":" in matched or " " in matched:
            return text[found.end():].strip(), _role_hit(found, "h_")
    return text, ""


def _is_bare_last_name(token: str) -> bool:
    """A token that can only be a last name written before a comma: one word, or particles and one word (``van Dorn``)
    ... never a first name and last name together (``Hans Müller``)."""
    words = token.split()
    if len(words) >= 2 and fold(words[-1]) in _NAME_SUFFIXES:
        # ``Hollister Jr., Ben``.
        words = words[:-1]
    if not 1 <= len(words) <= 3 or any(ch.isdigit() for ch in token):
        return False
    return len(words) == 1 or all(fold(word) in _PARTICLES for word in words[:-1])


def _names_of_piece(piece: str) -> list[str]:
    """The names in a piece that holds no hard separator: ``Last, First``, a list of full names (``Hans Müller, Anna
    Schmidt``), or both mixed (``Strobel, Arno, Arno Strobel``: one person written twice)."""
    tokens = [token.strip() for token in piece.split(",") if token.strip()]
    names: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if names and fold(token) in _NAME_SUFFIXES:
            # ``Hollister, Ben, Jr.``, ``Ben Hollister, Jr.``: the word belongs to the name before it.
            names[-1] = f"{names[-1]} {token}"
            index += 1
        elif following and _is_bare_last_name(token) and 1 <= len(following.split()) <= 3 \
                and not _swallows_full_name(tokens, index):
            names.append(f"{following} {token}")
            index += 2
        else:
            names.append(token)
            index += 1
    return names


def _swallows_full_name(tokens: list[str], index: int) -> bool:
    """Whether pairing ``tokens[index]`` with the next one would eat a full name that stands there as a name of its
    own (``Anna Schmidt`` after a bare ``Hans``): the next token has a last name already, so it is no first name."""
    following = tokens[index + 1]
    return len(following.split()) >= 2 and fold(following.split()[-1]) == fold(tokens[index])


def clean_authors(names: list[str], *, any_role: bool = False) -> list[str]:
    """A list of authors as one person each, in the form ``First Last``: repeated words and the same person written
    twice (also as initials against the full name, or as a last name alone) come together, the longer form is kept.
    Each entry is one person or the few a mark in it names (``read_people`` without its soft lists: ``Feldkamp, Mara
    (als Mara Lind)`` is both names, ``Ben Hollister liest Mara Feldkamp`` is the author); `` & `` and commas in one
    entry are not taken apart, a source names one person per entry. A person marked as a translator, narrator or
    illustrator (``Dietmar Weber - Übersetzer``, the way Audible lists a translator among the authors) is left out,
    unless the list is the one of that part (``any_role``)."""
    found: list[str] = []
    for raw in names:
        if not isinstance(raw, str):
            continue
        read = read_people(raw, lists=False)
        found.extend(name for name, role in read.people if any_role or role in AUTHOR_ROLES)
    return _tidy_names(found)


def _tidy_names(names: list[str]) -> list[str]:
    """The names in the form ``First Last``, without what is nobody, each person once (the longer form is kept)."""
    result: list[str] = []
    for raw in names:
        name = dedupe_words(display_name(raw))
        if not name or len(name) < 2 or fold(name) in _NOT_PEOPLE or not_a_person(name):
            continue
        for index, seen in enumerate(result):
            if person_key(seen) == person_key(name) or authors_compatible(seen, name) or _bare_of(seen, name):
                if len(name) > len(seen):
                    result[index] = name
                break
        else:
            result.append(name)
    return result


def _bare_of(a: str, b: str) -> bool:
    """Whether one of two names is only the last name of the other (``Strobel`` and ``Arno Strobel``)."""
    last_a, first_a = last_first(a)
    last_b, first_b = last_first(b)
    return bool(last_a) and last_a == last_b and (not first_a or not first_b)


# ---- People in one field: what a field of names says beside the names ---------------------------------------------
#
# Where these come from (measured on a real library and its sources): the creator of an e-book's package and the
# ``metadata.opf`` of Calibre (``Feldkamp, Mara (als Mara Lind)``, the file-as form with the pen name in a bracket), the
# authors of Audiobookshelf's ``metadata.json``, the artist, album artist and composer tags of a recording (``Ben
# Hollister liest Mara Feldkamp``, ``Mara Feldkamp schreibt als Mara Lind``, ``Der Leuchtturm (ungekürzt) - Mara
# Feldkamp``), and Audible's list of authors (``Dietmar Weber - Übersetzer``). Everything is read here, for every way a
# name comes in; what is not sure stays the way it came.

#: ``Reader liest Author``: the reader first (a tag that names both in one field).
_READS = re.compile(r"^(?P<reader>.+?)\s+(?:liest|lesen|reads|spricht|sprechen)\s+(?P<author>.+)$",
                    re.IGNORECASE)
#: ``Author gelesen von Reader``, ``Author, read by Reader``, ``Author (narrated by Reader)``: the author first.
_READ_BY = re.compile(
    r"^(?P<author>.+?)(?:\s*[,:;]|\s+[-–—]|\s)\s*[\(\[]?\s*(?:gelesen|vorgelesen|gesprochen|read|narrated|performed)\s+"
    r"(?:von|by)\s+(?P<reader>[^()\[\]]+?)\s*[\)\]]?\s*$", re.IGNORECASE)
#: ``Name schreibt als Pen Name``, ``Name writing as Pen Name``, ``Name alias Pen Name``.
_WRITES_AS = re.compile(
    r"^(?P<one>.+?)\s+(?:schreibt\s+als|writes\s+as|writing\s+as|alias|a\.\s?k\.\s?a\.|aka)\s+(?P<other>.+)$",
    re.IGNORECASE)
#: A pen name in a bracket after the name: ``Feldkamp, Mara (als Mara Lind)``, ``Mara Feldkamp (writing as Mara Lind)``,
#: ``(alias Mara Lind)``, ``(pseud. Mara Lind)``, ``[Pseudonym: Mara Lind]``.
_PEN_BRACKET = re.compile(
    r"\s*[\(\[]\s*(?:als|as|alias|aka|a\.\s?k\.\s?a\.|writing\s+as|writes\s+as|schreibt\s+als|pseud\.|pseudonym:?)"
    r"\s+(?P<pen>[^()\[\]]+?)\s*[\)\]]", re.IGNORECASE)
#: The real name in a bracket after a pen name: ``Lind, Mara (d. i. Mara Feldkamp)``, ``Mara Lind (Pseudonym von Mara
#: Feldkamp)``, ``(pseud. of Mara Feldkamp)``, ``(eigentlich Mara Feldkamp)``.
_REAL_BRACKET = re.compile(
    r"\s*[\(\[]\s*(?:d\.\s?i\.|eigentlich|real\s+name:?|wirklicher\s+name:?|"
    r"(?:pseud\.|pseudonym)\s+(?:von|of|f[uü]r|for))\s+(?P<real>[^()\[\]]+?)\s*[\)\]]", re.IGNORECASE)
#: The mark of a pen name without the other name: ``Mara Lind (Pseud.)``, ``Lind, Mara, pseud.``.
_PEN_MARK = re.compile(r"\s*[\(\[]\s*pseud(?:onym)?\.?\s*[\)\]]|\s*,\s*pseud(?:onym)?\.?\s*$", re.IGNORECASE)
#: A bracket, for the separators that must not cut through one.
_BRACKET = re.compile(r"[\(\[][^()\[\]]*[\)\]]")


@dataclass
class People:
    """What a field of names says: the people as ``(name, role)``, the pairs of names it says are one person as
    ``(name, pen name)``, and whether a mark in it was read (a pen name, a reader, a title beside the author)."""

    people: list[tuple[str, str]] = field(default_factory=list)
    pens: list[tuple[str, str]] = field(default_factory=list)
    marked: bool = False


def _person_like(text: str, least: int = 1) -> bool:
    """Whether a text can be the name of one person or of a few (``Mara Feldkamp``, ``Feldkamp, Mara``, ``Ben Hollister
    und Mara Lind``), with at least ``least`` words: words with a capital, particles and initials only, no digits, no
    brackets, no article in front. ``Der Leuchtturm`` and ``Wer liest, gewinnt`` are none. A bracket that names a pen
    name or a part (``Feldkamp, Mara (als Mara Lind)``, ``(Hrsg.)``) is no part of the name and does not count."""
    text = clean(text, 200)
    for mark in (_REAL_BRACKET, _PEN_BRACKET, _PEN_MARK, _ROLE_BRACKET):
        text = mark.sub("", text)
    text = text.strip(" ,")
    pieces = [piece for piece in _SOFT_SEPARATORS.split(text) if piece.strip()]
    if not pieces or len(text.replace(",", " ").split()) < least:
        return False
    return all(looks_like_author(piece.strip(" ,"), several=True) for piece in pieces)


def author_beside_title(text: str, titles: Any) -> str:
    """The author of a field that holds the title of the book and its author (``Der Leuchtturm (ungekürzt) - Mara
    Feldkamp``, ``Mara Feldkamp - Der Leuchtturm``), the way a tag writes both: the side that is a person's name, when
    the other side is the title of this very book (``titles``: the album, the title of the book). Empty when the field
    is no such thing; a title the book does not have proves nothing."""
    wanted = [title for title in (titles or ()) if isinstance(title, str) and title.strip()]
    if not wanted:
        return ""
    segments = [part.strip() for part in _SEPARATOR.split(clean(text, 400)) if part.strip()]
    if len(segments) != 2:
        return ""
    for title, name in ((segments[0], segments[1]), (segments[1], segments[0])):
        core = strip_noise(title) or title
        if _person_like(name, 2) and any(
                titles_alike(core, strip_noise(known) or known) for known in wanted):
            return name
    return ""


def _soft_pieces(text: str) -> list[str]:
    """``text`` cut at the soft separators (`` & ``, `` und ``) that stand outside a bracket: ``Mara Feldkamp (als Mara
    & Ben Lind)`` is one piece."""
    masked = _BRACKET.sub(lambda found: "\x00" * len(found.group(0)), text)
    pieces: list[str] = []
    start = 0
    for found in _SOFT_SEPARATORS.finditer(masked):
        pieces.append(text[start:found.start()])
        start = found.end()
    pieces.append(text[start:])
    return pieces


def _read_names(text: str, read: People, *, lists: bool, depth: int = 0, titles: Any = ()) -> None:
    """Reads one piece of a field (no hard separator in it) into ``read``."""
    text = text.strip(" ,")
    if not text:
        return
    beside = author_beside_title(text, titles)
    if beside:
        text, read.marked = beside, True
    if depth < 2:
        reads = _READS.match(text)
        if reads is None or not (_person_like(reads.group("reader"), 2) and _person_like(reads.group("author"), 2)):
            reads = None
            by = _READ_BY.match(text)
            if by is not None and _person_like(by.group("reader"), 2) and _person_like(by.group("author"), 2):
                reads = by
        if reads is not None:
            read.marked = True
            _read_names(reads.group("author"), read, lists=lists, depth=depth + 1)
            readers = People()
            _read_names(reads.group("reader"), readers, lists=True, depth=depth + 1)
            read.people.extend((name, "narrator") for name, _role in readers.people)
            read.pens.extend(readers.pens)
            return
        written = _WRITES_AS.match(text)
        if written is not None and _person_like(written.group("one")) and _person_like(written.group("other")):
            read.marked = True
            ones, others = People(), People()
            _read_names(written.group("one"), ones, lists=lists, depth=depth + 1)
            _read_names(written.group("other"), others, lists=lists, depth=depth + 1)
            read.people.extend(ones.people + others.people)
            read.pens.extend(ones.pens + others.pens)
            if len(ones.people) == 1 and len(others.people) == 1:
                read.pens.append((ones.people[0][0], others.people[0][0]))
            return
    if is_company(text):
        name, role = strip_role(text)
        if name:
            read.people.append((name, role))
        return
    for piece in _soft_pieces(text) if lists else [text]:
        piece = piece.strip(" ,")
        pen = real = ""
        # The real name first: ``(Pseudonym von X)`` is no pen name X.
        found = _REAL_BRACKET.search(piece)
        if found is not None:
            piece, real = (piece[: found.start()] + piece[found.end():]).strip(" ,"), found.group("real").strip(" ,")
            read.marked = True
        found = _PEN_BRACKET.search(piece)
        if found is not None:
            piece, pen = (piece[: found.start()] + piece[found.end():]).strip(" ,"), found.group("pen").strip(" ,")
            read.marked = True
        if _PEN_MARK.search(piece):
            piece, read.marked = _PEN_MARK.sub("", piece).strip(" ,"), True
        piece, role = strip_role(piece)
        if not piece:
            continue
        names = _names_of_piece(piece) if lists else [piece]
        read.people.extend((name, role) for name in names)
        # The other name counts only when it is one person's full name and the bracket stands beside one name: ``(als
        # Lind)`` and ``(als Mara & Ben Lind)`` say too little, and are only taken off.
        for other, is_pen in ((pen, True), (real, False)):
            if other and len(names) == 1 and _person_like(other, 2) and len(_soft_pieces(other)) == 1:
                read.people.append((other, role))
                read.pens.append((names[0], other) if is_pen else (other, names[0]))


def read_people(value: Any, *, titles: Any = (), lists: bool = True) -> People:
    """The people in a field that holds one name, several names or a list, as ``(name, role)``, and the pairs of names
    it says are one person.

    * Names are separated by ``;``, ``|``, `` / ``, `` & ``, `` and ``, `` und ``; a single comma is the comma of
      ``Last, First``, and a list of commas is read as pairs of ``Last, First`` where it can be (``Poznanski, Ursula |
      Strobel, Arno, Arno Strobel`` is two people). ``lists`` off: only ``;``, ``|`` and `` / `` separate (a source's
      entry, where ``Simon & Schuster`` and ``Feldkamp, Mara`` are one name each).
    * A pen name: ``Feldkamp, Mara (als Mara Lind)``, ``Mara Feldkamp schreibt als Mara Lind``, ``writing as``,
      ``alias``, ``(pseud. Mara Lind)``, ``Lind, Mara (d. i. Mara Feldkamp)``: both names, and the pair (the field says
      it, so it is the proof of the pen name, see ``services/aliases``). ``(Pseud.)`` alone is only taken off.
    * A reader: ``Ben Hollister liest Mara Feldkamp``, ``Mara Feldkamp, gelesen von Ben Hollister``, ``read by``,
      ``narrated by``: the reader is the narrator, not an author.
    * The title beside the author (``Der Leuchtturm (ungekürzt) - Mara Feldkamp``): the author, only when the title is
      the title of the book (``titles``).
    * The part a person played is cut off the name (``Hans Müller (Hrsg.)``, ``Dietmar Weber - Übersetzer``, ``Mara
      Feldkamp [Autorin]``); an organisation (``Orchard Press, Inc.``, ``Müller & Söhne GmbH``) is one name that is
      neither turned around nor split; ``Hollister, Ben, Jr.`` is one person.
    * Not people (``Unknown``, ``Diverse Autoren``, ``div.``) are dropped, repeated words and a person named twice are
      one.

    Both sides of a mark must read as names (``Wer liest, gewinnt`` is a title, not a reader and an author); what is
    not sure stays the way it came."""
    items: list[str] = []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list | tuple):
        items = [item for item in value if isinstance(item, str)]
    read = People()
    for item in items[:50]:
        for hard in _HARD_SEPARATORS.split(clean(item, 400)):
            _read_names(hard, read, lists=lists, titles=titles)
    kept: list[tuple[str, str]] = []
    for name, role in read.people:
        cleaned = _tidy_names([name])
        if cleaned:
            kept.append((cleaned[0], role))
    # The same person twice is one (also as initials against the full name): the longer form is kept, and a role named
    # once is a role.
    result: list[tuple[str, str]] = []
    for name, role in kept:
        for index, (seen, seen_role) in enumerate(result):
            if person_key(seen) == person_key(name) or authors_compatible(seen, name) or _bare_of(seen, name):
                result[index] = (name if len(name) > len(seen) else seen, seen_role or role)
                break
        else:
            result.append((name, role))
    pens: list[tuple[str, str]] = []
    for one, other in read.pens:
        tidy_one, tidy_other = _tidy_names([one]), _tidy_names([other])
        if tidy_one and tidy_other and person_key(tidy_one[0]) != person_key(tidy_other[0]):
            pair = (tidy_one[0], tidy_other[0])
            if pair not in pens:
                pens.append(pair)
    return People(people=result[:20], pens=pens[:20], marked=read.marked)


def pen_name_of(piece: str) -> tuple[str, str] | None:
    """``(name, pen name)`` when a piece says one person writes as another (``Mara Feldkamp schreibt als Mara Lind``,
    ``Feldkamp, Mara (als Mara Lind)``), else None. The tag says it, so the two names are one
    person."""
    pens = read_people(piece).pens
    return pens[0] if pens else None


def author_names(name: str) -> list[str]:
    """The names of the authors one credit holds: ``A schreibt als B`` is both, ``Reader liest Author`` is the author
    (the reader is no author), anything else the name itself."""
    read = read_people(name)
    if read.marked:
        found = [person for person, role in read.people if role in AUTHOR_ROLES]
        if found:
            return found
    return [name]


def split_people(value: Any, *, titles: Any = ()) -> list[tuple[str, str]]:
    """The people in a field that holds one name, several names or a list, as ``(name, role)`` (``read_people``)."""
    return read_people(value, titles=titles).people


#: The parts that make a person an author of a book in the lists of authors (an editor heads a collection).
AUTHOR_ROLES = ("", "editor")


def split_authors(value: Any, *, any_role: bool = False, titles: Any = ()) -> list[str]:
    """The names in a field of authors (see ``read_people``). A person marked as a translator, narrator or illustrator
    is no author and is left out, unless the field is the one of that part (``any_role``)."""
    return [name for name, role in split_people(value, titles=titles) if any_role or role in AUTHOR_ROLES]


def authors_of_field(value: Any, *, titles: Any = ()) -> tuple[list[str], list[list[str]]]:
    """``(authors, pen names)`` of a field of authors, the way a reader keeps them in its probe: the authors as
    ``split_authors`` gives them, and the pairs of names the field says are one person (``[name, pen name]``)."""
    read = read_people(value, titles=titles)
    return [name for name, role in read.people if role in AUTHOR_ROLES], [list(pair) for pair in read.pens]


# ---- Titles ------------------------------------------------------------------------------------------------------

_NOISE_WORDS = (
    r"ungek[uü]rzt\w*|gek[uü]rzt\w*|unabridged|abridged|h[oö]rbuch|h[oö]rspiel|audiobook|audio\s*book|lesung|"
    r"ebook|e-book|epub|neuausgabe|neuauflage|autorisierte\s+lesefassung|lesefassung|dramatisiert\w*"
)
_BRACKET_NOISE = re.compile(r"\s*[\(\[][^\)\]]*?\b(?:" + _NOISE_WORDS + r")\b[^\)\]]*[\)\]]", re.IGNORECASE)
_TRAILING_NOISE = re.compile(
    r"\s*(?:[-–—:|·•,]\s*)+(?:" + _NOISE_WORDS
    + r"|(?:(?:a|an|ein|eine|un|une|una|een)\s+)?(?:roman|thriller|krimi|novel)|taschenbuch)\.?\s*$", re.IGNORECASE)
_SUBTITLE_AT = re.compile(r"\s+[-–—·|]\s+|:\s+|\s*[\(\[]")
_LEADING_NUMBER = re.compile(r"^0*(\d{1,3})\s*[-–—.:]\s+(?=\S)")


# ---- What a recording adds to its title: the reader and the form -----------------------------------------------------

_NAME_WORD = r"[^\W\d_][\w.'’\-]*"
_ONE_NAME = _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,3}"
#: ``gelesen von Ida Leser``, ``read by Ida Leser und Ben Sprecher``: a reader phrase and one or two names.
_READER_NOTE = re.compile(
    r"(?:ungek(?:u|ue|ü)rzt\w*\s+)?(?:" + book_words.pattern(book_words.READER_PHRASES) + r")\s+"
    r"(?P<names>" + _ONE_NAME + r"(?:\s+(?:und|and|&|et|en)\s+" + _ONE_NAME + r")?)\.?", re.IGNORECASE)
_FORM_NOTE = re.compile(r"(?:" + book_words.pattern(book_words.RECORDING_NOISE) + r")\.?", re.IGNORECASE)
#: ``Kurt Beispiel: Übers.``: who translated it, as a shop writes it behind the title.
_TRANSLATOR_NOTE = re.compile(_ONE_NAME + r"\s*:\s*(?:übers\.?|übersetzer(?:in)?|translator)", re.IGNORECASE)
_NOTE_PARTS = re.compile(r"\s*[,;·|]\s*|\s+[-–—]\s+")
#: A note at the end of a title: in brackets, or after a dash, a colon, a comma, a bar or a full stop.
_NOTE_BRACKET = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$")
_NOTE_TAIL = re.compile(r"\s*[-–—,.:|;·]\s+")
_NAMES_JOIN = re.compile(r"\s+(?:und|and|&|et|en)\s+", re.IGNORECASE)


def _read_note(text: str) -> list[str] | None:
    """The readers a note names (possibly none) when the note is made of nothing but a reader phrase and words of the
    form (``gelesen von Ida Leser, ungekürzt``), else None: a note with anything else in it is left in the title."""
    readers: list[str] = []
    seen = False
    for part in _NOTE_PARTS.split(text.strip()):
        part = part.strip()
        if not part:
            continue
        found = _READER_NOTE.fullmatch(part)
        if found:
            readers.extend(name.strip(" .") for name in _NAMES_JOIN.split(found["names"]) if name.strip(" ."))
        elif not _FORM_NOTE.fullmatch(part) and not _TRANSLATOR_NOTE.fullmatch(part):
            return None
        seen = True
    return readers if seen else None


def strip_recording_note(title: str) -> tuple[str, list[str]]:
    """A title without the note a tool or a shop adds at its end, and the readers the note names:
    ``Die Laternenstraße (gelesen von Ida Leser)`` is ``Die Laternenstraße`` and ``Ida Leser``; the same for
    ``- gelesen von …``, ``, read by …``, ``(ungekürzt)``, ``(ungekürzte Lesung)``, ``(Hörbuch)``. The note needs a
    bracket or a mark before it and a title in front of it: ``Der Leser``, ``Das Hörbuch`` and a title that is only
    such a note stay as they are."""
    text = clean(title, 300)
    readers: list[str] = []
    while True:
        cut = None
        found = _NOTE_BRACKET.search(text)
        if found and text[: found.start()].strip():
            noted = _read_note(found.group(1))
            if noted is not None:
                cut = (found.start(), noted)
        if cut is None:
            for mark in reversed(list(_NOTE_TAIL.finditer(text))):
                head = text[: mark.start()].strip()
                noted = _read_note(text[mark.end():])
                if head and noted is not None:
                    cut = (mark.start(), noted)
                    break
        if cut is None:
            break
        text = text[: cut[0]].rstrip(" ,;:-–—|·")
        readers = [*cut[1], *readers]
    return (text if text else clean(title, 300)), list(dict.fromkeys(readers))


def strip_noise(title: str) -> str:
    """A title without the words that name the form and not the book: ``(ungekürzt)``, ``Hörbuch``, ``- Roman``."""
    previous = None
    while previous != title:
        previous = title
        title = _BRACKET_NOISE.sub("", title)
        title = _TRAILING_NOISE.sub("", title)
    return title.strip(" -–—:|·•,")


# ---- What a shop adds to a title to sell it ------------------------------------------------------------------------

#: ``(German Edition)``, ``[Kindle Edition]``: the edition a shop sells, no part of the title.
_EDITION_NOTE = re.compile(
    r"\s*[\(\[]\s*(?:german|english|french|spanish|italian|dutch|deutsche|englische|kindle|ebook|e-book)\s+"
    r"(?:edition|ausgabe|version)\s*[\)\]]", re.IGNORECASE)
#: The word of advertising: ``Bestseller``, ``Bestsellerautorin``, ``bestselling``, with what is said before it
#: (``Nr.1-``, ``Nr. 1 -``, ``#1``, ``No.1-``, ``SPIEGEL-``, ``New York Times``).
_PROMO = re.compile(
    r"(?:(?:(?:nr|no|n°|nummer|number)\.?\s*1|#\s*1|spiegel|new[\s-]+york[\s-]+times|sunday[\s-]+times|usa[\s-]+today)"
    r"[\s\-–—]*)*(?:bestseller\w*|best[\s-]?selling\w*)", re.IGNORECASE)
#: Where a text of advertising begins at the latest: a separator before it (a bar, a colon, a dash between spaces, a
#: bracket, the end of a sentence).
_PROMO_SEPARATOR = re.compile(r"\s+\|\s+|:\s+|\s+[-–—]\s+|\s*[\(\[]|\.\s+")
#: The words that join the advertising to a title: ``von``, ``vom``, ``by``, ``from the``, ``de``, ``par``.
_PROMO_JOIN = re.compile(
    r"\s+(?:von|vom|by|from|de|par|del|van|of)(?:\s+(?:the|dem|der|den|la|le|l'|el|einem|einer|a))?\s*$",
    re.IGNORECASE)


def strip_blurb(text: str) -> str:
    """A title or a subtitle without what a shop or a catalogue adds to sell it: ``(German Edition)``, and everything
    from the clause that holds ``Bestseller``, ``Nr.1-Bestsellerautor``, ``#1 bestselling author`` on (from the
    separator before it, a ``|`` first, or the ``von``/``by`` that joins it). ``Das stille Moor: Thriller | Auftakt
    der neuen Serie von Nr.1-Bestsellerautor Lena Vorlage (German Edition)`` is ``Das stille Moor: Thriller``. Empty
    when the whole text is such (``Der Nr.1-Bestsellerautor aus Norwegen``); a title that only *is* the word (``Der
    Bestseller``) stays."""
    value = _EDITION_NOTE.sub("", clean(text, 600)).strip()
    found = _PROMO.search(value)
    if found is None:
        return value
    head = value[: found.start()]
    bar = head.rfind(" | ")
    cut = bar if bar >= 0 else None
    if cut is None:
        separators = list(_PROMO_SEPARATOR.finditer(head))
        joined = _PROMO_JOIN.search(head)
        if joined is not None:
            cut = joined.start()
        elif separators:
            cut = separators[-1].start()
        else:
            cut = found.start()
    rest = value[:cut].strip(" -–—:|·•,.")
    words = rest.split()
    while words and fold(words[-1]) in _ARTICLE_WORDS:
        words.pop()
    rest = " ".join(words).strip(" -–—:|·•,.")
    if not rest:
        # Nothing but advertising before it: a text that only says it (a subtitle of a catalogue), unless the word is
        # the title itself.
        words = fold(value).split()
        bare = fold(found.group(0)) in ("bestseller", "bestsellers")
        return value if bare and len(words) <= 2 else ""
    return rest


_ARTICLE_WORDS = frozenset({"der", "die", "das", "dem", "den", "ein", "eine", "einem", "einer", "the", "a", "an", "le",
                            "la", "les", "el", "vom"})


#: What stands in front of a title and is no part of it: a number of a publisher's catalogue (``2812 - Es``), an ASIN
#: (``B0ABC12345 - Es``), an ISBN, a short code of a series and its volume (``HP2 - …``, ``RL4 - …``).
_LEADING_CODE = re.compile(
    r"^(?:\d{4,6}|B0[A-Z0-9]{8}|97[89](?:[\s-]?\d){10}|(?:\d[\s-]?){9}[\dXx]|[A-Z]{2,4}\d{1,3})\s*[-–—:]\s+(?=\S)")


def without_leading_code(title: str, authors: Any = ()) -> str:
    """``2812 - Es``, ``B0ABC12345 - Es``, ``978-3-9999-0010-0 - Es``, ``HP2 - Es`` and, when ``authors`` names the
    author, ``Rune Falkner - Es`` are ``Es``: what stands in front is a number, a code or the author, not the title.
    A title that would be nothing but the kind of book then (``1984 - Roman``) stays as it is."""
    text = clean(title, 300)
    rest = _LEADING_CODE.sub("", text, count=1)
    if rest == text:
        for author in authors or ():
            if not isinstance(author, str) or not author.strip():
                continue
            for written in {author.strip(), display_name(author), sort_name(author)}:
                found = re.match(re.escape(written) + r"\s*[-–—:]\s+(?=\S)", text, re.IGNORECASE)
                if found:
                    rest = text[found.end():]
                    break
            if rest != text:
                break
    rest = rest.strip()
    if not rest or rest == text or _FORM_ONLY.match(rest):
        return text
    return rest


def title_keys(title: str) -> tuple[str, str]:
    """``(key, main_key)`` of a title: the whole title folded without the noise, and as far as its first subtitle. A
    number or a code in front (``2812 - Es``) is no part of it (``without_leading_code``)."""
    stripped = strip_noise(without_leading_code(clean(title)))
    whole = key_fold(stripped)
    match = _SUBTITLE_AT.search(stripped)
    main = key_fold(stripped[: match.start()]) if match and match.start() > 0 else whole
    return whole, main or whole


#: A title says something by itself when its key has two words besides the articles, or this many letters.
DISTINCT_TITLE_LETTERS = 8
_TITLE_ARTICLES = frozenset({"der", "die", "das", "ein", "eine", "the", "a", "an", "le", "la", "les", "l", "el", "il",
                             "lo", "un", "une", "una", "los", "las", "het", "de", "een"})


def distinct_title_key(key: str) -> bool:
    """Whether a title key is no common word: ``the quiet orchard`` and ``obstgarten`` are, ``poems``, ``it``, ``2000``
    and ``alpha`` are not (many authors have a book of that name)."""
    words = key.split()
    return len([word for word in words if word not in _TITLE_ARTICLES]) >= 2 or len("".join(words)) >= \
        DISTINCT_TITLE_LETTERS


#: What the works of an author at a source prove: nothing, two books of ours among two works (each title a common
#: word), or a book of ours whose title says something by itself.
PROOF_NONE, PROOF_TWO, PROOF_DISTINCT = "", "two", "distinct"


def title_proof(ours: list[str], works: list[list[str]]) -> str:
    """How far the works a source lists for an author (each with its titles: labels, aliases, translations) prove that
    the author is the one of the books ``ours``: ``PROOF_DISTINCT`` when one work has a title of ours that is no common
    word (``distinct_title_key``), ``PROOF_TWO`` when two works have two different titles of ours, else
    ``PROOF_NONE``. A single book called ``Poems`` by somebody of the same name proves nothing: no photo is better than
    the wrong one."""
    wanted = [{key for key in title_keys(title) if key} for title in ours]
    titles_hit: set[int] = set()
    works_hit: set[int] = set()
    for index, names in enumerate(works):
        keys = {key for name in names for key in title_keys(name) if key}
        for number, mine in enumerate(wanted):
            common = mine & keys
            if not common:
                continue
            if any(distinct_title_key(key) for key in common):
                return PROOF_DISTINCT
            titles_hit.add(number)
            works_hit.add(index)
    return PROOF_TWO if len(titles_hit) >= 2 and len(works_hit) >= 2 else PROOF_NONE


def works_proved(ours: list[str], works: list[list[str]]) -> tuple[int, bool]:
    """How many works of a source are works of ours (``ours``: titles of different works), counted so that each of
    them is another of ours and another of theirs (at most the smaller of the two numbers), and whether a title among
    them says something by itself (``distinct_title_key``)."""
    wanted = [{key for key in title_keys(title) if key} for title in ours]
    titles_hit: set[int] = set()
    works_hit: set[int] = set()
    distinct = False
    for index, names in enumerate(works):
        keys = {key for name in names for key in title_keys(name) if key}
        for number, mine in enumerate(wanted):
            common = mine & keys
            if common:
                titles_hit.add(number)
                works_hit.add(index)
                distinct = distinct or any(distinct_title_key(key) for key in common)
    return min(len(titles_hit), len(works_hit)), distinct


def titles_prove_author(ours: list[str], works: list[list[str]]) -> bool:
    """Whether the works prove the author at all (``title_proof``)."""
    return bool(title_proof(ours, works))


# ---- The core of a title -------------------------------------------------------------------------------------------

#: Where a title stops being the title and starts being an addition: a bracket, a colon, a dash between spaces, a bar.
_CORE_CUT = re.compile(r"\s*[\(\[]|:(?:\s|$)|\s[-–—](?:\s|$)|\s\|\s")
#: Letters in brackets inside a word (``Bis(s) zum Morgenlicht``): part of the word, not the start of a subtitle.
IN_WORD_BRACKET = re.compile(r"(?<=[^\W\d_])\(([^\W\d_]{1,3})\)(?=[^\W\d_]|\s|$)")
#: Words at the end of a title that name the kind of book and not the book. One adjective may stand before them
#: ("Historischer Roman", "Spannender Thriller": only an adjective that ends so counts, a noun before the word is part
#: of the title).
_FORM_WORDS = (r"(?:(?:ein|eine|a|an)\s+)?(?:[a-zäöüß]{3,}(?:isch|end)(?:er|es|en|em|e)\s+)?"
               r"(?:kriminalroman|psychothriller|thriller|krimi|roman|novel|erzählung|erzaehlung)")
_CORE_TAIL = re.compile(r"\s+(?:" + _FORM_WORDS + r"|ungek[uü]rzt\w*|unabridged|h[oö]rbuch|audiobook)\.?$",
                        re.IGNORECASE)
#: A word before the form that makes it a part of the title ("Lena's Thriller", "Der Thriller").
_TAKES_FORM = frozenset({"der", "die", "das", "des", "dem", "den", "the", "of", "von", "mein", "dein", "sein", "ihr",
                         "unser", "euer", "mit", "und", "and", "my", "your", "his", "her", "our", "its"})
_FORM_ONLY = re.compile(r"^" + _FORM_WORDS + r"\.?$", re.IGNORECASE)
_BY_AUTHOR = re.compile(r"\s+by\s+([^\W\d_][\w.' -]{1,60})$", re.IGNORECASE)
_ARTICLES_TO_IGNORE = frozenset({"der", "die", "das", "ein", "eine", "the", "a", "an", "le", "la", "les", "l", "el",
                                 "il", "lo", "un", "une", "una", "los", "las", "het", "de", "een"})


def core_title(title: str, author: str = "") -> str:
    """The title without what a catalogue or a shop adds to it: the part before the first ``(``, ``[``, ``:``, `` - ``,
    `` – `` or `` | ``; without ``Roman``, ``Kriminalroman``, ``Thriller``, ``A Novel`` or ``ungekürzt`` at the end; and
    without ``by Author`` when the author is the one named. ``Eine unbeliebte Dame (Ein Krimi 1): Ein Mord, ein
    Selbstmord`` is ``Eine unbeliebte Dame``. What is left is never empty: a title that would shrink to nothing stays
    as it was."""
    original = clean(title, 300)
    text = without_leading_code(IN_WORD_BRACKET.sub(r"\1", original), [author] if author else ())
    text = split_series_title(text).title or text
    if author:
        found = _BY_AUTHOR.search(text)
        if found and (fold(found.group(1)) == fold(author) or person_key(found.group(1)) == person_key(author)):
            text = text[: found.start()]
    cut = _CORE_CUT.search(text, 1)
    if cut is not None and cut.start() > 0:
        text = text[: cut.start()]
    while True:
        shorter = _CORE_TAIL.sub("", text)
        left = shorter.split()
        if shorter == text or len(left) < 2 or left[-1].casefold() in _TAKES_FORM or left[-1].endswith(("'s", "’s")):
            break
        text = shorter
    text = text.strip(" -–—:|·•,.")
    return text if len(fold(text)) >= 1 else original


def compare_key(title: str, author: str = "") -> str:
    """The core of a title folded, without the article at the front: what two titles are compared by."""
    words = key_fold(core_title(title, author)).split()
    while len(words) > 1 and words[0] in _ARTICLES_TO_IGNORE:
        words.pop(0)
    return " ".join(words)


#: The last word of a subtitle that names the kind of book or a case of a series, not a book (``Ein Bretagne-Krimi``,
#: ``Ein Fall für Hanna``, ``A Novel``).
_KIND_WORDS = frozenset({"krimi", "kriminalroman", "thriller", "psychothriller", "roman", "novel", "mystery", "fall",
                         "case", "abenteuer", "erzahlung", "erzaehlung", "story", "geschichte", "saga", "romance",
                         "fantasy", "liebesroman", "jugendbuch", "kinderbuch"})


def subtitle_key(title: str, subtitle: str = "", series: str = "", authors: list[str] | None = None) -> str:
    """What the subtitle of a title says of *which* book it is, folded: the part after the core of the title (``Reihe:
    Der Titel des Bandes``) or the subtitle field, without advertising, without what names the kind of book (``Roman``,
    ``Ein Bretagne-Krimi``, ``Ein Fall für Hanna``), the series, a volume (any number) or an author. Empty when the
    subtitle says nothing of the kind: two books of one series with the same core and two different such subtitles are
    two books (``Reihe: Erster Band`` and ``Reihe: Zweiter Band``)."""
    base = split_series_title(clean(title, 300)).title
    cut = _CORE_CUT.search(base, 1)
    pieces = [base[cut.end():] if cut is not None else "", strip_blurb(subtitle or "")]
    series_key = compare_key(series) if series else ""
    names = [key_fold(name) for author in authors or [] for name in author_names(author)]
    for piece in pieces:
        piece = re.sub(r"[\(\[][^\)\]]*[\)\]]?", " ", piece)
        piece = strip_noise(piece).strip(" -–—:|·•,.")
        if not piece or _FORM_ONLY.match(piece):
            continue
        key = compare_key(piece)
        words = key.split()
        if len(key) < 3 or re.search(r"\d", key) or any(word in GENERIC_SERIES for word in words):
            continue
        plain = key_fold(piece).split()
        if words[-1] in _KIND_WORDS or (2 <= len(plain) <= 4 and plain[0] in ("ein", "eine", "a", "an")) or (
                len(words) == 1 and words[0].endswith(("saga", "krimi", "thriller"))):
            # (``Die Jahrhundertsaga``, ``Der Schwedenkrimi``: one word that names the kind of book.)
            continue
        if series_key and (series_key in key or key in series_key):
            continue
        if any(name and (name in key or key in name) for name in names):
            continue
        return key
    return ""


def subtitles_differ(a: str, b: str) -> bool:
    """Whether two subtitle keys (``subtitle_key``) name two different books: both say something and they are not
    alike."""
    return bool(a and b and a != b and a not in b and b not in a and not titles_alike(a, b))


def _same_word(a: str, b: str) -> bool:
    """Whether two words of a title are one word spelled differently (``prozess``, ``process``) and not two words that
    only look alike (``westen`` and ``osten``, ``bergdoktor`` and ``bergdoktorin``): close in their letters, and neither
    one the other with two letters or more added."""
    from difflib import SequenceMatcher

    if a == b:
        return True
    if abs(len(a) - len(b)) >= 2 and (a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def titles_alike(a: str, b: str, threshold: float = 0.9) -> bool:
    """Whether two titles are the same book's title: the cores equal without the article, or at least ``threshold``
    alike letter for letter. A volume number that differs is never alike (``Moorland 2`` and ``Moorland 3``), and
    neither is a word that differs for another word (``Im Westen Nebel`` and ``Im Osten Nebel`` are close
    letter for letter and two books)."""
    return keys_alike(compare_key(a), compare_key(b), threshold)


def keys_alike(ka: str, kb: str, threshold: float = 0.9) -> bool:
    """``titles_alike`` for two keys that are made already (``compare_key``): what a catalogue of many titles compares
    pair by pair, so that each key is made once. The cheap bounds come first: two keys whose lengths differ too much
    can never be ``threshold`` alike (the ratio is at most twice the shorter length over both lengths)."""
    from collections import Counter
    from difflib import SequenceMatcher

    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if 2 * min(len(ka), len(kb)) < threshold * (len(ka) + len(kb)):
        return False
    if re.findall(r"\d+", ka) != re.findall(r"\d+", kb):
        return False
    only_a = list((Counter(ka.split()) - Counter(kb.split())).elements())
    only_b = list((Counter(kb.split()) - Counter(ka.split())).elements())
    if only_a and only_b and not (
            all(any(_same_word(x, y) for y in only_b) for x in only_a)
            and all(any(_same_word(x, y) for x in only_a) for y in only_b)):
        return False
    matcher = SequenceMatcher(None, ka, kb)
    return matcher.real_quick_ratio() >= threshold and matcher.quick_ratio() >= threshold and \
        matcher.ratio() >= threshold


#: A trailing addition that names only the kind of book: ``Der Beobachter - Kriminalroman``, ``Ahnenland (Thriller)``.
_SUFFIX_CUTS = (re.compile(r"\s+[-–—|·]\s+([^-–—|·]{3,40})$"), re.compile(r":\s+([^:]{3,40})$"),
                re.compile(r"\s+\(([^()]{3,40})\)$"))


def split_form_suffix(title: str) -> tuple[str, str]:
    """``(title, form)``: a title with the kind of book at its end cut off (``Der Beobachter - Kriminalroman`` gives
    ``("Der Beobachter", "Kriminalroman")``). Only a closed list of words counts (roman, kriminalroman, thriller,
    psychothriller, krimi, novel, with the article and one word before them); any other addition is a subtitle and
    stays.
    The title when there is no such suffix, and an empty form."""
    text = clean(title, 300)
    # Each kind of ending by itself (``Titel – Untertitel: Thriller`` ends in a colon and a form, though a dash came
    # first).
    for pattern in _SUFFIX_CUTS:
        found = pattern.search(text)
        if found is None:
            continue
        form = found.group(1).strip()
        head = text[: found.start()].strip(" -–—|·:,")
        if head and _FORM_ONLY.match(form):
            return head, form
    return text, ""


def number_of(text: str) -> float | None:
    """A volume number like ``4``, ``04``, ``4.0``, ``2,5`` as a number, or None."""
    match = re.fullmatch(r"\s*#?0*(\d{1,4})(?:[.,](\d{1,2}))?\s*", text or "")
    if not match:
        return None
    value = float(match.group(1) + ("." + match.group(2) if match.group(2) else ""))
    return value if 0 < value < 10_000 else None


_SERIES_FIELD = re.compile(
    r"(.+?)\s*(?:[#,]|\bBd\.?|\bBand|\bBuch|\bBook|\bVol\.?|\bNr\.?)?\s*[\(\[]?(\d{1,4}(?:[.,]\d{1,2})?)[\)\]]?",
    re.IGNORECASE)


def series_from_field(text: str) -> tuple[str, float | None]:
    """A series as a field has it: ``Anselm Quist #1.0``, ``Anselm Quist 3``, ``Anselm Quist (4)``, or the name
    alone. Gives the name and the volume (None when there is none)."""
    text = clean(text, 200)
    match = _SERIES_FIELD.fullmatch(text)
    if match and len(fold(match.group(1))) >= 2 and not match.group(1).strip().isdigit():
        pos = number_of(match.group(2))
        if pos is not None:
            name = match.group(1).strip(" ,#-–")
            return ("", None) if is_generic_series(name) else (name, pos)
    return ("", None) if is_generic_series(text) else (text, None)


_MARKER = r"(?:Bd\.?|Band|Buch|Teil|Book|Vol\.?|Volume|Nr\.?|No\.?|#)"
_BRACKET_SERIES = re.compile(r"^\[\s*(.+?)\s+(\d{1,3}(?:[.,]\d{1,2})?)\s*\]\s*[-–—:•·|]?\s*(.+)$")
_MARKED_SERIES = re.compile(
    r"^(.+?)\s+" + _MARKER + r"\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*[-–—:•·|]\s*(.+)$", re.IGNORECASE)
_PLAIN_SERIES = re.compile(r"^(.+?)\s+(\d{1,3}(?:[.,]\d{1,2})?)\s*[-–—:•·|]\s*(.+)$")
_ONLY_SERIES = re.compile(r"^(.+?)\s+(?:" + _MARKER + r"\s*)?(\d{1,3}(?:[.,]\d{1,2})?)$", re.IGNORECASE)


@dataclass
class TitleParts:
    title: str
    series: str = ""
    pos: float | None = None


#: Words that name a part or a volume, never a series of their own (``Titel - Teil 2`` has no series "Teil").
GENERIC_SERIES = frozenset({
    "teil", "band", "bd", "buch", "book", "books", "cd", "cds", "disc", "discs", "disk", "folge", "folgen", "part",
    "parts", "volume", "volumes", "vol", "tome", "tomo", "tomes", "livre", "nr", "no", "kapitel", "chapter", "episode",
    "staffel", "season", "fall", "case", "teile", "baende", "bande", "reihe", "serie", "series"})
#: Words that join an author to a title; a series is never written with them (``... Serie von Nr.1-...``).
_SERIES_NEVER = frozenset({"von", "vom", "by", "from"})
#: What a title that is only an author's name says besides the name (``Bestsellerautor Lena Vorlage``).
_AUTHOR_ROLE_WORDS = re.compile(
    r"\b(?:bestseller-?autor(?:in)?|bestsellerautor(?:in)?|erfolgsautor(?:in)?|autor(?:in)?|author|writer|"
    r"erz[aä]hlt|liest|reads|presents|pr[aä]sentiert|von|vom|by|from|the|der|die|das|nr|no)\b\.?", re.IGNORECASE)


#: Words for a part of a recording that end the name of one of its tracks (``01-ada_feldkamp-kapitel_1_-_titel``).
_TRACK_PART_WORDS = frozenset({"kapitel", "chapter", "track", "tracks", "titel", "cd", "disc", "disk", "teil", "part"})


def is_generic_series(name: str) -> bool:
    """Whether a name of a series is only a word for a part or a volume (``Teil``, ``Band``, ``CD``, ``Book``), or the
    name of a track before its number: a track number first and a word for a part last (``01-ada feldkamp-kapitel``,
    the rest is the author or the narrator)."""
    words = fold(name).split()
    if not words:
        return False
    if all(word in GENERIC_SERIES or word.isdigit() for word in words):
        return True
    return len(words) >= 2 and words[0].isdigit() and words[-1] in _TRACK_PART_WORDS


def title_is_author(title: str, authors: list[str] | None) -> bool:
    """Whether a title is nothing but the name of one of the authors (with the words of a shop around it:
    ``Bestsellerautor Lena Vorlage (German Edition)``). Such a title names no book."""
    if not authors:
        return False
    text = _EDITION_NOTE.sub("", clean(title, 300))
    text = _PROMO.sub(" ", text)
    text = _AUTHOR_ROLE_WORDS.sub(" ", text)
    text = re.sub(r"[\s\-–—:|·•,()\[\]]+", " ", text).strip()
    if not text or len(text.split()) > 5:
        return False
    key = person_key(text)
    return bool(key) and any(
        name and (person_key(name) == key or authors_compatible(name, text) or key_fold(name) == key_fold(text))
        for author in authors for name in author_names(author))


def _whole_title(series: str, rest: str) -> str:
    """The title behind a series and its number: ``und der Stein am Fluss`` goes on from the name of the series, and
    the title is both; anything else stands on its own."""
    return f"{series} {rest}" if rest.split(" ", 1)[0] in book_words.TITLE_JOINS else rest


def _series_fits(series: str, rest: str, authors: list[str] | None = None) -> bool:
    """Whether the part before ``Band 2 -`` is a series and the part after it a title: a series is a short name (six
    words at most, no colon, no bar, not ending in a separator, no ``von``/``by``), never only a word for a volume, and
    what is left is no author's name and no advertising."""
    name = series.strip()
    if not name or re.search(r"[:|]", name) or re.search(r"[-–—:|]$", name) or len(name.split()) > 6:
        return False
    if any(word in _SERIES_NEVER for word in fold(name).split()) or is_generic_series(name):
        return False
    return not (_PROMO.match(rest.strip()) or title_is_author(rest, authors))


def split_series_title(title: str, known_series: str = "", known_pos: float | None = None,
                       authors: list[str] | None = None) -> TitleParts:
    """A title that carries its series: ``[Anselm Quist 01] • Nebelchor`` and ``Anselm Quist Bd. 5 - Ahnenland |
    Thriller`` give ``Nebelchor`` / ``Ahnenland`` with the series and the volume. ``002 - Frevelpfad`` loses its number
    when the series says it is volume 2. A title with no such sign is returned as it is: ``Lantern 451`` is a title.
    Advertising (``strip_blurb``) is taken off first, and a "series" that is a clause of it is none (``_series_fits``;
    ``authors`` tells a title that is only an author's name)."""
    title = clean(title)
    # What a shop adds to sell the book goes first: ``... | Auftakt der Serie von Nr.1-Bestsellerautor ...`` is no
    # series with a volume 1.
    title = strip_blurb(title) or title
    series, pos = "", None
    match = _BRACKET_SERIES.match(title)
    if match:
        series, pos, title = match.group(1), number_of(match.group(2)), match.group(3)
    else:
        match = _MARKED_SERIES.match(title)
        if match and _series_fits(match.group(1), match.group(3), authors):
            series, pos, title = match.group(1), number_of(match.group(2)), match.group(3)
        elif known_series:
            match = _PLAIN_SERIES.match(title)
            if match and fold(match.group(1)) == fold(known_series):
                series, pos, title = match.group(1), number_of(match.group(2)), match.group(3)
    if series:
        title = _whole_title(series.strip(" ,"), title)
    if known_pos is not None and not series:
        lead = _LEADING_NUMBER.match(title)
        if lead and float(lead.group(1)) == known_pos:
            title = title[lead.end():]
    # A genre after a bar: "Ahnenland | Thriller".
    if " | " in title:
        head = title.split(" | ", 1)[0].strip()
        if head:
            title = head
    return TitleParts(title=title.strip(), series=series.strip(" ,"), pos=pos)


# ---- A series in a title: "Reihe 04 - Titel", "Dupins zwölfter Fall" ------------------------------------------------

#: The ordinal words of a series of cases ("Kommissar Dupins zwölfter Fall"), in German and English, as they are
#: written with and without the umlaut.
_ORDINALS = {
    "erste": 1, "zweite": 2, "dritte": 3, "vierte": 4, "fünfte": 5, "fuenfte": 5, "sechste": 6, "siebte": 7,
    "siebente": 7, "achte": 8, "neunte": 9, "zehnte": 10, "elfte": 11, "zwölfte": 12, "zwoelfte": 12,
    "dreizehnte": 13, "vierzehnte": 14, "fünfzehnte": 15, "fuenfzehnte": 15, "sechzehnte": 16, "siebzehnte": 17,
    "achtzehnte": 18, "neunzehnte": 19, "zwanzigste": 20, "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}
_ORDINAL_WORDS = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_ORDINAL_CASE = re.compile(
    r"^(?P<name>.+?)\s+(?P<ord>" + _ORDINAL_WORDS +
    r")(?:r|s|n|m)?\s+(?:fall|fälle|case|roman|band|buch|book|abenteuer|"
    r"auftrag|mission)$", re.IGNORECASE)


def ordinal_series(text: str) -> tuple[str, float] | None:
    """``("Kommissar Dupin", 12.0)`` for ``Kommissar Dupins zwölfter Fall``: a name that says which case of a series it
    is and nothing more. It is the series and the volume, no title of a book. None for anything else."""
    found = _ORDINAL_CASE.match(clean(text, 200).strip())
    if found is None:
        return None
    name = found.group("name").strip(" ,")
    if name.casefold().endswith(("'s", "’s")):
        name = name[:-2]
    elif name.casefold().endswith("s") and len(name) > 3:
        name = name[:-1]
    position = _ORDINALS.get(found.group("ord").casefold())
    return (name, float(position)) if name and position else None


_VOLUME_WORD = r"(?:band|bd\.?|buch|teil|folge|book|vol\.?|volume|nr\.?|no\.?|#)"
_PREFIX_DASH = re.compile(
    r"^(?P<series>[^\d:]*?[^\W\d_][^\d:]*?)[\s,]+(?:(?P<word>" + _VOLUME_WORD + r")\s*)?(?P<n>\d{1,2})\.?\s*"
    r"[-–—:]\s+(?P<rest>\S.*)$", re.IGNORECASE)
_PREFIX_BRACKET = re.compile(
    r"^(?P<series>[^\d:]*?[^\W\d_][^\d:]*?)[\s,]+(?:(?P<word>" + _VOLUME_WORD + r")\s*)?(?P<n>\d{1,2})\s*"
    r"\(\s*(?P<rest>[^()]{2,100}?)\s*\)\s*$", re.IGNORECASE)


@dataclass
class PrefixSplit:
    title: str
    series: str
    pos: float


def split_prefix_series(title: str, confirm: str = "") -> PrefixSplit | None:
    """``Walter Pulaski 04 - Rachefrühling`` gives the title ``Rachefrühling``, the series ``Walter Pulaski`` and the
    volume 4; so do ``Carl Morck 01 (Erbarmen)`` and ``Bodenstein & Kirchhoff 03 - Tiefe Wunden``. The part after the
    separator is the title, the part before it the series with its number (also ``Band 3``, ``#3``, ``03``, ``3.``).
    Taken when a word names the number a volume, when it is written with a leading zero, or when the names of the folder
    and the file (``confirm``) hold the part after it: ``Lantern 451: A Novel`` and ``Apollo 13 - The Film`` are
    titles. None when the title is no such thing."""
    text = clean(title, 300).strip()
    found = _PREFIX_DASH.match(text) or _PREFIX_BRACKET.match(text)
    if found is None:
        return None
    series = found.group("series").strip(" ,-–—")
    rest = found.group("rest").strip(" -–—:")
    position = number_of(found.group("n"))
    if position is None or len(fold(series)) < 2 or not rest or len(fold(rest)) < 2 or is_generic_series(series):
        return None
    sure = bool(found.group("word")) or found.group("n").startswith("0")
    named = fold(confirm)
    if not sure and not (named and fold(rest) in named):
        return None
    return PrefixSplit(title=_whole_title(series, rest), series=series, pos=position)


def pen_pairs(value: Any) -> list[tuple[str, str]]:
    """The pairs of names a field says belong to one person (``A schreibt als B``, ``B, A (als C)``), as ``(name, pen
    name)`` (``read_people``)."""
    return read_people(value).pens


#: Words for a disc of a recording: a number after them is no volume.
_DISC_WORDS = frozenset({"cd", "cds", "disc", "discs", "disk", "part", "parts", "kapitel", "chapter"})
_VOLUME_WORDS = frozenset({"band", "bd", "buch", "teil", "folge", "book", "vol", "volume", "nr", "no", "cd", "disc",
                           "disk", "part", "kapitel", "chapter", "fall", "case"})
_SUFFIX_BRACKET = re.compile(
    r"^(?P<rest>\S.*?)\s*\(\s*(?P<series>[^()\d]*?[^\W\d_][^()\d]*?)[\s,]+(?:(?P<word>" + _VOLUME_WORD + r")\s*)?"
    r"(?P<n>\d{1,2})\s*\)\s*$", re.IGNORECASE)
_SUFFIX_SERIES = re.compile(
    r"^(?P<series>[^\d]*?[^\W\d_][^\d]*?)[\s,]+(?:(?P<word>" + _VOLUME_WORD + r")\s*)?(?P<n>\d{1,2})$", re.IGNORECASE)


def split_suffix_series(title: str) -> PrefixSplit | None:
    """The series with its number *after* the title: ``Erbarmen (Carl Mørck 1)`` gives the title ``Erbarmen``, the
    series ``Carl Mørck`` and the volume 1; ``Die Unverbesserlichen - Der große Coup - Die Unverbesserlichen 1`` loses
    its last part when that is the series, named at the front of the title or with a word that says it is a volume
    (``Band 2``). A bracket that holds only a word for a part (``(Teil 2)``) is no series. None when the title has no
    such end."""
    text = clean(title, 300).strip()
    found = _SUFFIX_BRACKET.match(text)
    if found is not None:
        series = found.group("series").strip(" ,-–—")
        position = number_of(found.group("n"))
        if position is not None and len(fold(series)) >= 2 and not is_generic_series(series) and fold(
                series) not in _VOLUME_WORDS:
            return PrefixSplit(title=found.group("rest").strip(" -–—:"), series=series, pos=position)
    segments = [part.strip() for part in _SEPARATOR.split(text) if part.strip()]
    if len(segments) >= 2:
        last = _SUFFIX_SERIES.match(segments[-1])
        if last is not None:
            series = last.group("series").strip(" ,-–—")
            position = number_of(last.group("n"))
            if position is not None and len(fold(series)) >= 2 and not is_generic_series(series) and (
                    last.group("word") or fold(series) == fold(segments[0])):
                return PrefixSplit(title=" - ".join(segments[:-1]), series=series, pos=position)
    return None


# ---- What a file or folder name says ----------------------------------------------------------------------------

#: The folders that only hold the parts of one book and are no book of their own, as ``fold`` writes them: a disc
#: (``CD 1``, ``cd01``, ``Disc 2``, ``Disk 2``, ``Teil 1``, ``Part 1``, ``Teil 1 von 6``, ``MP3 CD 2``), a bare
#: number, and the
#: general names a producer or a download tool gives (``Audio``, ``Hörbuch``, ``MP3``). A folder named for a volume
#: (``Band 3``) is a book of its own, not one of these.
_PART_FOLDER = re.compile(
    r"^(?:(?:mp3 )?(?:cd|dvd|disc|disk|part|teil|kapitel|side)\s?0*\d{1,3}(?: (?:von|of|v) 0*\d{1,3})?"
    r"|0*\d{1,2}|(?:mp3 )?(?:cd|dvd|disc|disk)"
    r"|audio(?: ?(?:books?|files?|dateien|spur|tracks?))?|audiobooks?|hoerbuch|hoerbuecher|horbuch|horbucher"
    r"|mp3s?|m4b|flac)$")
_PART_NUMBER = re.compile(r"(?:cd|dvd|disc|disk|part|teil|kapitel|side)\s?0*(\d{1,3})|^0*(\d{1,2})$")
#: The Roman numerals of a disc, from 1 to 29 (folded, so small).
_ROMAN = r"(?:x{0,2}(?:ix|iv|vi{0,3}|i{1,3})|x{1,2})"
#: The mark of a disc in a folded name: a carrier and its number (``cd1``, ``disc 02``, ``cd iv``), or the number before
#: it (``1 cd``, from ``1.CD``).
_DISC = rf"(?:(?:mp3 )?(?:cd|dvd|disc|disk) ?0*(?P<number>\d{{1,3}}|{_ROMAN})|0*(?P<before>\d{{1,2}}) ?cd)"
#: What may stand behind the mark and say no more than which part of the book the disc holds: ``von 3``, ``Part 1``,
#: ``Kapitel 1-12``, plain numbers.
_DISC_TAIL = (r"(?: (?:von|of|v) 0*\d{1,3})?"
              r"(?: (?:kapitel|kap|chapter|chapters|teil|part|track|tracks|titel|seite|side)(?: \d{1,4})*)?"
              r"(?: \d{1,4})*")
#: A folder named for a disc and nothing else (``CD 1``, ``Disc 02``, ``CD1 - Kapitel 1-12``): nothing but a disc of one
#: recording, whatever the albums of its files say. ``Teil 2`` and a bare number may be a book of a box as well.
_DISC_FOLDER = re.compile(rf"^{_DISC}{_DISC_TAIL}$")
#: A folder that begins with the mark of a disc and goes on in words of its own (``CD1 - Der Anfang``): the folder of a
#: disc as well, but the words may name a book: the albums tell.
_DISC_FIRST = re.compile(rf"^{_DISC} [^\W\d_]")
#: A folder that names a book and its disc (``Die Nebelbahn CD1``, ``Nebelbahn (CD 2)``, ``Titel - Disc 3``).
_DISC_OF_BOOK = re.compile(rf"^(?P<book>.*?[^\W\d_].*?) {_DISC}{_DISC_TAIL}$")
_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_YEAR_BRACKET = re.compile(r"\s*\(\s*(1[4-9]\d\d|20\d\d)\s*\)")
_ARTICLES = frozenset({"der", "die", "das", "ein", "eine", "the", "a", "an", "le", "la", "les", "el", "il", "een",
                       "het"})
_PLACEHOLDER_AUTHORS = frozenset({"audiobooks", "hoerbuecher", "ebooks", "books", "buecher", "bibliothek", "library",
                                  "media", "unknown", "unbekannt", "diverse", "hoerbuch", "ebook", "audio"})


def is_shelf_name(name: str) -> bool:
    """Whether a folder is called what a collection is called (``Hörbücher``, ``Books``, ``Audio``), not what a book
    is."""
    return fold(name) in _PLACEHOLDER_AUTHORS or is_cd_folder(name)


def is_cd_folder(name: str) -> bool:
    """A folder of a recording's parts (``CD 1``, ``Disc 02``, ``Teil 3``, ``Audio``, ``Hörbuch``, ``MP3``) rather
    than a book of its own: its files belong to the book of the folder it lies in."""
    key = fold(name)
    return bool(_PART_FOLDER.match(key) or _DISC_FOLDER.match(key) or _DISC_FIRST.match(key))


def _roman_value(written: str) -> int:
    values = {"i": 1, "v": 5, "x": 10}
    numbers = [values[letter] for letter in written]
    return sum(-number if number < after else number for number, after in zip(numbers, [*numbers[1:], 0], strict=True))


def _disc_number(found: re.Match[str]) -> int:
    written = found.group("number") or found.group("before")
    return int(written) if written.isdigit() else _roman_value(written)


def disc_of_folder(name: str) -> int | None:
    """The number a folder of parts is called (``CD 2`` is 2, ``Disc 02`` 2, ``2.CD`` 2, ``CD II`` 2), also when it
    names its book (``Die Nebelbahn CD 2``), None for one with no number."""
    key = fold(name)
    disc = _DISC_FOLDER.match(key) or _DISC_FIRST.match(key) or _DISC_OF_BOOK.match(key)
    if disc:
        return _disc_number(disc)
    found = _PART_NUMBER.search(key)
    if not found or not is_cd_folder(name):
        return None
    return int(found.group(1) or found.group(2))


def is_disc_folder(name: str) -> bool:
    """A folder that is a disc of one recording by its name (``CD 1``, ``Disc 02``), see ``_DISC_FOLDER``."""
    return bool(_DISC_FOLDER.match(fold(name)))


def disc_book(name: str) -> str:
    """The book a folder of a disc names, folded (``Die Nebelbahn CD1`` is ``die nebelbahn``), empty for any other
    folder. Such a folder is a disc of that book, and the book is a folder only where the name of the folder above says
    so: the discs of two books may lie side by side."""
    key = fold(name)
    if _DISC_FOLDER.match(key) or _DISC_FIRST.match(key):
        return ""
    found = _DISC_OF_BOOK.match(key)
    return found.group("book") if found else ""


def _holds_parts(parts: list[str], release: bool) -> bool:
    """Whether the last of ``parts`` is a folder of parts of the book in the folder above (``strip_part_folders``)."""
    if is_cd_folder(parts[-1]):
        return len(parts) >= (1 if release else 2)
    book = disc_book(parts[-1])
    return bool(book) and (release or (len(parts) >= 2 and fold(parts[-2]) == book))


def strip_part_folders(folder: str, *, release: bool = False) -> str:
    """A folder path without the folders of parts at its end (``Autor/Titel/Audio`` is ``Autor/Titel``,
    ``Autor/Titel/CD 1`` too, also both: ``Autor/Titel/Audio/CD 1``). The first folder of the library is never taken
    off: a library may keep its books in a folder called ``Audio``. In a release (``release``) that holds no such
    reason: its folder is the book, and ``CD 1`` right below it is a disc of that book, as is ``Die Nebelbahn CD 1``."""
    parts = [part for part in folder.split("/") if part != ""]
    while parts and _holds_parts(parts, release):
        parts.pop()
    return "/".join(parts)


def disc_in_path(path: str) -> int | None:
    """The disc a file lies on by the folders it lies in (``Titel/CD 2/track.mp3`` is 2), the nearest numbered one."""
    parts = [part for part in path.split("/") if part][:-1]
    for part in reversed(parts):
        if is_cd_folder(part) or disc_book(part):
            number = disc_of_folder(part)
            if number is not None:
                return number
        else:
            break
    return None


def spaced(name: str) -> str:
    """A name written with dots or underscores for spaces (``Falkner,.Rune.-.Anselm.Quist.5``) with spaces."""
    text = clean(name, 400)
    if text.count(" ") <= 1 and (text.count(".") >= 2 or text.count("_") >= 2):
        text = re.sub(r"[._]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text


#: Words of a folder that holds many books and no one's name (``Neue Hörbücher``): it is a shelf, not an author.
_SHELF_WORDS = frozenset({
    "hoerbuecher", "hoerbuch", "horbucher", "horbuch", "buecher", "bucher", "buch", "books", "audiobooks", "audiobook",
    "ebooks", "ebook", "audio", "bibliothek", "library", "sammlung", "collection", "mp3", "krimis", "thriller",
    "romane", "fantasy", "kinder"})


def looks_like_author(segment: str, several: bool) -> bool:
    text = clean(segment, 120)
    words = text.split()
    if not 1 <= len(words) <= 5 or re.search(r"[\d\[\]\(\):?!|•]", text) or fold(text) in _PLACEHOLDER_AUTHORS:
        return False
    if any(fold(word) in _SHELF_WORDS for word in words):
        return False
    if fold(words[0]) in _ARTICLES:
        return False
    if "," in text:
        parts = [part for part in text.split(",") if part.strip()]
        return len(parts) == 2 and all(1 <= len(part.split()) <= 3 for part in parts)
    if len(words) == 1:
        return several and words[0][:1].isupper()
    return all(word[:1].isupper() or fold(word) in _PARTICLES or word.endswith(".") for word in words)


@dataclass
class NameParts:
    title: str = ""
    author: str = ""
    series: str = ""
    pos: float | None = None
    extra: list[str] = field(default_factory=list)


#: The number of a volume in front of a title, as the scheme of a library writes it: up to three digits, more only when
#: the name ends on a year in brackets (a title may be a year: ``1984``).
_VOLUME_FIELD = re.compile(r"^0*(\d{1,3})$")
_LONG_VOLUME_FIELD = re.compile(r"^(\d{4,5})$")


def volume_in_front(segments: list[str], text: str) -> float | None:
    """The number a name begins with when it is the place of the book and no title: ``01 - Titel``, ``2473 - Titel
    (2008)``. None when what follows is a name of a person (``1984 - Rune Falkner`` is a title and its author)."""
    if len(segments) < 2 or (len(segments) == 2 and looks_like_author(_YEAR_BRACKET.sub("", segments[1]),
                                                                      several=False)):
        return None
    found = _VOLUME_FIELD.match(segments[0]) or (
        _LONG_VOLUME_FIELD.match(segments[0]) if _YEAR_BRACKET.search(text) else None)
    return float(found.group(1)) if found else None


def parse_name(name: str) -> NameParts:
    """What a file or folder name says: ``Author - Series 02 - Title``, ``Last, First - [Series 04] • Title``,
    ``Author - Title - Subtitle``, ``02 - Title`` (the name a scheme gives it), or the title alone. Nothing is made up:
    an unclear name gives only a title."""
    text = spaced(name)
    segments = [seg.strip() for seg in _SEPARATOR.split(text) if seg.strip()]
    if not segments:
        return NameParts()
    parts = NameParts()
    volume = volume_in_front(segments, text)
    if volume is not None:
        parts.pos = volume
        segments = segments[1:]
    if len(segments) >= 2 and looks_like_author(segments[0], several=len(segments) >= 3):
        parts.author = display_name(segments.pop(0))
    rest = segments
    lead = split_series_title(rest[0])
    if lead.series:
        parts.series, parts.pos, rest[0] = lead.series, lead.pos, lead.title
    elif len(rest) >= 2:
        series = _ONLY_SERIES.match(rest[0])
        if series and not rest[0].isdigit() and len(fold(series.group(1))) >= 2 and not is_generic_series(
                series.group(1)):
            parts.series, parts.pos = series.group(1).strip(" ,"), number_of(series.group(2))
            rest = rest[1:]
    parts.title = rest[0] if rest else ""
    parts.extra = rest[1:]
    if volume is not None:
        parts.title = _YEAR_BRACKET.sub("", parts.title).strip()
    return parts


def authors_in_path(path: str) -> list[str]:
    """The names in a path that are an author's: the name in the file name (``Author - Title``, ``Title - Author``), and
    a folder at the top of the library only when it is written ``Last, First``. A folder called by two capital words is
    far more often a series, a character or a shelf (``Inspector Wolff/``, ``Orchard Tales/``, ``Neue Bücher/``) than a
    person, so it names no author; and no folder further down does (a title looks like a name too often), and none of
    the words a collection is called (``Books``)."""
    parts = [part for part in path.split("/") if part]
    if not parts:
        return []
    stem = spaced(parts[-1].rsplit(".", 1)[0]) if "." in parts[-1] else spaced(parts[-1])
    found: list[str] = []
    segments = [seg.strip() for seg in _SEPARATOR.split(stem) if seg.strip()]
    if len(segments) >= 2:
        if looks_like_author(segments[0], several=len(segments) >= 3):
            found.append(display_name(segments[0]))
        elif looks_like_author(segments[-1], several=False):
            found.append(display_name(segments[-1]))
    if len(parts) >= 2:
        folder = spaced(parts[0])
        if "," in folder and looks_like_author(folder, several=False):
            found.append(display_name(folder))
    return clean_authors(found)


_AUDIO_EXTENSIONS = frozenset({"mp3", "m4a", "m4b", "flac", "ogg", "opus", "aac", "wma", "wav"})
_NAME_YEAR = re.compile(r"\(\s*(1[4-9]\d\d|20\d\d)\s*\)")


def year_in_path(path: str) -> int | None:
    """The year a file name or the folder of the book puts in brackets (``Auris (2019)``): the file name first, then
    the folder the file lies in (for the parts of a recording the folder above ``CD 1`` or ``Audio``). Not the folders
    further up: they hold many books, and a year in one of them (``Neu (2024)/``) is the year of a shelf, not of the
    book. None when there is none that a book can have."""
    from .text import valid_year

    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    looked = [parts[-1]]
    folders = parts[:-1]
    if folders:
        looked.append(folders[-1])
        # The parts of a recording lie in a folder per disc, or one called ``Audio``: the book's folder is the one
        # above.
        is_recording = parts[-1].rsplit(".", 1)[-1].lower() in _AUDIO_EXTENSIONS
        if is_recording:
            book = strip_part_folders("/".join(folders))
            if book and book != "/".join(folders):
                looked.append(book.rsplit("/", 1)[-1])
    for part in looked:
        for value in reversed(_NAME_YEAR.findall(part)):
            if valid_year(int(value)):
                return int(value)
    return None


# ---- The parts of a recording: what a file name says ---------------------------------------------------------------

_PART_WORDS = r"(?:teil|part|cd|disc|disk|track|tr|kapitel|chapter|side)"
_OF = r"(?:v|von|of|aus)"
#: ``1v6``, ``1 von 6``, ``1 of 6``: which part of how many.
_NAME_OF = re.compile(r"(?<![0-9a-z])0*(\d{1,3})\s?" + _OF + r"\s?0*(\d{1,3})(?![0-9a-z])", re.IGNORECASE)
_NAME_WORD_NUMBER = re.compile(r"(?<![a-z])" + _PART_WORDS + r"[\s._-]*0*(\d{1,3})(?![0-9])", re.IGNORECASE)
_NAME_HEAD_NUMBER = re.compile(r"^[\s(\[]*0*(\d{1,3})(?![0-9])")
_TAIL_PART = re.compile(r"[\s._(\[-]*(?:" + _PART_WORDS + r"[\s._-]*)?0*\d{1,3}\s?" + _OF + r"\s?0*\d{1,3}[\s)\]]*$",
                        re.IGNORECASE)
_TAIL_WORD_NUMBER = re.compile(r"[\s._(\[-]+" + _PART_WORDS + r"[\s._-]*0*\d{1,3}[\s)\]]*$", re.IGNORECASE)
_HEAD_PART = re.compile(
    r"^[\s(\[]*(?:" + _PART_WORDS + r"[\s._-]*0*\d{1,3}(?:\s?" + _OF + r"\s?0*\d{1,3})?[\s._)\]-]*"
    r"|0*\d{1,3}\s*[-._)\]]+\s*|0\d{1,3}\s+)", re.IGNORECASE)
_BARE_PART = re.compile(r"^[\s(\[]*(?:" + _PART_WORDS + r"[\s._-]*)?0*\d{1,4}(?:\s?" + _OF + r"\s?0*\d{1,3})?[\s)\]]*$",
                        re.IGNORECASE)
_NOTHING_WORDS = frozenset({"track", "titel", "title", "teil", "part", "disc", "disk", "cd", "kapitel", "chapter",
                            "audio", "hoerbuch", "audiobook", "side", "tr", "unknown", "unbekannt", "untitled"})


def part_number_of(stem: str) -> int | None:
    """Which part of a recording a file name says it is (``Titel-1v6`` is 1, ``Teil 2 von 6`` 2, ``CD3`` 3,
    ``001-Titel`` 1), None when it says none. A number is a part, never a title."""
    text = clean(stem, 300)
    found = _NAME_OF.search(text)
    if found:
        return int(found.group(1))
    found = _NAME_WORD_NUMBER.search(text)
    if found:
        return int(found.group(1))
    found = _NAME_HEAD_NUMBER.match(text)
    if found and (_HEAD_PART.match(text) or _BARE_PART.match(text)):
        return int(found.group(1))
    return None


def part_count_of(stem: str) -> int | None:
    """How many parts a file name says the recording has (``Titel-1v6`` is 6, ``Teil 2 von 6`` 6), None when it
    says none
    or says one."""
    found = _NAME_OF.search(clean(stem, 300))
    if found and int(found.group(2)) >= 2 and 1 <= int(found.group(1)) <= int(found.group(2)):
        return int(found.group(2))
    return None


def strip_part_marks(stem: str) -> str:
    """A file name without the number of the part it is (``Todesrache-1v6`` is ``Todesrache``, ``001-Todesrache`` too,
    ``Titel Teil 2 von 6``); empty when nothing but such a mark is left (``Track 01``, ``CD1``, ``003``)."""
    text = clean(stem, 300)
    if _BARE_PART.match(text) or fold(text) in _NOTHING_WORDS:
        return ""
    previous = None
    while previous != text:
        previous = text
        text = _TAIL_PART.sub("", text)
        text = _TAIL_WORD_NUMBER.sub("", text)
        head = _HEAD_PART.match(text)
        if head and text[head.end():].strip():
            text = text[head.end():]
        text = text.strip(" _.-")
    return "" if fold(text) in _NOTHING_WORDS or _BARE_PART.match(text) else text


# ---- The title of a recording: album tags and folder names
# ------------------------------------------------------------

#: What a tag or a folder name adds to a title that is no part of it: the disc, the carrier, the form. (The variant, a
#: shortened version or a radio play, is told apart elsewhere and stays in the name.)
_ALBUM_NOISE = (r"(?:0*\d{1,3}\s?(?:v|von|of|/)\s?0*\d{1,3}|0*\d{1,2}\s*(?:mp3[\s-]*)?(?:cds?|discs?)|"
                r"mp3[\s-]*cds?|mp3s?|(?:cd|disc|disk)s?[\s.-]*#?\s*0*\d{1,3}(?:\s*(?:von|of|/|-|–)\s*\d{1,3})?|cds?|"
                r"ungek[uü]rzt\w*|unabridged|"
                r"h[oö]rbuch(?:\s*download)?|audio[\s-]?books?|lesung|autorisierte\s+lesefassung|"
                r"(?:teil|part)[\s.-]*0*\d{1,3}\s*(?:von|of|/)\s*\d{1,3})")
_ALBUM_WORD = re.compile(r"(?<![\w])" + _ALBUM_NOISE + r"(?![\w])", re.IGNORECASE)
_ALBUM_BRACKET = re.compile(r"(\s*)([\(\[])([^\)\]]*)([\)\]])")
_ALBUM_TAIL = re.compile(r"[\s,;:|·•\-–—]+" + _ALBUM_NOISE + r"[\s.]*$", re.IGNORECASE)
_ALBUM_HEAD = re.compile(r"^" + _ALBUM_NOISE + r"\s*[:\-–—|]\s+", re.IGNORECASE)
_GENERIC_ALBUMS = frozenset({"", "audio", "track", "titel", "title", "unknown", "unbekannt", "album", "hoerbuch",
                             "audiobook", "cd", "disc", "mp3", "untitled", "unbenannt", "no album", "kein album",
                             "various", "diverse", "lesung", "gesamt", "komplett"})
_GENERIC_ALBUM_NUMBERED = re.compile(r"^(?:track|titel|title|disc|cd|teil|part|kapitel|chapter|album|audio)\s?\d*$")
#: The text of a shop or a series where a title should be: ``Ein Kate-Linville-Thriller, Band 6``.
_BLURB = re.compile(
    r"^(?:ein|eine|einer|a|an|the|der|die|das)\s.{0,80}\b(?:thriller|krimi|roman|novel|saga|serie|series|reihe|trilogie)"
    r"\b|[,;:]\s*(?:band|buch|bd\.?|book|vol\.?|teil|nr\.?)\s*\d{1,3}\s*$", re.IGNORECASE)


def clean_album(text: Any) -> str:
    """A title out of an album tag: without ``MP3CD``, ``CD 2``, ``Teil 1 von 6``, ``(ungekürzt)``, ``Hörbuch``,
    ``Audiobook``, ``Unabridged``. ``Todesrache MP3CD`` is ``Todesrache``. What is only such a word, or nothing, gives
    an empty string. A title that would be taken off whole by its last word stays (``Das Hörbuch``)."""
    value = clean(text, 300)
    previous = None
    while previous != value and value:
        previous = value

        def inside(found: re.Match[str]) -> str:
            inner = _ALBUM_WORD.sub("", found.group(3)).strip(" ,;:-–—|·•")
            return f"{found.group(1)}{found.group(2)}{inner}{found.group(4)}" if inner else ""

        value = _ALBUM_BRACKET.sub(inside, value)
        head = _ALBUM_HEAD.match(value)
        if head and value[head.end():].strip():
            value = value[head.end():]
        tail = _ALBUM_TAIL.search(value)
        if tail:
            left = value[: tail.start()].split()
            if left and fold(left[-1]) not in _TAKES_FORM and fold(left[-1]) not in _ARTICLES:
                value = value[: tail.start()]
        value = value.strip(" ,;:-–—|·•")
    if _ALBUM_WORD.fullmatch(value or ""):
        return ""
    return value


def album_is_generic(album: str) -> bool:
    """Whether a (cleaned) album names no book: ``Audiobook``, ``Track 1``, ``Unknown``, nothing."""
    key = fold(album)
    return key in _GENERIC_ALBUMS or bool(_GENERIC_ALBUM_NUMBERED.match(key))


def album_is_blurb(album: str) -> bool:
    """Whether an album tag is the text of a shop or a series and no title (``Ein Kate-Linville-Thriller, Band 6``)."""
    return bool(_BLURB.search(clean(album, 300)))


#: A title as a catalogue sorts it: the article behind it (``Frage der Nebelbahn, eine``).
_ARTICLE_BEHIND = re.compile(
    r"^(?P<title>.+?)\s*,\s*(?P<article>der|die|das|ein|eine|the|a|an|le|la|les|el|il|een|het)\.?$", re.IGNORECASE)


def article_in_front(title: str) -> str:
    """A title with its article where it is read (``Eine Frage der Nebelbahn``); a title that has one in front as well
    (``Die Frau, die``) is left as it is."""
    found = _ARTICLE_BEHIND.match(title.strip())
    if found is None or fold(found["title"].split()[0]) in _ARTICLES:
        return title
    article = found["article"]
    return f"{article[0].upper()}{article[1:].lower()} {found['title']}"


def title_fits(title: str, other: str) -> bool:
    """Whether two titles can be the same book's: the cores alike, or the shorter one stands in the longer one as
    whole words (``Todesrache`` in ``Todesrache - Das Finale``)."""
    ka, kb = compare_key(title), compare_key(other)
    if not ka or not kb:
        return False
    if ka == kb or titles_alike(title, other, 0.8):
        return True
    wa, wb = ka.split(), kb.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    count = len(short)
    return any(long_[i:i + count] == short for i in range(len(long_) - count + 1))


# ---- The folder of a book -------------------------------------------------------------------------------------------

#: A folder that begins with its number in a collection: ``1.-.Teilname``, ``02 - Titel``, ``3. Titel``, ``04 Titel``. A
#: bare number and a space (``12 Monkeys``) is a title.
_NUMBERED = re.compile(r"^0*(\d{1,3})(?:[\s._]*[-–—:.)][\s._]*|(?<=0\d)[\s_]+(?=\S))(?=\S)")
_SERIES_TAIL = re.compile(
    r"^(.+?)[\s,]+(?:(?:Bd\.?|Band|Buch|Teil|Book|Vol\.?|Volume|Nr\.?|No\.?|#)\s*)?(\d{1,3}(?:[.,]\d{1,2})?)$",
    re.IGNORECASE)


@dataclass
class FolderBook:
    """What the folder of a book says: ``Titel - Reihe 6 (2020)`` is the title, the series, the volume and the year."""

    title: str = ""
    author: str = ""
    series: str = ""
    pos: float | None = None
    year: int | None = None
    #: The folder is written the way a tool or a careful person names the folder of a book (a year in brackets, a series
    #: after the title, a number in a collection): its title is worth more than a tag that says something else.
    structured: bool = False


def _plain_folder_title(name: str) -> str:
    text = _YEAR_BRACKET.sub("", spaced(name)).strip()
    return strip_noise(text) or text


def folder_book(name: str, *, depth: int = 1, parent: str = "") -> FolderBook:
    """What the name of the folder of a book says. ``depth`` is how many folders the path of the book's folder has
    (``Autor/Titel`` is 2): from the second one up, the folder above holds the author or a series, and a title is
    expected in this one. Nothing is made up: a name that says nothing yields only a title."""
    from .text import valid_year

    raw = clean(name, 300)
    result = FolderBook()
    years = [int(value) for value in _YEAR_BRACKET.findall(spaced(raw))]
    result.year = next((year for year in reversed(years) if valid_year(year)), None)
    nested = depth >= 2
    number = _NUMBERED.match(raw) if nested else None
    long_number = None if number or not nested or not years else re.match(r"^(\d{4,5})\s+[-–—]\s+(?=\S)", raw)
    if long_number and volume_in_front(_SEPARATOR.split(raw, maxsplit=1), raw) is None:
        long_number = None
    number = number or long_number
    if number:
        rest = raw[number.end():]
        if " " not in rest.strip():
            rest = re.sub(r"[._]+", " ", rest).strip()
        result.pos = number_of(number.group(1))
        result.title = _plain_folder_title(rest)
        result.structured = True
        if depth >= 3 and parent:
            # The folder of a box holds the books in numbered folders: the name of the box is no series (``Auris 5 -
            # Tödlicher Schall`` is the box of the books of the series ``Auris``); the series is what the box name says
            # as one, else the box name.
            box = folder_book(parent, depth=depth - 1)
            result.series = box.series or box.title
        return result
    text = _YEAR_BRACKET.sub("", spaced(raw)).strip()
    segments = [seg.strip() for seg in _SEPARATOR.split(text) if seg.strip()]
    if len(segments) == 3 and (nested or result.year):
        # ``Schändung - Carl Mørck, Sonderdezernat Q - Band 2``: the title, the series, and which volume; or ``Titel -
        # Untertitel - Reihe 1``, the series named at the front of the title.
        again = split_suffix_series(" - ".join(segments))
        if again is not None:
            result.title = strip_noise(again.title) or again.title
            result.series, result.pos = again.series, again.pos
            result.structured = True
            return result
        marked = re.fullmatch(r"(?:" + _VOLUME_WORD + r")\s*(\d{1,3})", segments[2], re.IGNORECASE)
        if marked is not None and len(fold(segments[1])) >= 2 and not is_generic_series(segments[1]):
            result.title = strip_noise(segments[0]) or segments[0]
            result.series, result.pos = segments[1].strip(" ,"), number_of(marked.group(1))
            result.structured = True
            return result
    if len(segments) == 2 and (nested or result.year):
        ordinal = ordinal_series(segments[1])
        if ordinal is not None:
            # ``Bretonischer Ruhm - Kommissar Dupins zwölfter Fall``: the case of the series says which volume it is.
            result.title = strip_noise(segments[0]) or segments[0]
            result.series, result.pos = ordinal
            result.structured = True
            return result
        tail = _SERIES_TAIL.match(segments[1])
        if tail and len(fold(tail.group(1))) >= 2 and not tail.group(1).strip().isdigit():
            result.title = strip_noise(segments[0]) or segments[0]
            result.structured = True
            if is_generic_series(tail.group(1)):
                # ``Titel - Teil 2``, ``Titel - Band 3``: a volume and no series; ``Titel - CD 2`` is a disc, not even
                # a volume.
                if fold(tail.group(1)) not in _DISC_WORDS:
                    result.pos = number_of(tail.group(2))
                return result
            result.series = tail.group(1).strip(" ,#-–")
            result.pos = number_of(tail.group(2))
            return result
    if result.year is not None:
        result.structured = True
    parts = parse_name(text)
    if parts.author:
        result.author = parts.author
    if parts.series:
        result.series, result.pos = parts.series, parts.pos
    result.title = strip_noise(parts.title) or parts.title
    if len(segments) > 1 and parts.extra and not parts.author and not parts.series:
        # "Titel - Untertitel": the whole name stays the title, the core of it is found by whoever needs it.
        result.title = strip_noise(text) or text
    return result


def top_folder_author(path: str) -> str:
    """The author a book's path names by its top folder: ``Autor/Titel/Audio/x.mp3`` is ``Autor``. Only when the book
    lies at least two folders deep (``Autor/Titel``) and the top folder is written like a person: ``Vorname Nachname``
    or ``Nachname, Vorname``. A shelf (``Hörbücher``, ``Books``) is none."""
    parts = [part for part in path.split("/") if part]
    folders = strip_part_folders("/".join(parts[:-1])).split("/") if len(parts) >= 2 else []
    if len(folders) < 2:
        return ""
    top = spaced(folders[0])
    if not looks_like_author(top, several=False) or is_cd_folder(top) or is_company(top):
        return ""
    # A folder of a series, a box or a shelf looks like a name often enough (``Star Wars``, ``Spiegel Bestseller``): a
    # word of such a folder, or numbered folders below it (``Reihe Eins/1.-.Anfang``), say it is none. In doubt: none.
    if any(word in _COLLECTION_WORDS for word in key_fold(top).split()):
        return ""
    if _COLLECTION_NUMBERED.match(folders[1]):
        return ""
    names = clean_authors([display_name(top)])
    return names[0] if names else ""


#: A numbered folder of a collection the way a download names it (``1.-.Anfang``, ``02.-.Titel``): the folder above it
#: holds a series or a box. (``02 - Titel`` below an author's name is the author's second book.)
_COLLECTION_NUMBERED = re.compile(r"^0*\d{1,3}\.-\.\S")
#: Words of a folder that holds a series, a box, a shelf or a studio, and no person (folded, as ``key_fold`` writes).
_COLLECTION_WORDS = frozenset({
    "saga", "reihe", "serie", "series", "box", "boxset", "collection", "edition", "sammlung", "staffel", "folgen",
    "falle", "trilogie", "trilogy", "chroniken", "chronicles", "wars", "trek", "bestseller", "bestsellers", "spiegel",
    "horspiele", "horspiel", "studios", "studio", "verlag", "records", "productions", "presents", "universe",
    "abenteuer", "adventures", "club", "bande", "files", "akte", "akten", "geschichten", "stories", "tales", "marchen",
    "classics", "klassiker", "ordner", "neu", "neue", "new", "favoriten", "favorites", "gelesen", "ungelesen"})
