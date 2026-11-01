"""Libraries over the API: where they may lie, who makes them, who sees them, who may start a scan."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, Job, Library, LibraryMember, LibraryProblem

from .conftest import BOOKS_DIR, DATA_DIR, make_account, new_client
from .library_helpers import age, book, books_folder, make_link, run_jobs


def create(client: TestClient, folder: Path | str, name: str = "Books", **extra: object) -> dict:
    answer = client.post("/api/v1/libraries", json={"name": name, "path": str(folder), **extra})
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def error_code(answer) -> str:  # type: ignore[no-untyped-def]
    return answer.json()["detail"]["code"]  # type: ignore[no-any-return]


@pytest.fixture
def outside() -> Iterator[Path]:
    """A folder that is not in the folder for libraries."""
    folder = Path(tempfile.mkdtemp(prefix="nexbooks-outside-"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


# ---- where a library may lie ---------------------------------------------------------------------------------------


def test_a_library_lies_in_a_root_and_nowhere_else(client: TestClient, operator: Account, outside: Path) -> None:
    folder = books_folder("fine")
    made = create(client, folder)
    assert made["path"] == str(folder.resolve()) and made["kind"] == "both" and made["my_right"] == "manage"
    for path, code in ((outside, "path_outside_roots"), ("relative/folder", "path_relative"),
                       (folder / "nothing-here", "path_missing"), ("", "invalid_input")):
        answer = client.post("/api/v1/libraries", json={"name": "X" + code, "path": str(path)})
        assert answer.status_code == 422, (path, answer.text)
        assert error_code(answer) in (code, "invalid_input"), (path, answer.json())
    file_path = Path(BOOKS_DIR) / "a-file.txt"
    file_path.write_text("x", encoding="utf-8")
    assert error_code(client.post("/api/v1/libraries", json={"name": "F", "path": str(file_path)})) == "path_not_folder"


def test_dots_and_links_cannot_lead_out_of_the_roots(client: TestClient, operator: Account, outside: Path) -> None:
    books_folder("inner")
    sneaky = Path(BOOKS_DIR) / "inner" / ".." / ".." / outside.name
    assert error_code(client.post("/api/v1/libraries", json={"name": "Dots", "path": str(sneaky)})) == "path_outside_roots"
    # A link inside a root that leads out of it leads out of the root.
    make_link(Path(BOOKS_DIR) / "way-out", outside)
    answer = client.post("/api/v1/libraries", json={"name": "Link", "path": str(Path(BOOKS_DIR) / "way-out")})
    assert answer.status_code == 422 and error_code(answer) == "path_outside_roots"
    # A path to something below the link: the same.
    (outside / "below").mkdir()
    answer = client.post("/api/v1/libraries", json={"name": "Below", "path": str(Path(BOOKS_DIR) / "way-out" / "below")})
    assert error_code(answer) == "path_outside_roots"


def test_the_data_folder_cannot_be_a_library_nor_hold_one(client: TestClient, operator: Account,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when the roots are set so wide that the data folder is inside them."""
    monkeypatch.setattr(get_settings(), "library_roots", str(Path(DATA_DIR).parent))
    for path in (DATA_DIR, Path(DATA_DIR) / "logs", Path(DATA_DIR).parent):
        answer = client.post("/api/v1/libraries", json={"name": "Data", "path": str(path)})
        assert answer.status_code == 422 and error_code(answer) == "path_is_data", path
    assert client.get("/api/v1/libraries/admin").json() == []


def test_libraries_may_not_overlap(client: TestClient, operator: Account) -> None:
    outer = books_folder("outer")
    (outer / "inner").mkdir()
    create(client, outer / "inner", "Inner")
    assert error_code(client.post("/api/v1/libraries", json={"name": "Outer", "path": str(outer)})) == "path_overlap"
    assert error_code(client.post("/api/v1/libraries", json={"name": "Same", "path": str(outer / "inner")})) == "path_overlap"
    (outer / "sibling").mkdir()
    create(client, outer / "sibling", "Sibling")


def test_without_roots_there_are_no_libraries(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("x")
    monkeypatch.setattr(get_settings(), "library_roots", "")
    assert error_code(client.post("/api/v1/libraries", json={"name": "X", "path": str(folder)})) == "no_roots"


def test_the_picker_and_the_check_help_the_operator(client: TestClient, operator: Account, outside: Path) -> None:
    books_folder("alpha")
    books_folder("beta")
    shown = client.get("/api/v1/libraries/roots").json()["roots"]
    assert [Path(entry["root"]).resolve() for entry in shown] == [Path(BOOKS_DIR).resolve()]
    assert sorted(Path(f).name for f in shown[0]["folders"]) == ["alpha", "beta"]
    ok = client.post("/api/v1/libraries/check", json={"path": shown[0]["folders"][0]})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    bad = client.post("/api/v1/libraries/check", json={"path": str(outside)})
    assert bad.status_code == 422 and error_code(bad) == "path_outside_roots" and bad.json()["detail"]["field"] == "path"


# ---- making, changing, removing ------------------------------------------------------------------------------------


def test_making_a_library_queues_its_first_scan_and_makes_the_maker_a_member(client: TestClient, operator: Account) -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    made = create(client, folder, "Shelf", kind="ebook")
    assert made["state"] == "queued" and made["kind"] == "ebook" and made["scan_enabled"] is True
    assert [m["name"] for m in made["members"]] == ["tester"] and made["members"][0]["right"] == "manage"
    run_jobs()
    again = client.get("/api/v1/libraries/admin").json()[0]
    assert again["state"] == "idle" and again["counts"]["ebooks"] == 1 and again["scanned_at"] is not None
    assert again["report"]["added"] == 1 and again["progress"] == {"done": 0, "total": 0}


def test_names_are_unique_without_regard_to_case_and_kinds_are_checked(client: TestClient, operator: Account) -> None:
    create(client, books_folder("one"), "Shelf")
    two = books_folder("two")
    assert error_code(client.post("/api/v1/libraries", json={"name": "shelf", "path": str(two)})) == "name_taken"
    assert client.post("/api/v1/libraries", json={"name": "T", "path": str(two), "kind": "paper"}).status_code == 422
    assert client.post("/api/v1/libraries", json={"name": "x" * 81, "path": str(two)}).status_code == 422
    assert client.post("/api/v1/libraries", json={"name": "  ", "path": str(two)}).status_code == 422


def test_a_library_is_renamed_and_its_kind_changed_but_not_moved(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Shelf")
    changed = client.patch(f"/api/v1/libraries/{made['id']}", json={"name": "Renamed", "kind": "audio",
                                                                  "scan_enabled": False}).json()
    assert (changed["name"], changed["kind"], changed["scan_enabled"], changed["path"]) == (
        "Renamed", "audio", False, made["path"])
    assert client.patch(f"/api/v1/libraries/{made['id']}", json={"path": "/elsewhere"}).status_code == 200
    assert client.get("/api/v1/libraries/admin").json()[0]["path"] == made["path"]
    assert client.patch("/api/v1/libraries/999", json={"name": "X"}).status_code == 404


def test_removing_a_library_leaves_the_files(client: TestClient, operator: Account) -> None:
    folder = books_folder()
    path = book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    made = create(client, folder)
    run_jobs()
    assert client.get("/api/v1/works").json()["total"] == 1
    assert client.delete(f"/api/v1/libraries/{made['id']}").status_code == 204
    assert path.exists()
    assert client.get("/api/v1/libraries/admin").json() == []
    assert client.get("/api/v1/works").json()["total"] == 0
    assert client.delete(f"/api/v1/libraries/{made['id']}").status_code == 404


def test_only_the_operator_in_a_browser_makes_changes_and_sees_paths(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Shelf")
    rita = make_account("rita")
    with new_client(rita) as member:
        for method, url, body in (("POST", "/api/v1/libraries", {"name": "X", "path": made["path"]}),
                                  ("PATCH", f"/api/v1/libraries/{made['id']}", {"name": "X"}),
                                  ("DELETE", f"/api/v1/libraries/{made['id']}", None),
                                  ("PUT", f"/api/v1/libraries/{made['id']}/members", {"members": []}),
                                  ("PATCH", f"/api/v1/libraries/{made['id']}/members",
                                   {"changes": [{"user_id": rita.id, "was": "read", "right": "manage"}]}),
                                  ("GET", "/api/v1/libraries/admin", None), ("GET", "/api/v1/libraries/roots", None),
                                  ("POST", "/api/v1/libraries/check", {"path": made["path"]})):
            answer = member.request(method, url, json=body)
            assert (answer.status_code, error_code(answer)) == (403, "operator_only"), (method, url)
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    with TestClient(client.app, base_url="http://testserver") as device:
        answer = device.get("/api/v1/libraries/admin", headers={"Authorization": f"Bearer {token}"})
        assert (answer.status_code, error_code(answer)) == (403, "browser_only")
    assert client.get("/api/v1/libraries/admin").json()[0]["path"] == made["path"]


def test_a_device_and_a_key_may_not_make_change_or_manage_anything(client: TestClient, operator: Account) -> None:
    """The routes of the operator and of the managers are for a person in a browser: a device token and a key, even
    those of the operator's own account, answer 403 on every one of them."""
    made = create(client, books_folder(), "Shelf")
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    library = made["id"]
    routes = (("POST", "/api/v1/libraries", {"name": "X", "path": made["path"]}),
              ("PATCH", f"/api/v1/libraries/{library}", {"name": "X"}),
              ("DELETE", f"/api/v1/libraries/{library}", None),
              ("PUT", f"/api/v1/libraries/{library}/members", {"members": []}),
              ("PATCH", f"/api/v1/libraries/{library}/members",
               {"changes": [{"user_id": 1, "was": "read", "right": "manage"}]}),
              ("GET", "/api/v1/libraries/admin", None), ("GET", "/api/v1/libraries/roots", None),
              ("POST", "/api/v1/libraries/check", {"path": made["path"]}),
              ("POST", f"/api/v1/libraries/{library}/scan", None),
              ("GET", f"/api/v1/libraries/{library}/problems", None))
    with TestClient(client.app, base_url="http://testserver") as program:
        for secret in (token, key):
            for method, url, body in routes:
                answer = program.request(method, url, json=body, headers={"Authorization": f"Bearer {secret}"})
                assert answer.status_code == 403, (secret[:4], method, url, answer.status_code)
    # Nothing was done: the library is there as it was, no scan was queued by them.
    with SessionLocal() as db:
        assert db.scalars(select(Library)).one().name == "Shelf"


# ---- who sees what -------------------------------------------------------------------------------------------------


def test_a_library_is_seen_by_its_members_and_by_nobody_else(client: TestClient, operator: Account) -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    made = create(client, folder, "Family")
    run_jobs()
    rita, sam = make_account("rita"), make_account("sam")
    put = client.put(f"/api/v1/libraries/{made['id']}/members", json={"members": [
        {"user_id": operator.id, "right": "manage"}, {"user_id": rita.id, "right": "read"}]})
    assert put.status_code == 200
    assert {(m["name"], m["right"]) for m in put.json()["members"]} == {("tester", "manage"), ("rita", "read")}
    with new_client(rita) as ritas, new_client(sam) as sams:
        listed = ritas.get("/api/v1/libraries").json()
        assert [(item["name"], item["my_right"], item["counts"]["ebooks"]) for item in listed] == [("Family", "read", 1)]
        assert "path" not in listed[0] and "members" not in listed[0]
        assert ritas.get(f"/api/v1/libraries/{made['id']}").status_code == 200
        assert sams.get("/api/v1/libraries").json() == []
        # Not there for sam is exactly what not there looks like: the same answer as for a library that does not exist.
        mine, nothing = sams.get(f"/api/v1/libraries/{made['id']}"), sams.get("/api/v1/libraries/987")
        assert (mine.status_code, mine.json()) == (nothing.status_code, nothing.json()) == (404, nothing.json())


def test_the_operator_manages_every_library_but_sees_the_books_of_those_they_are_in(client: TestClient,
                                                                                 operator: Account) -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    made = create(client, folder, "Family")
    run_jobs()
    assert client.put(f"/api/v1/libraries/{made['id']}/members", json={"members": []}).status_code == 200
    assert client.get("/api/v1/libraries").json() == []
    assert client.get("/api/v1/works").json()["total"] == 0
    assert client.get("/api/v1/libraries/admin").json()[0]["counts"]["ebooks"] == 1  # numbers for managing, no titles
    assert client.post(f"/api/v1/libraries/{made['id']}/scan").status_code == 202


def test_members_are_set_all_at_once_and_a_refusal_changes_nothing(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    rita = make_account("rita")
    url = f"/api/v1/libraries/{made['id']}/members"
    assert client.put(url, json={"members": [{"user_id": rita.id, "right": "read"}]}).status_code == 200
    for body in ({"members": [{"user_id": rita.id, "right": "read"}, {"user_id": 9999, "right": "read"}]},
                 {"members": [{"user_id": rita.id, "right": "read"}, {"user_id": rita.id, "right": "manage"}]},
                 {"members": [{"user_id": rita.id, "right": "owner"}]}, {"members": [{"user_id": 0, "right": "read"}]}):
        assert client.put(url, json=body).status_code == 422, body
    assert [(m["name"], m["right"]) for m in client.get("/api/v1/libraries/admin").json()[0]["members"]] == [
        ("rita", "read")]
    assert client.put("/api/v1/libraries/999/members", json={"members": []}).status_code == 404


def test_only_the_changes_are_made_and_a_person_somebody_else_changed_makes_it_a_409(client: TestClient,
                                                                                      operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    rita, sam, tom = make_account("rita"), make_account("sam"), make_account("tom")
    url = f"/api/v1/libraries/{made['id']}/members"

    def members() -> set[tuple[str, str]]:
        return {(m["name"], m["right"]) for m in client.get("/api/v1/libraries/admin").json()[0]["members"]}

    client.put(url, json={"members": [{"user_id": rita.id, "right": "read"}, {"user_id": sam.id, "right": "read"}]})
    # Rita up, Tom in; Sam is not named and stays as he is.
    done = client.patch(url, json={"changes": [{"user_id": rita.id, "was": "read", "right": "acquire"},
                                               {"user_id": tom.id, "was": None, "right": "read"}]})
    assert done.status_code == 200, done.text
    assert members() == {("rita", "acquire"), ("sam", "read"), ("tom", "read")}
    # Somebody changed Sam meanwhile: the caller still thinks he reads. Nothing of the call happens, not even the
    # change of Rita that came first.
    client.put(url, json={"members": [{"user_id": rita.id, "right": "acquire"}, {"user_id": sam.id, "right": "manage"},
                                      {"user_id": tom.id, "right": "read"}]})
    stale = client.patch(url, json={"changes": [{"user_id": rita.id, "was": "acquire", "right": "read"},
                                                {"user_id": sam.id, "was": "read", "right": None}]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "members_changed", stale.text
    assert members() == {("rita", "acquire"), ("sam", "manage"), ("tom", "read")}
    # The same for a new member who is there already, and for one who left meanwhile.
    for change in ({"user_id": tom.id, "was": None, "right": "manage"}, {"user_id": rita.id, "was": "read", "right": None}):
        assert client.patch(url, json={"changes": [change]}).status_code == 409, change
    # A person who has another right than the caller saw is not moved to a third one: Tom reads, the caller thought he manages.
    wrong = client.patch(url, json={"changes": [{"user_id": tom.id, "was": "manage", "right": "acquire"}]})
    assert wrong.status_code == 409 and members() == {("rita", "acquire"), ("sam", "manage"), ("tom", "read")}
    both = client.patch(url, json={"changes": [{"user_id": rita.id, "was": "acquire", "right": "read"},
                                               {"user_id": tom.id, "was": None, "right": "manage"}]})
    assert both.status_code == 409 and members() == {("rita", "acquire"), ("sam", "manage"), ("tom", "read")}
    client.patch(url, json={"changes": [{"user_id": sam.id, "was": "manage", "right": None}]})
    gone = client.patch(url, json={"changes": [{"user_id": sam.id, "was": "manage", "right": "read"}]})
    assert gone.status_code == 409
    assert members() == {("rita", "acquire"), ("tom", "read")}


def test_a_change_of_rights_that_is_no_change_or_not_valid_is_refused(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    rita = make_account("rita")
    url = f"/api/v1/libraries/{made['id']}/members"
    for change in ({"user_id": rita.id, "was": "read", "right": "read"}, {"user_id": rita.id, "was": None, "right": None},
                   {"user_id": rita.id, "was": None, "right": "owner"}, {"user_id": 9999, "was": None, "right": "read"}):
        assert client.patch(url, json={"changes": [change]}).status_code == 422, change
    twice = [{"user_id": rita.id, "was": None, "right": "read"}, {"user_id": rita.id, "was": None, "right": "manage"}]
    assert client.patch(url, json={"changes": twice}).status_code == 422
    assert client.patch(url, json={"changes": []}).status_code == 422
    assert client.patch("/api/v1/libraries/999/members",
                        json={"changes": [{"user_id": rita.id, "was": None, "right": "read"}]}).status_code == 404
    assert [(m["name"], m["right"]) for m in client.get("/api/v1/libraries/admin").json()[0]["members"]] == [
        ("tester", "manage")], "nothing of it was done"


def test_a_deleted_account_leaves_its_libraries(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    rita = make_account("rita")
    client.put(f"/api/v1/libraries/{made['id']}/members", json={"members": [{"user_id": rita.id, "right": "read"}]})
    with SessionLocal() as db:
        db.delete(db.get(Account, rita.id))
        db.commit()
        assert db.scalars(select(LibraryMember).where(LibraryMember.user_id == rita.id)).all() == []


# ---- scanning and problems -----------------------------------------------------------------------------------------


def test_who_may_start_a_scan(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    run_jobs()
    rita, sam, tom = make_account("rita"), make_account("sam"), make_account("tom")
    client.put(f"/api/v1/libraries/{made['id']}/members", json={"members": [
        {"user_id": rita.id, "right": "read"}, {"user_id": sam.id, "right": "manage"}]})
    url = f"/api/v1/libraries/{made['id']}/scan"
    with new_client(rita) as ritas, new_client(sam) as sams, new_client(tom) as toms:
        assert (ritas.post(url).status_code, error_code(ritas.post(url))) == (403, "forbidden")
        assert toms.post(url).status_code == 404
        assert sams.post(url).status_code == 202
        # The click on the button again, while the scan waits, is the same scan.
        assert sams.post(url).json() == {"state": "queued"}
    with SessionLocal() as db:
        assert len(db.scalars(select(Job).where(Job.kind == "scan", Job.state == "queued")).all()) == 1
    assert client.post(url).status_code == 202
    assert client.post("/api/v1/libraries/999/scan").status_code == 404
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    with TestClient(client.app, base_url="http://testserver") as device:
        assert device.post(url, headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_the_list_of_problems_is_for_the_operator_and_the_managers(client: TestClient, operator: Account) -> None:
    folder = books_folder()
    for number in range(3):
        (folder / f"broken{number}.epub").write_bytes(b"not a book" * 50)
        age(folder / f"broken{number}.epub")
    made = create(client, folder, "Family")
    run_jobs()
    rita, sam = make_account("rita"), make_account("sam")
    client.put(f"/api/v1/libraries/{made['id']}/members", json={"members": [
        {"user_id": rita.id, "right": "read"}, {"user_id": sam.id, "right": "manage"}]})
    url = f"/api/v1/libraries/{made['id']}/problems"
    answer = client.get(url).json()
    assert answer["total"] == 3 and [i["path"] for i in answer["items"]] == ["broken0.epub", "broken1.epub", "broken2.epub"]
    assert answer["items"][0]["reason"] == "unreadable" and answer["items"][0]["message"]
    assert client.get(url, params={"limit": 1, "offset": 2}).json()["items"][0]["path"] == "broken2.epub"
    with new_client(sam) as sams, new_client(rita) as ritas:
        assert sams.get(url).json()["total"] == 3
        assert (ritas.get(url).status_code, error_code(ritas.get(url))) == (403, "forbidden")
        assert sams.get("/api/v1/libraries").json()[0]["counts"]["problems"] == 3
        assert ritas.get("/api/v1/libraries").json()[0]["counts"]["problems"] == 0  # not for readers
    with new_client(make_account("tom")) as toms:
        assert toms.get(url).status_code == 404


def test_the_problem_list_names_a_reason_for_every_code(client: TestClient, operator: Account) -> None:
    from app.routers.libraries import REASON_TEXT
    from app.services.readers.limits import REASONS

    assert set(REASON_TEXT) == set(REASONS)
    made = create(client, books_folder(), "Family")
    with SessionLocal() as db:
        db.add(LibraryProblem(library_id=made["id"], path="x/y.epub", reason="made-up", size=1, mtime_ns=1))
        db.commit()
    item = client.get(f"/api/v1/libraries/{made['id']}/problems").json()["items"][0]
    assert item["reason"] == "unreadable"  # a word that is none of ours is not passed on


def test_the_state_follows_the_queue(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    assert client.get("/api/v1/libraries/admin").json()[0]["state"] == "queued"
    with SessionLocal() as db:
        db.execute(Job.__table__.update().values(state="running"))
        db.commit()
    assert client.get("/api/v1/libraries/admin").json()[0]["state"] == "running"
    assert client.get(f"/api/v1/libraries/{made['id']}").json()["state"] == "running"


def test_everything_here_asks_for_a_sign_in(client: TestClient) -> None:
    with new_client() as nobody:
        for method, url in (("GET", "/api/v1/libraries"), ("GET", "/api/v1/libraries/1"), ("POST", "/api/v1/libraries"),
                            ("GET", "/api/v1/libraries/admin"), ("POST", "/api/v1/libraries/1/scan"),
                            ("GET", "/api/v1/libraries/1/problems"), ("PUT", "/api/v1/libraries/1/members")):
            assert nobody.request(method, url, json={}).status_code == 401, (method, url)


def test_a_device_and_a_key_read_the_libraries_of_their_account(client: TestClient, operator: Account) -> None:
    made = create(client, books_folder(), "Family")
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    with TestClient(client.app, base_url="http://testserver") as program:
        for secret in (token, key):
            answer = program.get("/api/v1/libraries", headers={"Authorization": f"Bearer {secret}"})
            assert answer.status_code == 200 and [item["id"] for item in answer.json()] == [made["id"]]
    # The record of the library was not changed by any of it.
    with SessionLocal() as db:
        assert db.scalars(select(Library)).one().path == made["path"]


# ---- the progress of a run ------------------------------------------------------------------------------------------


def test_a_member_sees_how_far_the_run_of_their_library_is_and_nobody_else_does(operator: Account, client: TestClient) -> None:
    folder = books_folder("progress")
    shelf = create(client, folder, "Slow shelf")
    other = create(client, books_folder("progress-other"), "Other shelf")
    run_jobs()
    reader = make_account("reader")
    outsider = make_account("outsider")
    client.put(f"/api/v1/libraries/{shelf['id']}/members", json={"members": [{"user_id": reader.id, "right": "read"}]})
    with SessionLocal() as db:
        for library_id, done, total in ((shelf["id"], 3353, 20592), (other["id"], 7, 9)):
            row = db.get(Library, library_id)
            assert row is not None
            row.progress_done, row.progress_total = done, total
        db.commit()
    seen = new_client(reader).get("/api/v1/libraries").json()
    assert [(item["name"], item["progress"]) for item in seen] == [("Slow shelf", {"done": 3353, "total": 20592})]
    assert new_client(outsider).get("/api/v1/libraries").json() == []
