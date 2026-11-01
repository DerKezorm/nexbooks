"""The words the search of the catalogues reads a record by, in one place: what says that a record is a play made of a
book, a study guide, a book about the book, a comic of it, a box, a part of it or one volume of a book printed in
several, and what around a title names only the edition.

They are folded the way ``readers.text.fold`` folds (lower case, no accents, ``ß`` as ``ss``, punctuation as spaces),
and they are matched against the whole title of a record with its subtitle (``build._said``). Every word here was
seen in the answers of Open Library, the German National Library and Audible.

The words that the names of releases and the titles of files use as well (a play, a comic, a study guide, a box, a part
of a book) are not written here: they come from ``book_words``, one list for every place that reads a title, and a
pattern here adds to them only what the search of the catalogues alone needs.
"""

from __future__ import annotations

import re

from .. import book_words
from ..readers.text import fold

_pattern = book_words.pattern

#: What a hit can be: a book, a box or collection, a study guide, another book about the book (a calendar, a book for
#: fans, the music or the book of the film), a play made of it, a part of it, a comic of it.
LABELS = ("work", "box", "guide", "companion", "drama", "part", "comic")

#: The words that ask for what is otherwise set apart (folded, as the words of a search are).
LABEL_WORDS: dict[str, frozenset[str]] = {
    "box": frozenset({"box", "boxset", "sammelband", "schuber", "gesamtausgabe", "trilogie", "trilogy", "omnibus",
                       "sammelbox", "bundle"}),
    "guide": frozenset({"lekturehilfe", "lekturehilfen", "interpretation", "erlauterungen", "lektureschlussel",
                         "sparknotes", "studyguide", "guide", "zusammenfassung", "analyse"}),
    "companion": frozenset({"kalender", "calendar", "soundtrack", "filmmusik", "begleitbuch", "film", "dvd"}),
    "drama": frozenset({"horspiel", "hoerspiel", "horspiele", "audiodrama", "folge", "folgen"}),
    "part": frozenset({"leseprobe", "sample"}),
    "comic": frozenset({"comic", "comics", "manga", "graphic"}),
}

DRAMA = re.compile(r"\b(" + _pattern(book_words.PLAY_WORDS) + r")\b")
#: What a library writes in the statement of who made a recording when it is a play (``Drehbuch: …``).
DRAMA_CREDITS = re.compile(r"\b(drehbuch|horspiel\w*|hoerspiel\w*|horspielbearbeitung|funkbearbeitung)\b")
GUIDE = re.compile(
    r"\b(" + _pattern(book_words.STUDY_WORDS, book_words.SCHOOL_WORDS) + r"|kopiervorlagen|"
    # (A catalogue that lost an umlaut to a space: ``Erla uterungen zu …``, ``Lektu rehilfe``.)
    r"erla uterungen|lektu rehilfe\w*|lektu reschlussel|"
    r"vokabelheft|vokabeln|lehrerheft|lehrerband|lehrerhandbuch|unterrichtsstunde|unterrichtseinheit|"
    r"unterrichtskonzept|unterrichtsentwurf|filmanalyse|eine analyse|tiefgehende analyse|im werk von|im kontext|"
    r"als projekt|materialien zu|eine systematische|"
    r"unterrichtsmodell\w*|materialien|arbeitsblatter|lesetagebuch|lesebegleiter|"
    r"teaching guide|teachers guide|teacher s guide|"
    r"discussion guide|activity guide|readers guide|reader s guide|literature guide|book analysis|"
    r"summary and analysis|quiz book|trivia|fiche de lecture|profil d une oeuvre|"
    r"guida alla lettura|werkanalyse|arbeitsbuch|im deutschunterricht|im unterricht|"
    r"deutschunterricht|klausurtraining|lernhilfe|am beispiel|hausarbeit|seminararbeit|"
    r"bachelorarbeit|masterarbeit|magisterarbeit|studienarbeit|facharbeit|examensarbeit|a comparison|ein vergleich|"
    r"literature unit|novel unit|lesson plans?)\b")
COMPANION = re.compile(
    r"\b(" + _pattern(book_words.COMPANION_WORDS, book_words.FILM_BOOK_WORDS) + r"|"
    r"kalender|calendar|wandkalender|tischkalender|poster|posterbuch|poster book|postkarten|postcards|bookmarks|"
    r"lesezeichen|notizbuch|malbuch|ausmalbuch|coloring|colouring|kochbuch|cookbook|rezepte|recipes|"
    r"stickerbuch|sticker book|kreativbuch|bastelbuch|mitmachbuch|activity book|freundebuch|meine freunde|"
    r"tagebuch zum|magisches tagebuch|lexikon|encyclopedia|"
    r"enzyklopadie|inoffizielle|unofficial|fur fans|for fans|soundtrack|filmmusik|motion picture|"
    r"movie book|official guide|companion to|"
    r"handbuch fur|origami|calvendo|dvd|blu ray|bluray|der film|das horbuch zum film|"
    r"\d+\s*film|film \d+|kinofilm|filmkunstwerks?|bildband zum film|puzzle|hakelset|strickset|bastelset|"
    r"buchstutze|spiel zum buch|brettspiel|kartenspiel|tarot|tarotkarten|oracle cards|orakelkarten)\b")
#: A comic of a book (or a comic of its own): told by the words the records carry in their titles and subtitles.
COMIC = re.compile(r"\b(" + _pattern(book_words.COMIC_WORDS) + r")\b")
#: A book retold after the book by another, for children or for learners (folded): not the book.
ADAPTED = re.compile(r"\b(nacherzahlt\w*|neu erzahlt\w*|retold|retelling|kinderbuchfassung|kinderfassung|"
                     r"vereinfacht\w*|simplified|easy readers?|graded readers?|penguin readers|oxford bookworms|"
                     r"adaptation|adaption)\b")
#: What a record says when it was made after a book of another (``nach dem Roman von Ada Example``, a comic of it): the
#: name that follows is the author of that book, not of this one (folded).
MADE_AFTER = re.compile(r"\b(?:nach (?:dem|der|einem|einer|den) (?:\w+ )?(?:roman|romanen|erzahlung|buch|vorlage|"
                        r"geschichte|novelle|klassiker|marchen|bestseller|kinderbuch)\w* (?:von|des|der)|"
                        r"(?:based on|after) the (?:novel|book|story|classic) (?:by|of))\s+(?P<who>[a-z .]+)")
#: The words of the subjects of a record that say it is a comic (Open Library, Hardcover).
COMIC_SUBJECTS = ("comic", "graphic novel", "manga", "bande dessin")
PART = re.compile(r"\b(" + _pattern(book_words.PART_UNITS, ("partie", "parte", "del", "osa")) + r")\s*\d+\s*("
                  + _pattern(book_words.PART_LINKS, ("av",)) + r")\s*\d+\b|"
                  r"\b(leseprobe|preview|extrait|sample|excerpt|kostprobe)\b")
#: A part written with a slash, read before the title is folded (folding takes the slash out).
PART_SLASH = re.compile(r"[(\[]\s*\d{1,2}\s*/\s*\d{1,2}\s*[)\]]")
BOX = re.compile(r"\b(" + _pattern(book_words.BOX_WORDS) + r"|box \d+|schuber|coffret|integrale|"
                 r"\d+\s*(books|bucher|buecher|bande|baende|bde|romane|romans|novels|geschichten|stories|"
                 r"abenteuer|falle|folgen|horspiele|erzahlungen|titel)|"
                 r"(band|bande|books?|tome|tomes|vol|volumes?|teil|folge|folgen)\s*\d{1,3}\b\s*(bis|to|a|und|and)?\s*"
                 r"\d{1,3}\b|\d+er box|teil i und ii|teil 1 und 2)\b|"
                 r"\b\d{1,2}\s+(bis\s+|to\s+)?\d{1,2}$")
#: One volume named in a title (``Band 2``, ``Bd. 2``, ``Teil 3``, ``Folge 12``, ``Vol. 4``, ``Book 2``, ``Tome 1``),
#: folded; never a range (``Band 1-3`` is a box).
VOLUME = re.compile(r"\b(?:band|bd|teil|folge|vol|volume|book|buch|tome|tomo|livre|libro|deel|del|bind|osa|nr|no)\s*"
                    r"(\d{1,3})\b(?!\s*(?:bis|to|a|und|and)?\s*\d)")
#: What a library or a shop writes about a book where its title would be, to sell it (folded): ``Atemberaubende
#: Fantasy-Action im Kampf gegen grauenhafte Titanen``, ``Die beliebte Novel jetzt auch als Manga in zwei Bänden!``.
ADVERTISING = re.compile(
    r"\b(jetzt|endlich|erstmals|erfolg\w*|beliebt\w*|fortsetzung|schopfer\w*|atemberaubend\w*|spannend\w*|packend\w*|"
    r"mitreissend\w*|fesselnd\w*|bestseller\w*|kultbuch\w*|kultserie\w*|weltweit\w*|millionenfach|millionen leser\w*|"
    r"millionen fans|\w*action|actiongeladen\w*|"
    r"nach dem (?:welt|gross|riesen)\w*|der neue|die neue|das neue|zum ersten mal|now also|the new|bestselling|"
    r"from the creator|vom schopfer|des schopfers)\b")
#: The most words a title has that stands where a subtitle is (longer is a sentence about the book).
TITLE_WORDS_MAX = 8

#: The words of a part behind a full stop that names the edition, not the book (folded).
EDITION_WORDS = frozenset({
    "roman", "novel", "a", "an", "novella", "novelle", "eine", "ein", "erzahlung", "thriller", "krimi", "kriminalroman",
    "edition", "special", "taschenbuch", "paperback", "hardcover", "gebunden", "illustriert", "illustrierte",
    "illustrated", "ungekurzt", "ungekurzte", "gekurzt", "gekurzte", "unabridged", "abridged", "horbuch", "audiobook",
    "lesung", "neu", "ubersetzt", "neuubersetzung", "auflage", "in", "klarer", "moderner", "einfacher", "leichter",
    "sprache", "language", "easy", "cd", "cds", "mp3", "mit", "und", "materialien", "originalfassung", "fassung",
    "vollstandige", "complete", "deluxe", "collector", "collectors", "s", "schmuck", "geschenk", "jubilaums",
    "film", "zum", "movie", "tie", "der", "die", "das", "von", "fur", "kinder", "jugendliche", "schulausgabe",
})

#: A title cut to what stands before its first mark must still be a title: words that are none by themselves (folded).
#: ``Bis(s) zum Morgenlicht`` is not ``Bis``, ``Detektiv Kenta - 5. Film`` is not ``Film``.
NO_TITLE_WORDS = frozenset({
    "bis", "film", "teil", "band", "bd", "folge", "buch", "book", "vol", "volume", "tome", "jahrhundert", "kapitel",
    "chapter", "episode", "staffel", "season", "nr", "no", "der", "die", "das", "the", "a", "an", "und", "and", "von",
    "of", "zum", "zur", "im", "in", "mit", "roman", "novel", "horbuch", "audiobook", "sonderband", "special",
    "spezial", "extra", "neu", "new", "dem", "den", "des", "ein", "eine", "le", "la", "les", "el", "il",
})
#: The last word of a series written in front of a number, that shows it is no series (``Das Geld im 21.
#: Jahrhundert``: ``Das Geld im`` is no series).
NO_SERIES_END = frozenset({"im", "in", "zum", "zur", "der", "die", "das", "des", "dem", "den", "von", "vom", "the",
                           "of", "a", "an", "und", "and", "mit", "for", "fur", "um", "nach", "seit", "bis", "ab",
                           "uber", "unter", "am"})


#: What a thing about a book is called behind its title, besides the words of ``COMPANION`` and ``GUIDE`` (folded).
ABOUT_A_BOOK = re.compile(r"\b(journal|notebook|notizbuch|planner|planer|artbook|art book|the art of|quiz|fakten|facts|"
                          r"postkarten|postcards|sticker|figuren|welt von|the world of|die welt von)\b")


def about_a_book(text: str) -> bool:
    """Whether the folded words behind a title name a thing about the book (a journal, postcards, a guide)."""
    return bool(ABOUT_A_BOOK.search(text) or COMPANION.search(text) or GUIDE.search(text))


def title_like(text: str) -> bool:
    """Whether a text can be a title: no sentence about the book (it does not end with ``!`` or ``.``, it has at most
    ``TITLE_WORDS_MAX`` words) and no advertising."""
    words = fold(text).split()
    stripped = text.strip()
    return bool(words) and len(words) <= TITLE_WORDS_MAX and not stripped.endswith(("!", ".")) and \
        ADVERTISING.search(fold(text)) is None


def volume_of(text: str) -> float | None:
    """The one volume a folded title names (``lantern saga band 2`` is 2), or None (none, or a range)."""
    found = VOLUME.findall(text)
    numbers = {float(number) for number in found}
    return next(iter(numbers)) if len(numbers) == 1 else None


#: Small words that say which language a title is in (folded). A title is put in a language only when its small words
#: say so clearly (``The Penmoor Mystery`` is English, ``Das Geheimnis von Penmoor`` German, ``Don Ramiro de la
#: Mancha`` says nothing for sure: ``de la`` is Spanish and French).
FUNCTION_WORDS: dict[str, frozenset[str]] = {
    "de": frozenset({"der", "die", "das", "und", "von", "vom", "zum", "zur", "im", "mit", "ein", "eine", "des", "dem",
                     "den", "fur", "uber", "auf", "aus", "nicht", "ist", "ich", "wie", "wir", "ins", "unter", "nach"}),
    "en": frozenset({"the", "of", "and", "a", "an", "to", "with", "for", "on", "from", "my", "your", "his", "her", "is",
                     "at", "by", "who", "what", "how"}),
    "fr": frozenset({"le", "la", "les", "des", "du", "et", "un", "une", "au", "aux", "pour", "sur", "dans", "l", "d",
                     "qui", "est"}),
    "es": frozenset({"el", "los", "las", "del", "y", "una", "con", "por", "para", "la", "un"}),
    "it": frozenset({"il", "lo", "gli", "della", "dei", "di", "e", "una", "con", "per", "nel", "la", "un"}),
    "nl": frozenset({"het", "een", "van", "en", "met", "voor", "op", "uit"}),
    "sv": frozenset({"och", "ett", "av", "med", "pa", "som"}),
}


def title_language(text: str) -> str:
    """The language a title's small words say clearly (one language has more of them than any other), or ``""``."""
    found = fold(text).split()
    scores = {code: sum(word in small for word in found) for code, small in FUNCTION_WORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best, second = ranked[0], ranked[1]
    return best[0] if best[1] >= 1 and best[1] > second[1] else ""


__all__ = [
    "ABOUT_A_BOOK",
    "ADVERTISING",
    "BOX",
    "COMIC",
    "COMIC_SUBJECTS",
    "COMPANION",
    "DRAMA",
    "DRAMA_CREDITS",
    "EDITION_WORDS",
    "FUNCTION_WORDS",
    "GUIDE",
    "LABELS",
    "LABEL_WORDS",
    "NO_TITLE_WORDS",
    "PART",
    "PART_SLASH",
    "TITLE_WORDS_MAX",
    "VOLUME",
    "about_a_book",
    "title_language",
    "title_like",
    "volume_of",
]
