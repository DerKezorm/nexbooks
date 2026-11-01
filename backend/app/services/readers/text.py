"""Text out of files: cleaned, repaired where a name or tag was decoded wrongly, folded for comparing.

Nothing here trusts its input: a field may be any type, any length, hold control characters or HTML. The result is a
short plain string.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

from .limits import DESCRIPTION_MAX, HTML_MAX

#: Control characters, zero-width and direction marks, the byte order mark, the mark of a failed decoding, and the lone
#: surrogates (0xD800 to 0xDFFF): they are no characters and cannot be written as UTF-8, so one in a tag would break the
#: database write of the whole file. (Written as numbers: the characters themselves do not survive every tool.)
_INVISIBLE = ((0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0xFEFF, 0xFEFF), (0xFFFD, 0xFFFD),
              (0xD800, 0xDFFF))
_CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _INVISIBLE) + "]")
_SPACES = re.compile(r"\s+")

#: UTF-8 bytes read as if they were code page 437 (what a share or an unpacker does to names) or Windows-1252: the
#: letters that betray it. A plain "Ä" or "ä" is no sign (German has them); "Γ" (the first byte of "•", "–" and of
#: quotation marks) and "Ã" (the first byte of every umlaut) followed by a symbol are.
_MOJIBAKE = re.compile(
    f"{chr(0x393)}[{chr(0xC7)}{chr(0xFC)}{chr(0xE9)}{chr(0xC9)}]|{chr(0xC3)}[{chr(0x80)}-{chr(0xBF)}{chr(0x152)}{chr(0x153)}"
    f"{chr(0x178)}{chr(0x201E)}]|{chr(0xE2)}{chr(0x20AC)}|{chr(0xC2)}[{chr(0xA0)}-{chr(0xBF)}]"
)

def _chars(*codes: int) -> str:
    return "".join(map(chr, codes))


#: What is left when a mixed name cannot be repaired as a whole: the sequences seen most (written as numbers, because
#: they are exactly the characters that break when a file is read with the wrong code page).
_KNOWN = {
    # Code page 437: 0xE2 0x80 0xXX of UTF-8 as "G", "C-cedilla", and the character of the third byte.
    _chars(0x393, 0xC7, 0xF3): _chars(0x2022), _chars(0x393, 0xC7, 0xF4): _chars(0x2013),
    _chars(0x393, 0xC7, 0xF6): _chars(0x2014), _chars(0x393, 0xC7, 0xD6): _chars(0x2019),
    _chars(0x393, 0xC7, 0xFF): _chars(0x2018), _chars(0x393, 0xC7, 0xA3): _chars(0x201C),
    _chars(0x393, 0xC7, 0xA5): _chars(0x201D), _chars(0x393, 0xC7, 0xAA): _chars(0x2026),
    _chars(0x393, 0xC7, 0xAB): _chars(0xAB), _chars(0x393, 0xC7, 0xBB): _chars(0xBB),
    _chars(0x393, 0xE9, 0xBC): _chars(0x20AC),
    # Windows-1252.
    _chars(0xE2, 0x20AC, 0xA2): _chars(0x2022), _chars(0xE2, 0x20AC, 0x2013): _chars(0x2013),
    _chars(0xE2, 0x20AC, 0x2014): _chars(0x2014), _chars(0xE2, 0x20AC, 0x2122): _chars(0x2019),
    _chars(0xE2, 0x20AC, 0x2DC): _chars(0x2018), _chars(0xE2, 0x20AC, 0x153): _chars(0x201C),
    _chars(0xE2, 0x20AC, 0x9D): _chars(0x201D), _chars(0xE2, 0x20AC, 0xA6): _chars(0x2026),
}


_SURROGATES = re.compile(f"[{chr(0xD800)}-{chr(0xDFFF)}]")


def scrub(value: Any) -> Any:
    """``value`` with every lone surrogate taken out of every string in it (also dict keys), whatever the nesting. The
    last net for what a reader returns: a text that cannot be written as UTF-8 never reaches the database."""
    if isinstance(value, str):
        return _SURROGATES.sub("", value) if _SURROGATES.search(value) else value
    if isinstance(value, dict):
        return {scrub(key): scrub(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(item) for item in value]
    return value


def repair(text: str) -> str:
    """A name or tag whose bytes were decoded with the wrong code page, put right where that is certain. A string that
    shows no sign stays as it is. What cannot be repaired loses the broken sequences instead of showing them."""
    if not _MOJIBAKE.search(text):
        return text
    for encoding in ("cp437", "cp1252", "latin-1"):
        try:
            fixed = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != text and not _MOJIBAKE.search(fixed):
            return fixed
    for broken, right in _KNOWN.items():
        text = text.replace(broken, right)
    return _MOJIBAKE.sub("", text) if _MOJIBAKE.search(text) else text


def clean(value: Any, limit: int = 300) -> str:
    """``value`` as one line of plain text of at most ``limit`` characters: control characters and the marks of a failed
    decoding out, a wrong code page put right, white space folded."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFC", repair(value[: limit * 4]))
    text = _SPACES.sub(" ", _CONTROL.sub("", text)).strip()
    return text[:limit].rstrip()


_FOLD = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l", "ð": "d", "þ": "th", "ı": "i",
                       # (``13½`` is ``13 1/2``: a fraction is a number of its own, not digits of the one before.)
                       "½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅓": " 1/3", "⅔": " 2/3"})
_NOT_WORD = re.compile(r"[^0-9a-z]+")


#: The German umlauts written with two letters, the way ``key_fold`` writes them.
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def key_fold(text: str) -> str:
    """``fold`` for the keys that tell whether two titles or two names are the same: an umlaut is the same as its
    spelling with two letters (``Süden`` and ``Sueden`` are one word, ``Straße`` and ``Strasse`` too). The umlaut is
    written out, the letters are never taken apart: ``Michael`` and ``Michal``, ``Neue`` and ``Neu`` stay two words.
    Only for comparing: nothing shown is written this way."""
    return fold(unicodedata.normalize("NFC", text).translate(_UMLAUTS))


#: What whoever copied the files wrote into a comment: ``upped by``, ``ripped by``, ``encoded with LAME 3.99``, an
#: address. Such a text is never the description of a book.
_RELEASE_NOTE = re.compile(
    r"\b(?:upped|uploaded|ripped|encoded|re-?encoded|converted|tagged|scanned|digitized|digitalisiert|released|"
    r"brought\s+to\s+you|bereitgestellt|hochgeladen|gerippt)\s+(?:by|with|von|mit)\b|"
    r"\b(?:lame|ffmpeg|itunes|openaudible|inaudible|audiobookconverter|mp3tag)\s*v?\d|"
    r"https?://|\bwww\.|\b[\w-]+\.(?:com|net|org|to|cc|info|biz|ws|ru|de)\b|"
    r"\bsupport\s+the\s+author\b|\bunterst[uü]tz(?:t|e)\s+(?:den|die)\s+autor", re.IGNORECASE)
#: A sentence: a word, then the end of the sentence.
_SENTENCE_END = re.compile(r"[^\W\d_][\"'»«”)]*[.!?…](?:\s|$)")


def release_text(text: str, *, strict: bool = False) -> bool:
    """Whether a text is the note of whoever copied the files and no description of the book: it names the copying
    (``upped by``, ``encoded with LAME``), an address, a release group. ``strict`` (a comment tag, the file beside the
    book) also takes a short line without a sentence for one (``TeamXY 2019``, ``Kapitel 1``): a description says
    something in sentences."""
    value = " ".join(str(text or "").split())
    if not value:
        return False
    if _RELEASE_NOTE.search(value):
        return True
    if strict:
        words = value.split()
        if len(value) < 60 or (not _SENTENCE_END.search(value) and len(words) < 15):
            return True
    return False


def fold(text: str) -> str:
    """The text as comparing wants it: lower case, no accents or umlauts (ä is a), ß as ss, only letters and digits,
    single spaces. "Müller, Hans" and "muller hans" are the same here."""
    lowered = unicodedata.normalize("NFKD", text.casefold().translate(_FOLD))
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return _NOT_WORD.sub(" ", stripped).strip()


# Between two paragraphs of a description the text keeps one empty line ("\n\n"). A single line break inside a paragraph
# is the wrap of the source (a file wrapped at 76 characters), which the page sets as a space; the empty line is the
# only thing that starts a paragraph.
_PARAGRAPH = "\x1f"
_BLANK_LINES = re.compile(r"\n[ \t\xa0]*\n(?:[ \t\xa0]*\n)*")


class _Plain(HTMLParser):
    """The text of a piece of HTML: tags dropped, a block (or a line break) starts a new paragraph, and the line breaks
    inside the text are spaces, as HTML reads them. Scripts and styles are not text. HTML.parser expands no entity
    declarations and fetches nothing."""

    BLOCKS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append(_PARAGRAPH)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCKS:
            self.parts.append(_PARAGRAPH)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append(_PARAGRAPH)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data.replace("\r", " ").replace("\n", " "))


def _lines(paragraph: str) -> str:
    """One paragraph: its lines trimmed and joined by a single line break, empty lines gone."""
    lines = [_SPACES.sub(" ", _CONTROL.sub("", line)).strip() for line in paragraph.split("\n")]
    return "\n".join(line for line in lines if line)


def html_to_text(value: Any, limit: int = DESCRIPTION_MAX) -> str:
    """A description that may be HTML (the usual case: Calibre wraps it in ``<p>``) as plain text: paragraphs apart by
    an empty line; a line break inside a paragraph is the source's own wrap."""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value[:HTML_MAX]
    if "<" in value and ">" in value:
        parser = _Plain()
        try:
            parser.feed(value)
            parser.close()
            value = "".join(parser.parts)
        except Exception:  # noqa: BLE001 - html.parser is forgiving, but whatever it throws, the text goes on without
            value = re.sub(r"<[^>]*>", _PARAGRAPH, value)
        parts = value.split(_PARAGRAPH)
    else:
        # Plain text: an empty line (a line with nothing but blanks) starts a paragraph.
        parts = _BLANK_LINES.split(unescape(value).replace("\r\n", "\n").replace("\r", "\n"))
    text = unicodedata.normalize("NFC", repair("\n\n".join(part for part in (_lines(item) for item in parts) if part)))
    return text[:limit].rstrip()


#: The first year a printed book can have (the press of Gutenberg), and how far past this year a date may lie (a book
#: announced for next year is real; a date beyond that is a mistake of the file).
FIRST_YEAR = 1450
YEARS_AHEAD = 1


def valid_year(year: int | None, this_year: int | None = None) -> bool:
    """Whether a year is one a book can have: not before ``FIRST_YEAR`` and not past next year. Calibre writes
    ``0101-01-01`` for a date it does not know, which is year 101, and some files carry a year like 2105 by a typing
    mistake: both are no year."""
    if not isinstance(year, int) or isinstance(year, bool):
        return False
    latest = (this_year if this_year is not None else datetime.now(UTC).year) + YEARS_AHEAD
    return FIRST_YEAR <= year <= latest


_ISO_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?![\d])")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _real_calendar_date(year: int, month: int | None, day: int | None) -> bool:
    """Whether a year, a month and a day make a date that exists (no month 13, no 31st of April)."""
    import calendar

    if month is None:
        return True
    if not 1 <= month <= 12:
        return False
    return day is None or 1 <= day <= calendar.monthrange(max(year, 1), month)[1]


def _parts_of(text: str) -> tuple[int, int | None, int | None] | None:
    """``(year, month, day)`` of a text that starts with a date (``2017``, ``2017-05``, ``2017-05-24``, ``20170524``),
    None when it does not, or when the date is none that exists."""
    found = _ISO_DATE.match(text)
    compact = None if found else _COMPACT_DATE.match(text)
    match = found or compact
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if not _real_calendar_date(year, month, day):
        return None
    return year, month, day


def year_of(value: Any) -> int | None:
    """The year in a date like ``2003-02-24T23:00:00+00:00`` or ``2017``, or None (also for a year no book can have:
    ``0101-01-01`` is Calibre's mark for an unknown date, and for a date that does not exist, ``2015-13-45``)."""
    text = str(value or "").strip()
    if re.match(r"^\d{4}-\d{2}", text) or _COMPACT_DATE.match(text):
        parts = _parts_of(text)
        return parts[0] if parts and valid_year(parts[0]) else None
    match = re.search(r"(?<!\d)(14[5-9]\d|1[5-9]\d\d|20\d\d)(?!\d)", text)
    if match is None:
        return None
    year = int(match.group(1))
    return year if valid_year(year) else None


def date_of(value: Any, limit: int = 10) -> str:
    """A date as the file wrote it (``2017``, ``2017-05``, ``2017-05-24``) when its year is a real one and the date
    exists, else an empty string. At most ``limit`` characters."""
    text = clean(value, 40)
    parts = _parts_of(text)
    if parts is None or not valid_year(parts[0]):
        return ""
    year, month, day = parts
    return "-".join([f"{year:04d}", *([f"{month:02d}"] if month else []), *([f"{day:02d}"] if day else [])])[:limit]


def unescape_deep(value: str) -> str:
    """A text with its HTML entities resolved, also when they were encoded twice (``Krimis &amp;amp; Thriller`` is
    ``Krimis & Thriller``): the file of a library goes through more than one tool."""
    for _round in range(3):
        resolved = unescape(value)
        if resolved == value:
            break
        value = resolved
    return value


_LANGUAGES = {
    "deu": "de", "ger": "de", "eng": "en", "fra": "fr", "fre": "fr", "spa": "es", "ita": "it", "nld": "nl", "dut": "nl",
    "por": "pt", "swe": "sv", "dan": "da", "nor": "no", "nob": "no", "fin": "fi", "pol": "pl", "ces": "cs", "cze": "cs",
    "rus": "ru", "jpn": "ja", "zho": "zh", "chi": "zh", "tur": "tr", "hun": "hu", "ell": "el", "gre": "el", "lat": "la",
}
_LANGUAGE_NAMES = {
    "deutsch": "de", "german": "de", "english": "en", "englisch": "en", "french": "fr", "französisch": "fr",
    "spanish": "es", "spanisch": "es", "italian": "it", "italienisch": "it", "dutch": "nl",
    "niederländisch": "nl",
}


def language_of(value: Any) -> str:
    """A language as a code of two or three letters in lower case: ``de-DE`` is ``de``, ``deu`` is ``de``, ``Deutsch``
    and ``German`` are ``de``. Empty when it is none of these."""
    text = clean(value, 40).casefold().replace("_", "-")
    if not text:
        return ""
    if text in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[text]
    primary = text.split("-")[0]
    if primary in _LANGUAGES:
        return _LANGUAGES[primary]
    if re.fullmatch(r"[a-z]{2}", primary):
        return primary
    return ""


def isbn13(value: Any) -> str:
    """An ISBN as 13 digits (an ISBN-10 converted), or an empty string for what is no valid ISBN. Only a value that
    consists of nothing but an ISBN (hyphens, spaces and a prefix like ``urn:isbn:`` allowed) counts: a number
    picked out of a longer identifier (a UUID has digits) is no ISBN."""
    raw = str(value or "").strip().lower()
    for prefix in ("urn:isbn:", "isbn:", "isbn"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    digits = raw.replace("-", "").replace(" ", "")
    if re.fullmatch(r"\d{9}[\dx]", digits):
        total = sum((10 - i) * (10 if c == "x" else int(c)) for i, c in enumerate(digits))
        if total % 11:
            return ""
        core = "978" + digits[:9]
        check = (10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(core)) % 10) % 10
        return core + str(check)
    if re.fullmatch(r"\d{13}", digits) and digits[:3] in ("978", "979"):
        total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits[:12]))
        if (10 - total % 10) % 10 == int(digits[12]):
            return digits
    return ""


# ---- The language of a text ------------------------------------------------------------------------------------

#: The most common short words of some languages. A simple test, enough to tell a German description from an English
#: one: the share of a text's words that a language's list holds, and the language that leads clearly wins.
_STOP_WORDS = {
    "de": (
        "der die das und ist nicht ein eine zu mit von den auf für sich dem im es auch als wie aber hat sein "
        "werden wird sie er ich war dass nach bei nur noch einem einer eines dieser diese dieses über um wenn"
    ),
    "en": (
        "the and of to in is that it for was on with as his her he she be at by this had from or are not but they "
        "which have an you all were their one when who will"
    ),
    "fr": (
        "le la les des est une un et en du que pour dans qui pas sur au avec ce il elle sont plus par mais ses son "
        "leur nous vous cette ont été comme aux"
    ),
    "es": (
        "el la los las de que y en un una es por con para su se no al lo como más pero sus le ya o este sí porque "
        "esta entre cuando muy sin sobre también"
    ),
    "it": (
        "il lo la le di che e un una per con non sono del della dei gli nel più ma suo sua come anche questo "
        "questa tra fra era essere"
    ),
    "nl": (
        "de het een en van is dat op te voor met zijn niet aan er maar om ook als bij of dit die wordt werd naar "
        "uit door hij zij"
    ),
}
#: A text with fewer words than this is too short to say which language it is.
_LANGUAGE_MIN_WORDS = 12


def guess_language(text: str) -> str:
    """The two letters of the language a text is written in (``de``, ``en``, ``fr``, ``es``, ``it``, ``nl``), or an
    empty string when it is too short or no language leads clearly. Counts the common short words; no more is
    claimed."""
    words = re.findall(r"[^\W\d_]+", text.casefold())[:300]
    if len(words) < _LANGUAGE_MIN_WORDS:
        return ""
    shares = {language: sum(1 for word in words if word in listed) / len(words)
              for language, listed in ((name, set(stop.split())) for name, stop in _STOP_WORDS.items())}
    ranked = sorted(shares.items(), key=lambda item: -item[1])
    (best, top), (_second, next_best) = ranked[0], ranked[1]
    return best if top >= 0.12 and top >= next_best * 1.5 else ""


#: What ``sample_language`` says of a text in two languages, and of one in a language it has no words for.
MIXED_TEXT, OTHER_TEXT = "mixed", "other"
#: How many words a sample needs before a lack of every known short word counts (headings, a title page and a list
#: hold none of them in any language).
_OTHER_MIN_WORDS = 40


#: Languages whose text the short words tell apart from the languages next to them (``sample_language`` reads
#: Portuguese, Catalan and Galician as Spanish, Afrikaans as Dutch): only these may outweigh what a package says.
CLEAR_LANGUAGES = frozenset({"de", "en", "fr", "it"})


def clear_sample_language(text: str) -> str:
    """The language of a sample when it leads by far (at least 15 % of the words and three times the share of the
    next one) and is one ``CLEAR_LANGUAGES`` holds; empty else. What may overrule the language a package states."""
    words = re.findall(r"[^\W\d_]+", text.casefold())[:300]
    if len(words) < _OTHER_MIN_WORDS:
        return ""
    shares = sorted((sum(1 for word in words if word in set(stop.split())) / len(words), language)
                    for language, stop in _STOP_WORDS.items())
    (next_best, _second), (top, best) = shares[-2], shares[-1]
    return best if best in CLEAR_LANGUAGES and top >= 0.15 and top >= next_best * 3 else ""


def sample_language(text: str) -> str:
    """The language of a sample of a book's text, for the check after a download: like ``guess_language``, and
    ``mixed`` when a second language holds at least 40 % of the share of the first (a book in two languages),
    ``other`` when the sample is long enough and none of the languages nexbooks knows the words of is in it (a book in
    Portuguese, Swedish, Polish): then the text is a witness against every language it knows, not a silent yes."""
    words = re.findall(r"[^\W\d_]+", text.casefold())[:300]
    if len(words) < _LANGUAGE_MIN_WORDS:
        return ""
    shares = {language: sum(1 for word in words if word in listed) / len(words)
              for language, listed in ((name, set(stop.split())) for name, stop in _STOP_WORDS.items())}
    ranked = sorted(shares.items(), key=lambda item: -item[1])
    (best, top), (_second, next_best) = ranked[0], ranked[1]
    if top < 0.03:
        return OTHER_TEXT if len(words) >= _OTHER_MIN_WORDS else ""
    if top >= 0.08 and next_best >= 0.05 and next_best >= top * 0.4:
        return MIXED_TEXT
    return best if top >= 0.12 and top >= next_best * 1.5 else ""


def text_fits_language(text: str, language: str) -> bool:
    """Whether a text can be written in ``language`` (two letters): true when either is not known, or the text is too
    short to tell, or the language of the text is the one asked; false when the text is clearly another language. A
    description from a catalogue that belongs to another book in another language is dropped by this."""
    if not text or not language:
        return True
    found = guess_language(text)
    return not found or found == language[:2].lower()
