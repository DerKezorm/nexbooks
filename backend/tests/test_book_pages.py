"""What the pages of a book stand on: the download of a file, the fields of an edition that the page to correct it
needs, and the catalogue search. Each with the rule of the interface: what a person may not see is not there."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import Account, BookFile, Edition, Library
from app.services import brakes

from .bookmaker import cover_image
from .conftest import TAB, make_account, new_client
from .fake_sources import audible_product, ol_doc
from .library_helpers import book, books_folder, make_library, make_link, scan
from .match_helpers import World, member
from .test_search import Shelves


@pytest.fixture
def shelves(client: TestClient, operator: Account) -> Shelves:
    return Shelves()


def file_id(path: str) -> int:
    with SessionLocal() as db:
        found = db.scalar(select(BookFile.id).where(BookFile.path == path))
        assert found is not None, path
        return found


# ---- Download -------------------------------------------------------------------------------------------------------


def test_a_member_downloads_the_file_as_it_lies_in_the_library(client: TestClient, shelves: Shelves) -> None:
    ident = file_id("alpha.epub")
    answer = client.get(f"/api/v1/files/{ident}/download")
    assert answer.status_code == 200
    assert answer.content == (shelves.family / "alpha.epub").read_bytes()
    assert answer.headers["content-type"] == "application/epub+zip"
    assert answer.headers["content-disposition"].startswith("attachment;") and "alpha.epub" in answer.headers[
        "content-disposition"]
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in answer.headers["cache-control"]


def test_a_part_of_it_is_served_when_asked_for(client: TestClient, shelves: Shelves) -> None:
    ident = file_id("alpha.epub")
    whole = (shelves.family / "alpha.epub").read_bytes()
    part = client.get(f"/api/v1/files/{ident}/download", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == whole[:10]


def test_the_name_of_the_file_is_told_safely(client: TestClient, shelves: Shelves) -> None:
    book(shelves.family, "Sommer Nächte ü.epub", title="Nächte", authors=("Eva Müller",), language="de")
    scan(shelves.family_id)
    with SessionLocal() as db:
        ident = db.scalar(select(BookFile.id).where(BookFile.path.like("Sommer%")))
    answer = client.get(f"/api/v1/files/{ident}/download")
    assert answer.status_code == 200
    header = answer.headers["content-disposition"]
    assert "\n" not in header and "\r" not in header
    assert "filename*=utf-8''Sommer%20N%C3%A4chte" in header


def test_a_file_of_a_library_the_caller_is_not_in_is_one_that_does_not_exist(client: TestClient,
                                                                              shelves: Shelves) -> None:
    family_file, club_file = file_id("alpha.epub"), file_id("delta.epub")
    with new_client(shelves.rita) as rita, new_client(shelves.sam) as sam, new_client(shelves.tom) as tom:
        assert rita.get(f"/api/v1/files/{family_file}/download").status_code == 200
        assert sam.get(f"/api/v1/files/{family_file}/download").status_code == 404
        missing = rita.get("/api/v1/files/999999/download")
        assert missing.status_code == 404
        # The same answer, word for word, whether the file is somebody else's or was never there.
        assert rita.get(f"/api/v1/files/{club_file}/download").json() == missing.json()
        assert tom.get(f"/api/v1/files/{family_file}/download").status_code == 404
    with TestClient(client.app, base_url="http://testserver") as anonymous:
        assert anonymous.get(f"/api/v1/files/{family_file}/download").status_code == 401


def test_only_the_files_of_the_book_itself_can_be_downloaded(client: TestClient, shelves: Shelves) -> None:
    (shelves.family / "cover.jpg").write_bytes(cover_image(kind="JPEG"))
    (shelves.family / "metadata.json").write_text('{"title": "x"}', encoding="utf-8")
    from .library_helpers import age_all

    age_all(shelves.family)
    scan(shelves.family_id)
    with SessionLocal() as db:
        sidecars = list(db.scalars(select(BookFile.id).where(BookFile.role == "sidecar")))
    assert sidecars, "the scan knows a file beside a book"
    for ident in sidecars:
        assert client.get(f"/api/v1/files/{ident}/download").status_code == 404


def test_a_file_that_is_gone_or_marked_missing_is_not_served(client: TestClient, shelves: Shelves) -> None:
    ident = file_id("beta.epub")
    (shelves.family / "beta.epub").unlink()
    assert client.get(f"/api/v1/files/{ident}/download").status_code == 404
    other = file_id("gamma.epub")
    with SessionLocal() as db:
        db.execute(update(BookFile).where(BookFile.id == other).values(missing_at=clock.now()))
        db.commit()
    assert client.get(f"/api/v1/files/{other}/download").status_code == 404


def test_a_path_in_the_database_cannot_lead_out_of_the_library(client: TestClient, shelves: Shelves) -> None:
    """The place on the disk comes from the database and is checked again: a row that was tampered with, a link that was
    put in the folder since the scan, none of it reaches a file outside the library."""
    secret = books_folder("outside") / "secret.txt"
    secret.write_text("not for you", encoding="utf-8")
    make_link(shelves.family / "door", secret.parent)
    ident = file_id("alpha.epub")
    for tampered in ("../outside/secret.txt", "door/secret.txt", "/etc/passwd", "..", "a/../../outside/secret.txt",
                     "C:/Windows/win.ini", "\\\\host\\share\\x", "alpha.epub\x00.txt", ""):
        with SessionLocal() as db:
            db.execute(update(BookFile).where(BookFile.id == ident).values(path=tampered))
            db.commit()
        answer = client.get(f"/api/v1/files/{ident}/download")
        assert answer.status_code == 404, tampered
        assert b"not for you" not in answer.content
    # Put right again, the same file is served.
    with SessionLocal() as db:
        db.execute(update(BookFile).where(BookFile.id == ident).values(path="alpha.epub"))
        db.commit()
    assert client.get(f"/api/v1/files/{ident}/download").status_code == 200


def test_a_program_with_a_key_downloads_only_from_the_libraries_of_its_account(client: TestClient,
                                                                               shelves: Shelves) -> None:
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    with new_client(shelves.rita) as rita:
        secret = rita.post("/api/v1/api-keys", json={"name": "reader"}).json()["secret"]
    family_file, club_file = file_id("alpha.epub"), file_id("delta.epub")
    with TestClient(client.app, base_url="http://testserver") as program:
        auth = {"Authorization": f"Bearer {secret}"}
        assert program.get(f"/api/v1/files/{family_file}/download", headers=auth).status_code == 200
        assert program.get(f"/api/v1/files/{club_file}/download", headers=auth).status_code == 404
        refused = program.get(f"/api/v1/files/{family_file}/download", headers={**auth, "Origin": "https://example.com"})
        assert refused.status_code == 403


# ---- What the page of a book needs ----------------------------------------------------------------------------------


def work_of(client: TestClient, title: str) -> dict:  # type: ignore[type-arg]
    items = client.get("/api/v1/works", params={"per_page": 100}).json()["items"]
    return next(item for item in items if item["title"] == title)


def test_an_edition_tells_its_own_authors_series_and_genre(client: TestClient, shelves: Shelves) -> None:
    work = client.get(f"/api/v1/works/{work_of(client, 'Beta')['id']}").json()
    [edition] = work["editions"]
    assert edition["authors"] == ["Ben Hollister"] and edition["series_name"] == "Saga"
    assert edition["series_pos"] == 2 and edition["genre"] == "fantasy"
    assert edition["corrected"] == [] and edition["matched_by"] == []


def test_what_the_caller_may_not_see_of_a_work_is_not_among_its_editions(client: TestClient, shelves: Shelves) -> None:
    work_id = work_of(client, "Alpha Lighthouse")["id"]
    with new_client(shelves.sam) as sam, new_client(shelves.rita) as rita:
        [seen] = sam.get(f"/api/v1/works/{work_id}").json()["editions"]
        assert seen["kind"] == "audio" and seen["authors"] == ["Ada Feldkamp"] and seen["narrators"] == ["Clara Vienne"]
        assert seen["library_name"] == "Club"
        [theirs] = rita.get(f"/api/v1/works/{work_id}").json()["editions"]
        assert theirs["kind"] == "ebook" and theirs["library_name"] == "Family" and theirs["isbn"] == "9783999900100"
        assert [f["format"] for f in theirs["files"]] == ["epub"]


def test_a_correction_by_hand_shows_as_one_and_can_be_taken_back(client: TestClient, shelves: Shelves) -> None:
    beta = work_of(client, "Beta")
    edition_id = client.get(f"/api/v1/works/{beta['id']}").json()["editions"][0]["id"]
    assert client.patch(f"/api/v1/editions/{edition_id}", json={"genre": "krimi", "series_pos": 7}).status_code == 200
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["corrected"] == ["genre", "series_pos"] and edition["genre"] == "krimi" and edition["series_pos"] == 7
    assert client.patch(f"/api/v1/editions/{edition_id}", json={"genre": None, "series_pos": None}).status_code == 200
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["corrected"] == [] and edition["genre"] == "fantasy" and edition["series_pos"] == 2


def test_the_title_corrected_by_hand_can_be_taken_back_too_but_never_made_empty(client: TestClient,
                                                                                 shelves: Shelves) -> None:
    beta = work_of(client, "Beta")
    edition_id = client.get(f"/api/v1/works/{beta['id']}").json()["editions"][0]["id"]
    assert client.patch(f"/api/v1/editions/{edition_id}", json={"title": "Beta, Retold"}).status_code == 200
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["title"] == "Beta, Retold" and edition["corrected"] == ["title"]
    # ``null`` takes the correction back: the file speaks again.
    assert client.patch(f"/api/v1/editions/{edition_id}", json={"title": None}).status_code == 200
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["title"] == "Beta" and edition["corrected"] == []
    # Nothing, or only blanks, is not a title; the answer names the field.
    for empty in ("", "   ", chr(10) + chr(9)):
        refused = client.patch(f"/api/v1/editions/{edition_id}", json={"title": empty})
        assert refused.status_code == 422, repr(empty)
        body = refused.json()["detail"]
        assert body["code"] == "invalid_input" and "title" in (body.get("fields") or [body.get("field")]), repr(empty)
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["title"] == "Beta"


# ---- Who may look after a book ---------------------------------------------------------------------------------------


def beta_edition(client: TestClient) -> int:
    beta = work_of(client, "Beta")
    return int(client.get(f"/api/v1/works/{beta['id']}").json()["editions"][0]["id"])


def hands(edition_id: int, work_id: int) -> list[tuple[str, str, dict | None]]:  # type: ignore[type-arg]
    return [
        ("patch", f"/api/v1/editions/{edition_id}", {"genre": "krimi"}),
        ("post", f"/api/v1/editions/{edition_id}/unlink", None),
        ("post", f"/api/v1/editions/{edition_id}/split", None),
        ("post", f"/api/v1/editions/{edition_id}/join", {"work_id": work_id}),
    ]


def test_a_member_who_may_only_read_cannot_correct_unlink_split_or_join(client: TestClient, shelves: Shelves) -> None:
    edition_id = beta_edition(client)
    work_id = work_of(client, "Über den Wolken")["id"]
    with new_client(shelves.rita) as rita:
        for method, url, body in hands(edition_id, work_id):
            answer = getattr(rita, method)(url, json=body) if body is not None else getattr(rita, method)(url)
            assert answer.status_code == 403, url
            assert answer.json()["detail"]["code"] == "forbidden", url
    # Nothing changed.
    [edition] = client.get(f"/api/v1/works/{work_of(client, 'Beta')['id']}").json()["editions"]
    assert edition["corrected"] == [] and edition["genre"] == "fantasy"


def test_somebody_who_does_not_see_the_library_finds_the_hands_as_if_the_book_were_not_there(client: TestClient,
                                                                                            shelves: Shelves) -> None:
    edition_id = beta_edition(client)
    work_id = work_of(client, "Über den Wolken")["id"]
    nowhere = 987654
    with new_client(shelves.sam) as sam, new_client(shelves.tom) as tom:
        for stranger in (sam, tom):
            for (method, url, body), (_, url_gone, body_gone) in zip(hands(edition_id, work_id), hands(nowhere, work_id),
                                                                      strict=True):
                real = getattr(stranger, method)(url, json=body) if body is not None else getattr(stranger, method)(url)
                gone = (getattr(stranger, method)(url_gone, json=body_gone) if body_gone is not None
                        else getattr(stranger, method)(url_gone))
                assert real.status_code == gone.status_code == 404, url
                assert real.json() == gone.json(), url
    [edition] = client.get(f"/api/v1/works/{work_of(client, 'Beta')['id']}").json()["editions"]
    assert edition["corrected"] == []


def test_a_blurb_wrapped_by_the_file_reaches_the_page_as_paragraphs_not_as_broken_lines(client: TestClient,
                                                                                         shelves: Shelves) -> None:
    # As e-books carry it: HTML in the description (escaped in the OPF), the text wrapped at the width of the file, two
    # line breaks between the paragraphs.
    blurb = ("<div>A keeper of the lamp, who has kept it for \nforty winters and says nothing of it to the \n"
             "family. <br><br>Then the ship comes in, and with it \nthe question of who is to be told.</div>")
    book(shelves.family, "wrapped.epub", title="Wrapped Blurb", authors=("Eva Mueller",), language="en", description=blurb)
    scan(shelves.family_id)
    found = client.get("/api/v1/works", params={"per_page": 100}).json()["items"]
    ident = next(item["id"] for item in found if item["title"] == "Wrapped Blurb")
    description = client.get(f"/api/v1/works/{ident}").json()["description"]
    assert description.split("\n\n") == [
        "A keeper of the lamp, who has kept it for forty winters and says nothing of it to the family.",
        "Then the ship comes in, and with it the question of who is to be told."]


def test_the_sources_that_know_an_edition_are_named(client: TestClient, shelves: Shelves) -> None:
    from app.models import ExternalRef

    beta = work_of(client, "Beta")
    edition_id = client.get(f"/api/v1/works/{beta['id']}").json()["editions"][0]["id"]
    with SessionLocal() as db:
        db.add(ExternalRef(edition_id=edition_id, source="openlibrary", kind="work", key="OL1W"))
        db.add(ExternalRef(edition_id=edition_id, source="hardcover", kind="work", key="77"))
        db.commit()
    [edition] = client.get(f"/api/v1/works/{beta['id']}").json()["editions"]
    assert edition["matched_by"] == ["hardcover", "openlibrary"]


# ---- The catalogue --------------------------------------------------------------------------------------------------


def test_the_catalogue_answers_that_nothing_is_on_when_the_main_switch_is_off(client: TestClient, operator: Account) -> None:
    # The tests start with the main switch off (``conftest``): nothing may go out.
    answer = client.post("/api/v1/search/catalog", json={"q": "Salt Orchard"})
    assert answer.status_code == 200
    assert answer.json() == {"available": False, "failed": False, "pending": False, "ticket": None, "items": [],
                             "people": [], "resting": []}


def test_the_catalogue_puts_together_what_the_sources_know(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary", "audible")
    world.fake.ol = [ol_doc("OL1W", "The Salt Orchard", ["Mara Lindqvist"], year=2019),
                     ol_doc("OL2W", "Salt Orchard Notes", ["Ada Feldkamp"], year=2021)]
    world.fake.audible = {"de": [audible_product("B000000001", "The Salt Orchard", ["Mara Lindqvist"], 600)],
                          "us": []}
    body = client.post("/api/v1/search/catalog", json={"q": "salt orchard"}).json()
    assert body["available"] is True and body["failed"] is False
    titles = {(item["title"], tuple(item["sources"])) for item in body["items"]}
    assert ("The Salt Orchard", ("openlibrary",)) in titles or any(
        item["title"] == "The Salt Orchard" and "openlibrary" in item["sources"] for item in body["items"])
    assert any(item["title"] == "Salt Orchard Notes" for item in body["items"])
    assert len(body["items"]) <= 6
    # Only the typed words went out: no file name, no path, no name of a library or of a person.
    sent = [str(request.url) for request in world.fake.log]
    assert sent and all("salt" in url.lower() or "orchard" in url.lower() or "cover" in url for url in sent), sent


def test_a_book_two_sources_know_is_one_hit(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary", "audible")
    world.fake.ol = [ol_doc("OL1W", "The Salt Orchard", ["Mara Lindqvist"], year=2019)]
    world.fake.audible = {"de": [audible_product("B000000001", "The Salt Orchard", ["Mara Lindqvist"], 600)], "us": []}
    items = client.post("/api/v1/search/catalog", json={"q": "salt"}).json()["items"]
    [hit] = [item for item in items if item["title"] == "The Salt Orchard"]
    assert sorted(hit["sources"]) == ["audible", "openlibrary"]


def test_a_catalogue_that_does_not_answer_is_told(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.status = {"openlibrary.org": 500}
    body = client.post("/api/v1/search/catalog", json={"q": "salt orchard"}).json()
    assert body["available"] is True and body["failed"] is True and body["items"] == []


def test_the_catalogue_is_slowed_down_per_person(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Salt Orchard", ["Mara Lindqvist"])]
    brakes.forget()
    codes = [client.post("/api/v1/search/catalog", json={"q": f"salt {n}"}).status_code
             for n in range(brakes.LIMITS["catalog_search"] + 2)]
    assert codes[: brakes.LIMITS["catalog_search"]] == [200] * brakes.LIMITS["catalog_search"] and codes[-1] == 429
    # Another person has a minute of their own.
    with new_client(make_account("rita")) as rita:
        assert rita.post("/api/v1/search/catalog", json={"q": "salt"}).status_code == 200


def test_the_catalogue_wants_text_and_a_browser(client: TestClient, operator: Account, world: World) -> None:
    for body in ({}, {"q": ""}, {"q": "x" * 500}, {"q": 5}):
        assert client.post("/api/v1/search/catalog", json=body).status_code == 422, body
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        assert nobody.post("/api/v1/search/catalog", json={"q": "salt"}).status_code == 401
    assert world.fake.log == []


def test_a_member_who_may_only_read_may_ask_the_catalogue_too(client: TestClient, operator: Account,
                                                              world: World) -> None:
    """The catalogue is not the library: it shows nothing of any library, so no right to one is needed."""
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Salt Orchard", ["Mara Lindqvist"])]
    folder = books_folder("only")
    library = make_library(folder, "Only")
    rita = make_account("rita")
    with new_client(rita) as session:
        member(library, "rita", "read")
        body = session.post("/api/v1/search/catalog", json={"q": "salt"}).json()
        assert body["available"] is True and body["items"]
    with SessionLocal() as db:
        assert db.get(Library, library) is not None
        assert db.scalar(select(Edition.id).limit(1)) is None


def test_a_recording_lies_where_the_scan_found_it(client: TestClient, shelves: Shelves) -> None:
    """A smoke test of the pieces together: the recording is found, downloaded and its edition says who narrated."""
    work = work_of(client, "Alpha Lighthouse")
    detail = client.get(f"/api/v1/works/{work['id']}").json()
    audio = next(e for e in detail["editions"] if e["kind"] == "audio")
    [part] = audio["files"]
    answer = client.get(f"/api/v1/files/{part['id']}/download")
    assert answer.status_code == 200 and answer.headers["content-type"] == "audio/mp4"
    assert answer.content == Path(shelves.club / "alpha" / "alpha.m4b").read_bytes()
