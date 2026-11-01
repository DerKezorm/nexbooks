"""What the name of a release on an indexer says about the book in it. Knows nothing of the book that is
looked for: ``release_match`` holds a name against a book.

What the measurements taught and what the names look like:

* **Three ways of writing a name.** Words with dots (``Rune.Falkner.-.Der.Nebelchor.GERMAN.EPUB-GROUP``), words with
  spaces and dashes (``Rune Falkner - Nebelchor 03 - Die Brandung``), and the scene form of audiobooks with dashes
  between the fields (``Rune Falkner-Die Brandung-MP3CD-DE-2021-GROUP``). A dot between two digits stays (``44.1KHZ``).
* **The language** is said by a mark (``GERMAN``, ``DE``, ``FRENCH``, ``(NL)``, ``SPANiSH``) or by words of a language
  (``Hörbuch``, ``ungekürzt``, ``Luisterboek``, ``Livre audio``). English is hardly ever marked: a name without a mark
  says nothing, and nothing here presumes a language. A two-letter code counts only in capitals and away from the start
  of the name (``ES`` and ``IT`` are titles of books too), or in brackets.
* **E-book or audiobook** by its formats and words (``EPUB``, ``eBook``; ``AudioBook``, ``ABook``, ``Hörbuch``,
  ``Luisterboek``, ``M4B``, ``MP3``, ``FLAC``, ``6CD``, ``16BIT``); a name that says neither is decided by its category
  and its size, which the caller knows.
* **Abridged** (``gekürzt``, ``abridged``) and **unabridged** (``ungekürzt``, ``onverkort``); a **dramatisation**
  (``Hörspiel``, ``full cast``, ``audio drama``) is no reading, even an "ungekürztes Hörspiel".
* **A bundle**: a range of volumes (``1-5``, ``Band 01 - 05``, ``Folge 1-4``), words like ``Box``, ``Collection``,
  ``Gesamtausgabe``, ``Trilogie``, weekly packs (``KW 41``). **A sample** (``Leseprobe``), a **soundtrack**, a
  **companion** (``Begleitbuch``, ``study guide``) are no book either.
* **Volumes** are numbers after a word for a volume (``Band 3``, ``Teil 3``, ``Folge 3``, ``Book 3``, ``#3``, ``E003``)
  or with a leading zero after a name (``Kate Linville 04``). A bare number is part of a title (``Opfer 2117``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import book_words
from ..readers.text import clean, fold

NAME_MAX = 400

EBOOK_FORMATS = ("epub", "azw3", "mobi", "pdf")
AUDIO_FORMATS = ("flac", "m4b", "aac", "mp3", "ogg", "opus", "wma")

#: Words of a language: a mark (sure) by the code it names.
_LANGUAGE_MARKS: dict[str, tuple[str, ...]] = {
    "de": ("german", "deutsch", "deutsche", "duitstalig", "allemand", "aleman"),
    "en": ("english", "englisch", "engelstalig", "engels", "anglais"),
    "fr": ("french", "francais", "franzosisch", "franstalig"),
    "es": ("spanish", "espanol", "castellano", "spanisch", "spaanstalig"),
    "it": ("italian", "italiano", "italienisch"),
    "nl": ("dutch", "nederlands", "nederlandstalig", "flemish", "vlaams", "hollandisch"),
    "sv": ("swedish", "svenska", "schwedisch"),
    "no": ("norwegian", "norsk", "norwegisch"),
    "da": ("danish", "dansk", "danisch"),
    "fi": ("finnish", "suomi", "finnisch"),
    "pl": ("polish", "polski", "polnisch"),
    "pt": ("portuguese", "portugues", "portugiesisch"),
    "ru": ("russian", "russkij", "russisch"),
}
#: Codes of three letters that are marks in capitals (``GER``, ``ENG``): words of their own in a scene name.
_THREE_LETTER: dict[str, str] = {"GER": "de", "ENG": "en", "FRA": "fr", "FRE": "fr", "SPA": "es", "ESP": "es",
                                 "ITA": "it", "NLD": "nl", "DUT": "nl", "SWE": "sv", "NOR": "no", "DAN": "da",
                                 "FIN": "fi", "POL": "pl", "POR": "pt", "RUS": "ru"}
#: Two-letter codes: in capitals after the start of the name, or in brackets anywhere (``(NL)``).
_TWO_LETTER = frozenset({"DE", "EN", "FR", "ES", "IT", "NL", "SV", "SE", "NO", "DA", "DK", "FI", "PL", "PT", "RU"})
_TWO_LETTER_CODE = {"SE": "sv", "DK": "da"}
#: Words that are said in one language only: a reading in that language (not as sure as a mark, sure enough).
_LANGUAGE_WORDS: dict[str, tuple[str, ...]] = {
    "de": ("horbuch", "hoerbuch", "horbucher", "hoerbuecher", "horspiel", "hoerspiel", "ungekurzt", "ungekuerzt",
           "ungekurzte", "ungekuerzte", "gekurzt", "gekuerzt", "gekurzte", "gekuerzte", "lesung", "autorenlesung",
           "kriminalroman", "liest", "gelesen", "horprobe", "hoerprobe", "leseprobe", "hoerspielserie",
           "horspielserie"),
    "nl": ("luisterboek", "luisterboeken", "onverkort", "hoorspel", "voorgelezen"),
    "fr": ("livre audio", "lu par", "texte integral"),
    "sv": ("ljudbok", "ljudbocker", "uppläsare"),
    "no": ("lydbok", "lydboker"),
    "da": ("lydbog", "lydboger"),
    "fi": ("aanikirja",),
    "en": ("a novel", "narrated by", "read by", "a thriller", "a mystery", "a memoir"),
}

_AUDIO_WORDS = frozenset({"audiobook", "audiobooks", "abook", "audio", "horbuch", "hoerbuch", "horbucher",
                          "hoerbuecher", "luisterboek", "luisterboeken", "audiolibro", "audiolivro", "ljudbok",
                          "lydbok", "lydbog", "aanikirja", "hoorspel", "horspiel", "hoerspiel", "lesung",
                          "autorenlesung", "mp3cd", "audiobuch", "narrated", "unabridged", "ungekurzt", "ungekuerzt",
                          "onverkort", "vorgelesen", "liest"})
_AUDIO_TECH = re.compile(r"^(?:\d{1,2}(?:mp3)?cds?|mp3cds?|cds|\d{2}bit|\d{2}(?:\.\d)?khz|\d{2,3}kbps|\d{2,3}kbit|"
                         r"\d{2,3}k|kbps|kbit|khz|vbr|cbr|aac|m4b|m4a|mp3|flac|ogg|opus|wma|cd)$", re.IGNORECASE)
_EBOOK_WORDS = frozenset({"ebook", "ebooks", "e book", "epub", "mobi", "azw3", "azw", "kf8", "pdf", "kindle", "retail"})
#: Tags of the scene that say nothing about the book.
_NEUTRAL_TAGS = frozenset({"web", "retail", "proper", "repack", "internal", "nfo", "readnfo", "read nfo", "dirfix",
                           "nzb", "rar", "scan", "ocr", "true", "hq", "lq", "complete", "uk", "us", "int", "mod",
                           "fixed", "update", "ebooks", "ebook", "audiobook", "audiobooks", "abook", "audio",
                           "multiformat", "mp3", "flac", "aac", "m4b", "m4a", "epub", "mobi", "azw3", "azw", "pdf",
                           "kf8", "vbr", "cbr", "cd", "mp3cd", "kindle", "lit", "rtf", "docx", "txt", "cbz",
                           "ogg", "opus", "wma", "german", "english", "french", "spanish", "dutch", "italian",
                           "swedish", "deutsch", "edition"})

#: "Autorisierte Lesefassung" is how German publishers name an abridged reading (``book_words.ABRIDGED``).
_ABRIDGED = book_words.ABRIDGED
_UNABRIDGED = re.compile(r"(?<![a-z])(?:ungek(?:u|ue)rzt\w*|unabridged|onverkort\w*|integral\w*|texte integral|"
                         r"vollstandige lesung|vollstaendige lesung)(?![a-z])")
_DRAMATIZED = book_words.DRAMATIZED
_SOUNDTRACK = re.compile(r"(?<![a-z])(?:soundtrack\w*|original score|filmmusik)(?![a-z])")
_SAMPLE = re.compile(r"(?<![a-z])(?:leseprobe\w*|h(?:o|oe)rprobe\w*|probekapitel|kostprobe|sample|samples|excerpt|"
                     r"preview|extrait|fragment|vorab(?:druck|kapitel|exemplar)?|ausz(?:u|ue)g|ausz(?:u|ue)ge|excerpts|"
                     r"selections)(?![a-z])")
_COMPANION = re.compile(r"(?<![a-z])(?:begleitbuch\w*|companion|study guide|summary of|zusammenfassung|"
                        r"lekt(?:u|ue)rehilfe\w*|workbook|arbeitsheft|unterrichtsmaterial\w*|fanbuch|"
                        r"interpretation|analysis of|reading guide|making of|behind the scenes|"
                        r"hinter den kulissen)(?![a-z])")


#: ``-02-`` between two words: a catalogue number of the volume.
_CATALOGUE_NUMBER = re.compile(r"(?<=\s)-0*(\d{1,3})-(?=\s)")
#: Two languages in one book.
_BILINGUAL = re.compile(r"(?<![a-z])(?:zweisprachig\w*|bilingu\w*|dual language|dual language edition|"
                        r"parallel text|paralleltext|deux langues|bilingue)(?![a-z])")
#: Box words that also name a series of several volumes: followed by the number of one volume they are its name.
_SERIES_WORDS = frozenset({"trilogie", "trilogy", "tetralogie", "quadrilogie", "pentalogie", "saga"})


def sample_word(text: str) -> bool:
    """Whether a title (not a release name) says it is a sample (``Leseprobe``, ``Excerpt``, ``Auszug``)."""
    return bool(_SAMPLE.search(f" {fold(text)} "))


def sample_words(text: str) -> str:
    """The words that make a text a sample (``leseprobe``, ``auszug``), or empty."""
    found = _SAMPLE.search(f" {fold(text)} ")
    return found.group(0) if found else ""


def companion_word(text: str) -> bool:
    """Whether a title says it is a book about the book (``Begleitbuch``, ``Making-of``, ``Study Guide``,
    ``Erläuterungen``): the list of ``book_words``, the same for releases, files and the catalogue."""
    return bool(_COMPANION.search(f" {fold(text)} ") or book_words.companion_word(text))


_BUNDLE_WORDS = re.compile(r"(?<![a-z])(?:box|" + book_words.pattern(book_words.BOX_WORDS) + r"|collection|sammlung|"
                           r"sammelausgabe|komplett\w*|complete series|bundle|megapack|mega pack|"
                           r"sammelpaket|bucherpaket|buecherpaket|trilogie|trilogy|tetralogie|quadrilogie|"
                           r"pentalogie|doppelband|in einem band|in one volume|\d in ?1|romane|novels|bande|"
                           r"baende|books \d+ ?(?:to|-) ?\d+|datenbank|kw ?\d{1,2})(?![a-z])")
#: A range of volumes: ``1-5``, ``01 - 05``, ``1 bis 5``. Not ``3-5 Jahre`` (an age), not a part of a date or of an ISBN
#: (``2021-03-14``, ``978-3-16``: a number with a dash and a digit on the other side), not ``5-16BIT`` (a technical
#: figure, taken out before).
_RANGE = re.compile(r"(?<![\d.])(?<!\d-)(\d{1,3})\s*(?:-|–|bis|to)\s*(\d{1,3})(?![\d.])(?!-\d)"
                    r"(?!\s*(?:jahre|years|ans|anos|anni|monate|months|tage|days|uhr|stunden|hours|min|minuten|"
                    r"minutes|seiten|pages|kapitel|chapters|prozent|percent|mal|times)(?![a-z]))", re.IGNORECASE)
#: Figures of the recording (``16BIT``, ``44-KHZ``, ``44.1KHZ``, ``320kbps``, ``128K``), dates (``2021-03-14``) and
#: ISBNs: numbers that are no volumes. Blanked in the head before volumes and ranges are read.
_NOT_VOLUMES = re.compile(
    r"(?<![\d.])\d{1,3}(?:\.\d)?[\s\-]*(?:bits?|khz|hz|kbps|kbit|kb/s)(?![a-z])|(?<![\d.])\d{2,3}k(?![a-z\d])|"
    r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)|(?<!\d)\d{1,2}-\d{1,2}-\d{4}(?!\d)|"
    r"(?<![\d-])97[89](?:[\s-]?\d){10}(?!\d)|(?<![\d-])\d(?:-?\d){1,6}(?:-\d{1,6}){2}-[\dXx](?![\d-])", re.IGNORECASE)
#: The name of a file that is no book, where a release name should be (``Autor - Titel.nfo``): spam, or a file of the
#: scene. Not the tag ``READ NFO`` in a name, but the ending of it.
_NOT_A_BOOK = re.compile(r"\.(?:nfo|txt|sfv|diz|url|exe|msi|iso|jpe?g|png|gif|html?|apk|dmg|srt|idx|par2)\s*$",
                         re.IGNORECASE)
_VOLUME_WORD = (r"(?:band|bd|buch|teil|folge|book|vol|volume|tome|tomo|deel|del|nr|no|episode|ep|fall|case|"
                r"livre|libro)")
_MARKED_VOLUME = re.compile(r"(?<![a-z])" + _VOLUME_WORD + r"\s*#?\s*0*(\d{1,3}(?:[.,]5)?)(?![\d])", re.IGNORECASE)
_HASH_VOLUME = re.compile(r"#\s*0*(\d{1,3}(?:[.,]5)?)(?![\d])")
_EPISODE = re.compile(r"(?<![a-z])e0*(\d{1,3})(?![\d])", re.IGNORECASE)
_ZERO_VOLUME = re.compile(r"(?<=[^\W\d_])\s+(0\d{1,2})(?![\d])")
_YEAR = re.compile(r"(?<![\d])(1[5-9]\d\d|20\d\d)(?![\d])")
_BITRATE = re.compile(r"(?<![\d])(\d{2,3})\s?(?:kbps|kbit|kb/s|k)(?![a-z])", re.IGNORECASE)
_BARE_BITRATES = frozenset({32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})
_CDS = re.compile(r"(?<![a-z\d])(\d{1,2})\s?(?:mp3)?\s?cds?(?![a-z])", re.IGNORECASE)
_GROUP = re.compile(r"^(?P<body>.*\S)-(?P<group>[A-Za-z0-9]{2,24})$")
#: The places a name is cut into words.
_SPLIT = re.compile(r"[\s\-–—,;:()\[\]{}+/|&!?\"'»«“”‚‘’]+")
_WORD_SPAN = re.compile(r"[^\s\-–—,;:()\[\]{}+/|&!?\"'»«“”‚‘’]+")


@dataclass(frozen=True)
class ReleaseName:
    #: The name as it came, cleaned.
    name: str
    #: The name with dots and underscores as spaces (a dot between digits stays).
    text: str
    #: The words of ``text`` in their order, as written.
    words: tuple[str, ...]
    #: The part that names the book: ``text`` without the tags of the scene, the year and the group.
    head: str
    #: ``ebook``, ``audio``, or None when the name says neither.
    kind: str | None
    #: The formats it names, lower case (``epub``, ``mp3`` ...), in the order of the name.
    formats: tuple[str, ...]
    #: kbit/s the name states (``128kbps``, ``.128``), None when it says nothing.
    bitrate: int | None
    #: The code of the language a mark or the words of a language state; None when the name says nothing.
    language: str | None
    #: ``mark`` (``GERMAN``, ``(NL)``) or ``words`` (``Hörbuch``, ``Luisterboek``); empty without a language.
    language_how: str
    #: Every mark and word of a language with the words it stands in: ``language_of`` weighs them once it is known
    #: which words are the title of the book (``The Dutch Garden`` is no Dutch release).
    language_signs: tuple[LanguageSign, ...]
    #: True for an abridged reading, False when it says it is unabridged, None when it says neither.
    abridged: bool | None
    dramatized: bool
    soundtrack: bool
    sample: bool
    companion: bool
    #: The name ends like a file that is no book (``.nfo``): spam.
    not_a_book: bool
    bundle: bool
    #: The words that make it a bundle (``trilogie``, ``box``): a title that holds the word itself is no bundle
    #: (``release_match`` looks).
    bundle_words: tuple[str, ...]
    #: The volumes a bundle holds (``(1, 5)``), when it names them.
    bundle_range: tuple[int, int] | None
    #: The numbers the name gives as volumes (``Band 3``, ``#3``, ``Linville 04``).
    volumes: tuple[float, ...]
    year: int | None
    group: str
    #: The number of discs (``6CD``).
    discs: int | None
    #: The words (their places in ``words``) of a boast that only looks like a volume: the ``1`` of ``#1 New York Times
    #: bestselling author``, ``No. 1 Bestseller``.
    ad_numbers: tuple[int, ...] = ()
    #: Evidence for the reasons, by name (``mark:GERMAN``, ``word:hoerbuch``): for the log of a trace, never shown.
    signs: tuple[str, ...] = field(default=(), compare=False)
    #: The words that make it an adaptation (``Graphic Novel``, ``Nacherzählt für Kinder``, ``Penguin Readers Level
    #: 5``), an edition with additions (``Schulausgabe mit Materialien``, ``Das Buch zum Film``); empty when none.
    adaptation: str = ""
    edition: str = ""
    #: A word that says two languages in one book (``Zweisprachig``, ``bilingual``, ``dual language``).
    bilingual: bool = False
    #: The words of a collection with the book in it (``and Other Stories``); empty when none.
    collection: str = ""


def spaced(name: str) -> str:
    """The name with its separators as spaces: dots (not between digits) and underscores."""
    text = clean(name, NAME_MAX).replace("_", " ")
    # A dot between two digits stays (``44.1KHZ``, ``2.5``), unless the first digit ends a word (``MP3.320kbps``).
    text = re.sub(r"(?<!\d)\.|\.(?!\d)|(?<=[^\W\d_]\d)\.", " ", text)
    return " ".join(text.split())


def words_of(text: str) -> list[str]:
    return [word for word in _SPLIT.split(text) if word]


def _folded_words(text: str) -> list[str]:
    return [fold(word) for word in words_of(text) if fold(word)]


#: One thing in a name that names a language: the code, ``mark`` or ``words``, and the words it stands in (from, to).
LanguageSign = tuple[str, str, int, int]


def _language_signs(body: str, words: list[str]) -> list[LanguageSign]:
    """Every mark and every word of a language in a name, with the words it stands in. What they come to is for
    ``language_of`` to say, and a sign inside the title of the book looked for is no sign (``release_match``)."""
    signs: list[LanguageSign] = []
    folded = [fold(word) for word in words]
    starts = [found.start() for found in _WORD_SPAN.finditer(body)]
    # Marks: whole words, any case.
    for index, (word, key) in enumerate(zip(words, folded, strict=True)):
        for code, marks in _LANGUAGE_MARKS.items():
            if key in marks:
                signs.append((code, "mark", index, index + 1))
        if word.upper() == word and word in _THREE_LETTER and len(words) > 2:
            signs.append((_THREE_LETTER[word], "mark", index, index + 1))
    # Two-letter codes: in capitals and not the first word, or in brackets.
    for index, word in enumerate(words):
        if word in _TWO_LETTER and index > 0 and word.isupper():
            signs.append((_TWO_LETTER_CODE.get(word, word.lower()), "mark", index, index + 1))
    for bracket in re.finditer(r"[\(\[]\s*([A-Za-z]{2})\s*[\)\]]", body):
        code = bracket.group(1)
        place = starts.index(bracket.start(1)) if bracket.start(1) in starts else -1
        if place < 0:
            continue
        if code.upper() in ("UK", "US"):
            # "Titel (UK)": the British or the American edition, in English.
            signs.append(("en", "mark", place, place + 1))
        elif code.upper() in _TWO_LETTER:
            signs.append((_TWO_LETTER_CODE.get(code.upper(), code.lower()), "mark", place, place + 1))
    for code, vocabulary in _LANGUAGE_WORDS.items():
        for word in vocabulary:
            parts = word.split()
            for index in range(len(folded) - len(parts) + 1):
                if folded[index : index + len(parts)] == parts:
                    signs.append((code, "words", index, index + len(parts)))
    return signs


def language_of(signs: tuple[LanguageSign, ...] | list[LanguageSign],
                outside: tuple[tuple[int, int], ...] = ()) -> tuple[str | None, str]:
    """The language a name comes to say, and how (``mark`` or ``words``), without the signs that stand inside one of
    the places in ``outside`` (the words of the title looked for: ``The Dutch Garden`` is no Dutch release). Two marks
    of different languages (``EN/DE``, a dual release) say nothing sure; so do two languages of words."""
    kept = [sign for sign in signs if not any(sign[2] >= start and sign[3] <= end for start, end in outside)]
    marks = {code for code, how, _from, _to in kept if how == "mark"}
    if marks:
        return (next(iter(marks)), "mark") if len(marks) == 1 else (None, "")
    spoken = {code for code, how, _from, _to in kept if how == "words"}
    return (next(iter(spoken)), "words") if len(spoken) == 1 else (None, "")


def _tail_start(words: list[str]) -> int:
    """Where the tags of the scene begin: from that word on, every word is a tag, a year, a number of discs or a bit
    rate."""
    index = len(words)
    while index > 0 and _is_tag(words[index - 1]):
        index -= 1
    return index


def _is_tag(word: str) -> bool:
    key = fold(word)
    if not key:
        return True
    if key in _NEUTRAL_TAGS or key in _AUDIO_WORDS or _AUDIO_TECH.match(word) or _YEAR.fullmatch(word):
        return True
    if word.isdigit() and int(word) in _BARE_BITRATES:
        return True
    if _DRAMATIZED.fullmatch(key) or _UNABRIDGED.fullmatch(key) or _ABRIDGED.fullmatch(key):
        return True
    if word in _TWO_LETTER and word.isupper():
        return True
    if word.isupper() and word in _THREE_LETTER:
        return True
    return any(key in marks for marks in _LANGUAGE_MARKS.values())


def _ad_numbers(words: list[str], folded: list[str]) -> list[int]:
    """The places of the ``1`` in a boast (``#1 New York Times bestselling author``, ``No. 1 Bestseller``, ``Nr.1-
    Bestsellerautor``): a ``1`` right after ``#``, ``No``, ``Nr`` or ``Number`` and a word of bestsellers within the
    next seven words. It is no volume."""
    found: list[int] = []
    for index, word in enumerate(words):
        if not (re.fullmatch(r"#0*1", word) or (word == "1" and index > 0 and folded[index - 1] in (
                "no", "nr", "number", "nummer"))):
            continue
        for later in range(index + 1, min(len(words), index + 8)):
            if folded[later].startswith("bestsell") or (folded[later] == "best" and later + 1 < len(words)
                                                        and folded[later + 1].startswith("sell")):
                found.append(index)
                break
    return found


def parse(name: str) -> ReleaseName:
    """What a release name says. Never raises; a name that says nothing gives an empty reading."""
    raw = clean(name, NAME_MAX)
    text = spaced(raw)
    # ``Spiegelwelt.-02-.und.die.Kammer``: a number between two dashes in the middle of a name is a mark of the volume
    # (a catalogue number), not a word of the title.
    catalogue = [float(found.group(1)) for found in _CATALOGUE_NUMBER.finditer(text)]
    if catalogue:
        text = " ".join(_CATALOGUE_NUMBER.sub(" ", text).split())
    group = ""
    body = text
    found_group = _GROUP.match(text)
    if found_group is not None:
        before = words_of(found_group.group("body"))
        if before and _is_tag(before[-1]):
            group, body = found_group.group("group"), found_group.group("body")
    words = words_of(body)
    folded = [fold(word) for word in words]
    joined = " " + " ".join(folded) + " "
    signs: list[str] = []

    formats: list[str] = []
    for key in folded:
        if key in ("epub", "mobi", "azw3", "pdf") and key not in formats:
            formats.append(key)
        elif key in ("azw", "kf8") and "azw3" not in formats:
            formats.append("azw3")
        elif key in ("m4b", "m4a") and "m4b" not in formats:
            formats.append("m4b")
        elif key in ("aac", "flac", "mp3", "ogg", "opus", "wma") and key not in formats:
            formats.append(key)
        elif re.fullmatch(r"\d{0,2}mp3cds?", key) and "mp3" not in formats:
            formats.append("mp3")
        elif re.fullmatch(r"\d{2}bit|\d{2}(?:\.\d)?khz", key) and "flac" not in formats:
            # 16BIT and 44.1KHZ name a lossless copy of a CD or a download.
            formats.append("flac")

    bitrate = None
    rate = _BITRATE.search(body)
    if rate is not None:
        bitrate = int(rate.group(1))
    elif words and words[-1].isdigit() and int(words[-1]) in _BARE_BITRATES and len(words) > 2:
        # ``…ABOOK.2010.TbT.128``: a bare rate as the last word of an audiobook.
        bitrate = int(words[-1])

    audio = any(key in _AUDIO_WORDS for key in folded) or any(_AUDIO_TECH.match(word) for word in words) or bool(
        re.search(r"audio\s?book|livre audio|h(?:o|oe)rbuch", fold(group)))
    ebook = any(key in ("ebook", "ebooks", "epub", "mobi", "azw3", "azw", "kf8", "pdf", "kindle") for key in folded) \
        or " e book " in joined
    ebook_formats = [item for item in formats if item in EBOOK_FORMATS]
    audio_formats = [item for item in formats if item in AUDIO_FORMATS]
    kind: str | None = None
    if ebook_formats and not audio_formats:
        kind = "ebook"
    elif (audio_formats and not ebook_formats) or (audio and not ebook):
        kind = "audio"
    elif ebook and not audio:
        kind = "ebook"
    elif audio and ebook:
        # Both said: a word of an audiobook in an e-book name is rare, an e-book word in an audiobook group name
        # common; the formats decide, else nothing is sure.
        kind = "ebook" if ebook_formats else ("audio" if audio_formats else None)
    if kind is not None:
        signs.append(f"kind:{kind}")

    language_signs = _language_signs(body, words)
    language, how = language_of(language_signs)
    signs.extend(f"{how_}:{words[start]}" for _code, how_, start, _end in language_signs)

    abridged: bool | None = None
    if _UNABRIDGED.search(joined):
        abridged = False
    elif _ABRIDGED.search(joined):
        abridged = True
    dramatized = bool(_DRAMATIZED.search(joined))
    soundtrack = bool(_SOUNDTRACK.search(joined))
    # The word may stand in the group too (``…EPUB-LESEPROBE``), which is cut off the body.
    sample = bool(_SAMPLE.search(joined) or _SAMPLE.search(f" {fold(group)} "))
    companion = bool(_COMPANION.search(joined) or book_words.companion_word(body))
    adaptation = book_words.adaptation_word(body)
    edition = book_words.edition_word(body)
    bilingual = bool(_BILINGUAL.search(joined))
    collection = book_words.collection_word(body)

    tail = _tail_start(words)
    spans = [found.span() for found in _WORD_SPAN.finditer(body)]
    head = body[: spans[tail][0]] if 0 < tail < len(spans) else body
    head = head.strip(" -–—,.;:([{")
    # What is read for volumes and ranges: the same head without the numbers that are no volumes (a boast like ``#1
    # New York Times bestselling``, the figures of a recording, a date, an ISBN), blanked letter for letter.
    ad_numbers = _ad_numbers(words, folded)
    scan = list(body)
    for index in ad_numbers:
        # The ``1`` and, when it is a word of its own, the ``No`` or ``Nr`` before it.
        for number in (index - 1, index) if words[index] == "1" else (index,):
            scan[spans[number][0] : spans[number][1]] = " " * (spans[number][1] - spans[number][0])
    scan_text = "".join(scan)
    scan_text = _NOT_VOLUMES.sub(lambda found: " " * len(found.group(0)), scan_text)
    scan_head = (scan_text[: spans[tail][0]] if 0 < tail < len(spans) else scan_text).strip(" -–—,.;:([{")

    bundle_range = None
    for found in _RANGE.finditer(scan_head):
        first, last = int(found.group(1)), int(found.group(2))
        if not first < last <= 100 or last - first > 60:
            continue
        before = scan_head[: found.start()].rstrip()
        if re.search(r"(?:cds?|discs?|disks?)$", fold(before)):
            # ``CD 7-12``: the discs of one recording, a part of it, not a bundle of books (``book_words.part_of``).
            continue
        zero = found.group(1).startswith("0") or found.group(2).startswith("0")
        tight = "-" in found.group(0) and " " not in found.group(0)
        marked = re.search(r"(?:" + _VOLUME_WORD + r"|b(?:a|ae)nde|teile|folgen|books)\s*$", fold(before)) is not None
        # ``1 - 3`` or ``2 bis 4`` after a name, without a word for volumes: a small range of small numbers.
        loose = first >= 1 and last <= 30 and last - first <= 12
        if zero or tight or marked or loose:
            bundle_range = (first, last)
            break
    bundle_words = tuple(dict.fromkeys(
        found.group(0) for found in _BUNDLE_WORDS.finditer(joined)
        # ``Edelstein-Trilogie 01 - Rubinrot``: the name of the series with the number of one volume, no box.
        if not (found.group(0) in _SERIES_WORDS and re.match(r"\s+(?:" + _VOLUME_WORD + r"\s*)?0*\d{1,2}(?![\d])",
                                                             joined[found.end():]))))
    more = book_words.more_bundle_word(body)
    if more:
        bundle_words += (more,)
    bundle = bundle_range is not None or bool(bundle_words)

    volumes: list[float] = []
    for pattern in (_MARKED_VOLUME, _HASH_VOLUME):
        for found in pattern.finditer(scan_head):
            value = float(found.group(1).replace(",", "."))
            if 0 < value < 1000 and value not in volumes:
                volumes.append(value)
    for found in _EPISODE.finditer(scan_head):
        if found.group(0)[1:].startswith("0"):
            value = float(found.group(1))
            if value not in volumes:
                volumes.append(value)
    for found in _ZERO_VOLUME.finditer(scan_head):
        value = float(found.group(1))
        if 0 < value and value not in volumes and not (bundle_range and found.start() >= 0 and value in bundle_range):
            volumes.append(value)

    for value in catalogue:
        if 0 < value and value not in volumes:
            volumes.append(value)
    # ``Das zweite Buch der Träume``, ``Der Tragödie zweiter Teil``, ``Band zwei``, ``Teil II``: a volume in words.
    for number in book_words.volumes_named(scan_head):
        if float(number) not in volumes:
            volumes.append(float(number))

    years = [int(item) for item in _YEAR.findall(body)]
    discs_found = _CDS.search(body)
    return ReleaseName(
        name=raw, text=text, words=tuple(words), head=head, kind=kind, formats=tuple(formats), bitrate=bitrate,
        language=language, language_how=how, language_signs=tuple(language_signs), abridged=abridged,
        dramatized=dramatized, soundtrack=soundtrack,
        sample=sample, companion=companion, not_a_book=bool(_NOT_A_BOOK.search(raw)), bundle=bundle,
        adaptation=adaptation, edition=edition, bilingual=bilingual, collection=collection,
        bundle_words=bundle_words, bundle_range=bundle_range,
        volumes=tuple(volumes), ad_numbers=tuple(ad_numbers),
        year=years[-1] if years else None, group=group, discs=int(discs_found.group(1)) if discs_found else None,
        signs=tuple(signs),
    )


def is_tag(word: str) -> bool:
    """Whether a word of a name is a tag of the scene, a year, a mark of a language or a figure of a recording."""
    return _is_tag(word)


__all__ = ["AUDIO_FORMATS", "EBOOK_FORMATS", "LanguageSign", "ReleaseName", "is_tag", "language_of", "parse",
           "spaced", "words_of"]
