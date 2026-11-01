"""What a release name and its indexer say beside the title: a bare number after a title is perhaps another volume, a
word of a language inside the name of the book is no sign of a language, the words of a play are one list, and the
author and the title an indexer states (``extended=1``) are a voice of their own. Only invented books and names."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.services import book_words
from app.services.procure import formats, judge, newznab, release_match, release_name

from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, by_title, codes, make_world, search

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
AUTHOR = "Rune Falkner"


def verdict(name: str, title: str, *, series: str = "", position: float | None = None, others: tuple[str, ...] = (),
            language: str = "de", stated_author: str = "", stated_title: str = "") -> judge.Verdict:
    book = release_match.Book(titles=(release_match.Title(title, language),), authors=(AUTHOR,), series=series,
                              position=position, others=others)
    parsed = release_name.parse(name)
    offer = judge.Offer(title=name, size=3_000_000, published_at=None, protocol="usenet", stated_author=stated_author,
                        stated_title=stated_title)
    return judge.judge(parsed, offer, release_match.match(parsed, book), judge.Wanted(book=book, language=language),
                       formats.default_config(), NOW)


def outcome(found: judge.Verdict) -> tuple[str, list[str]]:
    said = [item["code"] for item in found.reasons]
    return ("pass" if found.passes else "ask" if found.doubtful else "reject"), said


# --- A bare number after the title -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "series", "position"), [
    ("Rune.Falkner-Nebelchor.3.GERMAN.EPUB-EXAMPLE", "", None),
    ("Rune.Falkner-Nebelchor.01.GERMAN.EPUB-EXAMPLE", "", None),
    ("Rune Falkner - Nebelchor 2 (2021) GERMAN EPUB", "", None),
    # The series wished has another name, and the number is not its volume.
    ("Rune.Falkner-Nebelchor.2.GERMAN.EPUB-EXAMPLE", "Laternen", 3.0),
])
def test_a_bare_number_after_the_title_of_a_book_is_a_question(name: str, series: str, position: float | None) -> None:
    assert outcome(verdict(name, "Nebelchor", series=series, position=position)) == ("ask", ["maybe_other_volume"])


def test_a_bare_number_after_a_long_title_is_a_question() -> None:
    found = verdict("Rune.Falkner-Der.Wind.ist.dem.Regen.sein.Feind.03.GERMAN.EPUB-EXAMPLE",
                    "Der Wind ist dem Regen sein Feind")
    assert outcome(found) == ("ask", ["maybe_other_volume"])


@pytest.mark.parametrize(("name", "series", "position"), [
    ("Rune.Falkner-Nebelchor.2024.GERMAN.EPUB-EXAMPLE", "", None),
    ("Rune Falkner - Nebelchor (05-2021) GERMAN EPUB", "", None),
    ("Rune.Falkner-Nebelchor.3.GERMAN.EPUB-EXAMPLE", "Laternen", 3.0),
])
def test_a_year_a_date_and_the_volume_wished_are_no_question(name: str, series: str, position: float | None) -> None:
    assert outcome(verdict(name, "Nebelchor", series=series, position=position)) == ("pass", [])


def test_a_rate_after_the_title_is_no_word_of_another_book() -> None:
    found = verdict("Rune.Falkner-Nebelchor.320.kbps.GERMAN.MP3-EXAMPLE", "Nebelchor")
    assert "title_mismatch" not in outcome(found)[1] and "maybe_other_volume" not in outcome(found)[1]


def test_a_number_that_is_part_of_the_title_wished_is_the_title() -> None:
    assert outcome(verdict("Rune.Falkner-Nebelchor.3.GERMAN.EPUB-EXAMPLE", "Nebelchor 3")) == ("pass", [])


# --- A word of a language inside a name of the book ------------------------------------------------------------------


def test_a_word_of_a_language_in_the_name_of_the_series_is_no_sign() -> None:
    name = "Rune Falkner - French Quarter 2 - Die Laternenstrasse - GERMAN EPUB"
    found = verdict(name, "Die Laternenstraße", series="French Quarter", position=2.0)
    assert outcome(found) == ("pass", []) and found.language == "de"


def test_a_word_of_a_language_outside_the_names_of_the_book_still_counts() -> None:
    found = verdict("Rune Falkner - Die Laternenstrasse - FRENCH EPUB", "Die Laternenstraße")
    assert outcome(found)[0] == "reject" and found.language == "fr"


# --- The words of a play ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("words", ["Hoerspiel", "Full Cast", "Full-Cast", "GraphicAudio", "Graphic Audio", "Multicast",
                                   "Multi-Cast", "Audio Theatre", "Audio Theater", "Movie in Your Mind",
                                   "Ensemble Cast", "Cast Performance", "Radio Drama"])
def test_every_word_of_a_play_makes_a_release_a_play(words: str) -> None:
    assert release_name.parse(f"Rune Falkner - Nebelchor - {words} - MP3").dramatized
    assert book_words.dramatized_word(f"Nebelchor ({words})")


def test_a_reading_is_no_play() -> None:
    assert not release_name.parse("Rune Falkner - Nebelchor - Ungekuerzte Lesung - MP3").dramatized
    assert not book_words.dramatized_word("The Cast of the Ship")


# --- The author and the title an indexer states ----------------------------------------------------------------------


def _items(attributes: str) -> list[newznab.Item]:
    body = ('<?xml version="1.0"?><rss version="2.0" xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/">'
            '<channel><title>T</title><item><title>Nebelchor.GERMAN.EPUB-EXAMPLE</title><guid>a</guid>'
            '<enclosure url="https://indexer.example.com/get/1" length="9" type="application/x-nzb"/>'
            f'{attributes}</item></channel></rss>').encode()
    root, namespaces = newznab.parse_xml(body)
    assert root is not None
    return list(newznab.parse_feed(root, namespaces).releases)


def test_an_indexer_that_states_author_and_title_gives_them_with_the_item() -> None:
    [item] = _items('<newznab:attr name="author" value="  Rune   Falkner "/>'
                    '<newznab:attr name="booktitle" value="Nebelchor"/>')
    assert (item.author, item.book_title) == ("Rune Falkner", "Nebelchor")


def test_an_indexer_that_states_nothing_gives_nothing() -> None:
    [item] = _items('<newznab:attr name="author" value=""/>')
    assert (item.author, item.book_title) == ("", "")


def test_the_author_the_indexer_states_stands_in_for_the_author_the_name_leaves_out() -> None:
    name = "Regen.GERMAN.EPUB-EXAMPLE"
    assert outcome(verdict(name, "Regen")) == ("ask", ["author_missing"])
    assert outcome(verdict(name, "Regen", stated_author="Falkner, Rune")) == ("pass", [])
    assert outcome(verdict(name, "Regen", stated_author="Ida Brandt")) == ("ask", ["maybe_other_book", "author_missing"])


def test_another_author_or_another_book_the_indexer_states_is_a_question() -> None:
    name = "Rune.Falkner-Nebelchor.GERMAN.EPUB-EXAMPLE"
    assert outcome(verdict(name, "Nebelchor")) == ("pass", [])
    assert outcome(verdict(name, "Nebelchor", stated_author="Ida Brandt")) == ("ask", ["maybe_other_book"])
    assert outcome(verdict(name, "Nebelchor", others=("Nebelchor im Winter",),
                           stated_title="Nebelchor im Winter")) == ("ask", ["maybe_other_book"])
    # A title in another language or with a subtitle is no voice against the book.
    assert outcome(verdict(name, "Nebelchor", others=("Nebelchor im Winter",),
                           stated_title="Nebelchor: Ein Krimi")) == ("pass", [])


def test_a_search_hears_the_author_the_indexer_states(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, language="en")
    plain, stated = "Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", "Die.Laternenstrasse.GERMAN.EPUB-OTHER"
    world.indexer.items = [Item(plain), Item(stated, extra={"author": "Ida Brandt"})]
    found = by_title(search(world, language="de"))
    assert "maybe_other_book" not in codes(found[plain])
    assert "maybe_other_book" in codes(found[stated])


# --- A wish whose releases only have doubts --------------------------------------------------------------------------


def test_a_wish_whose_releases_only_have_doubts_waits_for_a_look_and_loads_nothing(operator: Any,
                                                                                   net: FakeNetwork) -> None:
    from app.db import SessionLocal
    from app.models import Download, ProcureWish

    from .library_helpers import run_jobs

    world = make_world(net, language="en")
    # The world's book is the second volume: a 3 after its title is perhaps another one.
    world.indexer.items = [Item("Rune.Falkner-Die.Laternenstrasse.3.GERMAN.EPUB-EXAMPLE")]
    before = world.client.get("/api/v1/procure/counts").json()["needs"]
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["ebook"], "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    with SessionLocal() as db:
        row = db.query(ProcureWish).one()
        assert row.state == "not_found" and (row.detail or {}).get("look") == 1, (row.state, row.detail)
        assert db.query(Download).count() == 0
    # (It stands under "Gesucht" with its releases to look at: nothing asks a person about it.)
    assert world.client.get("/api/v1/procure/counts").json()["needs"] == before
    [shown] = world.client.get("/api/v1/procure/wishes").json()["wishes"]
    assert shown["detail"]["look"] == 1


def test_a_wish_whose_releases_are_turned_down_has_nothing_to_look_at(operator: Any, net: FakeNetwork) -> None:
    from app.db import SessionLocal
    from app.models import ProcureWish

    from .library_helpers import run_jobs

    world = make_world(net, language="en")
    world.indexer.items = [Item("Rune.Falkner-Die.Laternenstrasse.Leseprobe.GERMAN.EPUB-EXAMPLE")]
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["ebook"], "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    with SessionLocal() as db:
        row = db.query(ProcureWish).one()
        assert row.state == "not_found" and "look" not in (row.detail or {}), (row.state, row.detail)


# --- The interface knows every reason --------------------------------------------------------------------------------

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_the_interface_has_words_for_every_reason_and_knows_the_doubts() -> None:
    import json
    import re

    for language in ("de", "en"):
        words = json.loads((FRONTEND / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        reasons = words["procure"]["releases"]["reasons"]
        missing = [code for code in judge.ORDER if not (reasons.get(code) or {}).get("chip")
                   or not (reasons.get(code) or {}).get("text")]
        assert missing == [], (language, missing)
    listed = re.search(r"DOUBTS: ReadonlySet<string> = new Set\(\[([^\]]*)\]\)",
                       (FRONTEND / "lib" / "releases.ts").read_text(encoding="utf-8"))
    assert listed is not None
    assert set(re.findall(r"'([a-z_]+)'", listed.group(1))) == set(judge.ASK)


# --- A better format replaces only the same edition of the text ------------------------------------------------------


def test_an_e_book_replaces_the_old_format_unless_what_both_say_tells_two_editions_apart() -> None:
    from app.services.procure import checking

    old = checking.Have(fmt="mobi", isbn="978-3-00-000000-1", translators=["Ida Leser"], pages=300,
                        publisher="Laternen Verlag")
    # The same ISBN or translator is the same text, whatever else differs.
    assert checking.same_text(old, isbn="9783000000001", pages=120, publisher="Anderer Verlag", fmt="epub")
    assert checking.same_text(old, translators=["Leser, Ida"], isbn="9783000000002", fmt="epub")
    # Two ISBNs, two translators: two editions.
    assert not checking.same_text(old, isbn="9783000000002", fmt="epub")
    assert not checking.same_text(old, translators=["Mara Brandt"], fmt="epub")
    # Pages far apart (a school edition), or apart from another publisher.
    bare = checking.Have(fmt="mobi", pages=300, publisher="Laternen Verlag")
    assert not checking.same_text(bare, pages=150, fmt="epub")
    assert not checking.same_text(bare, pages=380, publisher="Anderer Verlag", fmt="epub")
    assert checking.same_text(bare, pages=380, publisher="Laternen Verlag", fmt="epub")
    # Pages close but two publishers, and the old one says something of its own: nothing proves one edition, a
    # person decides.
    assert not checking.same_text(bare, pages=310, publisher="Anderer Verlag", fmt="epub")
    assert checking.text_against(bare, checking.Have(fmt="epub", pages=310, publisher="Anderer Verlag")) == "unsure"
    # Nothing known against it: the book the check took for the one wished.
    assert checking.same_text(checking.Have(fmt="pdf"), fmt="epub")
    # The pages of a PDF are those of its print: no measure against an EPUB.
    assert checking.same_text(checking.Have(fmt="pdf", pages=3), pages=300, fmt="epub")
    assert checking.same_version("ebook", old, language="de", text=checking.Have(fmt="epub", isbn="9783000000002")) \
        is False
