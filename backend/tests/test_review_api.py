"""The list of books the matching is not sure about, and what the people who look after a library do with it: who may see
and decide, the proposals, searching for others, covers, corrections by hand, joining and splitting works, and what none of
it may show to a member of another library."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import NARRATOR, Account, Credit, Edition, MatchEntry, Person
from app.services.match import overlay
from app.services.sources import cache

from .conftest import make_account, new_client
from .fake_sources import hc_doc, hc_edition, ol_doc
from .library_helpers import book, books_folder, make_library, run_jobs, scan
from .match_helpers import World, edition_of, entries, entry_for, member

#: A source that knows the book only under the last name of its author: always a question for a person.
WEAK = ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",), cover=88, year=2019)
#: The ISBN of the invented book (an e-book is sure on one catalogue's word only with the ISBN of its own file).
ISBN = "9780000000002"
STRONG = ol_doc("OL2W", "The Quiet Orchard", ["Mara Lindqvist"], languages=("eng",), cover=89, year=2019, isbns=(ISBN,))


def family(world: World, *titles: str, name: str = "Family") -> int:
    """A library of books the matching is unsure about; tester manages it. Returns the library."""
    world.only("openlibrary")
    world.fake.ol = [WEAK]
    folder = books_folder(name.lower())
    for title in titles or ("The Quiet Orchard",):
        book(folder, f"{title}.epub", title=title, authors=("Mara Lindqvist",), language="en")
    library = make_library(folder, name)
    member(library)
    scan(library)
    return library


def listing(client: TestClient, library: int) -> list[dict]:  # type: ignore[type-arg]
    answer = client.get(f"/api/v1/libraries/{library}/review")
    assert answer.status_code == 200, answer.text
    return answer.json()["items"]


# ---- Who may ----------------------------------------------------------------------------------------------------


def test_the_list_is_for_members_with_manage_and_nobody_else(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    reader, outsider = make_account("rita"), make_account("sam")
    member(library, "rita", "read")
    assert len(listing(client, library)) == 1
    with new_client(reader) as rita:
        assert rita.get(f"/api/v1/libraries/{library}/review").status_code == 403
    with new_client(outsider) as sam:
        assert sam.get(f"/api/v1/libraries/{library}/review").status_code == 404
    with TestClient(client.app, base_url="http://testserver") as nobody:
        assert nobody.get(f"/api/v1/libraries/{library}/review").status_code == 401


def test_one_list_holds_every_library_the_person_looks_after_and_no_other(client: TestClient, operator: Account,
                                                                          world: World) -> None:
    """"X Bücher brauchen deinen Blick" counts all libraries, so the page lists them all, each entry with its
    library; a library the person only reads, or is no member of, adds nothing."""
    books = family(world, "The Quiet Orchard", name="Family")
    listening = family(world, "The Quiet Meadow", name="Second")
    read_only = family(world, "The Quiet Harbour", name="Third")
    answer = client.get("/api/v1/review")
    assert answer.status_code == 200, answer.text
    found = answer.json()
    assert found["total"] == 3 and {item["library_id"] for item in found["items"]} == {books, listening, read_only}
    member(read_only, "tester", "read")
    found = client.get("/api/v1/review").json()
    assert found["total"] == 2 and {item["library_id"] for item in found["items"]} == {books, listening}
    page = client.get("/api/v1/review", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    reader, outsider = make_account("rita"), make_account("sam")
    member(books, "rita", "read")
    with new_client(reader) as rita:
        assert rita.get("/api/v1/review").json() == {"total": 0, "items": []}
    with new_client(outsider) as sam:
        assert sam.get("/api/v1/review").json() == {"total": 0, "items": []}
    boss = make_account("boss", "operator")
    with new_client(boss) as other:
        assert other.get("/api/v1/review").json()["total"] == 0
    with TestClient(client.app, base_url="http://testserver") as nobody:
        assert nobody.get("/api/v1/review").status_code == 401


def test_the_operator_is_no_exception_who_is_not_a_member(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    other = make_account("boss", "operator")
    with new_client(other) as boss:
        assert boss.get(f"/api/v1/libraries/{library}/review").status_code == 404
        entry = entry_for("The Quiet Orchard")
        assert boss.post(f"/api/v1/review/{entry.id}/keep").status_code == 404
        assert boss.post(f"/api/v1/review/{entry.id}/accept", json={"proposal_id": "openlibrary:OL1W:"}).status_code == 404
    member(library, "boss", "manage")
    with new_client(other) as boss:
        assert boss.get(f"/api/v1/libraries/{library}/review").status_code == 200


def test_a_decision_on_a_book_of_another_library_is_not_possible(client: TestClient, operator: Account,
                                                                  world: World) -> None:
    """The entry is asked for by number: a member with manage in another library gets nothing, for every way."""
    library = family(world)
    other_folder = books_folder("club")
    book(other_folder, "club.epub", title="Club Book", authors=("Zed Alpha",), language="en")
    club = make_library(other_folder, "Club")
    scan(club)
    sam = make_account("sam")
    member(club, "sam", "manage")
    entry = entry_for("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    with new_client(sam) as session:
        assert session.get(f"/api/v1/libraries/{library}/review").status_code == 404
        for method, path, body in (
                ("POST", f"/api/v1/review/{entry.id}/accept", {"proposal_id": "openlibrary:OL1W:"}),
                ("POST", f"/api/v1/review/{entry.id}/keep", None),
                ("POST", f"/api/v1/review/{entry.id}/search", {"title": "x"}),
                ("GET", f"/api/v1/review/{entry.id}/cover?id=openlibrary:OL1W:", None),
                ("POST", f"/api/v1/editions/{edition.id}/unlink", None),
                ("PATCH", f"/api/v1/editions/{edition.id}", {"genre": "krimi"}),
                ("POST", f"/api/v1/editions/{edition.id}/split", None),
                ("POST", f"/api/v1/editions/{edition.id}/join", {"work_id": edition.work_id}),
                ("GET", f"/api/v1/editions/{edition.id}/cover", None)):
            answer = session.request(method, path, json=body)
            assert answer.status_code == 404, (path, answer.status_code)
    assert entry_for("The Quiet Orchard").decision == "" and edition_of("The Quiet Orchard").genre == ""


def test_a_member_who_may_only_read_cannot_change_anything(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    make_account("rita")
    member(library, "rita", "read")
    entry = entry_for("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    with new_client(SessionLocal().scalar(select(Account).where(Account.name == "rita"))) as rita:
        for method, path, body in (
                ("POST", f"/api/v1/review/{entry.id}/accept", {"proposal_id": "openlibrary:OL1W:"}),
                ("POST", f"/api/v1/review/{entry.id}/keep", None),
                ("POST", f"/api/v1/review/{entry.id}/search", {"title": "x"}),
                ("GET", f"/api/v1/review/{entry.id}/cover?id=openlibrary:OL1W:", None),
                ("POST", f"/api/v1/editions/{edition.id}/unlink", None),
                ("PATCH", f"/api/v1/editions/{edition.id}", {"genre": "krimi"}),
                ("POST", f"/api/v1/editions/{edition.id}/split", None)):
            assert rita.request(method, path, json=body).status_code == 403, path
        # Reading the cover of a book of her library is fine.
        assert rita.get(f"/api/v1/editions/{edition.id}/cover").status_code in (200, 404)


def test_the_count_of_books_to_look_at_is_for_the_ones_who_manage(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    library = family(world, "The Quiet Orchard", "The Quiet Orchard Two")
    make_account("rita")
    member(library, "rita", "read")
    mine = next(item for item in client.get("/api/v1/libraries").json() if item["id"] == library)
    assert mine["counts"]["review"] == 2
    with new_client(SessionLocal().scalar(select(Account).where(Account.name == "rita"))) as rita:
        theirs = next(item for item in rita.get("/api/v1/libraries").json() if item["id"] == library)
        assert theirs["counts"]["review"] == 0 and theirs["counts"]["ebooks"] == 2
    admin = client.get("/api/v1/libraries/admin").json()
    assert next(item for item in admin if item["id"] == library)["counts"]["review"] == 2


# ---- The list ---------------------------------------------------------------------------------------------------


def test_each_entry_shows_the_book_and_the_proposals_with_their_reasons(client: TestClient, operator: Account,
                                                                         world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    assert item["title"] == "The Quiet Orchard" and item["authors"] == ["Mara Lindqvist"] and item["language"] == "en"
    assert item["state"] == "maybe" and item["reason"] == "author_weak" and item["reason_text"]
    assert item["file"] == "The Quiet Orchard.epub" and item["kind"] == "ebook"
    [proposal] = item["proposals"]
    assert proposal["id"] == "openlibrary:OL1W:" and proposal["source"] == "openlibrary"
    assert proposal["authors"] == ["Lindqvist"] and proposal["year"] == 2019 and proposal["languages"] == ["en"]
    assert [why["code"] for why in proposal["why"]][:2] == ["title_exact", "author_weak"]
    assert all(why["text"] for why in proposal["why"]) and proposal["score"] > 0 and proposal["fatal"] is False
    assert proposal["cover_url"].startswith(f"/api/v1/review/{item['id']}/cover?id=openlibrary")
    # Nothing of the source's own addresses reaches the browser.
    assert "openlibrary.org" not in str(item) and "archive.org" not in str(item)


def test_the_list_holds_the_books_to_look_at_and_not_those_decided_or_sure(client: TestClient, operator: Account,
                                                                          world: World) -> None:
    library = family(world, "The Quiet Orchard", "Another Book Entirely", "A Third One")
    assert len(listing(client, library)) == 3
    first = listing(client, library)[0]
    assert client.post(f"/api/v1/review/{first['id']}/keep").json() == {"to_look_at": 2}
    assert len(listing(client, library)) == 2
    assert client.get(f"/api/v1/libraries/{library}/review?limit=1&offset=1").json()["total"] == 2
    assert len(client.get(f"/api/v1/libraries/{library}/review?limit=1&offset=1").json()["items"]) == 1
    assert client.get(f"/api/v1/libraries/{library}/review?limit=0").status_code == 422
    assert client.get(f"/api/v1/libraries/{library}/review?limit=201").status_code == 422


# ---- Taking a proposal ------------------------------------------------------------------------------------------


def test_taking_a_proposal_puts_it_into_effect_and_it_stays(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    answer = client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": item["proposals"][0]["id"]})
    assert answer.status_code == 200 and answer.json() == {"to_look_at": 0}
    row = entry_for("The Quiet Orchard")
    assert (row.state, row.decision, row.decided_by) == ("sure", "accepted", operator.id) and row.decided_at
    edition = edition_of("The Quiet Orchard")
    assert edition.published == "2019" and edition.cover and overlay.has_source(edition.overlay)
    assert listing(client, library) == []
    # A run over everything again does not touch it.
    client.post("/api/v1/sources/rematch")
    run_jobs()
    assert entry_for("The Quiet Orchard").decision == "accepted"
    assert edition_of("The Quiet Orchard").published == "2019"


def test_a_proposal_that_is_not_in_the_list_cannot_be_taken(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    for bad in ("openlibrary:OL999W:", "hardcover:1:", "../../x", "x" * 150):
        answer = client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": bad})
        assert answer.status_code == 404 and answer.json()["detail"]["code"] == "proposal_gone", bad
    assert client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": "x" * 161}).status_code == 422
    assert client.post(f"/api/v1/review/{item['id']}/accept", json={}).status_code == 422
    assert client.post("/api/v1/review/999999/accept", json={"proposal_id": "abc"}).status_code == 404
    assert entry_for("The Quiet Orchard").decision == ""


def test_what_a_browser_sends_is_never_taken_as_the_data_of_the_book(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    """Only the id of a proposal is read from the request; a body with a title or a cover address of its own changes
    nothing."""
    library = family(world)
    [item] = listing(client, library)
    client.post(f"/api/v1/review/{item['id']}/accept", json={
        "proposal_id": item["proposals"][0]["id"], "c": {"title": "Hacked", "description": "Hacked words",
                                                          "cover_url": "https://evil.example.com/x.jpg"}})
    edition = edition_of("The Quiet Orchard")
    assert "Hacked" not in str(edition.overlay) and edition.description != "Hacked words"
    assert not any("evil.example.com" in h for h in world.fake.hosts())


def test_leaving_a_book_as_it_is_takes_it_off_the_list_and_asks_nobody(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    asked = len(world.fake.log)
    assert client.post(f"/api/v1/review/{item['id']}/keep").status_code == 200
    assert entry_for("The Quiet Orchard").decision == "kept" and listing(client, library) == []
    assert len(world.fake.log) == asked
    edition = edition_of("The Quiet Orchard")
    assert not edition.overlay


# ---- Searching for others ---------------------------------------------------------------------------------------


def test_looking_for_others_asks_the_sources_with_the_typed_words_and_adds_what_it_finds(client: TestClient, operator: Account,
                                                                                         world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    world.fake.ol = [STRONG, ol_doc("OL3W", "An Orchard of Another Kind", ["Mara Lindqvist"], cover=None)]
    answer = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Orchard Stories", "author": "Mara Lindqvist"})
    assert answer.status_code == 200, answer.text
    params = world.fake.urls("openlibrary.org")[-1]
    assert "Orchard+Stories" in params or "Orchard%20Stories" in params
    proposals = answer.json()["proposals"]
    assert [p["id"] for p in proposals][:2] == ["openlibrary:OL2W:", "openlibrary:OL3W:"] or proposals[0]["found"]
    assert {p["id"] for p in proposals} >= {"openlibrary:OL1W:", "openlibrary:OL2W:", "openlibrary:OL3W:"}
    assert [p["found"] for p in proposals if p["id"] == "openlibrary:OL1W:"] == [False]
    # What was found can be taken.
    taken = client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": "openlibrary:OL2W:"})
    assert taken.status_code == 200
    assert entry_for("The Quiet Orchard").decision == "accepted"


def test_a_search_with_nothing_typed_uses_the_book_itself(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    answer = client.post(f"/api/v1/review/{item['id']}/search", json={})
    assert answer.status_code == 200
    params = world.fake.urls("openlibrary.org")[-1]
    assert "The+Quiet+Orchard" in params or "The%20Quiet%20Orchard" in params


def test_a_search_says_what_went_wrong(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    world.fake.status["openlibrary.org"] = 503
    world.advance(100)
    failed = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Other"})
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "source_failed"
    world.settings(sources_enabled=False)
    off = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Other two"})
    assert off.status_code == 409 and off.json()["detail"]["code"] == "sources_off"


def test_a_search_that_gets_an_answer_nobody_can_read_says_so_and_does_not_break(
        client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    world.fake.bodies["openlibrary.org"] = b"[" * 200_000
    world.advance(100)
    failed = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Other"})
    assert failed.status_code == 502, failed.text
    assert failed.json()["detail"]["code"] == "source_failed" and failed.json()["detail"]["reason"] == "bad_answer"


def test_one_person_cannot_use_up_the_sources_with_searches(client: TestClient, operator: Account, world: World) -> None:
    from app.services import brakes

    assert brakes.LIMITS["review_search"] == 30, "thirty a minute: about one every two seconds is more than a hand does"
    library = family(world)
    [item] = listing(client, library)
    other = make_account("olga")
    member(library, "olga", "manage")
    for number in range(30):
        assert client.post(f"/api/v1/review/{item['id']}/search", json={"title": f"Orchard {number}"}).status_code == 200
    asked = len(world.fake.log)
    refused = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "One too many"})
    assert refused.status_code == 429 and refused.json()["detail"]["code"] == "review_search_too_often"
    assert int(refused.headers["retry-after"]) >= 1
    assert len(world.fake.log) == asked, "the sources were not asked"
    # Another person has a brake of their own, and the minute passing lets the first search again.
    with new_client(other) as olga:
        assert olga.post(f"/api/v1/review/{item['id']}/search", json={"title": "Olga looks"}).status_code == 200
    brakes.forget()
    assert client.post(f"/api/v1/review/{item['id']}/search", json={"title": "A new minute"}).status_code == 200


def test_a_search_cannot_hold_the_server_for_long_it_says_busy(client: TestClient, operator: Account, world: World,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.sources import config

    library = family(world)
    [item] = listing(client, library)
    monkeypatch.setitem(config.LIMITS, "openlibrary", config.Limits(rate=0.05, burst=1, per_day=1000))
    from app.services.sources import throttle

    throttle.take("openlibrary")  # the only token is gone
    busy = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Other three"})
    assert busy.status_code == 429 and busy.json()["detail"]["code"] == "source_busy"
    assert int(busy.headers["retry-after"]) >= 15


def test_what_is_typed_for_a_search_is_bounded(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    assert client.post(f"/api/v1/review/{item['id']}/search", json={"title": "x" * 201}).status_code == 422
    assert client.post(f"/api/v1/review/{item['id']}/search", json={"author": "x" * 201}).status_code == 422


# ---- Covers -----------------------------------------------------------------------------------------------------


def test_the_cover_of_a_proposal_comes_through_nexbooks_and_is_fetched_once(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    url = item["proposals"][0]["cover_url"]
    first = client.get(url)
    assert first.status_code == 200 and first.headers["content-type"] == "image/webp"
    assert first.content[:4] == b"RIFF" and first.headers["x-content-type-options"] == "nosniff"
    fetched = world.fake.count("covers.openlibrary.org")
    assert fetched == 1
    assert client.get(url).content == first.content and world.fake.count("covers.openlibrary.org") == 1, "kept"
    assert client.get(url.replace("OL1W", "OL999W")).status_code == 404


def test_the_cover_of_a_proposal_is_only_ever_fetched_from_the_sources_own_hosts(client: TestClient, operator: Account,
                                                                                  world: World) -> None:
    library = family(world)
    entry = entry_for("The Quiet Orchard")
    with SessionLocal() as db:
        row = db.get(MatchEntry, entry.id)
        assert row is not None
        evil = dict(row.proposals[0])
        evil["id"] = "openlibrary:OLEVIL:"
        evil["c"] = {**evil["c"], "cover_url": "https://evil.example.com/steal.jpg"}
        local = dict(row.proposals[0])
        local["id"] = "openlibrary:OLLOCAL:"
        local["c"] = {**local["c"], "cover_url": "https://covers.openlibrary.org.evil.example.com/x.jpg"}
        row.proposals = [*row.proposals, evil, local]
        db.commit()
    assert client.get(f"/api/v1/review/{entry.id}/cover?id=openlibrary:OLEVIL:").status_code == 404
    assert client.get(f"/api/v1/review/{entry.id}/cover?id=openlibrary:OLLOCAL:").status_code == 404
    assert not any("evil" in h for h in world.fake.hosts())
    assert library


def test_a_cover_that_is_not_a_picture_is_nothing(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    world.fake.cover = b"<html>not a picture</html>" * 20
    assert client.get(item["proposals"][0]["cover_url"]).status_code == 404
    assert library


def test_the_cover_of_an_edition_is_for_its_library_only(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    client.post(f"/api/v1/review/{listing(client, library)[0]['id']}/accept", json={"proposal_id": "openlibrary:OL1W:"})
    edition = edition_of("The Quiet Orchard")
    own = client.get(f"/api/v1/editions/{edition.id}/cover?v={edition.cover[:12]}")
    assert own.status_code == 200 and "immutable" in own.headers["cache-control"]
    assert client.get(f"/api/v1/editions/{edition.id}/cover", headers={"If-None-Match": own.headers["etag"]}).status_code == 304
    with new_client(make_account("sam")) as sam:
        assert sam.get(f"/api/v1/editions/{edition.id}/cover").status_code == 404
    assert client.get("/api/v1/editions/999999/cover").status_code == 404


# ---- Taking a match back, and the hand ---------------------------------------------------------------------------


def test_a_match_taken_back_leaves_the_book_as_the_files_describe_it(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [STRONG]
    folder = books_folder("family")
    book(folder, "a.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",), language="en", isbn=ISBN)
    library = make_library(folder, "Family")
    member(library)
    scan(library)
    edition = edition_of("The Quiet Orchard")
    assert edition.published == "2019" and entry_for("The Quiet Orchard").state == "sure"
    answer = client.post(f"/api/v1/editions/{edition.id}/unlink")
    assert answer.status_code == 200
    after = edition_of("The Quiet Orchard")
    assert after.published == "" and after.cover == "" and not after.overlay
    row = entry_for("The Quiet Orchard")
    assert (row.state, row.reason, row.decision) == ("open", "unlinked", "unlinked")
    client.post("/api/v1/sources/rematch")
    run_jobs()
    assert edition_of("The Quiet Orchard").published == "", "a person took it back: it is not matched again by itself"
    assert entries()[0][3] == "unlinked"


def test_a_match_taken_back_keeps_what_did_not_come_from_it(client: TestClient, operator: Account,
                                                              world: World) -> None:
    from app.services.match import apply

    from .library_helpers import recording

    world.only("openlibrary")
    folder = books_folder("family")
    for name in ("Das Moorlicht", "Die Salzflut", "Der Kieselweg"):
        book(folder, f"{name}.epub", title=name, authors=("Mara Lindqvist",), language="de")
    recording(folder, "Die Nebelwacht.m4b", title="Die Nebelwacht", artist="Mara Lindqvist", album="Die Nebelwacht")
    library = make_library(folder, "Family")
    member(library)
    scan(library)
    # (The language of the recording is the library's: nexbooks worked it out.)
    assert edition_of("Die Nebelwacht").language == "de"
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.title == "Die Nebelwacht"))
        assert edition is not None
        # A shop then knew the recording (its language replaced the one worked out), and a person set the genre.
        edition.overlay = overlay.with_hand(overlay.with_source(
            edition.overlay, {"language": "de", "series": "Moorland", "series_pos": 1.0},
            origins={"language": "audible", "series": "audible", "series_pos": "audible"}), {"genre": "lyrik"})
        apply.rebuild(db, edition)
        db.commit()
        ident = edition.id
    assert edition_of("Die Nebelwacht").series_name == "Moorland"
    assert client.post(f"/api/v1/editions/{ident}/unlink").status_code == 200
    after = edition_of("Die Nebelwacht")
    assert (after.language, after.series_name, after.genre) == ("de", "", "lyrik")
    assert overlay.is_guess(after.overlay, "language")
    with SessionLocal() as db:
        edition = db.get(Edition, ident)
        assert edition is not None
        apply.clear_match(db, edition)
        assert overlay.is_guess(edition.overlay, "language"), "what nexbooks worked out came from no match"


def test_a_correction_is_checked(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    edition = edition_of("The Quiet Orchard")
    patch = f"/api/v1/editions/{edition.id}"
    assert client.patch(patch, json={"genre": "cooking"}).status_code == 422
    assert client.patch(patch, json={"series_pos": 0}).status_code == 422
    assert client.patch(patch, json={"series_pos": 10000}).status_code == 422
    assert client.patch(patch, json={"language": "english"}).status_code == 422
    assert client.patch(patch, json={"authors": ["x"] * 11}).status_code == 422
    assert client.patch(patch, json={"authors": [""]}).status_code == 422
    assert client.patch(patch, json={"title": "x" * 301}).status_code == 422
    assert edition_of("The Quiet Orchard").overlay is None
    ok = client.patch(patch, json={"title": "Line\nBreak\x00Title", "authors": ["A\tB"]})
    assert ok.status_code == 200
    assert edition_of("Line Break Title").title == "Line Break Title", "control characters are spaces"
    assert library


def test_the_subtitle_and_the_narrators_are_corrected_by_hand_and_taken_back(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    from .library_helpers import recording

    world.only("openlibrary")
    folder = books_folder("voices")
    book(folder, "Die Nebelwacht.epub", title="Die Nebelwacht", authors=("Mara Lindqvist",), language="de")
    recording(folder, "Moorland/Die Nebelwacht.m4b", album="Die Nebelwacht", artist="Mara Lindqvist",
              narrator="Ben Hollister")
    library = make_library(folder, "Voices")
    member(library)
    scan(library)
    with SessionLocal() as db:
        ebook, audio = (db.scalar(select(Edition).where(Edition.kind == kind)) for kind in ("ebook", "audio"))
        assert ebook is not None and audio is not None
        ids = {"ebook": ebook.id, "audio": audio.id}
    answer = client.patch(f"/api/v1/editions/{ids['audio']}",
                          json={"subtitle": "Ein Fall im\nMoor", "narrators": ["Ida Brenner", " ", "Ben Hollister"]})
    assert answer.status_code == 200, answer.text
    shown = {e["id"]: e for e in client.get(f"/api/v1/works/{answer.json()['work_id']}").json()["editions"]}
    audio_out = shown[ids["audio"]]
    assert (audio_out["subtitle"], audio_out["narrators"]) == ("Ein Fall im Moor", ["Ida Brenner", "Ben Hollister"])
    assert {"subtitle", "narrators"} <= set(audio_out["corrected"])
    # An e-book has no narrators; a subtitle it has.
    assert client.patch(f"/api/v1/editions/{ids['ebook']}", json={"narrators": ["Ida Brenner"]}).status_code == 422
    assert client.patch(f"/api/v1/editions/{ids['ebook']}", json={"subtitle": "x" * 301}).status_code == 422
    assert client.patch(f"/api/v1/editions/{ids['audio']}", json={"narrators": ["x"] * 21}).status_code == 422
    # Taken back, the file speaks again.
    assert client.patch(f"/api/v1/editions/{ids['audio']}", json={"subtitle": None, "narrators": None}).status_code == 200
    with SessionLocal() as db:
        audio = db.get(Edition, ids["audio"])
        assert audio is not None and audio.subtitle == "" and overlay.hand_fields(audio.overlay) == []
        assert [name for name, in db.execute(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
            Credit.edition_id == audio.id, Credit.role == NARRATOR))] == ["Ben Hollister"]


def test_a_book_cannot_be_given_an_empty_title(client: TestClient, operator: Account, world: World) -> None:
    family(world)
    edition = edition_of("The Quiet Orchard")
    patch = f"/api/v1/editions/{edition.id}"
    for empty in ("", "   ", "\t\n", chr(0)):
        answer = client.patch(patch, json={"title": empty})
        assert answer.status_code == 422, (repr(empty), answer.text)
        assert "title" in answer.text, "the answer names the field"
    assert edition_of("The Quiet Orchard").overlay is None and edition_of("The Quiet Orchard").title == "The Quiet Orchard"
    assert client.patch(patch, json={"title": "The Quiet Orchard Revised", "series": ""}).status_code == 200


def test_a_corrected_book_is_looked_at_again_unless_a_person_decided(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    library = family(world)
    entry = entry_for("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    client.post(f"/api/v1/review/{entry.id}/keep")
    assert entry_for("The Quiet Orchard").decision == "kept"
    world.fake.ol = [STRONG]
    cache.forget()
    assert client.patch(f"/api/v1/editions/{edition.id}", json={"genre": "krimi"}).status_code == 200
    run_jobs()
    row = entry_for("The Quiet Orchard")
    # The kept book is a question again, and now the strong record is its proposal (one catalogue and no ISBN in the
    # file: a person says yes).
    assert (row.decision, row.state, row.reason) == ("", "maybe", "one_source"), (row.decision, row.state, row.reason)
    assert row.proposals and row.proposals[0]["id"].startswith("openlibrary:OL2W")
    assert library


# ---- Joining and splitting --------------------------------------------------------------------------------------


def test_a_book_is_joined_only_to_a_work_the_person_sees(client: TestClient, operator: Account, world: World) -> None:
    library = family(world, "The Quiet Orchard", "Another Book")
    mine = edition_of("The Quiet Orchard")
    other = edition_of("Another Book")
    # A work in a library the person is not in, and a work that is not there: the same answer.
    club_folder = books_folder("club")
    book(club_folder, "c.epub", title="Club Book", authors=("Zed Alpha",), language="en")
    club = make_library(club_folder, "Club")
    scan(club)
    club_work = edition_of("Club Book").work_id
    hidden = client.post(f"/api/v1/editions/{mine.id}/join", json={"work_id": club_work})
    gone = client.post(f"/api/v1/editions/{mine.id}/join", json={"work_id": 999999})
    assert hidden.status_code == gone.status_code == 404 and hidden.json() == gone.json()
    assert edition_of("The Quiet Orchard").work_id == mine.work_id
    ok = client.post(f"/api/v1/editions/{mine.id}/join", json={"work_id": other.work_id})
    assert ok.status_code == 200 and ok.json() == {"work_id": other.work_id}
    assert len(client.get("/api/v1/works").json()["items"]) == 1
    assert client.post(f"/api/v1/editions/{mine.id}/join", json={"work_id": 0}).status_code == 422
    assert library


# ---- What none of this shows another library --------------------------------------------------------------------


def test_a_translation_in_another_library_shows_nothing_of_the_original(client: TestClient, operator: Account,
                                                                         world: World) -> None:
    """A source knows the English book of one library and the German book of another as one work. They are one work for the
    server, and each member sees only the editions of their own library, and what those say."""
    world.connect_hardcover()
    world.only("hardcover")
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"])]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ISBN),
                                       hc_edition("Der stille Obstgarten", "German", isbn="9780000000026")]}
    private = books_folder("private")
    book(private, "en.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",), language="en", isbn=ISBN,
         subjects=("Krimi",), original_title="The Hidden Tale", series="Secret Orchard Saga", series_pos=3)
    club = books_folder("club")
    book(club, "de.epub", title="Der stille Obstgarten", authors=("Mara Lindqvist",), language="de",
         isbn="9780000000026")
    private_id, club_id = make_library(private, "Private"), make_library(club, "Club")
    sam = make_account("sam")
    member(private_id)
    member(club_id, "sam", "manage")
    scan(private_id)
    scan(club_id)
    assert [row[1] for row in entries()] == ["sure", "sure"]
    with SessionLocal() as db:
        english = db.scalar(select(Edition).where(Edition.title == "The Quiet Orchard"))
        german = db.scalar(select(Edition).where(Edition.title == "Der stille Obstgarten"))
        assert english is not None and german is not None and english.work_id == german.work_id
        # Make the private edition say things the club must never read.
        english.description = "A secret description from the private edition."
        english.original_title, english.original_language = "The Hidden Tale", "en"
        db.commit()
        work_id = english.work_id
    with new_client(sam) as session:
        everything = []
        for path in ("/api/v1/works", f"/api/v1/works/{work_id}", "/api/v1/works/stats", "/api/v1/libraries",
                     f"/api/v1/libraries/{club_id}/review"):
            answer = session.get(path)
            assert answer.status_code == 200, path
            everything.append(answer.text)
        shown = session.get(f"/api/v1/works/{work_id}").json()
        assert [e["language"] for e in shown["editions"]] == ["de"]
        assert shown["title"] == "Der stille Obstgarten"
        assert shown["original_title"] == "" and shown["original_language"] == "" and shown["description"] == ""
        assert shown["genre"] == "" and shown["series"] is None
        series = session.get(f"/api/v1/works/{work_id}").json()["series"]
        authors = shown["authors"][0]["id"]
        everything.append(session.get(f"/api/v1/people/{authors}").text)
        if series:
            everything.append(session.get(f"/api/v1/series/{series['id']}").text)
        text = " ".join(everything)
        for secret in ("secret description", "Hidden Tale", "The Quiet Orchard", "Private", "en.epub", "Secret Orchard",
                       "krimi"):
            assert secret not in text, secret
    # The member of the private library sees the English edition and not the German one.
    mine = client.get(f"/api/v1/works/{work_id}").json()
    assert [e["language"] for e in mine["editions"]] == ["en"] and "Obstgarten" not in str(mine)
    assert "Club" not in client.get("/api/v1/works").text


def test_the_lists_of_two_libraries_never_mix(client: TestClient, operator: Account, world: World) -> None:
    first = family(world, "The Quiet Orchard", name="First")
    second = family(world, "Another Book Altogether", name="Second")
    assert [i["title"] for i in listing(client, first)] == ["The Quiet Orchard"]
    assert [i["title"] for i in listing(client, second)] == ["Another Book Altogether"]
    assert len(client.get("/api/v1/libraries").json()) == 2


def test_the_edits_of_a_book_do_not_reach_the_source_or_the_log(client: TestClient, operator: Account, world: World) -> None:
    library = family(world)
    edition = edition_of("The Quiet Orchard")
    asked = len(world.fake.log)
    client.patch(f"/api/v1/editions/{edition.id}", json={"genre": "krimi"})
    client.post(f"/api/v1/editions/{edition.id}/split")
    assert len(world.fake.log) == asked or all("openlibrary" in h for h in world.fake.hosts()[asked:])
    assert library


def test_httpx_is_the_only_way_out_and_it_is_faked() -> None:
    # The guard of the suite: a test that reaches a real service fails at its end (``conftest.no_real_services``).
    assert httpx.__version__


def test_every_reason_the_rule_gives_has_words_in_the_api_and_in_both_languages() -> None:
    """The rule (``score.py``) names its reasons by codes; the API and the interface put them into words. A code
    without words shows as a hole in the list a person looks at."""
    import json
    import re
    from pathlib import Path

    from app.routers.review import REASON, WHY

    root = Path(__file__).resolve().parents[2]
    rule = (root / "backend" / "app" / "services" / "match" / "score.py").read_text(encoding="utf-8")
    ending = "match|exact|main|part|close|weak|unknown|ok|missing|differs|conflict|plausible|series"
    why = set(re.findall(rf'"((?:isbn|asin|title|author|language|volume|year|runtime)_(?:{ending})|companion|collection)"',
                         rule))
    reasons = (set(re.findall(r'(?m)^\s+reason = "([a-z_]+)"$', rule)) | set(re.findall(r'"maybe", "([a-z_]+)"', rule))
               | set(re.findall(r'Decision\("open", "([a-z_]+)"', rule)))
    assert len(why) >= 20 and len(reasons) >= 6, "the check found too little; has the rule changed its shape?"
    assert why <= set(WHY), sorted(why - set(WHY))
    assert reasons <= set(REASON), sorted(reasons - set(REASON))
    assert all(WHY[code] for code in why) and all(REASON[code] for code in reasons)
    for language in ("de", "en"):
        words = json.loads((root / "frontend" / "src" / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        assert why <= set(words["review"]["why"]), (language, sorted(why - set(words["review"]["why"])))
        assert reasons <= set(words["review"]["reason"]), (language, sorted(reasons - set(words["review"]["reason"])))


# ---- A decision and the matching meeting in the same moment ---------------------------------------------------------


def person_acts(title: str, what: str) -> None:
    """Somebody else acts on the book from another session (another tab, another person) while a request is away at the
    sources: ``keep``, ``unlink``, or ``again`` (everything is matched anew, so the entry waits again)."""
    from app.services.match import review

    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry).join(Edition, Edition.id == MatchEntry.edition_id)
                          .where(Edition.title == title))
        edition = db.get(Edition, entry.edition_id)
        if what == "keep":
            review.keep(db, entry, 1)
        elif what == "unlink":
            review.unlink(db, entry, edition, 1)
        elif what == "settled":
            # Another run of the matching answered for the book first.
            entry.state = "maybe"
            db.commit()
        elif what == "decide":
            # A word of a person on the entry, whatever state it is in (the guard on the decision stands alone).
            entry.decision = "kept"
            db.commit()
        else:
            entry.state, entry.proposals = "pending", None
            db.commit()


@pytest.mark.parametrize("what", ["unlink", "decide", "settled"])
def test_a_job_does_not_overwrite_a_decision_made_while_it_asked_the_sources(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch, what: str) -> None:
    from app.services.match import run as matching

    world.only("openlibrary")
    world.fake.ol = [STRONG]
    original = matching.lookup

    def asking_and_meanwhile(*args, **kwargs):  # type: ignore[no-untyped-def]
        found = original(*args, **kwargs)
        person_acts("The Quiet Orchard", what)
        return found

    monkeypatch.setattr(matching, "lookup", asking_and_meanwhile)
    folder = books_folder("race")
    book(folder, "a.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",), language="en")
    scan(make_library(folder, "Race"))
    row = entry_for("The Quiet Orchard")
    expected = {"unlink": ("open", "unlinked", "unlinked"), "decide": ("pending", "", "kept"),
                "settled": ("maybe", "", "")}[what]
    assert (row.state, row.reason, row.decision) == expected, "the person's word stands"
    edition = edition_of("The Quiet Orchard")
    assert not edition.overlay and edition.description == "" and edition.published == "", "the answer was dropped"


def test_taking_a_proposal_is_refused_when_the_book_changed_while_the_sources_were_asked(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.match import review

    library = family(world)
    [item] = listing(client, library)
    original = review.complete

    def completing_and_meanwhile(*args, **kwargs):  # type: ignore[no-untyped-def]
        original(*args, **kwargs)
        person_acts("The Quiet Orchard", "keep")

    monkeypatch.setattr(review, "complete", completing_and_meanwhile)
    answer = client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": item["proposals"][0]["id"]})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "entry_changed", answer.text
    row = entry_for("The Quiet Orchard")
    assert (row.decision, row.state) == ("kept", "maybe"), "the first decision stands"
    assert not edition_of("The Quiet Orchard").overlay, "nothing of the proposal went into the book"


def test_taking_a_proposal_is_refused_when_the_book_is_matched_anew_meanwhile(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.match import review

    library = family(world)
    [item] = listing(client, library)
    original = review.complete

    def completing_and_meanwhile(*args, **kwargs):  # type: ignore[no-untyped-def]
        original(*args, **kwargs)
        person_acts("The Quiet Orchard", "again")

    monkeypatch.setattr(review, "complete", completing_and_meanwhile)
    answer = client.post(f"/api/v1/review/{item['id']}/accept", json={"proposal_id": item["proposals"][0]["id"]})
    assert answer.status_code == 409
    assert (entry_for("The Quiet Orchard").decision, entry_for("The Quiet Orchard").state) == ("", "pending")
    assert not edition_of("The Quiet Orchard").overlay


def test_leaving_a_book_as_it_is_is_only_for_a_book_on_the_list_and_only_once(
        client: TestClient, operator: Account, world: World) -> None:
    library = family(world, "The Quiet Orchard", "Another Book")
    [first, second] = listing(client, library)
    assert client.post(f"/api/v1/review/{first['id']}/keep").status_code == 200
    again = client.post(f"/api/v1/review/{first['id']}/keep")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "entry_changed", "a double click"
    # A book that waits for the matching, or that the matching is sure about, is not on the list: nothing to leave.
    person_acts(second["title"], "again")
    assert client.post(f"/api/v1/review/{second['id']}/keep").status_code == 409
    assert entry_for(second["title"]).decision == ""
    sure = entry_for("The Quiet Orchard")
    with SessionLocal() as db:
        row = db.get(MatchEntry, sure.id)
        row.decision, row.state = "", "sure"
        db.commit()
    assert client.post(f"/api/v1/review/{sure.id}/keep").status_code == 409
    assert entry_for("The Quiet Orchard").decision == ""


def test_a_search_does_not_write_into_a_book_decided_while_it_was_away(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.match import review

    library = family(world)
    [item] = listing(client, library)
    before = [p["id"] for p in item["proposals"]]
    original = review.lookup

    def asking_and_meanwhile(*args, **kwargs):  # type: ignore[no-untyped-def]
        found = original(*args, **kwargs)
        person_acts("The Quiet Orchard", "keep")
        return found

    monkeypatch.setattr(review, "lookup", asking_and_meanwhile)
    world.fake.ol = [STRONG, ol_doc("OL5W", "Orchard Stories", ["Mara Lindqvist"], languages=("eng",))]
    answer = client.post(f"/api/v1/review/{item['id']}/search", json={"title": "Orchard Stories"})
    assert answer.status_code == 409
    row = entry_for("The Quiet Orchard")
    assert row.decision == "kept" and [p["id"] for p in row.proposals] == before


def test_a_search_on_a_book_that_is_not_on_the_list_asks_nobody(client: TestClient, operator: Account,
                                                                 world: World) -> None:
    library = family(world)
    [item] = listing(client, library)
    person_acts("The Quiet Orchard", "keep")
    asked = len(world.fake.log)
    assert client.post(f"/api/v1/review/{item['id']}/search", json={"title": "x"}).status_code == 409
    assert len(world.fake.log) == asked
