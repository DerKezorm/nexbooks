"""Which language a release and a book are in, and which language is taken first:

* a word of a language in the name of the author (``Lena French``, ``Tom Deutsch``) is no mark of a language.
* a title known in one language only says no language when the book is wished in another one (a title that is the same in both languages).
* after the download the package, a sample of the text and the name of the release are witnesses together; one
  that says another language than the wish is a question, never a silent yes or no.
* a recording is held against its tags, the name of the release and the name of its folder; nothing at all is a
  question, with one language allowed as well.
* the languages have an order; a release in the first beats any format in a lower one, the original language has
  its place in it, and an upgrade never goes from one language to another.

Only invented books and names.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.db import SessionLocal
from app.services import catalog
from app.services.procure import checking, formats, judge
from app.services.procure import release_match as rm
from app.services.procure.release_name import parse

from .procure_search_helpers import allow_languages

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
MB = 1_000_000


def verdict(name: str, wanted: judge.Wanted, *, size: int = 2 * MB) -> judge.Verdict:
    parsed = parse(name)
    found = judge.Offer(title=name, size=size, published_at=NOW - timedelta(days=3), protocol="usenet")
    return judge.judge(parsed, found, rm.match(parsed, wanted.book), wanted, formats.default_config(), NOW)


def codes(found: judge.Verdict) -> list[str]:
    return [item["code"] for item in found.reasons]


def book(title: str, authors: tuple[str, ...], *titles: tuple[str, str]) -> rm.Book:
    return rm.Book(titles=tuple(rm.Title(text, language) for text, language in titles) or (rm.Title(title, ""),),
                   authors=authors)


# --- The name of the author -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "name", "wanted"), [
    ("french-as-a-name", "Lena French - Blauer Montag - Hoerbuch",
     judge.Wanted(book=book("Blauer Montag", ("Lena French",), ("Blauer Montag", "de"), ("Blue Monday", "en")),
                  language="de")),
    ("french-as-a-name-en", "Lena.French.-.Blue.Monday.EPUB",
     judge.Wanted(book=book("Blue Monday", ("Lena French",), ("Blue Monday", "en")), language="en")),
    ("deutsch-as-a-name", "Tom Deutsch - The Fabric of Sky - EPUB",
     judge.Wanted(book=book("The Fabric of Sky", ("Tom Deutsch",), ("The Fabric of Sky", "en")), language="en")),
])
def test_a_word_of_a_language_in_the_name_of_the_author_is_no_mark(case: str, name: str,
                                                                  wanted: judge.Wanted) -> None:
    found = verdict(name, wanted, size=400 * MB if "Hoerbuch" in name else 2 * MB)
    assert "other_language" not in codes(found), (case, codes(found), found.language, found.language_how)


def test_a_mark_after_the_title_is_still_a_mark() -> None:
    # "ES" after the title is Spanish.
    wanted = judge.Wanted(book=book("Nebelchor", ("Rune Falkner",), ("Nebelchor", "de")), language="de")
    assert "other_language" in codes(verdict("Rune Falkner - Nebelchor - ES - EPUB", wanted))


# --- A title of one language -----------------------------------------------------------------------------------


def test_a_title_known_only_in_another_language_says_no_language() -> None:
    # a book of the sources from an English source knows only its one title; the German edition is called the same.
    wanted = judge.Wanted(book=book("Nachtfalter", ("Rune Falkner",), ("Nachtfalter", "en")), language="de")
    found = verdict("Rune.Falkner.Nachtfalter.EPUB", wanted)
    assert "other_language" not in codes(found) and found.language_how == "presumed", codes(found)


def test_a_title_of_another_language_still_counts_when_the_wished_one_is_known_and_another() -> None:
    wanted = judge.Wanted(book=book("Die Laternenstraße", ("Rune Falkner",), ("Die Laternenstraße", "de"),
                                    ("The Lantern Road", "en")), language="de")
    found = verdict("Rune.Falkner.-.The.Lantern.Road.EPUB", wanted)
    assert "other_language" in codes(found) and found.language_how == "title"


# --- The order of the languages ---------------------------------------------------------------------------------

LANTERN = book("Die Laternenstraße", ("Rune Falkner",), ("Die Laternenstraße", "de"), ("The Lantern Road", "en"))


def test_a_release_in_the_first_language_beats_any_format_in_a_lower_one() -> None:
    wanted = judge.Wanted(book=LANTERN, language="de", accepted=("de", "en"))
    german_mobi = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.MOBI-EXAMPLE", wanted)
    english_epub = verdict("Rune.Falkner.-.The.Lantern.Road.ENGLISH.EPUB-EXAMPLE", wanted)
    assert german_mobi.passes and english_epub.passes
    assert german_mobi.order < english_epub.order
    # The other way round when English comes first.
    first_english = judge.Wanted(book=LANTERN, language="en", accepted=("en", "de"))
    assert verdict("Rune.Falkner.-.The.Lantern.Road.ENGLISH.EPUB-EXAMPLE", first_english).order < verdict(
        "Rune.Falkner.-.Die.Laternenstrasse.GERMAN.MOBI-EXAMPLE", first_english).order


def test_an_upgrade_never_goes_from_one_language_to_another() -> None:
    wanted = judge.Wanted(book=LANTERN, language="de", accepted=("de", "en"), have={"ebook": "mobi"},
                          have_by={"ebook": {"": "mobi"}}, have_languages={"ebook": ("de",)})
    english = verdict("Rune.Falkner.-.The.Lantern.Road.ENGLISH.EPUB-EXAMPLE", wanted)
    assert codes(english) == ["other_language"]
    german = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", wanted)
    assert german.passes, codes(german)


def test_the_original_language_has_its_place_in_the_order(operator: Any) -> None:
    allow_languages("de", "original", "en", original=True)
    with SessionLocal() as db:
        assert formats.allowed_languages(db, "fr") == ["de", "fr", "en"]
        assert formats.allowed_languages(db, "en") == ["de", "en"]
        assert formats.allowed_languages(db, "") == ["de", "en"]
        assert formats.accepted_languages(db) == ("de", "en")
    allow_languages("de", "original", "en", original=False)
    with SessionLocal() as db:
        assert formats.config(db)["languages"] == ["de", "en"]
        assert formats.allowed_languages(db, "fr") == ["de", "en"]


def test_a_saved_choice_without_the_place_of_the_original_has_it_last(operator: Any) -> None:
    from app.services import settings_service

    with SessionLocal() as db:
        stored = formats.default_config()
        stored["languages"], stored["original"] = ["en", "de"], True
        settings_service.save(db, {formats.KEY: stored})
        assert formats.config(db)["languages"] == ["en", "de", "original"]
        with pytest.raises(HTTPException) as refused:
            formats.save(db, {**stored, "languages": ["de", "original", "original"]})
        assert refused.value.detail["code"] == "invalid_input"


def test_the_dialog_is_offered_the_languages_in_their_order(operator: Any, net: Any) -> None:
    from app.models import ForeignBook

    from .procure_search_helpers import AUTHOR, TITLE, make_world

    world = make_world(net)
    with SessionLocal() as db:
        db.add(ForeignBook(token="tok" + "o" * 13, ref="hardcover:5150", title=TITLE, authors=[AUTHOR],
                           language="fr"))
        db.commit()
    allow_languages("en", "original", "de", original=True)
    options = world.client.get("/api/v1/procure/book-options", params={"book": "tok" + "o" * 13}).json()
    assert options["languages"] == ["en", "fr", "de"]


# --- The language after the download -------------------------------------------------------------------------


def unit(kind: str = "ebook", *, package: str = "", text: str = "", folder: str = "release",
         listed: tuple[str, ...] = ()) -> checking.Unit:
    probe: dict[str, Any] = {"text_language": text}
    if listed:
        probe["languages"] = list(listed)
    name = "book.epub" if kind == "ebook" else "01.mp3"
    files = [SimpleNamespace(path=f"{folder}/{name}", probe=probe, format="epub" if kind == "ebook" else "mp3",
                             origin="source", size=1)]
    return checking.Unit(key="k", kind=kind, files=files, meta=catalog.Meta(kind=kind, language=package))


def wanted(kind: str = "ebook", *, language: str = "de", accepted: tuple[str, ...] = (), named: str = "",
           several: bool = False, decision: dict[str, Any] | None = None) -> checking.Wanted:
    return checking.Wanted(kind=kind, language=language, accepted=list(accepted) or [language], subject=None,
                           config=formats.default_config(), named_language=named, several_allowed=several,
                           decision=decision or {})


def outcome(found: checking.Verdict | None) -> str:
    return "take" if found is None else f"{found.outcome}:{found.code}"


@pytest.mark.parametrize(("case", "book_unit", "wish", "expected"), [
    ("all say the wish", unit(package="de", text="de"), wanted(named="de"), "take"),
    ("package only, the wish", unit(package="de"), wanted(), "take"),
    ("package only, another", unit(package="es"), wanted(), "reject:other_language"),
    ("package and text another", unit(package="es", text="es"), wanted(), "reject:other_language"),
    ("package wrong, text right", unit(package="es", text="de"), wanted(), "ask:language_unclear"),
    ("text against a fitting package", unit(package="de", text="en"), wanted(), "ask:language_unclear"),
    ("text alone says another", unit(text="en"), wanted(), "ask:language_unclear"),
    ("name against the package", unit(package="en"), wanted(named="de"), "ask:language_unclear"),
    ("two languages in the package", unit(package="es", listed=("es", "de")), wanted(), "ask:language_unclear"),
    ("nothing, several allowed", unit(), wanted(language="", accepted=("de", "en")), "ask:language_unknown"),
    ("nothing, one wished of several allowed", unit(), wanted(several=True), "ask:language_unknown"),
    # Looked for in one language only is no proof that it is in it.
    ("nothing, one allowed", unit(), wanted(), "ask:language_unknown"),
    ("only the name of the release, one allowed", unit(), wanted(named="de"), "take"),
    ("a person said which", unit(package="es", text="de"), wanted(decision={"language": True}), "take"),
])
def test_the_witnesses_of_the_language_of_an_e_book(case: str, book_unit: checking.Unit, wish: checking.Wanted,
                                                    expected: str) -> None:
    assert outcome(checking._language_verdict(book_unit, wish)) == expected, case


@pytest.mark.parametrize(("case", "book_unit", "wish", "expected"), [
    ("no tag, no mark, several allowed", unit("audio"), wanted("audio", language="", accepted=("de", "en")),
     "ask:language_unknown"),
    ("the folder says it", unit("audio", folder="Rune Falkner - Nebelchor - Hoerbuch - DE"),
     wanted("audio", language="", accepted=("de", "en")), "take"),
    ("the name says it", unit("audio"), wanted("audio", language="", accepted=("de", "en"), named="de"), "take"),
    ("the tag says another", unit("audio", package="en"), wanted("audio"), "reject:other_language"),
    ("the tag against the name", unit("audio", package="en"), wanted("audio", named="de"), "ask:language_unclear"),
])
def test_the_witnesses_of_the_language_of_a_recording(case: str, book_unit: checking.Unit, wish: checking.Wanted,
                                                     expected: str) -> None:
    assert outcome(checking._language_verdict(book_unit, wish)) == expected, case


# --- The language of the indexer's categories (real names of releases, fourth addition) -------------------------


def test_a_category_without_a_language_inherits_the_one_it_stands_under() -> None:
    from app.services.procure import newznab

    caps = {"categories": [
        {"id": 7000, "name": "Books", "subcats": [
            {"id": 7020, "name": "Ebook"}, {"id": 7100, "name": "Books - DE"}, {"id": 7120, "name": "Ebook"},
            {"id": 7300, "name": "Books - FR"}, {"id": 7320, "name": "Ebook"}]},
        {"id": 3100, "name": "Audio - DE", "subcats": [{"id": 3130, "name": "Audiobook"}]},
    ]}
    found = {item.id: item.lang for item in newznab.book_categories(caps)}
    assert found[7120] == "de" and found[7320] == "fr" and found[3130] == "de"
    assert found[7020] == "mixed"


def test_a_title_of_another_language_in_a_category_of_the_wished_one_is_a_question() -> None:
    wanted = judge.Wanted(book=LANTERN, language="de")
    parsed = parse("Falkner,.Rune.-.2021.-.The.Lantern.Road")
    offer = judge.Offer(title=parsed.text, size=2 * MB, published_at=NOW - timedelta(days=3), protocol="usenet",
                        category_language="de")
    found = judge.judge(parsed, offer, rm.match(parsed, wanted.book), wanted, formats.default_config(), NOW)
    assert codes(found) == ["language_unsure"] and not found.passes
    # The same in a category that names no language: the title says English, as before.
    plain = verdict("Falkner,.Rune.-.2021.-.The.Lantern.Road", wanted)
    assert codes(plain) == ["other_language"]


def test_the_name_of_an_author_with_a_word_of_a_language_is_no_mark_either() -> None:
    wanted = judge.Wanted(book=book("Das stille Moor", ("Tara French",), ("Das stille Moor", "de")), language="de")
    found = verdict("Tara.French.-.Das.stille.Moor.EPUB-EXAMPLE", wanted)
    assert "other_language" not in codes(found), (found.language, found.language_how)


def test_the_category_speaks_for_a_recording_only_when_nothing_else_does() -> None:
    wish = wanted("audio", language="", accepted=("de", "en"))
    wish.category_language = "de"
    assert outcome(checking._language_verdict(unit("audio"), wish)) == "take"
    other = wanted("audio")
    other.category_language = "en"
    assert outcome(checking._language_verdict(unit("audio"), other)) == "ask:language_unclear"
    tagged = wanted("audio", language="en")
    tagged.category_language = "de"
    assert outcome(checking._language_verdict(unit("audio", package="en"), tagged)) == "take"
