"""The themed shelves of Browse: the thirteen shelves in three departments, what the open sources are asked
for each, and what a book has to show to stand on one.

A shelf is a **topic**, not a genre of the person's own library. What stands on it is what the sources say about the
topic: Open Library by subject (the books people want to read, the newest, the ones that won a prize, the ones that
were written long ago) and Apple's chart of the genre. Nothing here is a list of books: the table says *what to ask*
and *what a book must show*, never *what to show*. A section no source can fill is not shown.

**A thin shelf is better than a wrong book.** Open Library's subjects are given by many hands: one word can stand on a
book that is about something else ("Political science" holds the word "science"), a single tag can put a novel on the
wrong shelf, and the people who tag a book for children are not always right. So a book of Open Library stands on a
shelf only when its subjects say so plainly (``judge``):

* the question asks for whole subjects (``subject_key``), never for a word inside one;
* enough of its subjects speak for the shelf (``MIN_HITS``, or a tenth of them), and none that the shelf rules out;
* a book Open Library gives a content warning, a comic or a manga stands on no shelf;
* for children and young people: at least two subjects of that kind, none that speaks of grown-up content, and the
  classics only from Apple's own charts of classics for children and for young people;
* a prize only from the structured tag (``award:hugo_award=…``), never from a word that may be about the author or about
  prizes in general;
* the year a book was first written only when a second edition backs it (``openlibrary.parse_shelf``).

Apple's charts are the charts of a genre, but a chart of one genre holds books that Apple files under another: a book
counts for a shelf only when the genre Apple files it under is one of the shelf's (``apple_ok``).

The words are fixed here and only ever go into a question as a subject key (``sources/openlibrary.subject_query``); a
person's text never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..readers.text import fold

DEPARTMENTS = ("bel", "sach", "kj")
#: ``new`` (came out lately), ``popular`` (many people want it now), ``award`` (won a prize the source names),
#: ``classic`` (written long ago and still read). ``soon`` is not a section of a shelf: it holds what is announced
#: and is read by the list of books to come.
SECTIONS = ("new", "popular", "award", "classic")
STORED_SECTIONS = (*SECTIONS, "soon")
#: A strip on the wall shows a section only when the sources give at least this many books for it.
MIN_STRIP = 3
#: Books a strip shows, and books a section holds at most.
STRIP_BOOKS = 14
SECTION_BOOKS = 40
#: A book is new when it came out in this many days, or (Open Library says the year only) this year or the one before.
NEW_DAYS = 183
#: How many subjects of a book of Open Library have to speak for a shelf (or a tenth of its subjects, when it has few),
#: and how many for the shelves of children and young people.
MIN_HITS = 3
SHARE = 0.10
#: The share counts only with this many subjects for the shelf: one word among a book's three subjects is no proof.
MIN_SHARE_HITS = 2
MIN_KIDS_HITS = 2
#: The books of one series a section shows at most, and of one author (Apple's charts name no series: ten volumes of one
#: author in a row are a series as well).
PER_SERIES = 2
PER_AUTHOR = 3
#: The evidence of a book in Apple's chart of the genre it is filed under: above any share of subjects.
APPLE_EVIDENCE = 2.0


@dataclass(frozen=True)
class Award:
    """A prize as Open Library tags it (``award:<tag>=…``), and the year it was first given: a book older than that was
    not given it, whatever the tag says."""

    name: str
    since: int
    #: The names of the tag (after ``award:``, folded with underscores), as Open Library writes them.
    tags: tuple[str, ...]


AWARDS = {a.name: a for a in (
    Award("Edgar Award", 1946, ("edgar_award", "edgar_allan_poe_award")),
    Award("Pulitzer Prize", 1917, ("pulitzer_prize",)),
    Award("Booker Prize", 1969, ("booker_prize", "man_booker_prize")),
    Award("National Book Award", 1950, ("national_book_award",)),
    Award("National Book Critics Circle Award", 1975, ("national_book_critics_circle_award",)),
    Award("Deutscher Buchpreis", 2005, ("deutscher_buchpreis",)),
    Award("Prix Goncourt", 1903, ("prix_goncourt",)),
    Award("Women's Prize for Fiction", 1996, ("womens_prize_for_fiction", "orange_prize",
                                              "baileys_womens_prize_for_fiction")),
    Award("Hugo Award", 1953, ("hugo_award",)),
    Award("Nebula Award", 1966, ("nebula_award",)),
    Award("World Fantasy Award", 1975, ("world_fantasy_award",)),
    Award("Locus Award", 1971, ("locus_award",)),
    Award("Newbery Medal", 1922, ("newbery_medal", "newbery_award", "newberry_award")),
    Award("Caldecott Medal", 1938, ("caldecott_medal", "caldecott_award")),
    Award("Carnegie Medal", 1936, ("carnegie_medal",)),
    Award("Kate Greenaway Medal", 1956, ("kate_greenaway_medal",)),
    Award("Printz Award", 2000, ("printz_award", "michael_l_printz_award")),
    Award("Wellcome Book Prize", 2009, ("wellcome_book_prize", "wellcome_prize")),
)}


@dataclass(frozen=True)
class Topic:
    """A topic inside a shelf ("Musik" among the biographies): the words (folded, the start of a word) that say a
    book is about it, looked for in its subjects and its title."""

    key: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class Shelf:
    key: str
    dept: str
    #: One of the eight genres of the shelf (``services/genres.py``): the look of a cover that has no picture, and
    #: the word that gives a book of the shelf a genre when its subjects give none.
    genre: str
    hint: str
    #: Open Library: a book of the shelf has one of these subjects (asked as whole subjects) and none of ``exclude``.
    subjects: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    #: The words (the start of a word) that make one of a book's subjects speak for the shelf, and the phrases that
    #: make a subject not count although it holds such a word ("political science" says nothing of nature).
    evidence: tuple[str, ...] = ()
    not_evidence: tuple[str, ...] = ()
    #: A book with a subject that holds one of these stands not on the shelf (a novel among the biographies).
    reject: tuple[str, ...] = ()
    #: The words of a kind of book that is not the shelf's: a book whose subjects speak more for it than for the shelf
    #: is not on the shelf ("Dracula" says "science fiction" three times and "horror" seven).
    rivals: tuple[str, ...] = ()
    #: Apple's genres (numbers); the charts of all of them make the shelf's chart, and the genres a book of the chart
    #: may be filed under to count (the genres asked and those below them).
    apple: tuple[int, ...] = ()
    apple_ok: frozenset[int] = frozenset()
    #: Apple's charts of classics (for children, for young people): the only source of the section "classic" there.
    apple_classic: tuple[int, ...] = ()
    awards: tuple[str, ...] = ()
    topics: tuple[Topic, ...] = ()
    #: A book first written in or before this year is a classic; ``classic_from`` is the oldest year looked at.
    classic_until: int = 1975
    classic_from: int = 1800
    #: The sections the shelf has (a shelf of classics has no section "new classics").
    sections: tuple[str, ...] = SECTIONS
    #: The sections Open Library is asked for (the rest come from Apple alone).
    ol_sections: tuple[str, ...] = SECTIONS
    #: ``kinder`` or ``jugend`` for the two shelves for children and young people: the strict rules.
    kids: str = ""


def _t(key: str, *words: str) -> Topic:
    return Topic(key, words)


def _below(*genres: int, without: tuple[int, ...] = ()) -> frozenset[int]:
    """The genres and those Apple files below them (``GENRES_BELOW``), but ``without``."""
    found = set(genres)
    for genre in genres:
        found.update(GENRES_BELOW.get(genre, ()))
    return frozenset(found - set(without))


#: Apple's genres below the genres the shelves ask (from Apple's table of the genres of books, 38).
GENRES_BELOW: dict[int, tuple[int, ...]] = {
    9032: (10050, 10051, 10052, 10053, 10054, 10055, 11259),
    9003: (10056, 10057, 10058, 10059, 10060, 10061, 10062, 11040, 11042, 11043, 11229, 11231, 11232, 11233, 50000091),
    10044: (11001, 11002, 11003, 11004, 11005, 11275),
    9008: (11027, 11028, 11029, 11030, 11031, 11032, 11033, 11034, 11035, 11036, 11037, 11038, 11039),
    9015: (10070, 10071, 10072, 10073, 10074, 10075, 10076, 10077, 10078, 10079, 10080),
    9019: (10085, 10088, 10111, 10112, 10113, 10114, 10115, 10116, 10117, 10118, 11063, 11064, 11065, 11066, 11067,
           11068, 11069, 11295, 11296, 11297, 11298, 11299, 11300),
    9034: (11260, 11261, 11262, 11263, 11264, 11265, 11266),
    10120: (11070, 11071, 11072, 11073, 11074, 11075, 11076, 11327),
    9025: (10004, 10069, 10094, 10119, 11062),
    10091: (11077, 11078, 11079, 11080, 11081, 11082, 11083, 11084, 11317, 11319, 11320),
    9004: (10139, 10140, 10141, 10142, 10143, 10144, 10145, 10146, 10147, 10148),
    # Kids (11086) and young adults (11165), each with all that lies below.
    11086: tuple(range(11087, 11165)),
    11165: tuple(range(11166, 11227)),
}
#: Comics and graphic novels for children and for young people: not books of a shelf.
_COMICS = (11116, 11181)
#: Apple's romance for young people: publishers file grown-up romances there (a first place of "Neu" in Germany was
#: one). The shelf for young people goes without it.
ROMANCE_FOR_YOUNG = 11194

_NONFICTION_PRIZES = ("Pulitzer Prize", "National Book Award", "National Book Critics Circle Award")
#: The subjects that mark a book for children, and for young people (``kids``), and the ones that speak of grown-up
#: content: a book with one of those is on neither shelf.
KIDS_WORDS = ("juvenile", "children s", "childrens", "picture book", "board book", "kinderbuch", "kinderbucher",
              "bilderbuch", "early reader", "easy reader", "beginning reader", "chapter book", "middle grade",
              "kinderliteratur")
YOUNG_MARKS = ("young adult", "teen", "teens", "teenage", "teenager", "teenagers", "jugendbuch", "jugendbucher",
               "jugendliteratur", "jugendliche", "adolescence", "high school", "high schools")
#: For young people Open Library's "juvenile fiction" (the word of the libraries for both) counts as well.
YOUNG_WORDS = (*YOUNG_MARKS, "juvenile fiction")
ADULT_WORDS = ("smut", "spicy", "erotic", "erotica", "dark romance", "new adult", "bdsm", "sex", "sexual", "steamy",
               "kink", "explicit", "mafia", "adult fiction", "adult romance", "adults", "for adults", "fur erwachsene",
               "erwachsene")
#: Kinds of books that are for grown-ups whatever else is said: not on the shelf for children.
GROWN_UP_GENRES = ("horror", "thriller", "thrillers", "murder", "serial murder", "psychological fiction", "romance",
                   "romantic", "suicide", "crime", "noir", "war fiction", "drug abuse", "drug addiction",
                   "substance abuse", "opioid abuse", "addiction", "alcoholism", "self harm", "rape", "sexual abuse")
#: Kinds of books that are for grown-ups: not on the shelf for young people either.
GROWN_UP_YOUNG = ("serial murder", "noir", "psychological fiction", "adult")
#: Not books of a shelf at all: comics, manga, books Open Library warns about.
NO_SHELF = ("manga", "manhwa", "webtoon", "webtoons", "comic", "comic book", "comic books", "comics", "graphic novel",
            "graphic novels", "bande dessinee", "bandes dessinees", "study guide", "study guides", "study aids")
#: The words of romance: a rival of the shelves of crime and of fantasy (a dark romance with a murder in it is a
#: romance).
_ROMANCE = ("romance", "dark romance", "romantic suspense", "contemporary romance", "erotica", "erotic",
            "erotic romance", "love stories", "billionaire romance", "paranormal romance", "mafia romance", "romantasy",
            "spicy")
_NOT_FICTION = ("fiction", "novel", "novels", "drama", "plays", "romane", "erzahlung", "erzahlungen",
                "short stories", "poetry")

SHELVES: tuple[Shelf, ...] = (
    Shelf("krimi", "bel", "krimi", "Krimi", ("thriller", "mystery", "detective and mystery stories", "crime fiction",
                                             "suspense", "fiction, thrillers, suspense"),
          exclude=("juvenile fiction", "young adult fiction", "children's fiction"),
          evidence=("thriller", "mystery", "mysteries", "detective", "crime", "suspense", "murder", "krimi",
                    "kriminal", "police procedural", "espionage", "spies", "noir", "whodunit", "private investigator",
                    "psychothriller", "serial murder"),
          reject=(*KIDS_WORDS, *YOUNG_WORDS, "true crime", "case studies"), rivals=("horror", *_ROMANCE),
          apple=(9032,), apple_ok=_below(9032),
          awards=("Edgar Award",),
          topics=(_t("ermittler", "ermittl", "detectiv", "detektiv", "police", "polizei", "sleuth", "spürnas",
                     "kommissar", "inspector", "cozy", "private investigator"),
                  _t("psycho", "psycholog", "psycho", "domestic", "psychothriller"),
                  _t("cosy", "cosy", "cozy", "cosy crime", "kuchen", "amateur sleuth", "village"),
                  _t("politik", "politic", "polit", "espionage", "spion", "legal", "justice", "konspir",
                     "conspiracy"))),
    Shelf("romane", "bel", "roman", "Roman", ("literary fiction", "contemporary fiction", "family life",
                                              "historical fiction", "love stories", "domestic fiction",
                                              "fiction, literary"),
          exclude=("juvenile fiction", "young adult fiction", "children's fiction", "fantasy", "science fiction",
                   "thriller"),
          evidence=("literary fiction", "contemporary fiction", "domestic fiction", "family life", "families",
                    "family saga", "historical fiction", "love stories", "romance", "women s fiction",
                    "psychological fiction", "man woman relationships", "marriage", "friendship",
                    "mothers and daughters",
                    "fiction literary", "fiction general", "roman", "literary", "familie", "liebe"),
          reject=(*KIDS_WORDS, *YOUNG_WORDS, "fantasy", "science fiction", "thriller", "thrillers", "detective",
                  "mystery", "mysteries", "horror", "crime fiction"),
          apple=(10049, 10047, 11050, 9003), apple_ok=_below(10049, 10047, 11050, 9003, without=(10056,)),
          awards=("Booker Prize", "Pulitzer Prize", "National Book Award", "Deutscher Buchpreis", "Prix Goncourt",
                  "Women's Prize for Fiction", "National Book Critics Circle Award"),
          topics=(_t("gegenwart", "contemporary", "gegenwart", "zeitgenöss", "literary", "literar", "modern"),
                  _t("familie", "family", "famil", "mother", "mutter", "sisters", "schwester", "generation"),
                  _t("liebe", "love", "liebe", "romance", "romant", "liebes"),
                  _t("historisch", "histor", "war", "krieg", "century", "jahrhundert"))),
    Shelf("fantasy", "bel", "fantasy", "Fantasy", ("fantasy", "epic fantasy", "urban fantasy", "fantasy fiction",
                                                   "fiction, fantasy, general", "fiction, fantasy, epic"),
          exclude=("juvenile fiction", "young adult fiction"),
          evidence=("fantasy", "fantasie", "magic", "magie", "wizards", "dragons", "drachen", "elves", "fairies",
                    "sword and sorcery", "witches", "imaginary places", "mythical", "quests"),
          not_evidence=("science fiction fantasy", "science fiction and fantasy", "magic realism", "magical realism"),
          reject=(*KIDS_WORDS, *YOUNG_WORDS),
          rivals=("horror", "science fiction", "dystopia", "dystopian", "dystopias", "magic realism", "magical realism",
                  *_ROMANCE),
          apple=(10044,), apple_ok=_below(10044),
          awards=("Hugo Award", "Nebula Award", "World Fantasy Award", "Locus Award"),
          topics=(_t("episch", "epic", "episch", "sword", "high fantasy", "heroic"),
                  _t("märchenhaft", "fairy", "märchen", "fable", "myth", "legend", "sage"),
                  _t("urban", "urban", "paranormal", "vampire", "witch", "hexe", "contemporary fantasy"))),
    Shelf("scifi", "bel", "scifi", "Science Fiction", ("science fiction", "dystopian", "space opera", "time travel",
                                                       "fiction, science fiction, general"),
          exclude=("juvenile fiction", "young adult fiction"),
          evidence=("science fiction", "sci fi", "dystopia", "dystopian", "dystopias", "space opera", "time travel",
                    "robots", "artificial intelligence", "extraterrestrial", "life on other planets", "interplanetary",
                    "space warfare", "cyberpunk", "aliens", "post apocalyptic", "space colonies", "genetic engineering",
                    "space flight", "space ships", "spaceships", "science fiction"),
          reject=(*KIDS_WORDS, *YOUNG_WORDS), rivals=("horror", "gothic", "vampires"),
          apple=(10063,), apple_ok=_below(10063),
          awards=("Hugo Award", "Nebula Award", "Locus Award"),
          topics=(_t("weltraum", "space", "weltraum", "alien", "planet", "starship", "galax", "mars"),
                  _t("zukunft", "dystop", "future", "zukunft", "post apocalyptic", "apokalyp", "climate", "artificial",
                     "robot", "cyberpunk"),
                  _t("zeitreise", "time travel", "zeitreise", "alternate history", "alternate"))),
    # The shelf of classics: the books people want now are Apple's chart of classics; Open Library gives the old ones
    # and their prizes.
    Shelf("klassiker", "bel", "klassiker", "Klassiker", ("classics", "classic literature", "english literature",
                                                         "literature"),
          exclude=("juvenile fiction", "children's fiction", "history and criticism"),
          evidence=("classic", "classics", "klassiker", "literature", "literatur", "fiction classics"),
          not_evidence=("history and criticism", "study and teaching", "juvenile literature"),
          reject=(*KIDS_WORDS, *YOUNG_WORDS, "history and criticism", "criticism and interpretation", "sex", "sexual",
                  "sex customs", "sex instruction", "erotic", "erotica"),
          apple=(10042,), apple_ok=frozenset({10042}),
          awards=("Pulitzer Prize", "Booker Prize", "National Book Award", "Prix Goncourt"),
          topics=(_t("jahrhundert19", "nineteenth", "19th", "victorian", "viktorian", "romantic", "realism"),
                  _t("moderne", "twentieth", "20th", "modern", "moderne", "lost generation", "expression"),
                  _t("weltliteratur", "world literature", "weltliteratur", "russian", "french", "german", "american",
                     "russisch", "französ")),
          classic_until=1950, classic_from=1700, sections=("popular", "award", "classic"),
          ol_sections=("award", "classic")),
    Shelf("bio", "sach", "sachbuch", "Biografie", ("biography", "autobiography", "memoir", "biographies",
                                                    "biography & autobiography"),
          exclude=("juvenile literature", "children's"),
          evidence=("biography", "biographies", "autobiography", "autobiographies", "memoir", "memoirs", "biographie",
                    "autobiografie", "personal narratives", "diaries", "lebenserinnerungen"),
          not_evidence=("biographical fiction",),
          reject=(*_NOT_FICTION, "biographical fiction", "juvenile literature", "children s"),
          apple=(9008,), apple_ok=_below(9008), awards=_NONFICTION_PRIZES,
          sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("musik", "music", "musik", "composer", "komponist", "singer", "sänger", "conductor", "dirigent",
                     "jazz", "rock", "band"),
                  _t("politik", "politic", "polit", "president", "präsident", "statesm", "prime minister", "kanzler",
                     "government", "regierung", "activist"),
                  _t("wissenschaft", "scien", "wissenschaft", "physic", "physik", "chemist", "mathemat", "biolog",
                     "astronom", "inventor", "forscher", "mediz"),
                  _t("kunst", "artist", "künstler", "painter", "maler", "art ", "kunst", "sculpt", "photograph",
                     "architect", "designer", "actor", "schauspiel", "film"),
                  _t("sport", "sport", "athlet", "football", "fussball", "tennis", "olympi", "racing", "boxing",
                     "soccer", "golf", "cycling"),
                  _t("abenteuer", "adventur", "abenteuer", "explor", "expedition", "voyage", "mountaineer", "polar",
                     "entdecker", "survival"))),
    Shelf("geschichte", "sach", "sachbuch", "Geschichte", ("history", "world history", "military history"),
          exclude=("fiction", "juvenile literature", "history and criticism"),
          evidence=("history", "historical", "geschichte", "military history", "world war", "civilization",
                    "antiquities", "archaeology", "middle ages", "revolution", "historiography", "kings and rulers",
                    "weltkrieg"),
          not_evidence=("history and criticism", "historical fiction", "natural history", "fiction"),
          reject=(*_NOT_FICTION, "historical fiction", "juvenile literature", "history and criticism"),
          apple=(9015,), apple_ok=_below(9015),
          awards=_NONFICTION_PRIZES, sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("antike", "ancient", "antike", "rome", "roman empire", "greece", "griech", "egypt", "ägypt"),
                  _t("mittelalter", "medieval", "mittelalter", "middle ages", "crusade", "kreuzzug", "viking"),
                  _t("neuzeit", "early modern", "neuzeit", "renaissance", "reformation", "napoleon", "revolution",
                     "colonial", "18th", "19th", "eighteenth", "nineteenth"),
                  _t("jahrhundert20", "20th", "twentieth", "world war", "weltkrieg", "cold war", "kalter krieg", "nazi",
                     "holocaust", "vietnam"))),
    Shelf("natur", "sach", "sachbuch", "Wissenschaft", ("science", "nature", "natural history", "biology", "physics",
                                                        "astronomy", "ecology", "medicine"),
          exclude=("fiction", "juvenile literature", "science fiction"),
          evidence=("science", "sciences", "nature", "natural history", "biology", "physics", "astronomy", "ecology",
                    "medicine", "evolution", "chemistry", "zoology", "botany", "animals", "plants", "geology",
                    "climate", "environment", "neuroscience", "brain", "cosmology", "universe", "mathematics",
                    "genetics", "naturwissenschaft", "wissenschaft", "natur", "biologie", "physik"),
          not_evidence=("political science", "social science", "social sciences", "science fiction", "computer science",
                        "library science", "military science", "christian science", "human nature", "fiction",
                        "philosophy of nature"),
          reject=(*_NOT_FICTION, "science fiction", "juvenile literature", "political science", "witchcraft", "magic",
                  "occultism", "occult", "wicca", "spells", "unidentified flying objects", "parapsychology",
                  "astrology",
                  "alternative medicine", "herbs", "herbal medicine", "traditional medicine", "pseudoscience"),
          apple=(9019,), apple_ok=_below(9019),
          awards=("Pulitzer Prize", "National Book Award", "Wellcome Book Prize", "National Book Critics Circle Award"),
          sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("tiere", "animal", "tier", "plant", "pflanze", "bird", "vögel", "insect", "forest", "wald",
                     "zoolog", "botan", "evolution", "ocean", "meer"),
                  _t("physik", "physic", "physik", "universe", "universum", "cosmo", "kosmos", "astronom", "quantum",
                     "space", "mathemat", "relativity"),
                  _t("medizin", "medic", "mediz", "body", "körper", "brain", "gehirn", "disease", "krankheit", "health",
                     "genetic", "gene ", "neuro"),
                  _t("klima", "climate", "klima", "earth", "erde", "geolog", "environment", "umwelt", "weather",
                     "wetter", "ecology", "ökolog"))),
    Shelf("gesellschaft", "sach", "sachbuch", "Politik", ("politics and government", "social conditions", "sociology",
                                                          "economics", "political science", "social sciences"),
          exclude=("fiction", "juvenile literature"),
          evidence=("politics", "political", "social conditions", "social aspects", "sociology", "economics",
                    "economic", "political science", "social sciences", "social science", "government", "democracy",
                    "capitalism", "inequality", "public policy", "civil rights", "racism", "feminism", "globalization",
                    "journalism", "politik", "gesellschaft", "wirtschaft", "soziologie"),
          reject=(*_NOT_FICTION, "juvenile literature"),
          apple=(9034, 10120), apple_ok=_below(9034, 10120),
          awards=_NONFICTION_PRIZES, sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("politik", "politic", "politik", "government", "democra", "demokrat", "election", "wahl", "power"),
                  _t("arbeit", "work", "arbeit", "labor", "labour", "econom", "wirtschaft", "business", "capital",
                     "employment"),
                  _t("stadt", "city", "cities", "stadt", "urban", "rural", "land ", "village", "dorf", "housing"),
                  _t("medien", "media", "medien", "journalis", "internet", "social media", "technolog", "digital",
                     "press", "presse"))),
    Shelf("leben", "sach", "sachbuch", "Psychologie", ("psychology", "self-help", "health", "philosophy",
                                                       "personal development", "mental health"),
          exclude=("fiction", "juvenile literature"),
          evidence=("psychology", "psychological aspects", "self help", "health", "philosophy", "personal development",
                    "mental health", "happiness", "success", "mindfulness", "meditation", "habits", "motivation",
                    "emotions", "interpersonal relations", "parenting", "conduct of life", "spirituality", "well being",
                    "stress", "ethics", "stoicism", "selbsthilfe", "psychologie", "ratgeber", "lebenshilfe",
                    "self actualization", "self realization"),
          not_evidence=("psychological fiction",),
          reject=(*_NOT_FICTION, "psychological fiction", "juvenile literature"),
          apple=(9025, 10094, 10119, 10091, 9030), apple_ok=_below(9025, 10094, 10119, 10091, 9030),
          awards=("National Book Critics Circle Award", "National Book Award", "Pulitzer Prize"),
          sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("psychologie", "psycholog", "mind", "mental", "psyche", "emotion", "habit", "gewohnheit",
                     "self-help", "self help", "motivation"),
                  _t("gesundheit", "health", "gesundheit", "sleep", "schlaf", "nutrition", "ernähr", "fitness", "yoga",
                     "meditat", "wellness", "diet"),
                  _t("familie", "family", "famil", "parent", "eltern", "child", "kind", "marriage", "partner",
                     "relationship", "beziehung", "friend"),
                  _t("philosophie", "philosoph", "ethic", "ethik", "stoic", "meaning", "sinn", "wisdom", "weisheit"))),
    Shelf("reisen", "sach", "sachbuch", "Reisen", ("travel", "voyages and travels", "description and travel",
                                                   "hiking", "adventure travel"),
          exclude=("fiction", "juvenile literature"),
          evidence=("travel", "travels", "voyages", "description and travel", "hiking", "adventure travel",
                    "guidebooks", "travel writing", "walking", "pilgrims", "pilgrimages", "exploration", "reise",
                    "reisen", "reisefuhrer", "wandern"),
          reject=(*_NOT_FICTION, "juvenile literature", "cooking", "cookbooks", "cookery", "recipes"),
          apple=(9004,), apple_ok=_below(9004),
          awards=("National Book Critics Circle Award", "National Book Award", "Pulitzer Prize"),
          sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"),
          topics=(_t("europa", "europe", "europa", "france", "frankreich", "italy", "italien", "spain", "spanien",
                     "germany", "deutschland", "england", "greece", "griechen", "alps", "alpen"),
                  _t("norden", "nordic", "norden", "scandinav", "norway", "norwegen", "sweden", "schweden", "iceland",
                     "island", "finland", "arctic", "greenland"),
                  _t("uebersee", "america", "amerika", "asia", "asien", "africa", "afrika", "australia", "japan",
                     "china", "india", "indien", "pacific", "south"),
                  _t("wandern", "hiking", "wander", "trail", "camino", "walking", "pilgrim", "trek", "mountain",
                     "berg"))),
    # For children and for young people Apple speaks first, and the classics come from Apple's charts of classics for
    # them alone: Open Library's "children's" and "young adult" tags stand on too many books for grown-ups.
    Shelf("kinder", "kj", "kinder", "Kinderbuch", ("children's fiction", "juvenile fiction", "picture books",
                                                  "children's stories", "children's literature"),
          exclude=("young adult fiction", "young adult"),
          evidence=KIDS_WORDS,
          reject=(*YOUNG_MARKS, *ADULT_WORDS, *GROWN_UP_GENRES),
          apple=(11112, 11114, 11119), apple_ok=_below(11086, without=_COMICS), apple_classic=(11115,),
          awards=("Newbery Medal", "Caldecott Medal", "Carnegie Medal", "Kate Greenaway Medal"),
          ol_sections=("new", "popular", "award"), kids="kinder",
          topics=(_t("bilderbuch", "picture book", "bilderbuch", "bedtime", "gute nacht", "illustrat", "board book"),
                  _t("erstlesen", "easy reader", "erstles", "early reader", "beginning reader", "first reader",
                     "reading level"),
                  _t("ab8", "chapter book", "middle grade", "ab 8", "ab 10", "adventure", "abenteuer", "mystery"),
                  _t("wissen", "knowledge", "wissen", "science", "natur", "animals", "tiere", "history", "geschichte",
                     "how things work", "encyclopedia"))),
    Shelf("jugend", "kj", "kinder", "Jugendbuch", ("young adult fiction", "young adult", "teenagers"),
          exclude=("picture books",),
          evidence=YOUNG_WORDS,
          reject=(*ADULT_WORDS, *GROWN_UP_YOUNG, "picture book", "picture books", "board book", "board books",
                  "early reader", "easy reader"),
          apple=(11183, 11185, 11191, 11195, 11196), apple_ok=_below(11165, without=(*_COMICS, ROMANCE_FOR_YOUNG)),
          awards=("Printz Award", "Newbery Medal", "Carnegie Medal"),
          # No classics: Apple's chart of classics for young people holds the classics for grown-ups (Austen,
          # Hemingway, Wuthering Heights), and Open Library's tags are no better.
          sections=("new", "popular", "award"), ol_sections=("new", "popular", "award"), kids="jugend",
          topics=(_t("fantasy", "fantasy", "magic", "magie", "dragon", "drachen", "myth", "witch"),
                  _t("gegenwart", "contemporary", "gegenwart", "coming of age", "school", "schule", "friendship",
                     "freundschaft", "family", "identity"),
                  _t("spannung", "thriller", "mystery", "spannung", "crime", "krimi", "dystop", "suspense", "survival"),
                  _t("liebe", "love", "liebe", "romance", "romant", "first love"))),
)

BY_KEY = {shelf.key: shelf for shelf in SHELVES}
INDEX_OF = {shelf.key: number for number, shelf in enumerate(SHELVES)}

#: The shelf a book of the person's own library, by its genre, is closest to: where "because you read ..." looks for
#: more.
SHELVES_OF_GENRE: dict[str, tuple[str, ...]] = {
    "roman": ("romane",), "krimi": ("krimi",), "fantasy": ("fantasy",), "scifi": ("scifi",),
    "klassiker": ("klassiker",), "kinder": ("kinder", "jugend"),
    "sachbuch": ("bio", "geschichte", "natur", "gesellschaft", "leben", "reisen"),
}


@lru_cache(maxsize=4096)
def _matcher(words: tuple[str, ...]) -> re.Pattern[str]:
    """The words as one question to a folded text: each is the start of a word (a trailing space ends the word)."""
    parts = ["(?<![a-z0-9])" + re.escape(fold(word)) + ("(?![a-z0-9])" if word.endswith(" ") else "")
             for word in words if fold(word)]
    return re.compile("|".join(parts) if parts else r"(?!x)x")


@lru_cache(maxsize=4096)
def _whole(words: tuple[str, ...]) -> re.Pattern[str]:
    """The words as whole words (or phrases) of a folded text."""
    parts = ["(?<![a-z0-9])" + re.escape(fold(word)) + "(?![a-z0-9])" for word in words if fold(word)]
    return re.compile("|".join(parts) if parts else r"(?!x)x")


#: "young adult" is not grown-up: the word "adult" counts only on its own.
_YOUNG_ADULT = re.compile(r"(?<![a-z0-9])young adults?(?![a-z0-9])")
_NON_FICTION = re.compile(r"(?<![a-z0-9])non fiction(?![a-z0-9])")


def topics_of(shelf: Shelf, text: str) -> list[str]:
    """The topics of ``shelf`` a book is about, by the words in ``text`` (its subjects and its title): none, one or
    several."""
    folded = fold(text)
    if not folded:
        return []
    return [topic.key for topic in shelf.topics if _matcher(topic.words).search(folded)]


# ---- What a book of Open Library has to show -----------------------------------------------------------------------


def key_of(subject: str) -> str:
    """Open Library's key of a subject: lower case, the spaces as underscores ("Children's fiction" is
    ``children's_fiction``)."""
    return re.sub(r"\s+", "_", subject.strip().casefold())


def award_tags(subjects: list[str]) -> set[str]:
    """The prizes a book's structured tags name (``award:hugo_award=novel`` is ``hugo_award``)."""
    found = set()
    for subject in subjects:
        if subject[:6].casefold() == "award:":
            name = subject[6:].split("=", 1)[0]
            found.add(re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", fold(name))).strip("_"))
    return found


def _plain(subjects: list[str]) -> list[str]:
    """The subjects that are words about the book (not a tag of a list, a prize or a series), folded."""
    out = []
    for subject in subjects:
        head = subject.split(":", 1)[0].casefold() if ":" in subject[:20] else ""
        if head in ("award", "series", "nyt", "content_warning", "place", "person", "time", "openlibrary_staff_picks",
                    "accessible_book", "protected_daisy", "in_library", "lending_library"):
            continue
        folded = _NON_FICTION.sub("nonfiction", fold(subject))
        if folded:
            out.append(folded)
    return out


def _hits(words: tuple[str, ...], unwords: tuple[str, ...], subjects: list[str]) -> int:
    match, unmatch = _whole(words), _whole(unwords)
    return sum(1 for text in subjects if match.search(text) and not unmatch.search(text))


def nyt_lists(subjects: list[str]) -> set[str]:
    """The kinds of the New York Times lists a book stood on, by Open Library's tags (``nyt:young-adult-hardcover=…``):
    ``kids``, ``young`` or ``adult`` (the lists of series and of graphic books say neither)."""
    found = set()
    for subject in subjects:
        if subject[:4].casefold() != "nyt:":
            continue
        name = subject[4:].split("=", 1)[0].casefold()
        if name.startswith(("childrens", "middle-grade", "picture-books")):
            found.add("kids")
        elif name.startswith("young-adult"):
            found.add("young")
        elif not name.startswith(("series-books", "graphic-books")):
            found.add("adult")
    return found


def series_of(subjects: list[str]) -> str:
    """The series a structured tag names (``series:Die_Deichchronik``), folded; empty when none does."""
    for subject in subjects:
        if subject[:7].casefold() == "series:":
            return fold(subject[7:].replace("_", " "))[:120]
    return ""


#: The number of a volume at the end of a title ("One Piece 12", "Band 3", "Sonder-Edition 101").
_VOLUME = re.compile(r"(?:\b(?:band|bd|vol|volume|tome|teil|folge|nr|no|book|buch)\b\s*)?\d+[a-z]?\s*$")


def series_key(title: str, authors: list[str], subjects: list[str]) -> str:
    """What the books of one series have in common: the series a tag names, or the title without the number of the
    volume and the first author. Two books with one key are counted against ``PER_SERIES``."""
    named = series_of(subjects)
    if named:
        return f"s:{named}"
    folded = fold(title)
    stem = _VOLUME.sub("", folded).strip()
    if not stem or stem == folded:
        # No number at the end: a book of its own (or a series whose volumes have titles of their own).
        return ""
    return f"t:{stem}|{fold(authors[0]) if authors else ''}"[:160]


def judge(shelf: Shelf, subjects: list[str], *, title: str = "") -> float | None:
    """Whether a book of Open Library with these subjects stands on ``shelf``, and how strongly its subjects say so (the
    share of them that speak for the shelf); None when it does not stand there."""
    raw = [s for s in subjects if isinstance(s, str)]
    if any(s.casefold().startswith("content_warning") for s in raw):
        return None
    plain = _plain(raw)
    if not plain:
        return None
    text = " | ".join(plain)
    if _whole(NO_SHELF).search(text) or _whole(NO_SHELF).search(fold(title)):
        return None
    if shelf.dept == "sach" and series_of(raw):
        return None
    # A book on the New York Times lists for grown-ups is not one for children or young people, and one on the lists for
    # young people not one for children.
    lists = nyt_lists(raw)
    if shelf.kids == "kinder" and lists & {"adult", "young"}:
        return None
    if shelf.kids == "jugend" and "adult" in lists:
        return None
    # On the shelf for young people "young adult" is the shelf's own word, not a grown-up one.
    if shelf.reject and any(_whole(shelf.reject).search(_YOUNG_ADULT.sub(" ", t) if shelf.kids == "jugend" else t)
                            for t in plain):
        return None
    hits = _hits(shelf.evidence, shelf.not_evidence, plain)
    if shelf.rivals and _hits(shelf.rivals, (), plain) > hits:
        return None
    if shelf.kids:
        if hits < MIN_KIDS_HITS:
            return None
    elif hits < MIN_HITS and not (hits >= MIN_SHARE_HITS and hits >= SHARE * len(plain)):
        return None
    return hits / len(plain)


#: A book "new" in Open Library that is an old text in a new record (Sun Tzu, a reprint of 1926) says so.
OLD_TEXT = ("early works to 1800", "classics", "classic literature", "early works")


def is_old_text(subjects: list[str]) -> bool:
    """Whether a book's subjects say that it is an old text (and so not a new book, whatever the year of its record)."""
    return any(_whole(OLD_TEXT).search(text) for text in _plain(subjects))


def award_of(shelf: Shelf, subjects: list[str], year: int | None) -> str:
    """The prize of the shelf's table a structured tag of the book names, and the book can have won (it was written
    after the prize was first given); empty when none."""
    tags = award_tags(subjects)
    for name in shelf.awards:
        award = AWARDS[name]
        if tags.intersection(award.tags) and (year is None or year >= award.since):
            return name
    return ""


#: The words of a title that make a book several books (a box, a collected edition), and volumes one to three.
_BUNDLE = re.compile(r"(?<![a-z0-9])(?:box|box set|boxed set|boxset|sammelband|sammelbaende|sammelbande|sammelbox|"
                     r"omnibus|gesamtausgabe|sammelausgabe|bundle|collection|collected|complete series|"
                     r"trilogy|trilogie|tetralogy|quartet|books|bande)(?![a-z0-9])")
_RANGE = re.compile(r"(?<![0-9])\d{1,2}\s*(?:-|\u2013|\u2014|bis|to|&|und|and)\s*\d{1,2}(?![0-9])")


def is_bundle(title: str) -> bool:
    """Whether a title is that of several books in one (a box, a collected edition, "Band 1-3")."""
    return bool(_BUNDLE.search(fold(title)) or _RANGE.search(title.casefold()))
