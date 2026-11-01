"""The judgement of a release: which kind and format it is, which language, whether it is the book
looked for, and the reasons against it as codes; then the order of those that pass.

**The reasons** (codes, the interface words them): ``blocklisted``, ``not_a_book``, ``sample``, ``soundtrack``,
``companion``, ``adaptation``, ``partial``, ``bundle``, ``author_mismatch``, ``title_mismatch``, ``other_volume``,
``other_language``, ``abridged``, ``dramatized``, ``format_not_wanted``, ``too_small``, ``too_large``,
``not_enough_seeders``, ``older_than_retention``, ``not_better``. A release with no reason passes.

**Doubts** (``ASK``): a release nexbooks never loads by itself and never turns down, because one thing speaks for it
and another one against, or only a weak one for it. "Releases ansehen" shows both and loads it after the question; a
wish whose releases are all doubtful says so under "Braucht dich". ``language_unsure`` (the title says one language, the
category another), ``bilingual`` (two languages in one book), ``edition`` (a school edition with materials, the book of
the film), ``title_short`` (only the end of the title: ``Tom Sawyer`` for ``Die Abenteuer des Tom Sawyer``),
``maybe_partial`` (``Teil 1`` after the title, without the number of parts: a part, or the title of the work),
``maybe_other_volume`` (a number after the title of a book whose series nobody named: ``Erebos 3``),
``maybe_other_book`` (another field after the title that nothing explains: ``Der Räuber Hotzenplotz - Schluss mit der
Räuberei``), ``author_missing`` (no author in the name and a title that alone proves little, or a field nothing
explains), ``size_odd`` (far bigger or smaller than the other releases of the book in the same format: a bundle or a
part). A release that names no author and a title that says something by itself passes, below the ones that name
the author: the file decides after the download.

**The order of those that pass** (the formats and places of the operator, ``formats.config``): the place of the
language in the operator's ranking first (a German reading before an English one, whatever its version), then the kind
of reading for an audiobook (unabridged before a dramatisation before abridged), then the place of the format in the
ranking, a language that is sure before one that is presumed, the priority of the indexer, seeders of a torrent or the
age of a Usenet post, and the size last.

**Language**: a mark in the name (``GERMAN``, ``(NL)``) or words of a language (``Hörbuch``, ``Luisterboek``) are
sure. Without them: a title only known in one language, then the language the operator gave the category, else the
language looked for is presumed (a name without a mark says nothing: English names are hardly ever marked, and so are
many others). Only a sure language or a title of one language can be a reason against a release; a presumed one never
is, and neither is the language an operator gave a category (an indexer fills its categories with books of every
language: it is a hint). A mark that is part of the title looked for (``The Dutch Garden``) is no mark. After the
download the file decides.

**The version before the format** (``Wanted.have_by``): an unabridged reading takes the place of an abridged one or a
dramatisation at any format; a fuller version than the release's is never replaced by it; only a release of the same
version is weighed by format, against the goal of the ranking. A dramatisation is no unabridged reading.

**Formats**: the name says it (``EPUB``, ``FLAC``, ``MP3 128kbps``), or for an audiobook the bit rate is worked out
from the size and the running time of the book when that is known. A format the name does not tell stands at the
place of the last ticked format and is no reason against a release; the file says it after the download.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import book_words
from ..readers import names
from ..readers.text import key_fold
from . import release_match, release_name
from .release_name import ReleaseName

#: Ebook formats of a name to the formats of the ranking.
_EBOOK_RANK = {"epub": "epub", "azw3": "azw3", "mobi": "mobi", "pdf": "pdf"}
MB = 1_000_000
#: Above this size a release the name and the category say nothing about is an audiobook.
AUDIO_FROM_BYTES = 100 * MB
#: The variants of a reading in the order they are taken.
VARIANT_ORDER = {"unabridged": 0, "dramatized": 1, "abridged": 2}
#: Torrents with fewer seeders than this are not taken.
MIN_SEEDERS = 1
SURE_LANGUAGE = ("mark", "words")
#: Reasons in the order they are shown, the strongest first.
ORDER = ("blocklisted", "not_a_book", "sample", "soundtrack", "companion", "adaptation", "partial", "bundle",
         "author_mismatch", "title_mismatch", "other_volume", "other_language", "language_unsure", "bilingual",
         "edition", "collection", "maybe_partial", "title_short", "maybe_other_volume", "maybe_other_book",
         "author_missing", "size_odd", "abridged", "dramatized", "format_not_wanted", "too_small", "too_large",
         "not_enough_seeders", "older_than_retention",
         "not_better")
#: The doubts: never loaded by itself, never turned down (see the top).
ASK = frozenset({"language_unsure", "bilingual", "edition", "collection", "maybe_partial", "title_short",
                 "maybe_other_volume", "maybe_other_book", "author_missing", "size_odd"})
#: A release whose sizes are this far from the middle of the others of its format is a bundle or a part.
SIZE_ODD_FACTOR = 3.0
#: How many releases of a format there must be for the middle to say anything.
SIZE_ODD_FROM = 3
#: Up to this rate (kbit/s) an MP3 is the thin one of the ranking, ``mp3_64``, with what a changing rate and a little
#: room add to 64; a reading of 80 or 96 kbit/s, which is what much of a voice is made at, sounds like the next one.
MP3_THIN_UP_TO = 72
#: Bigger than this, whatever the others: an e-book (MB), an audiobook that is no lossless copy (MB). A whole series.
SIZE_ODD_EBOOK_MB = 30
SIZE_ODD_AUDIO_MB = 5000


@dataclass(frozen=True)
class Offer:
    """A release as an indexer gave it."""

    title: str
    size: int | None
    published_at: datetime | None
    protocol: str
    seeders: int | None = None
    peers: int | None = None
    grabs: int | None = None
    #: The priority of the indexer, 1 first.
    priority: int = 25
    #: ``ebook``, ``audio`` or None: what the category of the release is for.
    category_kind: str | None = None
    #: The language the indexer's category states (``de``) or ``mixed``.
    category_language: str = "mixed"
    blocklisted: bool = False
    #: The author and the title of the book as the indexer states them beside the name (``newznab.Item``); empty when
    #: it states none, and then nothing changes.
    stated_author: str = ""
    stated_title: str = ""


@dataclass(frozen=True)
class Wanted:
    book: release_match.Book
    #: The language looked for (a code); the first of ``accepted`` when several are.
    language: str
    #: The best format there is already, per kind (``epub``, ``mp3_128`` ...): an upgrade must be better. For an
    #: audiobook the format of its most complete version; ``have_by`` says more.
    have: dict[str, str | None] = field(default_factory=dict)
    #: The best format there is already per kind and version (``{"audio": {"abridged": "flac"}}``; an e-book has the
    #: version ``""``): a release must be better than what holds the same or a fuller version of the book, and a fuller
    #: version than any it has is never "not better".
    have_by: dict[str, dict[str, str | None]] = field(default_factory=dict)
    #: The languages that are taken when the person named none (every language the operator accepts); empty when
    #: ``language`` is the only one.
    accepted: tuple[str, ...] = ()
    #: The running time of the book in seconds, when a source or an edition knows it: for the bit rate.
    duration: int | None = None
    #: Days a Usenet post may be old; 0 for no limit.
    retention_days: int = 0
    #: The languages of what is there already, per kind (the editions ``have`` speaks of): an upgrade never goes from
    #: one language to another.
    have_languages: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def languages(self) -> tuple[str, ...]:
        return self.accepted or (self.language,)

    def held(self, kind: str) -> dict[str, str]:
        """What is there already, per version, for a kind: the formats that are known."""
        found = self.have_by.get(kind)
        if found is None:
            single = self.have.get(kind)
            found = {"": single} if kind == "ebook" and single else {"unabridged": single} if single else {}
        return {version: fmt for version, fmt in found.items() if fmt}


@dataclass
class Verdict:
    kind: str
    #: The format in the ranking's words (``epub``, ``m4b_aac``, ``mp3_192``), None when the name does not tell.
    format: str | None
    #: ``name``, ``estimate`` or ``unknown``.
    format_how: str
    tier: int
    language: str
    #: ``mark``, ``words``, ``title``, ``category`` or ``presumed``.
    language_how: str
    #: ``unabridged``, ``abridged``, ``dramatized`` for an audiobook, empty for an e-book.
    variant: str
    #: The bit rate in kbit/s (said or worked out), None when unknown.
    bitrate: int | None
    reasons: list[dict[str, Any]]
    score: int = 0
    rank: int | None = None
    #: The order among those of its kind that pass (smaller first).
    order: tuple[Any, ...] = ()

    @property
    def passes(self) -> bool:
        return not self.reasons

    @property
    def doubtful(self) -> bool:
        """Only doubts stand against it (``ASK``): a person may load it after the question."""
        return bool(self.reasons) and all(item["code"] in ASK for item in self.reasons)

    @property
    def language_sure(self) -> bool:
        return self.language_how in SURE_LANGUAGE


def reason(code: str, arg: Any = None) -> dict[str, Any]:
    return {"code": code} if arg is None else {"code": code, "arg": arg}


def kind_of(name: ReleaseName, offer: Offer) -> str:
    if name.kind:
        return name.kind
    if offer.category_kind:
        return offer.category_kind
    return "audio" if (offer.size or 0) >= AUDIO_FROM_BYTES else "ebook"


def audio_format(codec: str | None, bitrate: int | None) -> str | None:
    """The place in the audiobook ranking of a codec at a bit rate."""
    if codec == "flac":
        return "flac"
    if codec in ("m4b", "aac", "m4a", "opus", "ogg"):
        # AAC sounds at about half the rate as MP3 does: from 128 kbit/s it is the place of its own, below it the
        # place of an MP3 of twice the rate.
        if bitrate is None or bitrate >= 128:
            return "m4b_aac"
        return "mp3_128" if bitrate >= 64 else "mp3_64"
    if codec in ("mp3", "wma") or (codec is None and bitrate is not None):
        if bitrate is None:
            return None
        if bitrate >= 256:
            return "mp3_320"
        if bitrate >= 160:
            return "mp3_192"
        if bitrate > MP3_THIN_UP_TO:
            return "mp3_128"
        return "mp3_64"
    return None


def _ranking(config: dict[str, Any], kind: str) -> list[str]:
    return [item["f"] for item in config[kind]]


def _ticked(config: dict[str, Any], kind: str) -> list[str]:
    return [item["f"] for item in config[kind] if item["on"]]


def _format(name: ReleaseName, offer: Offer, kind: str, wanted: Wanted, config: dict[str, Any]) -> tuple[
        str | None, str, int | None]:
    if kind == "ebook":
        known = [_EBOOK_RANK[item] for item in name.formats if item in _EBOOK_RANK]
        if not known:
            return None, "unknown", None
        # A pack of several formats: the best one of the ranking that is ticked, else the first.
        order = _ranking(config, "ebook")
        ticked = set(_ticked(config, "ebook"))
        known.sort(key=lambda item: (item not in ticked, order.index(item)))
        return known[0], "name", None
    codecs = [item for item in name.formats if item in ("flac", "m4b", "aac", "mp3", "ogg", "opus", "wma")]
    codec = codecs[0] if codecs else None
    bitrate = name.bitrate
    how = "name"
    if bitrate is None and codec in (None, "mp3", "wma") and wanted.duration and offer.size:
        # Worked out from the size: a whole book in this many bytes over its running time.
        bitrate = int(offer.size * 8 / wanted.duration / 1000)
        how = "estimate"
        if codec is None and bitrate >= 400:
            return "flac", "estimate", bitrate
    found = audio_format(codec, bitrate)
    if found is None and codec == "mp3":
        return "mp3_128", "estimate", bitrate
    if found is None:
        return None, "unknown", bitrate
    return found, how if (codec is not None or how == "estimate") else "estimate", bitrate


def _title_tells(matched: release_match.Match, wanted: Wanted) -> bool:
    """Whether the title that matched says the language: only when it is not a language wished, and the book has a
    title in a language wished that is another one (``Nachtfalter`` known only from an English source is
    ``Nachtfalter`` in German as well: it says no language)."""
    if not matched.title_language or matched.title_language in wanted.languages():
        return bool(matched.title_language)
    found = [title for title in wanted.book.titles if title.language in wanted.languages()]
    return any(not release_match.title_is(title.text, release_match.Book(
        titles=tuple(item for item in wanted.book.titles if item.language == matched.title_language),
        authors=wanted.book.authors)) for title in found)


def _language(name: ReleaseName, matched: release_match.Match, offer: Offer, wanted: Wanted) -> tuple[str, str]:
    # A mark that is part of the title looked for (``The Dutch Garden``) or of the author's name (``Lena French``) is no
    # mark.
    code, how = release_name.language_of(name.language_signs, names_of_the_book(matched))
    if code and how:
        return code, how
    # Words of a language outside the title and the author (``The Stand - Das letzte Gefecht``, ``Aan de grond in
    # Londen en Parijs``): said in that language, stronger than a title known in one language only.
    if matched.words_language and matched.words_language not in wanted.languages():
        return matched.words_language, "words"
    if matched.words_language and matched.title_language and matched.title_language != matched.words_language:
        return matched.words_language, "words"
    if matched.title_language and _title_tells(matched, wanted):
        category = offer.category_language
        if category in wanted.languages() and matched.title_language not in wanted.languages():
            # The title is one of another language, the category of the indexer says another one (a German edition
            # under its English title in "Books - DE"): nothing to be sure of; a person decides.
            return category, "unsure"
        return matched.title_language, "title"
    if matched.article_language and matched.article_language not in wanted.languages() and not any(
            release_match.article_language(title.text) == matched.article_language
            for title in wanted.book.titles if title.language in wanted.languages() or not title.language):
        # ``The Hobbit`` for ``Der Hobbit``: the title is the one of both languages, its article the one of another
        # language than the one wished. A person decides (the file says it after the download).
        return wanted.language, "unsure"
    if offer.category_language and offer.category_language != "mixed":
        return offer.category_language, "category"
    return wanted.language, "presumed"


def judge(name: ReleaseName, offer: Offer, matched: release_match.Match, wanted: Wanted, config: dict[str, Any],
          now: datetime) -> Verdict:
    kind = kind_of(name, offer)
    found_format, format_how, bitrate = _format(name, offer, kind, wanted, config)
    ranking = _ranking(config, kind)
    ticked = _ticked(config, kind)
    if found_format is not None and found_format in ranking:
        tier = ranking.index(found_format)
    else:
        tier = ranking.index(ticked[-1]) if ticked else len(ranking)
    language, language_how = _language(name, matched, offer, wanted)
    variant = ""
    if kind == "audio":
        variant = "dramatized" if name.dramatized else ("abridged" if name.abridged else "unabridged")

    reasons: list[dict[str, Any]] = []
    if offer.blocklisted:
        reasons.append(reason("blocklisted"))
    if name.not_a_book:
        reasons.append(reason("not_a_book"))
    wished = " ".join(release_match.title_words(wanted.book))
    if name.sample and not _in_wished(release_name.sample_words(name.text), wished):
        # (A word of a sample the title wished holds itself is a word of the title: ``Der Auszug``.)
        reasons.append(reason("sample"))
    if name.soundtrack:
        reasons.append(reason("soundtrack"))
    if name.companion and not _in_wished(book_words.companion_word(name.text), wished):
        reasons.append(reason("companion"))
    if name.adaptation and not _in_wished(name.adaptation, wished):
        reasons.append(reason("adaptation"))
    part = _part(name, matched, wanted.book)
    if part is not None:
        reasons.append(reason("partial" if _part_is_sure(part, matched, wanted.book) else "maybe_partial", part))
    if name.bundle and not wanted.book.bundle and _bundle_counts(name, wanted.book):
        reasons.append(reason("bundle", list(name.bundle_range) if name.bundle_range else None))
    missing = False
    stated_author = release_match.names_an_author(offer.stated_author, wanted.book)
    if matched.other_volume:
        reasons.append(reason("other_volume", matched.volumes[0] if matched.volumes else None))
    elif matched.author_close and matched.title:
        # ``AB Feldkamp``, ``Feldkamp, Anna Berit``: the author in a close form; another person may have the
        # initials. A person decides.
        reasons.append(reason("maybe_other_book"))
    elif not matched.author and matched.title and stated_author and not matched.unexplained_before:
        # The name leaves the author out, the indexer names the author wished: the author is there.
        pass
    elif not matched.author and matched.title:
        if matched.unexplained_before:
            # Another name stands where the author stands.
            reasons.append(reason("author_mismatch"))
        elif matched.unexplained_after or not _title_says_enough(wanted.book):
            reasons.append(reason("author_missing"))
        else:
            # No author in the name, nothing against it, a title that says something by itself: it may be the book;
            # it comes after those that name the author, and the file decides.
            missing = True
    elif not matched.title and matched.subtitle_after and (matched.author or matched.author_close):
        # ``Das Parfum. Die Geschichte eines Mörders``, ``Momo oder Die seltsame Geschichte …``: the title wished and a
        # subtitle the catalogue does not know, or another book. A person decides.
        reasons.append(reason("maybe_other_book"))
    elif not matched.title:
        reasons.append(reason("title_short") if matched.short else reason("title_mismatch"))
    if matched.title and matched.author and (matched.unexplained_after or matched.unexplained_before) \
            and not matched.other_volume:
        reasons.append(reason("maybe_other_book"))
    if matched.title and not matched.other_volume and not any(item["code"] == "maybe_other_book" for item in reasons) \
            and (stated_author is False or release_match.names_another_book(offer.stated_title, wanted.book)):
        # The indexer states another author, or another book of the author, beside a name that fits: two voices, a
        # person decides.
        reasons.append(reason("maybe_other_book"))
    if matched.title and wanted.book.position is not None and matched.field_numbers and not matched.other_volume \
            and wanted.book.position not in matched.field_numbers:
        # ``Zwergen-Saga - 2 - Die Tenfel`` for the first volume: the number of another series, or another volume.
        reasons.append(reason("maybe_other_volume", matched.field_numbers[0]))
    if matched.title and part is None and not matched.other_volume and (
            matched.number_after is not None or (not wanted.book.series and book_words.names_volume(matched.tail))) \
            and not any(item["code"] == "maybe_other_volume" for item in reasons):
        # ``Erebos 3``, ``Krieg und Frieden - Band 2``: a volume of a book nobody knows a series of; ``Die Nacht 3``
        # for the second volume of a series the name does not say.
        reasons.append(reason("maybe_other_volume", matched.number_after))
    if name.edition and not _in_wished(name.edition, wished):
        reasons.append(reason("edition"))
    if name.collection and not _in_wished(name.collection, wished):
        reasons.append(reason("collection"))
    if name.bilingual or _two_languages(name, matched):
        reasons.append(reason("bilingual"))
    # Only the name (a mark or words of a language) and a title of one language can be a reason; the language the
    # operator gave a category is a hint, because indexers fill their categories with books of every language.
    if language not in wanted.languages() and language_how in ("mark", "words", "title"):
        reasons.append(reason("other_language", language))
    if language_how == "unsure":
        # Never loaded by itself, never turned down: "Releases ansehen" loads it after the question.
        reasons.append(reason("language_unsure", matched.title_language or matched.article_language))
    if kind == "audio":
        if variant == "dramatized" and not config.get("dramatized"):
            reasons.append(reason("dramatized"))
        if variant == "abridged" and (config.get("variant") == "unabridged" or not config.get("abridged")):
            reasons.append(reason("abridged", "settings"))
    if found_format is not None and found_format not in ticked:
        reasons.append(reason("format_not_wanted", found_format))
    if offer.size is not None:
        low, high = config["size"][kind]
        megabytes = offer.size / MB
        if megabytes < low:
            reasons.append(reason("too_small"))
        elif megabytes > high:
            reasons.append(reason("too_large"))
    if offer.protocol == "torrent" and offer.seeders is not None and offer.seeders < MIN_SEEDERS:
        reasons.append(reason("not_enough_seeders"))
    age = _age_days(offer, now)
    if offer.protocol == "usenet" and wanted.retention_days and age is not None and age > wanted.retention_days:
        reasons.append(reason("older_than_retention"))
    there = wanted.have_languages.get(kind) or ()
    ranked_languages = wanted.languages()
    higher = bool(there) and language in ranked_languages and all(
        ranked_languages.index(language) < (ranked_languages.index(item) if item in ranked_languages
                                            else len(ranked_languages)) for item in there)
    if not reasons and wanted.held(kind) and there and language not in there and not higher:
        # What is there is in another language: a release in this one is no better format of it, and a book that has
        # the kind in a language allowed needs no other, unless the release is of a language ranked above every one
        # there is (an upgrade over the language goes up, never down).
        reasons.append(reason("other_language", language))
    if not reasons and not higher:
        better = _not_better(kind, variant, found_format, tier, wanted, config, ranking)
        if better is not None:
            reasons.append(reason("not_better", better))
    reasons.sort(key=lambda item: ORDER.index(item["code"]))

    availability = _availability(offer, age)
    # The language the operator put first beats any format of a language below it (settings, "Sprachen").
    ranked = wanted.languages()
    language_rank = ranked.index(language) if language in ranked else len(ranked)
    order = (language_rank, VARIANT_ORDER.get(variant, 0), 1 if missing else 0, tier,
             0 if language_how in SURE_LANGUAGE else 1, offer.priority, -availability, -(offer.size or 0))
    score = 100 - 10 * tier - (0 if language_how in SURE_LANGUAGE else 8) - 15 * VARIANT_ORDER.get(variant, 0) \
        - min(9, max(0, offer.priority - 1) // 3) - round((1 - availability) * 5) - (12 if missing else 0)
    return Verdict(kind=kind, format=found_format, format_how=format_how, tier=tier, language=language,
                   language_how=language_how, variant=variant, bitrate=bitrate, reasons=reasons,
                   score=max(1, min(100, score)), order=order)


def _not_better(kind: str, variant: str, found_format: str | None, tier: int, wanted: Wanted, config: dict[str, Any],
                ranking: list[str]) -> str | None:
    """The format of what is there already when the release brings nothing, else None. The version of the book comes
    before the format: an unabridged reading takes the place of an abridged one or a dramatisation at any format, a
    version that is fuller than the release's is never replaced by it, and only a release of the same version is
    weighed by format, against the goal of the ranking."""
    held = wanted.held(kind)
    if not held:
        return None
    mine = VARIANT_ORDER.get(variant, 0)
    fuller = [fmt for version, fmt in held.items() if VARIANT_ORDER.get(version or "unabridged", 0) < mine]
    if fuller:
        return min(fuller, key=lambda fmt: ranking.index(fmt) if fmt in ranking else len(ranking))
    same = next((fmt for version, fmt in held.items() if VARIANT_ORDER.get(version or "unabridged", 0) == mine), None)
    if same is None:
        return None
    have_tier = ranking.index(same) if same in ranking else len(ranking)
    target = config["target"][kind]
    target_tier = ranking.index(target) if target in ranking else 0
    if found_format is None or tier >= have_tier or have_tier <= target_tier:
        return same
    return None


#: Words of a bundle that also name the whole of one work: ``Die Spiegelwelt - Gesamtausgabe`` is the work wished
#: when the book is no volume of a series.
_WHOLE_WORK = frozenset({"gesamtausgabe", "komplett", "komplettausgabe", "komplette ausgabe", "in einem band",
                         "in one volume", "complete", "vollstandige ausgabe", "vollstaendige ausgabe"})


def _bundle_counts(name: ReleaseName, book: release_match.Book) -> bool:
    """A range of volumes always makes a bundle; a word only when it is not a word of the title looked for (``Das
    Paket``, ``Die Trilogie der …``), and a word of the whole work (``Gesamtausgabe``) not for a book that is no volume
    of a series: that is the work in one."""
    if name.bundle_range is not None:
        return True
    title_words = " ".join(release_match.title_words(book))
    whole = book.position is None and not book.series
    return any(word.replace(" ", "") not in title_words.replace(" ", "") and not (whole and word in _WHOLE_WORK)
               for word in name.bundle_words)


def _in_wished(word: str, wished: str) -> bool:
    """Whether the words that make a release another book are part of the title wished (``Die Graphic Novel der
    Welt``)."""
    return bool(word) and word.replace(" ", "") in wished.replace(" ", "")


def _part(name: ReleaseName, matched: release_match.Match, book: release_match.Book) -> list[int | None] | None:
    """``[part, parts]`` when what follows the title names a part (``Teil 2 von 2``, ``Part 1``, ``CD 7-12``, ``Band 2
    von 4``), else None. For a volume of a series a part counts beside the volume (``Band 01 - Die Tenfel, Teil 1``)
    and in a form that never names a volume (``Teil 1 von 2``, ``Part 2``, ``CD 1-4``, ``Disc 3``): ``Teil 3`` alone
    is the volume there, and the volume decides. A part the title wished names itself (``Faust. Der Tragödie erster
    Teil``) is that book."""
    if not matched.title:
        return None
    found = book_words.part_of(matched.tail)
    if found is None:
        return None
    titled = release_match.titles_volume(book)
    if titled is not None and found[0] == titled and found[1] is None:
        return None
    if titled is None and found == (1, None) and book_words.part_of(matched.tail, words=False) is None:
        # ``Faust - Der Tragödie erster Teil`` for ``Faust``: the first part in words is the work as it is known.
        return None
    wished = book_words.part_of(" ".join(item.text for item in book.titles))
    if wished is not None and wished[0] == found[0]:
        return None
    # What stands before the tail: the words of the name up to it (the tail is joined by spaces, the name by its own
    # marks, so the words are counted, not the text cut).
    words = release_name.words_of(name.text)
    before = " ".join(words[:max(0, len(words) - len(release_name.words_of(matched.tail)))])
    if book.position is not None and not book_words.names_volume(before):
        sure = book_words.sure_part(matched.tail)
        if sure is not None:
            found = sure
        elif not (found[0] != book.position and book.position in matched.volumes):
            # ``Teil 3`` alone is the volume; beside the volume wished given elsewhere (``Reihe 1 - Titel - Teil 2``)
            # it is a part of it.
            return None
    return [found[0], found[1]]


def _part_is_sure(part: list[int | None], matched: release_match.Match, book: release_match.Book) -> bool:
    """A part is sure when the name says how many there are (``Teil 2 von 2``), gives a disc or a range of them (``CD
    7-12``, ``Disc 3``), or, beside a volume of a series, a part that is not the number of the volume (``Part 2`` for
    the first one); a bare ``Teil 1`` or ``Part 1`` may also be a title (``Faust. Der Tragödie erster Teil``): a
    question."""
    if part[1] is not None or book_words.disc_range(matched.tail):
        return True
    if re.search(r"(?<![a-z])(?:cd|disc|disk)\s*\d", book_words.spelled_numbers(matched.tail)):
        return True
    return book.position is not None and part[0] != book.position


def _title_says_enough(book: release_match.Book) -> bool:
    """Whether a title of the book says something by itself (two words besides the articles, or a long word): only
    then a name without an author can be the book."""
    return any(names.distinct_title_key(key_fold(names.core_title(title.text))) for title in book.titles)


def _two_languages(name: ReleaseName, matched: release_match.Match) -> bool:
    """Two marks of two languages outside the title and the author (``DE-EN``, ``Englisch-Deutsch``): a book in two
    languages."""
    marks = {code for code, how, start, end in name.language_signs if how == "mark" and not any(
        start >= first and end <= last for first, last in names_of_the_book(matched))}
    return len(marks) >= 2


def names_of_the_book(matched: release_match.Match) -> tuple[tuple[int, int], ...]:
    """The places of the name that are a title of the book, an author or the series: a word of a language in there is
    part of a name (``The Dutch Garden``, ``Lena French``), never a sign of the language of the release."""
    return matched.title_spans + matched.author_spans + matched.series_spans


def _age_days(offer: Offer, now: datetime) -> float | None:
    if offer.published_at is None:
        return None
    return max(0.0, (now - offer.published_at).total_seconds() / 86400)


def _availability(offer: Offer, age: float | None) -> float:
    """0 to 1: how likely the download comes through. A torrent by its seeders, a Usenet post by its age (an old post
    has lost parts more often)."""
    if offer.protocol == "torrent":
        return min(offer.seeders or 0, 20) / 20
    if age is None:
        return 0.75
    return 1.0 - min(age, 3000) / 3000 * 0.5


def _odd_sizes(verdicts: list[Verdict], sizes: dict[int, int]) -> None:
    """A release of a format far bigger or smaller than the middle of the others of that format and kind that pass
    (``SIZE_ODD_FACTOR``): a bundle without a word for it (``Die Zwerge`` in 12 GB), or a part (85 MB beside 300 MB).
    Only where there are enough of them for a middle to say anything."""
    for kind in ("ebook", "audio"):
        groups: dict[str | None, list[Verdict]] = {}
        for item in verdicts:
            if item.kind == kind and (item.passes or item.doubtful) and sizes.get(id(item)):
                groups.setdefault(item.format, []).append(item)
        odd: dict[int, str] = {}
        for group in groups.values():
            if len(group) >= SIZE_ODD_FROM:
                ordered = sorted(sizes[id(item)] for item in group)
                middle = ordered[len(ordered) // 2]
                for item in group:
                    size = sizes[id(item)]
                    if size > middle * SIZE_ODD_FACTOR or size * SIZE_ODD_FACTOR < middle:
                        odd[id(item)] = "large" if size > middle else "small"
            for item in group:
                megabytes = sizes[id(item)] / MB
                if (kind == "ebook" and item.format != "pdf" and megabytes > SIZE_ODD_EBOOK_MB) or (
                        kind == "audio" and item.format != "flac" and megabytes > SIZE_ODD_AUDIO_MB):
                    odd[id(item)] = "large"
        for item in verdicts:
            if id(item) in odd and item.passes:
                item.reasons.append(reason("size_odd", odd[id(item)]))


def rank(verdicts: list[Verdict], sizes: list[int | None] | None = None) -> None:
    """Numbers those that pass per kind, the best first. An abridged reading passes only when no unabridged one does
    (the operator's rule "only when there is no unabridged one"); a dramatisation is no unabridged reading and keeps
    nobody out. With ``sizes`` (the size of each release, in the order of ``verdicts``) a release far from the others of
    its format is a doubt (``size_odd``)."""
    if sizes is not None:
        _odd_sizes(verdicts, {id(item): size for item, size in zip(verdicts, sizes, strict=False) if size})
    for kind in ("ebook", "audio"):
        passing = [item for item in verdicts if item.kind == kind and item.passes]
        if kind == "audio" and any(item.variant == "unabridged" for item in passing):
            for item in passing:
                if item.variant == "abridged":
                    item.reasons.append(reason("abridged", "alternative"))
            passing = [item for item in passing if item.passes]
        passing.sort(key=lambda item: item.order)
        for position, item in enumerate(passing, start=1):
            item.rank = position


def summary(verdicts: list[Verdict], kind: str) -> dict[str, Any]:
    """The card of a kind: how many there are, how many pass, the most frequent reasons."""
    mine = [item for item in verdicts if item.kind == kind]
    counts: dict[str, int] = {}
    for item in mine:
        for found in item.reasons:
            counts[found["code"]] = counts.get(found["code"], 0) + 1
    top = sorted(counts.items(), key=lambda pair: (-pair[1], ORDER.index(pair[0])))[:3]
    return {"total": len(mine), "passing": sum(1 for item in mine if item.passes),
            "reasons": [{"code": code, "count": count} for code, count in top]}


__all__ = ["ASK", "ORDER", "Offer", "Verdict", "Wanted", "audio_format", "judge", "kind_of", "rank", "summary"]
