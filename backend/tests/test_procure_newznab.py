"""What an indexer says, read safely (``services/procure/newznab.py``): the caps with the book search and its parameters,
the book categories sorted out of them, the error documents, and XML that is not what it claims to be."""

from __future__ import annotations

import pytest

from app.services.procure import newznab

from .procure_fakes import error_document, fixture


def caps(name: str = "newznab_caps.xml") -> dict:
    root, _ = newznab.parse_xml(fixture(name), "application/xml")
    assert root is not None
    found = newznab.parse_caps(root)
    assert found is not None
    return found


def test_the_caps_keep_the_supported_parameters_of_every_search() -> None:
    found = caps()
    assert found["search_params"] == ["q", "group", "limit", "cat"]
    assert found["book_search"] is True and found["book_params"] == ["q", "author", "isbn"]
    assert found["audio_search"] is True and found["audio_params"] == ["q", "artist", "album"]
    assert found["limit_max"] == 100 and found["limit_default"] == 50
    assert [category["id"] for category in found["categories"]] == [3000, 5000, 7000]
    assert {"id": 7120, "name": "Books/EBook German"} in found["categories"][2]["subcats"]


def test_an_indexer_without_a_book_search_says_so() -> None:
    found = caps("torznab_caps.xml")
    assert found["book_search"] is True and found["book_params"] == ["q", "author", "title"]
    root, _ = newznab.parse_xml(b'<caps><searching><search available="yes" supportedParams="q"/>'
                                b'<book-search available="no" supportedParams="q,author"/></searching></caps>')
    assert root is not None
    plain = newznab.parse_caps(root)
    assert plain is not None and plain["book_search"] is False and plain["book_params"] == []


def test_the_book_categories_are_taken_from_the_caps_and_sorted_into_ebooks_and_audiobooks() -> None:
    listed = {item.id: item for item in newznab.book_categories(caps(), ("de",))}
    assert sorted(listed) == [3030, 3130, 3330, 7010, 7020, 7030, 7040, 7050, 7120, 7320]
    assert {i for i, item in listed.items() if item.type == "audio"} == {3030, 3130, 3330}
    assert {i for i, item in listed.items() if item.type == "ebook"} == {7010, 7020, 7030, 7040, 7050, 7120, 7320}
    # Not the parents "Books" and "Audio", not music, not television.
    assert 7000 not in listed and 3000 not in listed and 3010 not in listed and 3040 not in listed and 5030 not in listed


def test_the_suggestion_is_the_books_of_the_language_and_the_mixed_ones() -> None:
    listed = {item.id: item for item in newznab.book_categories(caps(), ("de",))}
    assert {i for i, item in listed.items() if item.suggested} == {3030, 3130, 7020, 7120}
    assert listed[7120].lang == "de" and listed[7320].lang == "fr" and listed[7020].lang == "mixed"
    assert listed[3330].lang == "fr" and listed[3030].lang == "mixed"
    english = {item.id for item in newznab.book_categories(caps(), ("en",)) if item.suggested}
    assert english == {3030, 7020}, "the German and French categories are not suggested to an English reader"
    french = {item.id for item in newznab.book_categories(caps(), ("fr",)) if item.suggested}
    assert french == {3030, 3330, 7020, 7320}


@pytest.mark.parametrize("name", ["Books/Comics", "Books/Mags", "Books/Technical", "Books/Other"])
def test_comics_magazines_technical_and_other_books_are_listed_but_not_suggested(name: str) -> None:
    root, _ = newznab.parse_xml(
        f'<caps><categories><category id="7000" name="Books"><subcat id="7020" name="Books/EBook"/>'
        f'<subcat id="7099" name="{name}"/></category></categories></caps>'.encode())
    assert root is not None
    listed = {item.id: item for item in newznab.book_categories(newznab.parse_caps(root), ())}
    assert listed[7099].suggested is False and listed[7020].suggested is True


def test_a_category_of_a_language_not_fetched_is_never_suggested_only_without_any_language_given() -> None:
    """An indexer that keeps only French books gives nothing to suggest to somebody who fetches German books (the
    search would never ask a French category); without languages every book category is suggested."""
    root, _ = newznab.parse_xml(b'<caps><categories><category id="7000" name="Books">'
                                b'<subcat id="7320" name="Books/EBook French"/></category></categories></caps>')
    assert root is not None
    listed = newznab.book_categories(newznab.parse_caps(root), ("de",))
    assert [item.id for item in listed if item.suggested] == []
    assert [item.id for item in newznab.book_categories(newznab.parse_caps(root)) if item.suggested] == [7320]


def test_with_no_caps_the_two_newznab_categories_are_asked() -> None:
    assert newznab.default_categories(None) == [7020, 3030]
    assert newznab.default_categories({"categories": []}) == [7020, 3030]
    assert newznab.book_categories(None) == []


def test_a_category_named_in_another_language_is_found() -> None:
    assert newznab.language_of("Books/EBook Deutsch") == "de"
    assert newznab.language_of("Livres/Français") == "fr"
    assert newznab.language_of("Hörbuch Niederländisch") == "nl"
    assert newznab.language_of("Books/EBook") == "mixed"
    assert newznab.language_of("Audio/Audiobook") == "mixed"


@pytest.mark.parametrize(("name", "language"), [
    # A code of two letters as a word of its own: at the end, in brackets, after a hyphen or a slash, in capitals.
    ("E-Book DE", "de"), ("Hörbücher DE", "de"), ("Ebook FR", "fr"), ("Ebook NL", "nl"), ("Audiobooks (EN)", "en"),
    ("Books/ES", "es"), ("Ebook-IT", "it"), ("Books > Ebooks [SV]", "sv"), ("EN Ebooks", "en"), ("Ebook DE Krimi", "de"),
    ("Hörbuch - PL", "pl"), ("Ebooks (pt)", "pt"), ("Ebooks NO", "no"),
    # The same letters as a piece of a word, or as the little word of a language, are not a language.
    ("Livres de poche", "mixed"), ("Libros de bolsillo", "mixed"), ("De Boeken", "mixed"), ("Books/de poche", "mixed"),
    ("Delivery", "mixed"), ("Ebook Mono", "mixed"), ("Nederland Boeken", "mixed"), ("Genre Thriller", "mixed"),
    ("Libri in vendita", "mixed"), ("Livres audio", "mixed"),
    # Two codes are no answer.
    ("EN/DE Ebooks", "mixed"),
])
def test_a_code_of_two_letters_in_a_category_name_is_a_language_only_as_a_word_of_its_own(name: str,
                                                                                          language: str) -> None:
    assert newznab.language_of(name) == language


def test_an_indexer_with_language_codes_in_its_categories_gets_the_suggestion_of_the_language() -> None:
    root, _ = newznab.parse_xml(
        b'<caps><categories><category id="7000" name="Books"><subcat id="7020" name="Books/EBook"/>'
        b'<subcat id="7120" name="E-Book DE"/><subcat id="7320" name="Ebook FR"/></category>'
        b'<category id="3000" name="Audio"><subcat id="3030" name="Audiobook"/>'
        + '<subcat id="3130" name="Hörbücher DE"/></category></categories></caps>'.encode())
    assert root is not None
    found = newznab.parse_caps(root)
    listed = {item.id: item for item in newznab.book_categories(found, ("de",))}
    assert listed[7120].lang == "de" and listed[7320].lang == "fr" and listed[3130].lang == "de"
    assert {i for i, item in listed.items() if item.suggested} == {7020, 7120, 3030, 3130}
    english = {item.id for item in newznab.book_categories(found, ("en",)) if item.suggested}
    assert english == {7020, 3030}, "the German and French categories are not suggested to an English reader"


# --- Safe XML ----------------------------------------------------------------------------------------------------------


def test_an_error_document_is_read_at_any_status() -> None:
    root, _ = newznab.parse_xml(error_document(100, "Incorrect user credentials"), "application/rss+xml")
    assert root is not None and newznab.local_name(root.tag) == "error" and root.get("code") == "100"


def test_a_page_of_html_is_no_indexer() -> None:
    assert newznab.parse_xml(b"<!doctype html><html><body>hello</body></html>", "text/html") == (None, set())
    assert newznab.parse_xml(b"<html><body>hello</body></html>", "application/xml") == (None, set())
    assert newznab.parse_xml(b"not xml at all", "text/plain") == (None, set())
    assert newznab.parse_xml(b"", "application/xml") == (None, set())


@pytest.mark.parametrize("document", [
    b'<?xml version="1.0"?><!DOCTYPE caps [<!ENTITY x "boom">]><caps>&x;</caps>',
    b'<?xml version="1.0"?><!DOCTYPE caps [<!ENTITY x SYSTEM "file:///etc/passwd">]><caps>&x;</caps>',
    b'<?xml version="1.0"?><!DOCTYPE caps SYSTEM "http://127.0.0.1:9/evil.dtd"><caps/>',
    (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
     b'<caps>&b;</caps>'),
])
def test_a_document_with_a_dtd_or_an_entity_is_refused(document: bytes) -> None:
    assert newznab.parse_xml(document, "application/xml") == (None, set())


def test_a_document_nested_without_end_is_refused_not_crashed() -> None:
    assert newznab.parse_xml(b"<caps>" + b"<a>" * 5000, "application/xml") == (None, set())
    # Deeply nested but closed: read or refused, never a crash of the request.
    answer = newznab.parse_xml(b"<caps>" + b"<a>" * 30000 + b"</a>" * 30000 + b"</caps>", "application/xml")
    assert isinstance(answer, tuple)


def test_the_categories_of_a_huge_caps_document_are_capped() -> None:
    many = "".join(f'<category id="{7000 + n}" name="c{n}"/>' for n in range(newznab.MAX_CATEGORIES + 500))
    root, _ = newznab.parse_xml(f"<caps><categories>{many}</categories></caps>".encode())
    assert root is not None
    parsed = newznab.parse_caps(root)
    assert parsed is not None and len(parsed["categories"]) == newznab.MAX_CATEGORIES


def test_the_kind_of_a_feed_is_told_by_its_enclosures_and_its_namespaces() -> None:
    nzb, ns = newznab.parse_xml(fixture("newznab_search.xml"), "application/rss+xml")
    torrent, torrent_ns = newznab.parse_xml(fixture("torznab_search.xml"), "application/rss+xml")
    assert nzb is not None and torrent is not None
    assert newznab.detect_kind(nzb, ns) == "newznab" and newznab.detect_kind(torrent, torrent_ns) == "torznab"
    assert newznab.parse_feed(nzb, ns).items == 1


def test_apilimits_are_read_in_either_spelling_and_a_bad_number_counts_as_missing() -> None:
    root, namespaces = newznab.parse_xml(
        b'<rss xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/"><channel>'
        b'<newznab:apilimits apiCurrent="40" apiMax="50" grabCurrent="x" grabmax="-3" '
        b'apiNextAvailable="Tue, 06 Oct 2026 10:00:00 +0000"/></channel></rss>')
    assert root is not None
    limits = newznab.parse_feed(root, namespaces).limits
    assert limits is not None
    assert (limits.api_current, limits.api_max, limits.grab_current, limits.grab_max) == (40, 50, None, None)
    assert limits.api_next_at == "Tue, 06 Oct 2026 10:00:00 +0000"


def test_a_document_that_is_not_a_feed_is_not_newznab() -> None:
    root, namespaces = newznab.parse_xml(b"<other><channel/></other>")
    assert root is not None
    with pytest.raises(newznab.IndexerError) as caught:
        newznab.parse_feed(root, namespaces)
    assert caught.value.code == "indexer_not_newznab"


# --- The items of a feed -------------------------------------------------------------------------------


def _feed(items: str, kind: str = "newznab") -> newznab.Feed:
    space = ('xmlns:torznab="http://torznab.com/schemas/2015/feed"' if kind == "torznab"
             else 'xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/"')
    body = (f'<?xml version="1.0"?><rss version="2.0" {space}><channel><title>T</title>'
            f'<{kind}:response offset="0" total="7"/>{items}</channel></rss>').encode()
    root, namespaces = newznab.parse_xml(body)
    assert root is not None
    return newznab.parse_feed(root, namespaces)


def test_an_item_gives_title_size_date_categories_and_a_hidden_link() -> None:
    found = _feed(
        '<item><title>Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE</title><guid>abc</guid>'
        '<link>https://indexer.example.com/get/1?apikey=SECRETKEY</link><pubDate>Mon, 12 Oct 2026 10:00:00 +0000</pubDate>'
        '<enclosure url="https://indexer.example.com/get/1?apikey=SECRETKEY" length="999" type="application/x-nzb"/>'
        '<newznab:attr name="category" value="7000"/><newznab:attr name="category" value="7120"/>'
        '<newznab:attr name="size" value="2048"/><newznab:attr name="grabs" value="7"/>'
        '<newznab:attr name="usenetdate" value="Sun, 11 Oct 2026 09:00:00 +0000"/></item>')
    [item] = found.releases
    assert item.title == "Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"
    assert (item.size, item.categories, item.grabs, found.total) == (2048, (7000, 7120), 7, 7)
    assert item.published_at is not None and item.published_at.day == 11
    assert item.link.endswith("apikey=SECRETKEY") and "SECRETKEY" not in repr(item)
    assert item.guid and item.guid != "abc"


def test_a_torznab_item_gives_seeders_peers_the_hash_and_the_seed_goal() -> None:
    found = _feed(
        '<item><title>Rune Falkner - Die Laternenstrasse</title><guid>x</guid>'
        '<enclosure url="magnet:?xt=urn:btih:' + "a" * 40 + '" length="5" type="application/x-bittorrent"/>'
        '<torznab:attr name="seeders" value="4"/><torznab:attr name="leechers" value="2"/>'
        '<torznab:attr name="infohash" value="' + "B" * 40 + '"/><torznab:attr name="minimumratio" value="1.0"/>'
        '<torznab:attr name="minimumseedtime" value="86400"/></item>', kind="torznab")
    [item] = found.releases
    assert (item.seeders, item.peers, item.info_hash) == (4, 6, "b" * 40)
    assert (item.min_ratio, item.min_seed_seconds) == (1.0, 86400)
    assert item.link.startswith("magnet:")


@pytest.mark.parametrize("link", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.example.com/a",
                                  "https://x.example.com/a b"])
def test_a_link_that_is_not_http_https_or_magnet_is_dropped(link: str) -> None:
    found = _feed(f'<item><title>Some Name</title><link>{link}</link></item>')
    assert found.releases[0].link == ""


def test_an_item_without_a_title_is_skipped_and_a_huge_feed_is_cut() -> None:
    found = _feed("<item><guid>1</guid></item>" + "<item><title>A</title></item>" * 600)
    assert found.items == 601 and len(found.releases) == newznab.MAX_ITEMS - 1
