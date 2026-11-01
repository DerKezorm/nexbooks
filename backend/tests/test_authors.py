"""Photos and short biographies of authors: the Wikidata and Commons client, the job that looks an author up, the
photos in the authors' folders, and the API (the grid of authors, the photo, the page of an author).

The rules under test: **a photo only for an author who is certain** (a name alone is nothing: a book of theirs that nexbooks
holds must be among the works that name the item as author), **a picture only under a free licence**, **only hosts of the
table**, and **an author is seen through their books** (the grid, the count, the photo and the page are made of what the
caller may see). No test reaches a real service."""

from __future__ import annotations

import copy
import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import clock
from app.db import SessionLocal
from app.models import (
    AUTHOR,
    Account,
    Credit,
    Edition,
    Job,
    LibraryMember,
    Person,
    PersonInfo,
    PersonPhoto,
)
from app.services import authors as authors_service
from app.services import jobs, people_info, photos, portraits, settings_service
from app.services.sources import cache, config, http, wikidata

from .bookmaker import cover_image
from .conftest import new_client
from .fake_sources import fixture, host_of
from .fake_wikidata import AUTHOR as ADA
from .fake_wikidata import NAMESAKE, FakeAuthors
from .library_helpers import age, run_jobs
from .reading_helpers import World


class Net:
    """The sources on with the stand-in of the services, and time that moves only when the code waits."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake = FakeAuthors()
        self.offset = 0.0
        start = clock.now()
        monkeypatch.setattr(clock, "now", lambda: start + timedelta(seconds=self.offset))
        monkeypatch.setattr(http, "sleep", self.advance)
        monkeypatch.setattr(http, "transport", self.fake)
        self.settings(sources_enabled=True, author_info=True, source_wikidata=True, source_openlibrary=True,
                      source_hardcover=False, source_audible=False, source_dnb=False, source_apple=False)

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def days(self, days: float) -> None:
        self.offset += days * 86400

    def settings(self, **values: Any) -> None:
        with SessionLocal() as db:
            settings_service.save(db, values)


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    return Net(monkeypatch)


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


def person_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Person.id).where(Person.name == name)).one()


def look_up(name: str) -> PersonInfo | None:
    """Runs the job for an author by hand, in a session of its own."""
    ident = person_id(name)
    with SessionLocal() as db:
        people_info.look_up(db, ident)
    with SessionLocal() as db:
        row = db.get(PersonInfo, ident)
        if row is not None:
            db.expunge(row)
        return row


# ---- Reading the answers ---------------------------------------------------------------------------------------------


def test_the_search_answer_is_cut_down_to_items() -> None:
    hits = wikidata.parse_search(fixture("wikidata_search.json"))
    assert [(h.id, h.label, h.matched) for h in hits] == [(NAMESAKE, "Ada Feldkamp", "Ada Feldkamp"),
                                                           (ADA, "Ada Feldkamp", "Ada Feldkamp")]
    assert wikidata.parse_search({"search": [{"id": "not-an-item"}, 5, {"id": "Q1; DROP"}]}) == []
    assert wikidata.parse_search("nonsense") == [] and wikidata.parse_search(None) == []


def test_an_item_is_read_for_its_names_its_picture_and_its_years() -> None:
    found = wikidata.parse_entities(fixture("wikidata_entities.json"))
    ada = found[ADA]
    assert ada.human and ada.image == "Ada Feldkamp reading 2019.jpg" and ada.born == 1971 and ada.died is None
    assert ada.descriptions["de"] == "erfundene Schriftstellerin"
    assert "A. Feldkamp" in ada.names() and "Ada Feldkamp" in ada.names()
    # A year given only to the decade or century is no year of a life.
    namesake = found[NAMESAKE]
    assert namesake.human and namesake.born == 1990 and namesake.image == ""
    assert found["Q9100001"].human is False
    assert wikidata.parse_entities({"entities": {"Q1": {"id": "Q1", "missing": ""}}}) == {}
    assert wikidata.parse_entities([]) == {}


def test_a_picture_marked_deprecated_or_with_an_odd_name_is_not_taken() -> None:
    data = copy.deepcopy(fixture("wikidata_entities.json"))
    claim = data["entities"][ADA]["claims"]["P18"][0]
    claim["rank"] = "deprecated"
    assert wikidata.parse_entities(data)[ADA].image == ""
    claim["rank"] = "normal"
    for odd in ("../../etc/passwd.jpg", "x.svg", "a|b.jpg", "x.tif", "no-extension", "x" * 300 + ".jpg"):
        claim["mainsnak"]["datavalue"]["value"] = odd
        assert wikidata.parse_entities(data)[ADA].image == "", odd


@pytest.mark.parametrize("licence,free", [
    ("CC BY-SA 4.0", True), ("CC BY 2.0", True), ("CC BY-SA 3.0 de", True), ("CC0", True), ("CC0 1.0", True),
    ("Public domain", True), ("PD-old-100", True), ("PD US", True),
    ("CC BY-NC 2.0", False), ("CC BY-ND 4.0", False), ("CC BY-NC-SA 3.0", False), ("All rights reserved", False),
    ("Fair use", False), ("", False), ("GFDL", False), ("CC BY-SA", False),
])
def test_only_free_licences_may_be_shown(licence: str, free: bool) -> None:
    assert wikidata.free(licence) is free


def test_a_picture_comes_with_its_author_and_licence() -> None:
    picture = wikidata.parse_picture(fixture("commons_imageinfo.json"))
    assert picture is not None
    assert (picture.license, picture.author) == ("CC BY-SA 4.0", "Pia Example")
    assert picture.license_url == "https://creativecommons.org/licenses/by-sa/4.0"
    assert picture.page_url == "https://commons.wikimedia.org/wiki/File:Ada_Feldkamp_reading_2019.jpg"
    assert picture.thumb_url.startswith("https://upload.wikimedia.org/")


def _imageinfo(change: Any) -> dict[str, Any]:
    data = copy.deepcopy(fixture("commons_imageinfo.json"))
    info = next(iter(data["query"]["pages"].values()))["imageinfo"][0]
    change(info)
    return data


@pytest.mark.parametrize("change", [
    lambda info: info["extmetadata"]["LicenseShortName"].update(value="CC BY-NC 4.0"),
    lambda info: info["extmetadata"].pop("LicenseShortName"),
    lambda info: info["extmetadata"]["NonFree"].update(value="true"),
    lambda info: info.update(thumburl="https://evil.example.com/p.jpg"),
    lambda info: info.update(thumburl="http://upload.wikimedia.org/p.jpg"),
    lambda info: info.update(thumburl="https://upload.wikimedia.org.evil.example.com/p.jpg"),
    lambda info: info.pop("extmetadata"),
])
def test_a_picture_under_another_licence_or_on_another_host_is_not_taken(change: Any) -> None:
    assert wikidata.parse_picture(_imageinfo(change)) is None


def test_the_author_is_the_text_of_the_artist_without_markup() -> None:
    def tag(info: dict[str, Any]) -> None:
        info["extmetadata"]["Artist"]["value"] = "<script>x</script><b>Pia &amp; Co</b>"

    assert wikidata.parse_picture(_imageinfo(tag)).author == "x Pia & Co"  # type: ignore[union-attr]

    def link(info: dict[str, Any]) -> None:
        info["extmetadata"]["LicenseUrl"]["value"] = "http://creativecommons.org/licenses/by-sa/3.0"

    # An old http address of the licence is shown with https; one that is not a licence page is not shown at all.
    assert wikidata.parse_picture(_imageinfo(link)).license_url == "https://creativecommons.org/licenses/by-sa/3.0"  # type: ignore[union-attr]

    def elsewhere(info: dict[str, Any]) -> None:
        info["extmetadata"]["LicenseUrl"]["value"] = "https://evil.example.com/licenses/x"

    assert wikidata.parse_picture(_imageinfo(elsewhere)).license_url == ""  # type: ignore[union-attr]


# ---- Who the author is -----------------------------------------------------------------------------------------------


def identify(net: Net, name: str = "Ada Feldkamp", titles: tuple[str, ...] = ("Alpha", "Beta"),
             language: str = "en") -> Any:
    with SessionLocal() as db:
        return wikidata.identify(db, name, list(titles), language)


def test_the_author_is_the_item_with_a_book_of_ours_among_its_works(world: World, net: Net) -> None:
    found = identify(net)
    assert found is not None and found.qid == ADA and found.born == 1971
    assert found.image == "Ada Feldkamp reading 2019.jpg" and found.descriptions["de"] == "erfundene Schriftstellerin"


def test_a_translated_title_is_a_title_of_the_work_too(world: World, net: Net) -> None:
    assert identify(net, titles=("Der stille Obstgarten",), language="de").qid == ADA  # type: ignore[union-attr]
    assert identify(net, titles=("Obstgarten",)).qid == ADA  # type: ignore[union-attr]


def test_one_title_that_is_a_common_word_proves_nothing_two_of_them_or_a_telling_one_do(world: World, net: Net) -> None:
    """``Alpha`` alone could be anybody's book (the author of a book called ``Poems`` is not found by it); ``Alpha`` and
    ``Beta``, two books of ours among two works of the item, are; and so is one title that says something by itself."""
    assert identify(net, titles=("Alpha",)) is None
    assert identify(net, titles=("Beta",)) is None
    assert identify(net, titles=("Alpha", "Beta")).qid == ADA  # type: ignore[union-attr]
    assert identify(net, titles=("The Quiet Orchard",)).qid == ADA  # type: ignore[union-attr]


def test_two_titles_of_ours_that_one_work_carries_are_one_book(world: World, net: Net) -> None:
    """Two common words that are the names of one and the same work (a label and an alias) are one proof, not two."""
    net.fake.entities["Q9100001"]["aliases"] = {"en": [{"language": "en", "value": "Beta"}]}
    net.fake.works[ADA] = ["Q9100001"]
    assert identify(net, titles=("Alpha", "Beta")) is None


def test_two_common_titles_are_not_enough_when_another_human_of_the_name_wrote_something(world: World, net: Net) -> None:
    """The namesake has a work too, none of ours. ``Alpha`` and ``Beta`` are common words: two writers of one name, no
    photo rather than the wrong one. A title that says something by itself still tells the two apart (scientists
    with articles often share the name of a novelist)."""
    net.fake.entities["Q9100099"] = {"type": "item", "id": "Q9100099",
                                     "labels": {"en": {"language": "en", "value": "Football Days"}}, "aliases": {}}
    net.fake.works[NAMESAKE] = ["Q9100099"]
    assert identify(net, titles=("Alpha", "Beta")) is None
    assert identify(net, titles=("The Quiet Orchard",)).qid == ADA  # type: ignore[union-attr]
    net.fake.works[NAMESAKE] = []
    cache.forget()
    assert identify(net, titles=("Alpha", "Beta")).qid == ADA  # type: ignore[union-attr]


def test_a_pen_name_the_item_knows_as_an_alias_never_gets_the_person_behind_it(world: World, net: Net) -> None:
    """Wikidata knows ``Nora Quell`` as another name of Ada Feldkamp. A book under ``Nora Quell`` does not get Ada's
    photo or text: a pen name stays a pen name. Initials of the label are still her name."""
    net.fake.entities[ADA]["aliases"] = {"en": [{"language": "en", "value": "Nora Quell"},
                                                {"language": "en", "value": "A. Feldkamp"}]}
    hit = copy.deepcopy(net.fake.search["search"][1])
    hit["match"] = {"type": "alias", "language": "en", "text": "Nora Quell"}
    net.fake.search["search"].append(hit)
    assert identify(net, name="Nora Quell") is None
    assert identify(net, name="A. Feldkamp").qid == ADA  # type: ignore[union-attr]
    assert net.fake.count("wikidata", "api.php") > 0


def test_the_item_itself_must_carry_the_name_not_only_the_search_hit(world: World, net: Net) -> None:
    """The search lists the item under the name, but its own labels name somebody else: not the author."""
    net.fake.entities[ADA]["labels"] = {"en": {"language": "en", "value": "Ida Feldkamp"}}
    net.fake.entities[ADA]["aliases"] = {}
    assert identify(net) is None


def test_a_name_without_a_book_of_ours_is_no_author(world: World, net: Net) -> None:
    """The footballer of the same name has no works; and the novelist is not taken for a book nexbooks does not hold."""
    assert identify(net, titles=("Zeta",)) is None
    assert identify(net, titles=()) is None
    assert identify(net, name="") is None


def test_two_authors_that_pass_are_no_author(world: World, net: Net) -> None:
    net.fake.works[NAMESAKE] = ["Q9100001"]
    assert identify(net) is None


def test_an_item_that_is_no_human_or_has_another_name_is_skipped(world: World, net: Net) -> None:
    claims = net.fake.entities[ADA]["claims"]
    claims["P31"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q4167410"
    assert identify(net) is None
    claims["P31"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q5"
    cache.forget()
    assert identify(net) is not None
    # Another name: the search finds the item (its label holds the words) but the name is not the author's.
    assert identify(net, name="Ada Feld") is None
    assert identify(net, name="Ida Feldkamp") is None
    # The last name alone is never enough.
    assert identify(net, name="Feldkamp") is None


def test_initials_fit_the_name_but_never_another_first_name(world: World, net: Net) -> None:
    net.fake.search["search"][1]["label"] = "A. Feldkamp"
    net.fake.search["search"][1]["match"]["text"] = "A. Feldkamp"
    net.fake.entities[ADA]["labels"] = {"en": {"language": "en", "value": "A. Feldkamp"}}
    net.fake.entities[ADA]["aliases"] = {}
    assert identify(net, name="Ada Feldkamp") is not None
    assert identify(net, name="Berta Feldkamp") is None


def test_an_author_with_more_books_than_the_search_lists_is_asked_for_title_by_title(world: World, net: Net) -> None:
    many = [f"Q95{n:05d}" for n in range(wikidata.LISTED)]
    for ident in many:
        net.fake.entities[ident] = {"type": "item", "id": ident, "labels": {"en": {"language": "en", "value": f"Other {ident}"}},
                                    "aliases": {}}
    net.fake.works[ADA] = many + ["Q9100001", "Q9100003"]
    # The fake lists them all for the plain question (it does not cut at fifty), so cut here, as the real search does.
    original = net.fake.handle_request

    def cut(request: httpx.Request) -> httpx.Response:
        answer = original(request)
        query = request.url.params.get("srsearch", "")
        if query == f"haswbstatement:P50={ADA}":
            body = answer.json()
            body["query"]["search"] = body["query"]["search"][: wikidata.LISTED]
            return httpx.Response(200, json=body, request=request)
        return answer

    net.fake.handle_request = cut  # type: ignore[method-assign]
    found = identify(net)
    assert found is not None and found.qid == ADA
    assert net.fake.count("wikidata", "api.php") > 0


def test_only_hosts_of_the_table_are_asked_and_a_name_is_only_ever_a_query_value(world: World, net: Net) -> None:
    identify(net, name="Ada Feldkamp", titles=("Alpha",))
    with SessionLocal() as db:
        wikidata.fetch_picture(db, "https://upload.wikimedia.org/wikipedia/commons/a/ab/x.jpg")
        assert wikidata.fetch_picture(db, "https://evil.example.com/x.jpg") is None
        assert wikidata.fetch_picture(db, "http://upload.wikimedia.org/x.jpg") is None
        assert wikidata.picture_of(db, "../../x.jpg") is None
    hosts = {host_of(request) for request in net.fake.log}
    assert hosts <= set(config.HOSTS["wikidata"])
    for request in net.fake.log:
        assert request.url.scheme == "https" and request.url.path in ("/w/api.php", "/wikipedia/commons/a/ab/x.jpg")


def test_a_name_with_quotes_and_a_title_with_a_backslash_do_no_harm(world: World, net: Net) -> None:
    assert identify(net, name='Ada "Feldkamp"', titles=('Al"pha\\',)) is None
    for request in net.fake.log:
        query = request.url.params.get("srsearch", "")
        assert query.count('"') in (0, 2) and "\\" not in query


def test_answers_are_kept_so_that_nothing_is_asked_twice(world: World, net: Net) -> None:
    identify(net)
    asked = len(net.fake.log)
    identify(net)
    assert len(net.fake.log) == asked


def test_a_source_that_is_off_is_not_asked_at_all(world: World, net: Net) -> None:
    net.settings(source_wikidata=False)
    with SessionLocal() as db, pytest.raises(http.SourceOff):
        wikidata.identify(db, "Ada Feldkamp", ["Alpha"], "en")
    net.settings(source_wikidata=True, sources_enabled=False)
    with SessionLocal() as db, pytest.raises(http.SourceOff):
        wikidata.identify(db, "Ada Feldkamp", ["Alpha"], "en")
    assert net.fake.log == []


# ---- The job ---------------------------------------------------------------------------------------------------------


def test_the_job_keeps_the_photo_with_its_author_and_licence_and_the_biography(world: World, net: Net) -> None:
    info = look_up("Ada Feldkamp")
    assert info is not None and info.wikidata_state == "sure" and info.wikidata_id == ADA
    assert info.photo and info.photo_source == "wikidata"
    assert (info.photo_author, info.photo_license) == ("Pia Example", "CC BY-SA 4.0")
    assert info.photo_page.startswith("https://commons.wikimedia.org/wiki/File:")
    assert info.bios["life"] == {"born": 1971, "died": None}
    assert info.bios["texts"]["de"]["text"] == "erfundene Schriftstellerin" and info.bios["texts"]["de"]["source"] == "wikidata"
    path = photos.path_of(info.photo)
    assert path is not None and path.read_bytes()[:4] == b"RIFF"  # drawn anew as WebP


def test_an_author_nothing_is_sure_of_has_no_photo_and_is_marked_as_looked_at(world: World, net: Net) -> None:
    net.fake.works[ADA] = []
    net.fake.ol_authors = {"docs": []}
    info = look_up("Ada Feldkamp")
    assert info is not None and info.photo == "" and info.wikidata_state == "none" and info.checked_at is not None


def test_a_photo_under_a_licence_that_may_not_be_shown_is_not_kept(world: World, net: Net) -> None:
    net.fake.commons["query"]["pages"]["99000001"]["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "CC BY-NC 2.0"
    net.settings(source_openlibrary=False)
    info = look_up("Ada Feldkamp")
    # The author is known (and the short text kept), but there is no picture to show.
    assert info is not None and info.photo == "" and info.wikidata_state == "sure" and info.bios["texts"]["de"]


def test_what_is_not_a_picture_or_is_too_large_is_not_kept(world: World, net: Net) -> None:
    net.settings(source_openlibrary=False)
    net.fake.picture = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>" + b" " * 100
    assert look_up("Ada Feldkamp").photo == ""  # type: ignore[union-attr]
    net.fake.picture = b"%PDF-1.4 " + b"0" * 200
    assert look_up("Ada Feldkamp").photo == ""  # type: ignore[union-attr]


def test_nobody_else_gives_a_photo_when_wikidata_has_none(world: World, net: Net) -> None:
    """Open Library and Hardcover name no author and no licence for a picture: they are not asked for one, also when
    Hardcover is connected and Wikidata has nothing."""
    from app.security import encrypt_secret

    net.fake.entities[ADA]["claims"].pop("P18")
    net.settings(source_hardcover=True,
                 hardcover_token_enc=encrypt_secret("token-for-the-test-only", config.TOKEN_CONTEXT))
    info = look_up("Ada Feldkamp")
    assert info is not None and info.photo == "" and info.photo_source == "" and info.wikidata_state == "sure"
    assert net.fake.count("openlibrary") == 0 and net.fake.count("hardcover") == 0
    assert net.fake.count("covers.openlibrary") == 0 and net.fake.count("assets.hardcover") == 0
    # Not a text of those sources either: the biography is Wikidata's.
    assert [item["source"] for item in info.bios["texts"].values()] == ["wikidata", "wikidata"]


def test_with_wikidata_off_nothing_is_asked_and_nothing_is_marked(world: World, net: Net) -> None:
    net.settings(source_wikidata=False)
    ident = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        assert people_info.enabled(db) is False
        assert people_info.enqueue_missing(db, [ident]) == 0
        people_info.look_up(db, ident)
        assert db.get(PersonInfo, ident) is None
    assert net.fake.log == []
def test_nothing_is_marked_when_no_source_could_be_asked(world: World, net: Net) -> None:
    net.settings(sources_enabled=False)
    ident = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        people_info.look_up(db, ident)
        assert db.get(PersonInfo, ident) is None
    assert net.fake.log == []


def test_a_source_that_is_busy_makes_the_job_try_again(world: World, net: Net) -> None:
    net.fake.status["www.wikidata.org"] = 503
    ident = person_id("Ada Feldkamp")
    with SessionLocal() as db, pytest.raises(jobs.Retry):
        people_info.look_up(db, ident)
    with SessionLocal() as db:
        assert db.get(PersonInfo, ident) is None


def test_the_switch_of_the_authors_photos_stops_every_look_up(world: World, net: Net) -> None:
    net.settings(author_info=False)
    with SessionLocal() as db:
        assert people_info.enqueue_missing(db, [person_id("Ada Feldkamp")]) == 0
        people_info.look_up(db, person_id("Ada Feldkamp"))
        assert db.scalar(select(func.count()).select_from(PersonInfo)) == 0
    assert net.fake.log == []


def test_an_author_looked_up_is_not_queued_again_until_the_recheck(world: World, net: Net) -> None:
    ident = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        assert people_info.enqueue_missing(db, [ident]) == 1
        assert people_info.enqueue_missing(db, [ident]) == 1  # still waiting: the same job, not a second
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "pinfo")) == 1
    run_jobs()
    with SessionLocal() as db:
        assert db.get(PersonInfo, ident).photo  # type: ignore[union-attr]
        assert people_info.enqueue_missing(db, [ident]) == 0
    net.days(181)
    with SessionLocal() as db:
        assert people_info.enqueue_missing(db, [ident]) == 1


def test_a_look_up_queues_at_most_forty_authors_at_a_visit(world: World, net: Net) -> None:
    with SessionLocal() as db:
        ids = []
        for n in range(60):
            row = Person(name=f"Person {n}", sort_name=f"Person, {n}", key=f"person {n}")
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
        assert people_info.enqueue_missing(db, ids) == people_info.QUEUE_PER_VISIT


# ---- The photos in the authors' folders --------------------------------------------------------------------------------


def put_photo(world: World, relative: str, color: tuple[int, int, int] = (60, 90, 140)) -> Path:
    path = world.family / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cover_image(color, (300, 400), "JPEG"))
    age(path)
    return path


def run_portraits(library_id: int) -> dict[str, int]:
    with SessionLocal() as db:
        from app.models import Library

        return portraits.run(db, db.get(Library, library_id))  # type: ignore[arg-type]


def folder_photos() -> dict[str, str]:
    with SessionLocal() as db:
        return {p.name: row.path for row, p in db.execute(
            select(PersonPhoto, Person).join(Person, Person.id == PersonPhoto.person_id))}


def test_a_photo_in_the_authors_folder_is_read(world: World, net: Net) -> None:
    put_photo(world, "Ada Feldkamp/author.jpg")
    assert run_portraits(world.family_id) == {"found": 1, "kept": 0, "removed": 0}
    assert folder_photos() == {"Ada Feldkamp": "Ada Feldkamp/author.jpg"}


def test_folder_jpg_counts_in_a_folder_without_books_of_its_own(world: World, net: Net) -> None:
    put_photo(world, "Ada Feldkamp/folder.png".replace(".png", ".jpg"))
    run_portraits(world.family_id)
    assert folder_photos() == {"Ada Feldkamp": "Ada Feldkamp/folder.jpg"}


def test_author_wins_over_folder_and_jpeg_over_png(world: World, net: Net) -> None:
    put_photo(world, "Ada Feldkamp/folder.jpg")
    put_photo(world, "Ada Feldkamp/author.jpeg")
    run_portraits(world.family_id)
    assert folder_photos() == {"Ada Feldkamp": "Ada Feldkamp/author.jpeg"}


def test_folder_jpg_in_a_folder_that_holds_books_is_their_cover_not_the_authors_photo(world: World, net: Net) -> None:
    from .library_helpers import book

    book(world.family, "Flat Author/flat.epub", title="Flat", authors=("Flat Author",), language="en")
    from app.services import libraries

    with SessionLocal() as db:
        libraries.start_scan(db, world.family_id)
    run_jobs()
    put_photo(world, "Flat Author/folder.jpg")
    run_portraits(world.family_id)
    assert folder_photos() == {}
    put_photo(world, "Flat Author/author.jpg")
    run_portraits(world.family_id)
    assert folder_photos() == {"Flat Author": "Flat Author/author.jpg"}


def test_a_folder_that_is_no_authors_folder_of_the_library_gives_no_photo(world: World, net: Net) -> None:
    put_photo(world, "Somebody Else/author.jpg")
    put_photo(world, "Ben Hollister/author.jpg")  # an author of the other library
    run_portraits(world.family_id)
    assert folder_photos() == {}


def test_a_photo_that_did_not_change_is_not_read_again_and_one_that_is_gone_goes(world: World, net: Net,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    path = put_photo(world, "Ada Feldkamp/author.jpg")
    run_portraits(world.family_id)
    reads: list[str] = []
    original = portraits._read
    monkeypatch.setattr(portraits, "_read", lambda library, relative: reads.append(relative) or original(library, relative))
    assert run_portraits(world.family_id) == {"found": 0, "kept": 1, "removed": 0} and reads == []
    path.write_bytes(cover_image((200, 20, 20), (320, 420), "JPEG"))
    run_portraits(world.family_id)
    assert reads == ["Ada Feldkamp/author.jpg"]
    path.unlink()
    assert run_portraits(world.family_id)["removed"] == 1 and folder_photos() == {}


def test_a_link_a_text_and_a_picture_too_large_are_not_photos(world: World, net: Net, tmp_path: Path) -> None:
    from .library_helpers import can_link_files, make_link

    folder = world.family / "Ada Feldkamp"
    (folder / "author.jpg").write_bytes(b"this is not a picture at all" * 10)
    run_portraits(world.family_id)
    assert folder_photos() == {}
    (folder / "author.jpg").unlink()
    if can_link_files():
        real = tmp_path / "secret.jpg"
        real.write_bytes(cover_image((1, 2, 3), (300, 400), "JPEG"))
        make_link(folder / "author.jpg", real)
        run_portraits(world.family_id)
        assert folder_photos() == {}
        (folder / "author.jpg").unlink()
    # A link to a folder that holds a photo is a folder that is not followed (the link is named as the author is, last name
    # first, so that it would be taken for the author's folder were it followed).
    outside = tmp_path / "outside"
    (outside).mkdir()
    (outside / "author.jpg").write_bytes(cover_image((1, 2, 3), (300, 400), "JPEG"))
    make_link(world.family / "Feldkamp, Ada", outside)
    run_portraits(world.family_id)
    assert folder_photos() == {}


def test_a_scan_queues_the_job_that_reads_the_photos(world: World, net: Net) -> None:
    from app.services import libraries

    put_photo(world, "Ada Feldkamp/author.jpg")
    with SessionLocal() as db:
        libraries.start_scan(db, world.family_id)
    run_jobs()
    assert folder_photos() == {"Ada Feldkamp": "Ada Feldkamp/author.jpg"}


# ---- The API ------------------------------------------------------------------------------------------------------------


def grid(client: TestClient, **params: object) -> dict[str, Any]:
    answer = client.get("/api/v1/authors", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_the_grid_holds_the_authors_of_the_books_the_caller_sees_and_counts_those_books_only(world: World, net: Net) -> None:
    net.settings(sources_enabled=False)
    with new_client(world.rita) as rita:
        found = grid(rita)
        assert [a["name"] for a in found["items"]] == ["Cy Dorn", "Ada Feldkamp"] or [a["name"] for a in found["items"]] == [
            "Ada Feldkamp", "Cy Dorn"]
        by_name = {a["name"]: a for a in found["items"]}
        assert set(by_name) == {"Ada Feldkamp", "Cy Dorn"}
        # Alpha (an e-book and a recording are one book) and Beta; the recording counts as one book with sound.
        assert by_name["Ada Feldkamp"]["works"] == 2 and by_name["Ada Feldkamp"]["audio"] == 1
        assert by_name["Cy Dorn"]["works"] == 1 and by_name["Cy Dorn"]["audio"] == 0
        assert found["total"] == 2
    with new_client(world.sam) as sam:
        assert [a["name"] for a in grid(sam)["items"]] == ["Ben Hollister"]
    with new_client(world.tom) as tom:
        assert grid(tom) == {"items": [], "total": 0, "page": 1, "per_page": 60, "genres": {}}
    with new_client(world.tester()) as operator:
        assert grid(operator)["total"] == 3


def test_an_author_in_two_libraries_counts_only_the_books_of_the_callers_libraries(world: World, net: Net) -> None:
    from .library_helpers import book

    net.settings(sources_enabled=False)
    book(world.club, "Ada Feldkamp/Gamma Club/g.epub", title="Club Gamma", authors=("Ada Feldkamp",), language="en")
    from app.services import libraries

    with SessionLocal() as db:
        libraries.start_scan(db, world.club_id)
    run_jobs()
    with new_client(world.sam) as sam:
        found = {a["name"]: a for a in grid(sam)["items"]}
        assert found["Ada Feldkamp"]["works"] == 1 and set(found) == {"Ada Feldkamp", "Ben Hollister"}
    with new_client(world.rita) as rita:
        assert {a["name"]: a for a in grid(rita)["items"]}["Ada Feldkamp"]["works"] == 2
    with new_client(world.tester()) as operator:
        assert {a["name"]: a for a in grid(operator)["items"]}["Ada Feldkamp"]["works"] == 3


def test_the_grid_is_sorted_filtered_and_paged(world: World, net: Net) -> None:
    net.settings(sources_enabled=False)
    with SessionLocal() as db:
        for edition in db.scalars(select(Edition)):
            edition.genre = "krimi" if edition.title in ("Alpha", "Beta") else "fantasy"
        db.commit()
    with new_client(world.tester()) as client:
        assert [a["name"] for a in grid(client, sort="name")["items"]] == ["Dorn, Cy", "Feldkamp, Ada", "Hollister, Ben"] \
            or [a["name"] for a in grid(client, sort="name")["items"]] == ["Cy Dorn", "Ada Feldkamp", "Ben Hollister"]
        assert next(a["name"] for a in grid(client, sort="count")["items"]) == "Ada Feldkamp"
        recent = grid(client, sort="recent")["items"]
        assert [a["added_at"] for a in recent] == sorted((a["added_at"] for a in recent), reverse=True)
        krimi = grid(client, genre="krimi")
        assert [a["name"] for a in krimi["items"]] == ["Ada Feldkamp"] and krimi["total"] == 1
        assert krimi["genres"] == {"krimi": 1, "fantasy": 2}
        assert grid(client, genre="none")["total"] == 0
        assert [a["name"] for a in grid(client, q="hollis")["items"]] == ["Ben Hollister"]
        assert [a["name"] for a in grid(client, q=" ADA   feld ")["items"]] == ["Ada Feldkamp"]
        first = grid(client, per_page=2)
        assert len(first["items"]) == 2 and first["total"] == 3
        assert len(grid(client, per_page=2, page=2)["items"]) == 1
        assert grid(client, per_page=2, page=9)["items"] == []
        assert client.get("/api/v1/authors", params={"sort": "luck"}).status_code == 422
        assert client.get("/api/v1/authors", params={"per_page": 201}).status_code == 422
        assert client.get("/api/v1/authors", params={"genre": "poetry"}).status_code == 422


def test_narrators_are_not_in_the_grid_of_authors(world: World, net: Net) -> None:
    net.settings(sources_enabled=False)
    with new_client(world.tester()) as client:
        assert "Rune Falkner" not in [a["name"] for a in grid(client)["items"]]


def test_the_photo_is_served_to_those_who_see_the_author_and_to_nobody_else(world: World, net: Net) -> None:
    look_up("Ada Feldkamp")
    ada = person_id("Ada Feldkamp")
    ben = person_id("Ben Hollister")
    with new_client(world.rita) as rita:
        card = {a["name"]: a for a in grid(rita)["items"]}["Ada Feldkamp"]
        assert card["photo_url"] and card["photo_url"].startswith(f"/api/v1/people/{ada}/photo?v=")
        answer = rita.get(card["photo_url"])
        assert answer.status_code == 200 and answer.headers["content-type"] == "image/webp"
        assert "immutable" in answer.headers["cache-control"] and answer.content[:4] == b"RIFF"
        again = rita.get(f"/api/v1/people/{ada}/photo", headers={"If-None-Match": answer.headers["etag"]})
        assert again.status_code == 304
        assert "immutable" not in rita.get(f"/api/v1/people/{ada}/photo?v=wrong").headers["cache-control"]
        # Not an author the caller sees, not one with a photo, not one that exists: the same answer.
        assert rita.get(f"/api/v1/people/{ben}/photo").status_code == 404
        assert rita.get("/api/v1/people/999999/photo").status_code == 404
    with new_client(world.sam) as sam:
        # sam is not in the library of Ada's books: no photo, no author.
        assert sam.get(f"/api/v1/people/{ada}/photo").status_code == 404
        assert sam.get(f"/api/v1/people/{ada}").status_code == 404
    with new_client(world.tom) as tom:
        assert tom.get(f"/api/v1/people/{ada}/photo").status_code == 404


def test_the_photo_of_a_folder_is_shown_to_members_of_that_library_only_and_comes_first(world: World, net: Net) -> None:
    look_up("Ada Feldkamp")
    put_photo(world, "Ada Feldkamp/author.jpg", (200, 30, 30))
    run_portraits(world.family_id)
    ada = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        folder = db.scalar(select(PersonPhoto.photo))
        source = db.get(PersonInfo, ada).photo  # type: ignore[union-attr]
        assert folder and source and folder != source
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/people/{ada}/photo").headers["etag"].strip('"') == folder[:20]
        page = rita.get(f"/api/v1/people/{ada}").json()
        # The library's own photo needs no credit line.
        assert page["photo_url"].endswith(folder[:12]) and page["credit"] is None
    # A member of another library that holds a book of the author is not shown the folder of the family library.
    from .library_helpers import book

    book(world.club, "Ada Feldkamp/Club Ada/ca.epub", title="Club Ada", authors=("Ada Feldkamp",), language="en")
    from app.services import libraries

    with SessionLocal() as db:
        libraries.start_scan(db, world.club_id)
    run_jobs()
    with new_client(world.sam) as sam:
        answer = sam.get(f"/api/v1/people/{ada}/photo")
        assert answer.status_code == 200 and answer.headers["etag"].strip('"') == source[:20]
        assert sam.get(f"/api/v1/people/{ada}").json()["credit"]["license"] == "CC BY-SA 4.0"


def test_the_page_of_an_author_has_the_photo_the_credit_and_the_biography_in_the_language_asked(world: World,
                                                                                                    net: Net) -> None:
    look_up("Ada Feldkamp")
    ada = person_id("Ada Feldkamp")
    with new_client(world.rita) as rita:
        german = rita.get(f"/api/v1/people/{ada}", params={"language": "de"}).json()
        assert german["photo_url"] and german["credit"] == {
            "source": "wikidata", "author": "Pia Example", "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
            "page_url": "https://commons.wikimedia.org/wiki/File:Ada_Feldkamp_reading_2019.jpg"}
        assert german["bio"]["texts"][0] == {"text": "erfundene Schriftstellerin", "language": "de", "source": "wikidata",
                                             "url": f"https://www.wikidata.org/wiki/{ADA}"}
        assert (german["bio"]["born"], german["bio"]["died"]) == (1971, None)
        english = rita.get(f"/api/v1/people/{ada}", params={"language": "en-GB"}).json()
        assert english["bio"]["texts"][0]["text"] == "Invented novelist (born 1971)"
        # A language the source has no text in falls back to English.
        assert rita.get(f"/api/v1/people/{ada}", params={"language": "sw"}).json()["bio"]["texts"][0]["language"] == "en"
        assert rita.get(f"/api/v1/people/{ada}", params={"language": "x" * 30}).status_code == 422
        assert len(german["wrote"]) == 2


def test_an_author_without_a_look_up_has_no_photo_no_credit_and_no_biography(world: World, net: Net) -> None:
    net.settings(sources_enabled=False)
    ada = person_id("Ada Feldkamp")
    with new_client(world.rita) as rita:
        page = rita.get(f"/api/v1/people/{ada}").json()
        assert page["photo_url"] is None and page["credit"] is None and page["bio"] is None


def test_reading_the_grid_queues_the_authors_that_have_no_photo_yet(world: World, net: Net) -> None:
    with new_client(world.rita) as rita:
        grid(rita)
        grid(rita)
    with SessionLocal() as db:
        queued = sorted(db.scalars(select(Job.ref).where(Job.kind == "pinfo")))
        assert queued == sorted([person_id("Ada Feldkamp"), person_id("Cy Dorn")])
    run_jobs()
    with new_client(world.rita) as rita:
        found = {a["name"]: a for a in grid(rita)["items"]}
        assert found["Ada Feldkamp"]["photo_url"] and found["Cy Dorn"]["photo_url"] is None


def test_a_narrator_has_a_page_but_is_not_looked_up(world: World, net: Net) -> None:
    with new_client(world.rita) as rita:
        narrator = person_id("Rune Falkner")
        page = rita.get(f"/api/v1/people/{narrator}").json()
        assert page["narrated"] and page["wrote"] == [] and page["photo_url"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "pinfo", Job.ref == narrator)) == 0


def test_a_key_reads_the_grid_and_the_photo(world: World, net: Net, client: TestClient) -> None:
    look_up("Ada Feldkamp")
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    from app.services import apikeys

    apikeys.forget()
    head = {"Authorization": f"Bearer {key}"}
    ada = person_id("Ada Feldkamp")
    with new_client() as program:
        assert program.get("/api/v1/authors", headers=head).json()["total"] == 3
        assert program.get(f"/api/v1/people/{ada}/photo", headers=head).status_code == 200


def test_the_credits_are_those_of_the_picture_that_was_kept(world: World, net: Net) -> None:
    info = look_up("Ada Feldkamp")
    assert info is not None
    digest = hashlib.sha256(photos.path_of(info.photo).read_bytes()).hexdigest()[:20] + ".webp"  # type: ignore[union-attr]
    assert info.photo == digest
    assert authors_service.credit_of is not None


def test_a_photo_nothing_refers_to_any_more_is_swept(world: World, net: Net) -> None:
    info = look_up("Ada Feldkamp")
    assert info is not None
    stray = photos.folder() / ("0" * 20 + ".webp")
    stray.write_bytes(b"x")
    with SessionLocal() as db:
        people_info.sweep(db)
    assert not stray.exists() and photos.path_of(info.photo) is not None


def test_deleting_a_person_takes_the_info_and_the_photos_rows_with_it(world: World, net: Net) -> None:
    look_up("Ada Feldkamp")
    put_photo(world, "Ada Feldkamp/author.jpg")
    run_portraits(world.family_id)
    ada = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        db.query(Credit).filter_by(person_id=ada).delete()
        db.delete(db.get(Person, ada))
        db.commit()
        assert db.scalar(select(func.count()).select_from(PersonInfo)) == 0
        assert db.scalar(select(func.count()).select_from(PersonPhoto)) == 0


def test_removing_a_library_takes_its_folder_photos_with_it(world: World, net: Net, client: TestClient) -> None:
    put_photo(world, "Ada Feldkamp/author.jpg")
    run_portraits(world.family_id)
    assert client.delete(f"/api/v1/libraries/{world.family_id}").status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PersonPhoto)) == 0
        assert db.scalar(select(func.count()).select_from(LibraryMember).where(
            LibraryMember.library_id == world.family_id)) == 0


def test_the_sources_page_lists_wikidata_with_its_switch(world: World, net: Net, client: TestClient) -> None:
    state = client.get("/api/v1/sources").json()
    assert "wikidata" in [s["id"] for s in state["sources"]]
    off = client.put("/api/v1/sources", json={"sources": {"wikidata": False}}).json()
    assert {s["id"]: s["enabled"] for s in off["sources"]}["wikidata"] is False
    assert client.put("/api/v1/sources", json={"sources": {"elsewhere": True}}).status_code == 422


def test_the_authors_of_the_unauthenticated_are_none(client: TestClient) -> None:
    from fastapi.testclient import TestClient as _Client

    from app.main import app

    with _Client(app, base_url="http://testserver") as anonymous:
        assert anonymous.get("/api/v1/authors").status_code in (401, 403)
        assert anonymous.get("/api/v1/people/1/photo").status_code in (401, 403)


def test_clock_is_not_needed_for_the_credit(world: World, net: Net) -> None:
    assert clock.now() is not None and age is not None and AUTHOR == "author"


# ---- The numbers of the authors are kept between two requests --------------------------------------------------------


def test_the_cards_of_the_authors_are_worked_out_once_and_again_when_something_changes(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = {"n": 0}
    real = authors_service._rows

    def counted(db: Any, uid: int) -> Any:
        reads["n"] += 1
        return real(db, uid)

    monkeypatch.setattr(authors_service, "_rows", counted)
    rita = world.rita.id
    with SessionLocal() as db:
        first = authors_service.cards(db, rita)
        first_numbers = {c.person.name: (c.works, c.audio, c.genre) for c in first.values()}
        # What the caller does with a card does not reach the next caller.
        next(iter(first.values())).genres["roman"] += 5
        second = authors_service.cards(db, rita)
        assert reads["n"] == 1
        assert {c.person.name: (c.works, c.audio, c.genre) for c in second.values()} == first_numbers
    # A change to what the numbers are made of makes the next look work them out again.
    with SessionLocal() as db:
        db.execute(Edition.__table__.update().values(genre="krimi"))
        db.commit()
    with SessionLocal() as db:
        third = authors_service.cards(db, rita)
        assert reads["n"] == 2 and {c.genre for c in third.values()} == {"krimi"}
    # A membership that is withdrawn is never answered from what was kept.
    with SessionLocal() as db:
        db.query(LibraryMember).filter(LibraryMember.user_id == rita).delete()
        db.commit()
    with SessionLocal() as db:
        assert authors_service.cards(db, rita) == {}


def test_the_numbers_of_one_person_are_not_the_numbers_of_another(world: World) -> None:
    with SessionLocal() as db:
        rita = {c.person.name for c in authors_service.cards(db, world.rita.id).values()}
        sam = {c.person.name for c in authors_service.cards(db, world.sam.id).values()}
        assert rita and sam and rita.isdisjoint(sam)
        assert {c.person.name for c in authors_service.cards(db, world.rita.id).values()} == rita
        assert authors_service.cards(db, world.tom.id) == {}


# ---- Photos of earlier versions from sources without an author and a licence --------------------------------------------


def keep_photo_of(name: str, source: str, color: tuple[int, int, int]) -> str:
    """A photo for ``name`` as an earlier version kept it (a file of the photos folder and a row that names the source)."""
    from app.services.readers.images import reduce_portrait

    ident = person_id(name)
    made = reduce_portrait(cover_image(color, (300, 400), "JPEG"))
    assert made is not None
    stored = photos.save(made)
    with SessionLocal() as db:
        info = db.get(PersonInfo, ident) or PersonInfo(person_id=ident)
        info.photo, info.photo_source, info.checked_at = stored, source, clock.now()
        info.photo_page = "https://example.com/author"
        if source == "openlibrary":
            info.bios = {"life": {}, "texts": {"en": {"text": "A text", "source": "wikidata", "url": ""},
                                               "en+": {"text": "A long text", "source": "openlibrary", "url": ""}}}
        db.add(info)
        db.commit()
    return stored


def test_a_photo_from_a_source_without_an_author_and_a_licence_is_not_shown(world: World, net: Net) -> None:
    stored = keep_photo_of("Ada Feldkamp", "openlibrary", (10, 80, 120))
    ada = person_id("Ada Feldkamp")
    assert photos.path_of(stored) is not None
    # A client that is not entered does not start a server, so nothing has cleaned up yet: the page itself holds back.
    rita = new_client(world.rita)
    assert rita.get(f"/api/v1/people/{ada}/photo").status_code == 404
    page = rita.get(f"/api/v1/people/{ada}").json()
    assert page["photo_url"] is None and page["credit"] is None


def test_the_photos_of_earlier_versions_from_those_sources_are_removed_and_the_authors_looked_up_again(
        world: World, net: Net) -> None:
    gone_ol = keep_photo_of("Ada Feldkamp", "openlibrary", (10, 80, 120))
    gone_hc = keep_photo_of("Cy Dorn", "hardcover", (120, 80, 10))
    kept = keep_photo_of("Rune Falkner", "wikidata", (30, 30, 130))
    with SessionLocal() as db:
        assert people_info.drop_unlicensed(db) == 2
    assert photos.path_of(gone_ol) is None and photos.path_of(gone_hc) is None
    assert photos.path_of(kept) is not None
    with SessionLocal() as db:
        ada = db.get(PersonInfo, person_id("Ada Feldkamp"))
        assert ada is not None and ada.photo == "" and ada.photo_source == "" and ada.photo_page == ""
        # Looked up again at the next visit of a page, and the text of the source without a licence is gone.
        assert ada.checked_at is None and list(ada.bios["texts"]) == ["en"]
        rune = db.get(PersonInfo, person_id("Rune Falkner"))
        assert rune is not None and rune.photo == kept and rune.photo_source == "wikidata"
        assert people_info.drop_unlicensed(db) == 0


def test_the_photos_are_removed_when_the_server_starts(world: World, net: Net) -> None:
    stored = keep_photo_of("Ada Feldkamp", "openlibrary", (10, 80, 120))
    assert photos.path_of(stored) is not None
    with new_client():  # the start of a server
        pass
    assert photos.path_of(stored) is None
    with SessionLocal() as db:
        info = db.get(PersonInfo, person_id("Ada Feldkamp"))
        assert info is not None and info.photo == ""
