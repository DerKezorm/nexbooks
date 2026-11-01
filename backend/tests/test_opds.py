"""The OPDS catalogue for reading apps: every feed is well-formed Atom (read with a real parser), a person sees what the
account sees and no more, nothing of the disk is written into a feed, a wrong or blocked secret is turned away, wrong
guesses are braked, and the way is no way for a browser."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, EreaderLink, Person, Series, utcnow
from app.security import brake
from app.services import apikeys, brakes, devices, opds, settings_service
from app.services import works as works_service

from .conftest import BOOKS_DIR, PASSWORD, make_account, new_client
from .ereader_helpers import Reading, basic, connect, opds_client
from .library_helpers import books_folder, join, make_library, run_jobs

ATOM = "{http://www.w3.org/2005/Atom}"
OPDS_TYPES = ("application/atom+xml;profile=opds-catalog;kind=navigation",
              "application/atom+xml;profile=opds-catalog;kind=acquisition")


@dataclass
class Page:
    path: str
    raw: bytes
    root: ET.Element
    headers: dict[str, str]

    def titles(self) -> list[str]:
        return [entry.findtext(f"{ATOM}title") or "" for entry in self.root.findall(f"{ATOM}entry")]

    def rels(self, rel: str) -> list[ET.Element]:
        return [link for link in self.root.findall(f"{ATOM}link") if link.get("rel") == rel]

    def entry(self, title: str) -> ET.Element:
        (found,) = [e for e in self.root.findall(f"{ATOM}entry") if e.findtext(f"{ATOM}title") == title]
        return found


def get_page(app: TestClient, path: str, auth: dict[str, str]) -> Page:
    answer = app.get(path, headers=auth)
    assert answer.status_code == 200, (path, answer.status_code, answer.text[:200])
    return Page(path, answer.content, ET.fromstring(answer.content), dict(answer.headers))


def links_of(entry: ET.Element, rel: str) -> list[ET.Element]:
    return [link for link in entry.findall(f"{ATOM}link") if link.get("rel") == rel]


@dataclass
class Setup:
    reading: Reading
    app: TestClient
    auth: dict[str, str]
    secret: str


@pytest.fixture
def ada_app(reading: Reading, ada_browser: TestClient) -> Setup:
    secret = connect(ada_browser)
    return Setup(reading, opds_client(), basic("ada", secret), secret)


@pytest.fixture
def ben_app(reading: Reading) -> Setup:
    with new_client(reading.shelf.ben) as browser:
        secret = connect(browser)
    return Setup(reading, opds_client(), basic("ben", secret), secret)


def crawl(setup: Setup, start: str = "/opds") -> dict[str, Page]:
    """Every feed that can be reached from ``start`` by its links (the address of a search with its words missing is
    left alone), each parsed."""
    seen: dict[str, Page] = {}
    waiting = [start]
    while waiting and len(seen) < 300:
        path = waiting.pop()
        if path in seen:
            continue
        page = get_page(setup.app, path, setup.auth)
        seen[path] = page
        for link in page.root.iter(f"{ATOM}link"):
            href = link.get("href") or ""
            if link.get("type") in OPDS_TYPES and "{" not in href:
                assert href.startswith("/opds"), href
                waiting.append(href)
    return seen


def titles_everywhere(pages: dict[str, Page]) -> set[str]:
    return {title for page in pages.values() for title in page.titles()}


# --- The feeds ---------------------------------------------------------------------------------------------------------


def test_every_feed_is_atom_that_a_real_parser_reads(ada_app: Setup) -> None:
    pages = crawl(ada_app)
    assert {"/opds", "/opds/new", "/opds/authors", "/opds/series"} <= set(pages)
    assert len(pages) >= 8
    for path, page in pages.items():
        assert page.root.tag == f"{ATOM}feed", path
        assert page.headers["content-type"] in OPDS_TYPES, path
        for field in ("id", "title", "updated"):
            assert page.root.findtext(f"{ATOM}{field}"), (path, field)
        assert page.rels("self")[0].get("href") == path or path.startswith(page.rels("self")[0].get("href", "?")), path
        assert page.rels("start")[0].get("href") == "/opds"
        for entry in page.root.findall(f"{ATOM}entry"):
            for field in ("id", "title", "updated"):
                assert entry.findtext(f"{ATOM}{field}"), (path, field)
            assert entry.findall(f"{ATOM}link"), path
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "set-cookie" not in page.headers


def test_a_book_has_what_a_reading_app_needs(ada_app: Setup) -> None:
    page = get_page(ada_app.app, "/opds/new", ada_app.auth)
    entry = page.entry("The Quiet Orchard (Orchard Tales 1)")
    assert entry.findtext(f"{ATOM}id").startswith("urn:nexbooks:work:")
    assert [name.text for name in entry.findall(f"{ATOM}author/{ATOM}name")] == ["Mara Lindqvist"]
    assert entry.findtext(f"{ATOM}summary") == "A garden at the end of the world."
    assert entry.findtext("{http://purl.org/dc/terms/}language") == "en"
    (acquisition,) = links_of(entry, "http://opds-spec.org/acquisition")
    assert acquisition.get("type") == "application/epub+zip" and acquisition.get("title") == "EPUB"
    assert acquisition.get("href").startswith("/opds/files/") and acquisition.get("href").endswith(".epub")
    assert int(acquisition.get("length")) > 1000
    assert links_of(entry, "http://opds-spec.org/image") and links_of(entry, "http://opds-spec.org/image/thumbnail")
    (related,) = links_of(entry, "related")
    assert related.get("title") == "Orchard Tales" and related.get("href").startswith("/opds/series/")
    # A work with no cover offers none.
    assert links_of(page.entry("Kite Season"), "http://opds-spec.org/image") == []


def test_what_is_listed_is_what_the_files_are_and_the_audiobook_alone_is_not_there(ada_app: Setup) -> None:
    titles = titles_everywhere(crawl(ada_app))
    assert {"The Quiet Orchard (Orchard Tales 1)", "The Loud Orchard (Orchard Tales 2)", "Kite Season",
            'Tin & <Brass> "Dust"', "The Locked Door"} <= titles
    assert not any("Night Train" in title for title in titles)
    # The recording of a book that has an e-book is no second book and offers no file.
    new = get_page(ada_app.app, "/opds/new", ada_app.auth)
    formats = [link.get("type") for entry in new.root.findall(f"{ATOM}entry")
               for link in links_of(entry, "http://opds-spec.org/acquisition")]
    assert set(formats) == {"application/epub+zip"} and len(formats) == 5


def test_the_start_has_the_sections_the_libraries_the_person_is_in_and_a_search(
        reading: Reading, ada_app: Setup, ben_app: Setup) -> None:
    ada = get_page(ada_app.app, "/opds", ada_app.auth)
    assert ada.titles() == ["New", "Authors", "Series", "Books"]
    ben = get_page(ben_app.app, "/opds", ben_app.auth)
    assert ben.titles() == ["New", "Authors", "Series", "Books", "Private"]
    searches = ada.rels("search")
    assert {link.get("type") for link in searches} == {
        "application/opensearchdescription+xml", "application/atom+xml;profile=opds-catalog;kind=acquisition"}
    assert ada.root.findtext(f"{ATOM}title") == "nexbooks"
    # A library that holds audiobooks only is not offered.
    folder = books_folder("audio-only")
    audio = make_library(folder, "Listening", kind="audio")
    join(audio, reading.shelf.ada.id, "read")
    assert get_page(ada_app.app, "/opds", ada_app.auth).titles() == ["New", "Authors", "Series", "Books"]


def test_a_library_lists_its_books_by_title(ada_app: Setup) -> None:
    root = get_page(ada_app.app, "/opds", ada_app.auth)
    href = links_of(root.entry("Books"), "subsection")[0].get("href")
    page = get_page(ada_app.app, href, ada_app.auth)
    assert page.titles() == ['Kite Season', 'The Locked Door', 'The Loud Orchard (Orchard Tales 2)',
                             'The Quiet Orchard (Orchard Tales 1)', 'Tin & <Brass> "Dust"']
    assert page.rels("up")[0].get("href") == "/opds"


def test_the_authors_and_their_books(ada_app: Setup) -> None:
    authors = get_page(ada_app.app, "/opds/authors", ada_app.auth)
    assert authors.titles() == ["Brandauer, Jonas", "Falkner, Rune", "Lindqvist, Mara"]
    assert authors.entry("Lindqvist, Mara").findtext(f"{ATOM}content") == "2 books"
    assert authors.entry("Brandauer, Jonas").findtext(f"{ATOM}content") == "1 book"
    href = links_of(authors.entry("Lindqvist, Mara"), "subsection")[0].get("href")
    books = get_page(ada_app.app, href, ada_app.auth)
    assert books.titles() == ["The Quiet Orchard (Orchard Tales 1)", "The Loud Orchard (Orchard Tales 2)"] or sorted(
        books.titles()) == ["The Loud Orchard (Orchard Tales 2)", "The Quiet Orchard (Orchard Tales 1)"]
    assert books.root.findtext(f"{ATOM}title") == "Mara Lindqvist"
    assert books.rels("up")[0].get("href") == "/opds/authors"


def test_an_author_with_a_recording_only_is_not_in_the_list(reading: Reading, ada_app: Setup) -> None:
    with SessionLocal() as db:
        assert db.scalar(select(Person.id).where(Person.name == "Iris Vale")) is not None
    assert not any("Vale" in title for title in get_page(ada_app.app, "/opds/authors", ada_app.auth).titles())


def test_the_series_and_their_volumes_in_order(ada_app: Setup) -> None:
    series = get_page(ada_app.app, "/opds/series", ada_app.auth)
    assert series.titles() == ["Orchard Tales"] and series.entry("Orchard Tales").findtext(f"{ATOM}content") == "2 books"
    href = links_of(series.entry("Orchard Tales"), "subsection")[0].get("href")
    volumes = get_page(ada_app.app, href, ada_app.auth)
    assert volumes.titles() == ["The Quiet Orchard (Orchard Tales 1)", "The Loud Orchard (Orchard Tales 2)"]
    assert volumes.rels("up")[0].get("href") == "/opds/series"


def test_the_search_finds_e_books_by_the_words_of_the_title_and_the_author(ada_app: Setup) -> None:
    found = get_page(ada_app.app, "/opds/search?q=orchard", ada_app.auth)
    assert sorted(found.titles()) == ["The Loud Orchard (Orchard Tales 2)", "The Quiet Orchard (Orchard Tales 1)"]
    assert get_page(ada_app.app, "/opds/search?q=Brandauer", ada_app.auth).titles() == ["Kite Season"]
    # A recording with no e-book is not found, and nothing typed is syntax.
    assert get_page(ada_app.app, "/opds/search?q=night+train", ada_app.auth).titles() == []
    for odd in ("", "   ", '"', "OR NOT (", "* ^ {", "a" * 150):
        assert get_page(ada_app.app, "/opds/search", ada_app.auth).titles() == []
        assert ada_app.app.get("/opds/search", params={"q": odd}, headers=ada_app.auth).status_code == 200
    assert ada_app.app.get("/opds/search", params={"q": "x" * 201}, headers=ada_app.auth).status_code == 422


def test_the_description_of_the_search_is_opensearch(ada_app: Setup) -> None:
    answer = ada_app.app.get("/opds/search.xml", headers=ada_app.auth)
    assert answer.headers["content-type"] == "application/opensearchdescription+xml"
    root = ET.fromstring(answer.content)
    namespace = "{http://a9.com/-/spec/opensearch/1.1/}"
    assert root.tag == f"{namespace}OpenSearchDescription"
    (url,) = root.findall(f"{namespace}Url")
    assert url.get("template") == "/opds/search?q={searchTerms}" and url.get("type") in OPDS_TYPES
    # The template leads to a feed that works.
    assert get_page(ada_app.app, url.get("template").replace("{searchTerms}", "kite"), ada_app.auth).titles() == [
        "Kite Season"]


def test_pages_hold_fifty_and_lead_to_each_other(ada_app: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    assert opds.PER_PAGE == 50
    monkeypatch.setattr(opds, "PER_PAGE", 2)
    first = get_page(ada_app.app, "/opds/new", ada_app.auth)
    assert len(first.titles()) == 2 and first.rels("previous") == [] and first.rels("first") == []
    assert first.root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") == "5"
    assert first.root.findtext("{http://a9.com/-/spec/opensearch/1.1/}itemsPerPage") == "2"
    seen = list(first.titles())
    page = first
    while page.rels("next"):
        href = page.rels("next")[0].get("href")
        page = get_page(ada_app.app, href, ada_app.auth)
        assert page.rels("previous") and page.rels("first")
        seen += page.titles()
    assert len(seen) == 5 and len(set(seen)) == 5
    assert page.rels("last") == [] and page.rels("self")[0].get("href") == "/opds/new?page=3"
    # Past the end: an empty page, not an error.
    assert get_page(ada_app.app, "/opds/new?page=99", ada_app.auth).titles() == []
    for bad in ("0", "-1", "x", "100001", "1.5"):
        assert ada_app.app.get("/opds/new", params={"page": bad}, headers=ada_app.auth).status_code == 422


# --- What leaves, and what does not ------------------------------------------------------------------------------------


def test_text_is_escaped_and_comes_back_as_it_was(ada_app: Setup) -> None:
    page = get_page(ada_app.app, "/opds/new", ada_app.auth)
    entry = page.entry('Tin & <Brass> "Dust"')
    # The description is text by the time it is read (the markup of a source is taken out), and what is left is escaped.
    assert entry.findtext(f"{ATOM}summary") == "Cogs & gears in a quiet town."
    assert b"&amp;" in page.raw and b"&lt;Brass&gt;" in page.raw and b"<b>" not in page.raw and b"<Brass>" not in page.raw


def test_what_xml_cannot_hold_is_taken_out_and_the_feed_still_reads() -> None:
    assert opds.clean("a\x00b\x01c\x1fd\ufffee\ud800") == "abcde" or opds.clean("a\x00b\x01c\x1fd") == "abcd"
    feed = opds.Feed(id="urn:test\x00", title="T\x0bitle", href="/opds/x?a=1&b=<2>", kind=opds.ACQUISITION,
                     entries=[opds.Entry(id="e", title="\x00\x1f<>&\"'", updated=utcnow(),
                                         links=[opds.Link("self", "/opds/\x00?q=\"'<", "x\"y", 't"<')],
                                         authors=[("N\x00", "u\x00")], summary="s\x00" * 5, category="c\x00")])
    root = ET.fromstring(feed.xml())
    assert root.findtext(f"{ATOM}title") == "Title"
    entry = root.find(f"{ATOM}entry")
    assert entry is not None and entry.findtext(f"{ATOM}title") == "<>&\"'"
    assert root.find(f"{ATOM}link").get("href") == "/opds/x?a=1&b=<2>"


def test_no_path_and_no_name_from_the_disk_is_written_anywhere(ada_app: Setup) -> None:
    pages = crawl(ada_app)
    everything = b"\n".join(page.raw for page in pages.values()).decode()
    for secret_word in ("hidden7f3a", BOOKS_DIR, "Orchard.epub", "Kite.epub", "Locked.epub", "books-", "\\", "C:/",
                        ".m4b", "Night Train", "Secret"):
        assert secret_word not in everything, secret_word
    # Nor in the headers of a file.
    link = pages["/opds/new"].root.find(f"{ATOM}entry/{ATOM}link[@rel='http://opds-spec.org/acquisition']")
    answer = ada_app.app.get(link.get("href"), headers=ada_app.auth)
    assert answer.status_code == 200
    assert "hidden7f3a" not in str(answer.headers) and BOOKS_DIR not in str(answer.headers)
    assert "/" not in answer.headers["content-disposition"].split("filename", 1)[1].replace("''", "")


def test_a_file_is_the_one_the_download_gives_and_its_ending_decides_nothing(reading: Reading, ada_app: Setup,
                                                                              ada_browser: TestClient) -> None:
    file_id = reading.file_of("The Quiet Orchard")
    with SessionLocal() as db:
        _row, path = works_service.file_of(db, reading.shelf.ada.id, file_id)
    bytes_on_disk = path.read_bytes()
    via_opds = ada_app.app.get(f"/opds/files/{file_id}/{file_id}.epub", headers=ada_app.auth)
    via_api = ada_browser.get(f"/api/v1/files/{file_id}/download")
    assert via_opds.status_code == 200 and via_opds.content == bytes_on_disk == via_api.content
    assert via_opds.headers["content-type"] == "application/epub+zip"
    assert unquote(via_opds.headers["content-disposition"]).endswith("The Quiet Orchard.epub")
    # The ending is only a hint for the app; the number names the file.
    assert ada_app.app.get(f"/opds/files/{file_id}/x.pdf", headers=ada_app.auth).content == bytes_on_disk
    for name in ("..", "a b", "a%2F..%2Fb", "x" * 41, "a;b"):
        assert ada_app.app.get(f"/opds/files/{file_id}/{name}", headers=ada_app.auth).status_code in (404, 422)
    # A part of it, for an app that asks for one.
    part = ada_app.app.get(f"/opds/files/{file_id}/{file_id}.epub", headers={**ada_app.auth, "Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == bytes_on_disk[:10]
    # A book with copy protection can be fetched like in the interface (it is only not opened).
    locked = reading.file_of("The Locked Door")
    assert ada_app.app.get(f"/opds/files/{locked}/{locked}.epub", headers=ada_app.auth).status_code == 200


def test_covers_are_pictures(reading: Reading, ada_app: Setup) -> None:
    new = get_page(ada_app.app, "/opds/new", ada_app.auth)
    href = links_of(new.entry("The Quiet Orchard (Orchard Tales 1)"), "http://opds-spec.org/image")[0].get("href")
    answer = ada_app.app.get(href, headers=ada_app.auth)
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/webp"
    assert answer.content[:4] == b"RIFF" and answer.content[8:12] == b"WEBP"
    assert ada_app.app.get(href.replace("cover", "thumbnail"), headers=ada_app.auth).status_code == 200
    assert ada_app.app.get(href, headers={**ada_app.auth, "If-None-Match": answer.headers["etag"]}).status_code == 304


# --- Whose it is ---------------------------------------------------------------------------------------------------------


def test_another_library_is_not_there_for_the_account_that_is_not_in_it(
        reading: Reading, ada_app: Setup, ben_app: Setup) -> None:
    ada_titles = titles_everywhere(crawl(ada_app))
    ben_titles = titles_everywhere(crawl(ben_app))
    assert "A Secret Shelf" not in ada_titles and "Brandt, Ida" not in ada_titles
    assert {"A Secret Shelf", "Brandt, Ida"} <= ben_titles
    secret_file = reading.file_of("A Secret Shelf")
    with SessionLocal() as db:
        ida = db.scalar(select(Person.id).where(Person.name == "Ida Brandt"))
    secret_work = reading.shelf.works["A Secret Shelf"]
    for path in (f"/opds/libraries/{reading.shelf.private}", f"/opds/authors/{ida}", f"/opds/files/{secret_file}/x.epub",
                 f"/opds/works/{secret_work}/cover", f"/opds/works/{secret_work}/thumbnail",
                 "/opds/libraries/99999", "/opds/authors/99999", "/opds/series/99999", "/opds/files/99999/x.epub"):
        answer = ada_app.app.get(path, headers=ada_app.auth)
        assert answer.status_code == 404, (path, answer.status_code)
    # A thing that is not there and a thing that may not be seen answer alike.
    assert (ada_app.app.get(f"/opds/files/{secret_file}/x.epub", headers=ada_app.auth).json()
            == ada_app.app.get("/opds/files/99999/x.epub", headers=ada_app.auth).json())
    assert ben_app.app.get(f"/opds/files/{secret_file}/x.epub", headers=ben_app.auth).status_code == 200
    assert get_page(ada_app.app, "/opds/search?q=secret", ada_app.auth).titles() == []
    assert get_page(ben_app.app, "/opds/search?q=secret", ben_app.auth).titles() == ["A Secret Shelf"]


def test_a_recording_is_not_served(reading: Reading, ada_app: Setup) -> None:
    audio = reading.audio_file_of("Night Train")
    assert ada_app.app.get(f"/opds/files/{audio}/{audio}.m4b", headers=ada_app.auth).status_code == 404
    orchard_audio = reading.audio_file_of("The Quiet Orchard")
    assert ada_app.app.get(f"/opds/files/{orchard_audio}/x.m4b", headers=ada_app.auth).status_code == 404


def test_a_membership_taken_away_takes_the_books_away(reading: Reading, ada_app: Setup) -> None:
    assert "Kite Season" in titles_everywhere(crawl(ada_app))
    from app.models import LibraryMember

    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=reading.shelf.ada.id).delete()
        db.commit()
    from app.services.discover import owned

    owned.forget()
    assert titles_everywhere(crawl(ada_app)) <= {"New", "Authors", "Series"}


# --- The way in -----------------------------------------------------------------------------------------------------------


def test_nothing_or_something_else_is_a_401_that_says_how_to_sign_in(ada_app: Setup, reading: Reading,
                                                                      ada_browser: TestClient) -> None:
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        _device, device_token = devices.make(db, ada, "phone")
        settings_service.save(db, {"api_keys_allowed": True})
        _key, key = apikeys.make(db, ada, "dash", "read")
    cookie = {"Cookie": "; ".join(f"{k}={v}" for k, v in ada_browser.cookies.items())}
    assert cookie["Cookie"]
    tries = {
        "nothing": {},
        "a session and no secret": cookie,
        "the password of the account": basic("ada", PASSWORD),
        "a wrong secret": basic("ada", "nbo_" + "x" * 32),
        "another name": basic("ben", ada_app.secret),
        "no name": basic("", ada_app.secret),
        "bearer": {"Authorization": f"Bearer {ada_app.secret}"},
        "a device token as the secret": basic("ada", device_token),
        "an api key as the secret": basic("ada", key),
        "not base64": {"Authorization": "Basic !!!"},
        "no colon": {"Authorization": "Basic " + __import__("base64").b64encode(b"ada").decode()},
        "too long": basic("ada", "nbo_" + "x" * 800),
        "another scheme": {"Authorization": "Digest user=ada"},
    }
    for name, headers in tries.items():
        answer = ada_app.app.get("/opds/new", headers=headers)
        assert answer.status_code == 401, name
        assert answer.headers["www-authenticate"].startswith("Basic realm="), name
        assert "set-cookie" not in answer.headers, name
    # And the session of the browser works for the interface, which shows that the cookie was a real one.
    assert ada_browser.get("/api/v1/auth/me").status_code == 200


def test_the_secret_works_with_the_name_in_any_case_and_marks_the_connection_used(
        reading: Reading, ada_app: Setup, ada_browser: TestClient) -> None:
    assert ada_browser.get("/api/v1/ereaders").json()["links"][0]["last_used_at"] is None
    assert ada_app.app.get("/opds", headers=basic("ADA", ada_app.secret)).status_code == 200
    assert ada_app.app.get("/opds", headers=basic(" Ada ", ada_app.secret)).status_code == 200
    assert ada_browser.get("/api/v1/ereaders").json()["links"][0]["last_used_at"] is not None


def test_a_blocked_deleted_or_switched_off_connection_is_turned_away(
        client: TestClient, operator: Account, reading: Reading, ada_app: Setup, ada_browser: TestClient) -> None:
    other = connect(ada_browser, "second")
    mine = ada_browser.get("/api/v1/ereaders").json()["links"]
    first, second = mine[0]["id"], mine[1]["id"]
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 200
    # The operator blocks one for good; the other still works.
    assert client.post(f"/api/v1/admin/ereaders/links/{first}/block").status_code == 200
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 401
    assert ada_app.app.get("/opds", headers=basic("ada", other)).status_code == 200
    # Deleted: gone.
    assert ada_browser.delete(f"/api/v1/ereaders/links/{second}").status_code == 204
    assert ada_app.app.get("/opds", headers=basic("ada", other)).status_code == 401
    # Switched off by the operator: nothing works, and it says why.
    # The block stays until the operator deletes the connection; only then may the person make one of that kind.
    assert client.delete(f"/api/v1/admin/ereaders/links/{first}").status_code == 204
    fresh = connect(ada_browser, "third")
    assert ada_app.app.get("/opds", headers=basic("ada", fresh)).status_code == 200
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    off = ada_app.app.get("/opds", headers=basic("ada", fresh))
    assert off.status_code == 403 and off.json()["detail"]["code"] == "ereaders_off"
    assert ada_app.app.get("/opds/new").status_code == 403
    assert client.put("/api/v1/settings", json={"ereaders_allowed": True}).status_code == 200
    assert ada_app.app.get("/opds", headers=basic("ada", fresh)).status_code == 200


def test_a_blocked_account_has_no_catalogue_but_a_lock_after_wrong_passwords_does_not_reach_it(reading: Reading, ada_app: Setup) -> None:
    from datetime import timedelta

    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 200
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = utcnow()
        db.commit()
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 401
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = None
        ada.locked_until = utcnow() + timedelta(minutes=5)
        db.commit()
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 200, "the lock is for the password"


def test_a_connection_of_another_kind_is_no_key_to_the_catalogue(reading: Reading, ada_app: Setup) -> None:
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        link.kind = "kosync"
        db.commit()
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 401


def test_wrong_secrets_are_braked_but_a_right_one_is_never_turned_away(reading: Reading, ada_app: Setup,
                                                                        ada_browser: TestClient) -> None:
    same = basic("ada", "nbo_" + "g" * 32)
    # The same wrong secret again and again (an app that was not told) counts once.
    for _ in range(12):
        assert ada_app.app.get("/opds", headers=same).status_code == 401
    # New guesses each count; the fifth is the last that is answered, then the sender rests.
    answers = [ada_app.app.get("/opds", headers=basic("ada", f"nbo_{index:032d}")).status_code for index in range(8)]
    assert answers[:4] == [401] * 4 and set(answers[5:]) == {429}
    rested = ada_app.app.get("/opds", headers=basic("ada", "nbo_" + "h" * 32))
    assert rested.status_code == 429 and int(rested.headers["retry-after"]) > 0
    # The connection that exists is looked up first: it still works, from the same address.
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 200
    # The count is the catalogue's own: the sign-in with the password still works.
    login = opds_client().post("/api/v1/auth/login", headers={"X-Nexbooks-Client": "tab-test0000"},
                               json={"name": "ada", "password": PASSWORD})
    assert login.status_code == 200, login.text
    brake.forget()
    assert ada_app.app.get("/opds", headers=basic("ada", "nbo_" + "h" * 32)).status_code == 401


def test_a_flood_of_requests_is_braked_per_account(reading: Reading, ada_app: Setup, ben_app: Setup,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(brakes.LIMITS, "opds", 3)
    assert [ada_app.app.get("/opds", headers=ada_app.auth).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert ada_app.app.get("/opds", headers=ada_app.auth).headers["retry-after"]
    assert ben_app.app.get("/opds", headers=ben_app.auth).status_code == 200


def test_it_is_no_way_for_a_browser(reading: Reading, ada_app: Setup, ada_browser: TestClient) -> None:
    # Only reading: anything that changes is a method not allowed, with or without a secret, a cookie or a tab.
    for method in ("post", "put", "patch", "delete"):
        for headers in ({}, ada_app.auth, {**ada_app.auth, "X-Nexbooks-Client": "tab-test0000"}):
            answer = getattr(ada_app.app, method)("/opds/new", headers=headers)
            assert answer.status_code in (404, 405), (method, answer.status_code)
    # The cookie of a signed-in browser opens nothing here, and the catalogue never sets or reads one.
    assert ada_browser.get("/opds").status_code == 401
    assert ada_browser.get("/opds/files/1/x.epub").status_code == 401
    # No page on another site is let in: there is no CORS, and a web page cannot set the header.
    answer = ada_app.app.get("/opds", headers={**ada_app.auth, "Origin": "https://example.com"})
    assert "access-control-allow-origin" not in answer.headers


def test_the_secret_and_the_name_never_reach_the_log(reading: Reading, ada_app: Setup,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    ada_app.app.get("/opds", headers=ada_app.auth)
    ada_app.app.get("/opds", headers=basic("ada", "nbo_" + "q" * 32))
    assert ada_app.secret not in caplog.text and "q" * 32 not in caplog.text


def test_a_new_account_without_a_library_sees_an_empty_catalogue(reading: Reading) -> None:
    rita = make_account("rita")
    with new_client(rita) as browser:
        secret = connect(browser)
    app = opds_client()
    auth = basic("rita", secret)
    assert get_page(app, "/opds", auth).titles() == ["New", "Authors", "Series"]
    for path in ("/opds/new", "/opds/authors", "/opds/series", "/opds/search?q=orchard"):
        assert get_page(app, path, auth).titles() == []


def test_the_files_of_a_work_that_went_missing_are_not_offered(reading: Reading, ada_app: Setup) -> None:
    from app.models import BookFile

    file_id = reading.file_of("Kite Season")
    with SessionLocal() as db:
        db.query(BookFile).filter_by(id=file_id).update({"missing_at": utcnow()})
        db.commit()
    new = get_page(ada_app.app, "/opds/new", ada_app.auth)
    assert "Kite Season" not in new.titles()
    assert ada_app.app.get(f"/opds/files/{file_id}/x.epub", headers=ada_app.auth).status_code == 404


def test_a_series_is_only_the_one_the_person_sees(reading: Reading, ada_app: Setup, ben_app: Setup) -> None:
    with SessionLocal() as db:
        series = db.scalar(select(Series.id).where(Series.name == "Orchard Tales"))
    assert get_page(ben_app.app, f"/opds/series/{series}", ben_app.auth).titles() == [
        "The Quiet Orchard (Orchard Tales 1)", "The Loud Orchard (Orchard Tales 2)"]
    # A library of one's own with the same series name does not widen it for the other.
    folder = books_folder("more")
    from .library_helpers import book

    book(folder, "Z/Third.epub", title="The Third Orchard", authors=("Mara Lindqvist",), language="en",
         series="Orchard Tales", series_pos=3.0)
    other = make_library(folder, "More", scan=True)
    run_jobs()
    join(other, reading.shelf.ben.id, "read")
    from app.services.discover import owned

    owned.forget()
    assert "The Third Orchard (Orchard Tales 3)" in get_page(ben_app.app, f"/opds/series/{series}", ben_app.auth).titles()
    assert "The Third Orchard (Orchard Tales 3)" not in get_page(
        ada_app.app, f"/opds/series/{series}", ada_app.auth).titles()


def test_the_start_address_works_with_and_without_the_slash(ada_app: Setup) -> None:
    assert urlsplit("/opds/").path == "/opds/"
    assert ada_app.app.get("/opds/", headers=ada_app.auth).status_code == 200
    assert ada_app.app.get("/opds", headers=ada_app.auth).status_code == 200
