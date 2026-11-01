"""The words that say a book is not the work itself, one list for the names of releases, the titles of files and the
entries of the catalogue (``release_name``, ``procure.checking``, ``match.score``, ``catalog``).

* **An adaptation** is another book made of the work: a graphic novel, a comic, a manga, a retelling for children, a
  simplified text for learners (an easy or graded reader, a level of a reader series). It is never the work wished,
  and it never shares a work with it.
* **A companion** is a book about the book: a study guide, an interpretation, notes for an exam (``Königs
  Erläuterungen``, ``Lektüreschlüssel``, ``Textanalyse``), a making-of.
* **An edition with additions** holds the text with more around it, or may be the novel or a book about a film: a
  school edition with materials, the book of the film, an annotated edition. Nobody can tell from the name which one it
  is: a person decides.
* **A play** is a recording with a cast, not a reading: ``Hörspiel``, ``full cast``, ``GraphicAudio``, ``audio
  theatre``, ``movie in your mind``. It is another version of the work (``dramatized``), never the reading wished.
* **A part** of a work: ``Teil 2 von 2``, ``Band 2 von 4``, ``Part 1``, ``CD 7-12``, a bare ``1/2`` at the end. A
  part is not the whole, unless the work wished is that volume of a series.
* **A new edition** of a book that is out: ``Neuausgabe``, ``Neuauflage``, ``Jubiläumsausgabe``, ``new edition``.
* **A new translation** of a book that is out in the language: ``Neu übersetzt von``, ``Neuübersetzung``, ``new
  translation``.

The words are kept here once, as pieces of a pattern (``COMIC_WORDS``, ``PLAY_WORDS``, ``STUDY_WORDS``,
``COMPANION_WORDS``, ``FILM_BOOK_WORDS``, ``SCHOOL_WORDS``, ``BOX_WORDS``, ``REISSUE_WORDS``, ``TRANSLATION_WORDS``,
``PART_UNITS``,
``PART_LINKS``). The names
of releases, the titles of files and the search of the catalogues (``booksearch.words``) build their patterns from
them and add only what they alone need: a word that says a book is a comic or a play is written once, and a word
added here is a word for every one of them.

What is matched is the folded text (``readers.text.fold``: lower case, no accents, ``ä`` as ``a``) with a space on both
sides; an umlaut written out (``ue``) is taken as well.
"""

from __future__ import annotations

import re

from .readers.text import fold

#: The pieces of the patterns below, in folded text (``ae`` for ``ä`` as well). Each is a list of alternatives to be
#: joined with ``|`` (``pattern``). The search of the catalogues builds its own patterns from the same pieces.
#:
#: A comic, a graphic novel, a manga: the book told in pictures.
COMIC_WORDS = (r"graphic novel\w*", r"comic(?:s|roman\w*|fassung|version|adaption)?", r"manga", r"bande dessinee",
               r"fumetto")
#: A play: a recording with a cast, not a reading.
PLAY_WORDS = (r"\w*h(?:o|oe)rspiel\w*", r"hoorspel\w*", r"dramati[sz]ed", r"dramatisiert\w*", r"dramati[sz]ation",
              r"full[ -]?cast", r"ensemble cast", r"cast performance", r"multi[ -]?cast", r"graphic ?audio",
              r"audio ?theat(?:re|er)", r"movie in your mind", r"audio ?drama", r"radio drama", r"radio ?play",
              r"b(?:u|ue)hnenfassung", r"(?:radio|rundfunk|funk)fassung\w*", r"voll(?:er|en) besetzung")
#: A book to study a book by: a guide to the text, an interpretation, a summary, notes for an exam.
STUDY_WORDS = (r"lekt(?:u|ue)rehilfe\w*", r"lekt(?:u|ue)reschl(?:u|ue)ssel", r"interpretationshilfe\w*",
               r"interpretation(?:en)?", r"textanalyse", r"erl(?:a|ae)uterungen", r"zusammenfassung", r"summary of",
               r"analysis of", r"study guides?", r"reading guide", r"sparknotes", r"cliffs ?notes", r"workbook",
               r"arbeitsheft", r"unterrichtsmaterial\w*", r"abiturwissen")
#: A book around the book: a book for fans, a making-of.
COMPANION_WORDS = (r"begleitbuch\w*", r"fanbuch", r"making of")
#: The book of a film.
FILM_BOOK_WORDS = (r"filmbuch", r"(?:das )?buch zum (?:film|kinofilm)")
#: The edition for school.
SCHOOL_WORDS = (r"schullekt(?:u|ue)re",)
#: Several books in one.
BOX_WORDS = (r"box ?set", r"boxed set", r"sammelband\w*", r"sammelbox", r"gesamtausgabe", r"omnibus")
#: The words that name a part of a book or a recording (not a volume of a series), and the words between the number of
#: the part and the number of the parts (``Teil 2 von 3``, ``Part 1 of 2``, ``Deel 2 van 3``).
PART_UNITS = ("teil", "part", "deel")
PART_LINKS = ("von", "of", "aus", "de", "di", "van")


#: What a recording adds to its title and that is no part of the title (written for the text as it came, in lower or
#: upper case, with the umlaut or its two letters): the form it is sold in. ('Gekürzt' is not here: it says which
#: version it is.)
RECORDING_NOISE = (r"ungek(?:u|ue|ü)rzt\w*(?: [^()]*)?", r"unabridged", r"h(?:o|oe|ö)rbuch(?:[ -]?download)?",
                   r"audio[ -]?books?", r"lesung", r"autorisierte lesefassung", r"mp3[ -]?cd", r"audio[ -]?cd",
                   r"deutsche ausgabe", r"german edition")
#: The words that put the reader beside a recording (``gelesen von Ida Leser``, ``read by``, ``Sprecher: ...``), each
#: with the word that joins it to the name. A word that does not take a name after it (``Der Leser``) is none of them.
READER_PHRASES = (r"(?:vor)?gelesen (?:von|vom|durch)", r"gesprochen (?:von|vom|durch)", r"gelezen door",
                  r"voorgelezen door", r"read by", r"narrated by", r"performed by", r"lue? par", r"sprecher(?:in)?:",
                  r"leser(?:in)?:", r"narrator:", r"(?:ungek(?:u|ue|ü)rzte )?lesung (?:von|mit|durch)")


#: Words a title can go on with after the name of its series (``Anselm Quist 01 - und der Stein am Fluss``): what
#: follows them is no title of its own.
TITLE_JOINS = ("und", "and", "et", "y", "e", "en", "oder", "or", "&")


def pattern(*pieces: tuple[str, ...] | str) -> str:
    """Pieces joined as the alternatives of one pattern (a piece is a word or a list of them)."""
    return "|".join(item for piece in pieces for item in ((piece,) if isinstance(piece, str) else piece))


_ADAPTATION = re.compile(
    r"(?<![a-z])(?:" + pattern(COMIC_WORDS) + r"|"
    r"nacherz(?:a|ae)hl\w*|neu erz(?:a|ae)hlt\w*|retold|retelling|"
    r"f(?:u|ue)r kinder(?: erz(?:a|ae)hlt)?|for (?:children|kids|young readers)|kinderbuchfassung|kinderfassung|"
    r"kinder(?:buch)? ?version|"
    r"vereinfacht\w*|simplified|easy readers?|graded readers?|penguin readers|oxford bookworms|"
    r"lernlekt(?:u|ue)re\w*|lekt(?:u|ue)re zum (?:englisch|deutsch|franzosisch|spanisch)\w* lernen|"
    r"(?:englisch|deutsch|french|english|spanish|german) lernen|learn (?:english|german|french|spanish)|"
    r"leichte sprache|einfache sprache|easy (?:english|german|french)|"
    r"(?:reader|readers|stufe|level|niveau) [abc]?[1-6](?:\+)?|[abc][12] (?:reader|lekture|lektuere))(?![a-z])")
_COMPANION = re.compile(
    r"(?<![a-z])(?:" + pattern(STUDY_WORDS, COMPANION_WORDS) + r"|companion|interpretation\w*|"
    r"k(?:o|oe)nigs (?:erl|lernhilfe)\w*|abitur|behind the scenes|hinter den kulissen)(?![a-z])")
_EDITION = re.compile(
    r"(?<![a-z])(?:" + pattern(FILM_BOOK_WORDS, SCHOOL_WORDS) + r"|schulausgabe\w*|mit materialien|"
    r"mit (?:anmerkungen|kommentar)|zum (?:kinofilm|film)|filmausgabe|movie tie in|film tie in|"
    r"annotated|kommentierte ausgabe|studienausgabe)(?![a-z])")
#: The words of a play (folded): ``Hörspiel``, ``full cast``, ``GraphicAudio``, ``multicast``, ``audio theatre``.
DRAMATIZED = re.compile(r"(?<![a-z])(?:" + pattern(PLAY_WORDS) + r")(?![a-z])")
#: The words of an abridged reading (folded): ``gekürzt``, ``abridged``, ``Lesefassung`` (how German publishers name
#: one), ``Kurzfassung``, ``Hörfassung`` (a reading made shorter for listening).
ABRIDGED = re.compile(r"(?<![a-z])(?:gek(?:u|ue)rzt\w*|abridged|verkort\w*|abrege|(?:autorisierte )?lesefassung|"
                      r"kurzfassung\w*|h(?:o|oe)rfassung\w*|condensed)(?![a-z])")
#: ``Teil 2 von 2``, ``Band 2 von 4``, ``Part 1 of 3``, ``CD 3/6``: a part with the number of parts.
_PART_OF = re.compile(r"(?<![a-z])(?:" + pattern(PART_UNITS) + r"|band|bd|buch|book|vol|volume|cd|disc|tome|tomo)"
                      r"\s*0*(\d{1,3})\s*(?:" + pattern(PART_LINKS) + r"|/)\s*0*(\d{1,3})(?![\d])")
#: Two volumes in one book (``Buch 1&2``, ``Book 1 and 2``, ``Band 1 und 2``), as ``fold`` writes them: a part of a work
#: that has more, when the number of the second follows the first.
_JOINED_VOLUMES = re.compile(r"(?<![a-z])(?:band|bd|buch|book|teil|part|vol|volume|tome|tomo)\s*0*(\d{1,2})\s+"
                             r"(?:und |and |u |et )?0*(\d{1,2})(?![\d])")
_DISC_MARK = re.compile(r"(?<![a-z])(?:cd|disc|disk)\s*0*\d{1,2}(?![\d])")
#: A word of a volume of a series (``Band 3``, ``Book 2``, ``#4``): beside a part (``Teil 1``) the part is one of the
#: volume.
_VOLUME = re.compile(r"(?<![a-z])(?:band|bd|buch|book|vol|volume|folge|tome|tomo|nr)\s*0*\d{1,3}(?![\d])|#\s*\d")
_VOLUME_NUMBER = re.compile(r"(?<![a-z])(?:band|bd|vol|volume|tome|tomo)\s*0*(\d{1,2})(?![\d])")
#: ``Teil 2``, ``Part 1``, ``CD 3``, ``Disc 2``: a part without the number of parts.
_PART = re.compile(r"(?<![a-z])(?:teil|part|cd|disc|disk)\s*0*(\d{1,2})(?![\d])(?!\s*(?:kbps|kbit|kb|k|khz|bit)\b)")
#: ``CD 7-12``, ``CDs 1 - 6``: a range of discs is a part of one recording, not a bundle of books.
_DISC_RANGE = re.compile(r"(?<![a-z])(?:cds?|discs?|disks?)\s*0*(\d{1,2})\s*(?:-|bis|to|\s)\s*0*(\d{1,2})(?![\d])")
#: Words that make a release a bundle where the box words do not say it: ``All 6 …``, ``Saga komplett``, a typo of
#: ``Trilogie``.
_MORE_BUNDLE = re.compile(r"(?<![a-z])(?:all \d{1,2}|alle \d{1,2} (?:bande|baende|teile|romane|bucher|buecher)|"
                          r"saga komplett|sammelpack\w*|triologie|trilogy box|series box|"
                          # ``Zwei Romane in einem Band``, ``Three Novels in One``: earlier books bound together.
                          r"(?:zwei|drei|vier|funf|fuenf|[2-5]) (?:romane|bucher|buecher|krimis|thriller|bande|"
                          r"baende|novellen|titel) in einem (?:band|buch)|"
                          r"(?:two|three|four|[2-4]) (?:novels|books|thrillers|mysteries|titles) in one|"
                          r"die komplette (?:reihe|serie|saga)|the complete (?:series|saga|trilogy)|"
                          # ``Foundation Series``, ``Twilight Serie``: a series as a whole, not one volume of it.
                          r"(?:series|serie|reihe|saga)(?= *$| +(?:audiobook|abook|hoerbuch|horbuch|ebook|epub|mp3|"
                          r"m4b|german|english|deutsch|komplett|complete)\b))(?![a-z])")


#: A new edition of a book that is out (folded): ``Neuausgabe``, ``Neuauflage``, ``Jubiläumsausgabe``, ``überarbeitete
#: Ausgabe``, ``new edition``, ``anniversary edition``. The work is old, the edition new.
REISSUE_WORDS = (r"neuausgabe\w*", r"neuauflage\w*", r"neu aufgelegt\w*", r"wiederauflage\w*",
                 r"jubil(?:a|ae)umsausgabe\w*", r"(?:u|ue)berarbeitete (?:neu)?(?:ausgabe|auflage|fassung)",
                 r"erweiterte (?:neu)?(?:ausgabe|auflage)", r"new edition", r"revised edition", r"anniversary edition",
                 r"reissued?", r"nouvelle edition", r"edition anniversaire")
_REISSUE = re.compile(r"(?<![a-z])(?:" + pattern(REISSUE_WORDS) + r")(?![a-z])")
#: A new translation of a book that is out in the language (folded): ``Neu übersetzt von``, ``Neuübersetzung``, ``neu
#: übertragen``, ``new translation``, ``nouvelle traduction``. There was a translation before.
TRANSLATION_WORDS = (r"neu (?:u|ue)bersetzt\w*", r"neu(?:u|ue)bersetzung\w*", r"neu (?:u|ue)bertragen\w*",
                     r"neu(?:u|ue)bertragung\w*", r"new translation", r"newly translated", r"nouvelle traduction",
                     r"nouvellement traduit\w*")
_TRANSLATION = re.compile(r"(?<![a-z])(?:" + pattern(TRANSLATION_WORDS) + r")(?![a-z])")
#: ``Der tiefe Hof 1/2``, ``(1 von 2)``, a library's ``1 von 2``: the number of a part and of the parts, without a word
#: for a part, at the end of a text (read as it came: folding takes the slash out).
_BARE_PART = re.compile(r"(?<![\w/.,])0*(\d{1,2})\s*(?:/|von|of|aus|van)\s*0*(\d{1,2})\s*[)\]]?\s*$", re.IGNORECASE)
#: The most parts a bare ``n/m`` may name (a larger second number is a date, a score or a fraction, not a part).
BARE_PARTS = 12


def _folded(text: str) -> str:
    return f" {fold(text or '')} "


# --- Volumes and parts in words: ``zweiter Teil``, ``Das dritte Buch``, ``Band zwei``, ``Book Two``, ``Teil II`` -----

#: The words a volume is named by, before or after its number (folded).
_UNIT = r"buch|band|bd|teil|book|volume|vol|folge|tome|tomo|livre|deel"
#: The words that name a part of one recording or of a book in two, never a volume of a series.
_PART_UNIT = r"part|cd|disc|disk"
#: German ordinals by their stem (``zweite``, ``zweiter``, ``zweites``, ``zweiten``), English ones whole.
_ORDINAL_STEMS = {1: "erst", 2: "zweit", 3: "dritt", 4: "viert", 5: "(?:funft|fuenft)", 6: "sechst",
                  7: "sieb(?:en)?t", 8: "acht", 9: "neunt", 10: "zehnt"}
_ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
                  8: "eighth", 9: "ninth", 10: "tenth"}
_NUMBER_WORDS = {1: ("eins", "one"), 2: ("zwei", "two"), 3: ("drei", "three"), 4: ("vier", "four"),
                 5: ("funf", "fuenf", "five"), 6: ("sechs", "six"), 7: ("sieben", "seven"), 8: ("acht", "eight"),
                 9: ("neun", "nine"), 10: ("zehn", "ten")}
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_ORDINAL = "|".join(f"(?:{stem})(?:e|er|es|en|em)" for stem in _ORDINAL_STEMS.values()) + "|" + "|".join(
    _ORDINAL_WORDS.values())
_NUMBER = "|".join(word for words in _NUMBER_WORDS.values() for word in words)
#: ``das zweite Buch``, ``Der Tragödie zweiter Teil``, ``the third volume``, ``second part`` (folded).
_ORDINAL_BEFORE = re.compile(rf"(?<![a-z])(?P<word>{_ORDINAL})\s+(?P<unit>{_UNIT}|{_PART_UNIT})(?![a-z])")
#: ``Band zwei``, ``Book Two``, ``Teil drei``, ``Part Three`` (folded).
_NUMBER_AFTER = re.compile(rf"(?<![a-z])(?P<unit>{_UNIT}|{_PART_UNIT})\s+(?P<word>{_NUMBER}|{_ORDINAL})(?![a-z])")
#: ``Teil II``, ``Volume IV``, ``Part III``: a Roman numeral in capitals after such a word, read in the text as it
#: came (in lower case ``i`` is a word of its own).
_ROMAN_AFTER = re.compile(rf"(?i:(?<![^\W\d_])(?P<unit>{_UNIT}|{_PART_UNIT}))[\s._\-]*"
                          r"(?P<roman>VIII|VII|VI|IV|IX|III|II|X|V|I)(?![^\W\d_])")
#: The number of a volume after a word for one (``Band 3``, ``Teil 2``, ``Buch 1``), not a part of it (``Teil 1 von
#: 2``).
_VOLUME_OF = re.compile(rf"(?<![a-z])(?:{_UNIT})\s*0*(\d{{1,2}})(?![\d])(?!\s*(?:von|of|aus|de|di|van)\s*\d)")


def _value_of(word: str) -> int:
    for number, stem in _ORDINAL_STEMS.items():
        if re.fullmatch(f"(?:{stem})(?:e|er|es|en|em)", word):
            return number
    for number, item in _ORDINAL_WORDS.items():
        if word == item:
            return number
    return next((number for number, words in _NUMBER_WORDS.items() if word in words), 0)


def spelled_numbers(text: str) -> str:
    """``text`` folded, with a volume or a part it names in words written as a number: ``Der Tragödie zweiter Teil``
    is ``der tragodie teil 2``, ``Das dritte Buch der Träume`` is ``das buch 3 der traume``, ``Band zwei`` is ``band
    2``, ``Teil II`` is ``teil 2``, ``Book Two`` is ``book 2``. A number in words without such a word beside it (``Die
    zwei Türme``, ``Der erste Tag``) stays a word of the title."""
    text = _ROMAN_AFTER.sub(lambda found: f"{found.group('unit')} {_ROMAN[found.group('roman').upper()]}"
                            if found.group("roman").isupper() else found.group(0), text or "")
    folded = fold(text)
    folded = _ORDINAL_BEFORE.sub(lambda found: f"{found.group('unit')} {_value_of(found.group('word'))}", folded)
    return _NUMBER_AFTER.sub(lambda found: f"{found.group('unit')} {_value_of(found.group('word'))}", folded)


def _spelled(text: str) -> str:
    return f" {spelled_numbers(text)} "


def volumes_named(text: str) -> list[int]:
    """The volumes a text names by a word for a volume and a number in digits, words or Roman numerals (``Band 2``,
    ``das zweite Buch``, ``Band zwei``, ``Teil II``), in their order. A part (``Teil 1 von 2``, ``Part 2``, ``CD 3``)
    is no volume."""
    found = [int(match.group(1)) for match in _VOLUME_OF.finditer(_spelled(text))]
    return [number for number in dict.fromkeys(found) if number > 0]


def title_volume(text: str, parts: bool = True) -> int | None:
    """The one volume a title names (``Faust. Der Tragödie erster Teil`` is 1, ``Krieg und Frieden, Band 2`` is 2),
    None when it names none or two. Without ``parts`` a ``Teil`` with a number in digits does not count (``… - Teil
    1`` is how a recording in several parts names its first one); ``zweiter Teil`` and ``Teil II`` do."""
    found = volumes_named(text)
    if not parts:
        plain = {int(match.group(1)) for match in re.finditer(r"(?<![a-z])teil\s*0*(\d{1,2})(?![\d])", fold(text))}
        found = [number for number in found if number not in plain]
    return found[0] if len(found) == 1 else None


#: ``and Other Stories``, ``und andere Erzählungen``: the work with others in one book.
_COLLECTION = re.compile(r"(?<![a-z])(?:and other (?:stories|tales|novels|plays|poems)|"
                         r"und andere (?:erzahlungen|erzaehlungen|geschichten|novellen|romane|dramen)|"
                         r"et autres (?:nouvelles|recits|contes)|sammelband|"
                         r"collected (?:stories|works|novels))(?![a-z])")


_BOX = re.compile(r"(?<![a-z])(?:" + pattern(BOX_WORDS) + r")(?![a-z])")
#: A package of books named as one word (``Krimi-Paket``, ``Sommerpaket``), read before the text is folded: folding
#: takes the hyphen out, and ``Das Paket`` alone is the title of a novel. A weak sign: a reason to ask, never to turn a
#: book down.
_PACKAGE = re.compile(r"(?<![^\W\d_])[^\W\d_]{2,}(?:-p|p)a(?:ck|k)et(?:e)?(?![^\W\d_])", re.IGNORECASE)


def box_word(text: str) -> str:
    """The words that make ``text`` several books in one (``Sammelband``, ``Box Set``, ``Gesamtausgabe``), or empty."""
    found = _BOX.search(_folded(text))
    return found.group(0) if found else ""


def package_word(text: str) -> str:
    """A word that may name a package of books (``Moor-Paket``), or empty. Only a reason to ask (see ``_PACKAGE``)."""
    found = _PACKAGE.search(text or "")
    return found.group(0) if found else ""


def dramatized_word(text: str) -> str:
    """The words that make a recording a play (``full cast``, ``Hörspiel``), or empty."""
    found = DRAMATIZED.search(_folded(text))
    return found.group(0) if found else ""


def collection_word(text: str) -> str:
    found = _COLLECTION.search(_folded(text))
    return found.group(0) if found else ""


def adaptation_word(text: str) -> str:
    """The words that make ``text`` an adaptation (``graphic novel``, ``nacherzahlt fur kinder``), or empty."""
    found = _ADAPTATION.search(_folded(text))
    return found.group(0) if found else ""


def companion_word(text: str) -> str:
    found = _COMPANION.search(_folded(text))
    return found.group(0) if found else ""


def edition_word(text: str) -> str:
    found = _EDITION.search(_folded(text))
    return found.group(0) if found else ""


def more_bundle_word(text: str) -> str:
    found = _MORE_BUNDLE.search(_folded(text))
    return found.group(0) if found else ""


def part_of(text: str, volumes: bool = False, words: bool = True) -> tuple[int, int | None] | None:
    """``(part, parts)`` a text names (``Teil 2 von 2`` is ``(2, 2)``, ``Part 1`` is ``(1, None)``, ``CD 7-12`` is
    ``(7, None)``), or None. ``parts`` only when the text says how many there are. With ``volumes`` a volume
    (``Band 2``) counts as a part too: for a work that is no volume of a series. Without ``words`` only a number in
    digits counts (``Teil 2``, not ``zweiter Teil``)."""
    folded = _spelled(text) if words else _folded(text)
    found = _PART_OF.search(folded)
    if found is not None:
        part, parts = int(found.group(1)), int(found.group(2))
        if 0 < part <= parts <= 99:
            return part, parts
    ranged = _DISC_RANGE.search(folded)
    if ranged is not None:
        return int(ranged.group(1)), None
    joined = _JOINED_VOLUMES.search(folded)
    if joined is not None and int(joined.group(2)) == int(joined.group(1)) + 1:
        return int(joined.group(1)), None
    plain = _PART.search(folded)
    if plain is not None and int(plain.group(1)) > 0:
        return int(plain.group(1)), None
    volume = _VOLUME_NUMBER.search(folded) if volumes else None
    if volume is not None and int(volume.group(1)) > 0:
        return int(volume.group(1)), None
    return None


def bare_part(text: str) -> tuple[int, int] | None:
    """``(part, parts)`` of a text that ends in a bare ``1/2`` or ``1 von 2`` (``Der tiefe Hof 1/2``), or None. Two
    parts at least and at most ``BARE_PARTS``."""
    found = _BARE_PART.search((text or "").strip())
    if found is None:
        return None
    part, parts = int(found.group(1)), int(found.group(2))
    return (part, parts) if 0 < part <= parts and 2 <= parts <= BARE_PARTS else None


def reissue_word(text: str) -> str:
    """The words that make ``text`` a new edition of a book that is out (``Neuausgabe``, ``new edition``), or empty."""
    found = _REISSUE.search(_folded(text))
    return found.group(0) if found else ""


def translation_word(text: str) -> str:
    """The words that make ``text`` a new translation of a book that is out (``Neu übersetzt``, ``new translation``),
    or empty."""
    found = _TRANSLATION.search(_folded(text))
    return found.group(0) if found else ""


def disc_mark(text: str) -> bool:
    """Whether a name holds the mark of a disc (``CD01-01``, ``Disc 3``): the name of a track, no title."""
    return bool(_DISC_MARK.search(_folded(text)))


def names_volume(text: str) -> bool:
    """Whether a text names a volume of a series (``Band 3``, ``Book 2``, ``#4``, ``Band zwei``)."""
    return _VOLUME.search(_spelled(text).replace("#", " # ")) is not None or "#" in (text or "")


def disc_range(text: str) -> bool:
    return _DISC_RANGE.search(_spelled(text)) is not None


#: ``Teil 2 von 3``, ``Part 1 of 2``, ``CD 3/6``: a part with the number of parts, by a word that never names a volume
#: (not ``Band 2 von 7``, which is the second volume of seven).
_SURE_PART_OF = re.compile(r"(?<![a-z])(?:" + pattern(PART_UNITS) + r"|cd|disc|disk)\s*0*(\d{1,3})\s*(?:"
                           + pattern(PART_LINKS) + r")\s*0*(\d{1,3})(?![\d])")
#: ``Part 2``, ``CD 3``, ``Disc 1``: a part of a recording or a book, whatever the series.
_SURE_PART = re.compile(r"(?<![a-z])(?:part|cd|disc|disk)\s*0*(\d{1,2})(?![\d])"
                        r"(?!\s*(?:kbps|kbit|kb|k|khz|bit)(?![a-z]))")


def sure_part(text: str) -> tuple[int, int | None] | None:
    """``(part, parts)`` a text names in a form that is a part also beside a volume of a series: ``Teil 2 von 3``,
    ``Part 1 of 2``, ``CD 7-12``, ``Disc 3``, ``Part Two``. Not ``Teil 2`` alone, nor ``Band 2 von 7``: beside a series
    those are the volume."""
    folded = _spelled(text)
    found = _SURE_PART_OF.search(folded)
    if found is not None and 0 < int(found.group(1)) <= int(found.group(2)) <= 99:
        return int(found.group(1)), int(found.group(2))
    ranged = _DISC_RANGE.search(folded)
    if ranged is not None:
        return int(ranged.group(1)), None
    plain = _SURE_PART.search(folded)
    if plain is not None and int(plain.group(1)) > 0:
        return int(plain.group(1)), None
    return None


def not_the_work(text: str, wished: str = "") -> str:
    """What makes ``text`` another book than the work ``wished`` names: ``adaptation``, ``companion`` or ``edition``
    (the strongest first), empty when nothing does. A word the title wished holds itself counts for nothing (``Die
    Graphic Novel der Welt`` may be wished)."""
    for code, finder in (("adaptation", adaptation_word), ("companion", companion_word), ("edition", edition_word)):
        word = finder(text)
        if word and word not in _folded(wished):
            return code
    return ""


__all__ = ["adaptation_word", "bare_part", "box_word", "companion_word", "disc_range", "edition_word",
           "more_bundle_word", "not_the_work", "package_word", "part_of", "reissue_word", "spelled_numbers",
           "sure_part", "title_volume", "translation_word", "volumes_named"]
