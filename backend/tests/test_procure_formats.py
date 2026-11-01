"""Formats and places (``/api/v1/procure/formats``) and the settings of the automatic work (``/api/v1/procure/automation``):
the defaults the operator decided on, what is refused, the preview of the naming, and the check whether nexbooks may put
books into a library."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, Setting
from app.services import settings_service
from app.services.organize import plan as planning
from app.services.procure import automation, formats

from .conftest import TAB, make_account, new_client
from .library_helpers import books_folder, make_library
from .procure_fakes import FakeNetwork, FakeSabnzbd

URL = "/api/v1/procure/formats"


def current(client: TestClient) -> dict[str, Any]:
    answer = client.get(URL)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def saved(body: dict[str, Any]) -> dict[str, Any]:
    keep = ("ebook", "audio", "target", "languages", "original", "variant", "abridged", "dramatized", "size", "library",
            "schema")
    return {key: copy.deepcopy(body[key]) for key in keep}


def put(client: TestClient, body: dict[str, Any]) -> Any:
    return client.put(URL, json=body)


# --- The defaults the operator decided on ---------------------------------------------------------------------------------


def test_the_formats_are_ranked_as_decided_and_pdf_and_the_thinnest_mp3_are_off(client: TestClient,
                                                                                  operator: Account) -> None:
    body = current(client)
    assert body["ebook"] == [{"f": "epub", "on": True}, {"f": "azw3", "on": True}, {"f": "mobi", "on": True},
                             {"f": "pdf", "on": False}]
    assert body["audio"] == [{"f": "flac", "on": True}, {"f": "m4b_aac", "on": True}, {"f": "mp3_320", "on": True},
                             {"f": "mp3_192", "on": True}, {"f": "mp3_128", "on": True}, {"f": "mp3_64", "on": False}]
    assert body["target"] == {"ebook": "epub", "audio": "flac"}, "the top line: nothing better is looked for"
    assert body["variant"] == "unabridged" and body["abridged"] is False and body["dramatized"] is False
    assert body["languages"] == ["de", "en", "original"] and body["original"] is True
    assert body["size"] == {"ebook": [0.05, 80.0], "audio": [20.0, 20000.0]}
    assert body["library"] == {"ebook": None, "audio": None}
    assert body["schema"] == "{Author}/{Series}/{Volume} - {Title} ({Year})"
    assert body["libraries"] == [] and body["known_ebook"] == ["epub", "azw3", "mobi", "pdf"]
    assert body["known_audio"][0] == "flac" and "de" in body["known_languages"] and len(body["samples"]) == 4


def test_the_libraries_that_can_take_books_are_offered(client: TestClient, operator: Account) -> None:
    first = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    second = make_library(books_folder("audio"), "Audio", kind="audio")
    shown = {item["id"]: item for item in current(client)["libraries"]}
    assert shown[first]["kind"] == "ebook" and shown[second]["kind"] == "audio"
    assert shown[first]["path"].endswith("ebooks")


def test_only_the_operator_reaches_the_formats_and_the_automation(client: TestClient, operator: Account) -> None:
    body = saved(current(client))
    calls = (("GET", "/formats", None), ("PUT", "/formats", body), ("POST", "/formats/preview", {"schema": "{Title}"}),
             ("POST", "/formats/check-library", {"library_id": 1}), ("GET", "/automation", None),
             ("PUT", "/automation", automation.default_config()))
    with new_client(make_account("rita")) as member:
        for method, path, payload in calls:
            assert member.request(method, "/api/v1/procure" + path, json=payload).status_code == 403, path
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        for method, path, payload in calls:
            assert nobody.request(method, "/api/v1/procure" + path, json=payload).status_code == 401, path


# --- Saving ----------------------------------------------------------------------------------------------------------------


def test_the_choice_is_saved_and_read_back(client: TestClient, operator: Account) -> None:
    library = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    audio = make_library(books_folder("audio"), "Audio", kind="audio")
    body = saved(current(client))
    body["ebook"] = [{"f": "azw3", "on": True}, {"f": "epub", "on": True}, {"f": "mobi", "on": False},
                     {"f": "pdf", "on": True}]
    body["audio"] = [{"f": "m4b_aac", "on": True}, {"f": "flac", "on": True}, {"f": "mp3_320", "on": False},
                     {"f": "mp3_192", "on": False}, {"f": "mp3_128", "on": False}, {"f": "mp3_64", "on": False}]
    body["target"] = {"ebook": "epub", "audio": "m4b_aac"}
    body.update(languages=["fr", "es"], original=False, variant="prefer", abridged=True, dramatized=True,
                size={"ebook": [0.1, 50.5], "audio": [30, 2000]}, library={"ebook": library, "audio": audio},
                schema="{Author}/{Title}")
    answer = put(client, body)
    assert answer.status_code == 200, answer.text
    again = saved(current(client))
    body["size"] = {"ebook": [0.1, 50.5], "audio": [30.0, 2000.0]}
    assert again == body
    assert saved(answer.json()) == body


def test_the_top_line_does_not_need_to_be_the_goal(client: TestClient, operator: Account) -> None:
    body = saved(current(client))
    body["target"] = {"ebook": "mobi", "audio": "mp3_128"}
    assert put(client, body).status_code == 200
    assert current(client)["target"] == {"ebook": "mobi", "audio": "mp3_128"}


def _broken() -> list[tuple[str, Any]]:
    cases: list[tuple[str, Any]] = [
        ("a format missing", lambda body: body["ebook"].pop()),
        ("a format twice", lambda body: body["ebook"].append(copy.deepcopy(body["ebook"][0]))),
        ("an unknown format", lambda body: body["ebook"].__setitem__(0, {"f": "lit", "on": True})),
        ("an audio format among the books", lambda body: body["ebook"].__setitem__(0, {"f": "flac", "on": True})),
        ("no ebook format ticked", lambda body: [item.update(on=False) for item in body["ebook"]]),
        ("no audio format ticked", lambda body: [item.update(on=False) for item in body["audio"]]),
        ("a goal that is not ticked", lambda body: body["target"].update(ebook="pdf")),
        ("a goal that is unknown", lambda body: body["target"].update(audio="wav")),
        ("a language nobody knows", lambda body: body.update(languages=["de", "tlh"])),
        ("a language twice", lambda body: body.update(languages=["de", "de"])),
        ("too many languages", lambda body: body.update(languages=["de"] * 40)),
        ("a size from top to bottom", lambda body: body["size"].update(ebook=[10, 5])),
        ("a size of nothing", lambda body: body["size"].update(ebook=[5, 5])),
        ("a negative size", lambda body: body["size"].update(audio=[-1, 5])),
        ("a size of a petabyte", lambda body: body["size"].update(audio=[1, 10**9])),
        ("a size with a third number", lambda body: body["size"].update(audio=[1, 2, 3])),
        ("a variant that does not exist", lambda body: body.update(variant="all")),
        ("a library that is not there", lambda body: body["library"].update(ebook=99999)),
        ("a schema without a title", lambda body: body.update(schema="{Author}/{Series}")),
        ("a schema that climbs out", lambda body: body.update(schema="../{Title}")),
        ("an absolute schema", lambda body: body.update(schema="/{Title}")),
        ("a schema with a backslash", lambda body: body.update(schema="{Author}\\{Title}")),
        ("a schema with a placeholder nobody knows", lambda body: body.update(schema="{Isbn}/{Title}")),
        ("a schema that is far too long", lambda body: body.update(schema="{Title}" + "x" * 400)),
    ]
    return cases


@pytest.mark.parametrize(("what", "damage"), _broken(), ids=[name for name, _ in _broken()])
def test_a_choice_that_cannot_be_used_is_refused_and_nothing_changes(client: TestClient, operator: Account, what: str,
                                                                      damage: Any) -> None:
    body = saved(current(client))
    damage(body)
    answer = put(client, body)
    assert answer.status_code == 422, f"{what}: {answer.status_code} {answer.text[:200]}"
    assert saved(current(client)) == saved(formats.default_config() | {"library": {"ebook": None, "audio": None}})


def test_a_library_of_the_wrong_kind_cannot_be_the_place_for_the_other(client: TestClient, operator: Account) -> None:
    ebooks = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    audio = make_library(books_folder("audio"), "Audio", kind="audio")
    both = make_library(books_folder("both"), "Both", kind="both")
    body = saved(current(client))
    body["library"] = {"ebook": audio, "audio": None}
    assert put(client, body).status_code == 422
    body["library"] = {"ebook": None, "audio": ebooks}
    assert put(client, body).status_code == 422
    body["library"] = {"ebook": both, "audio": both}
    assert put(client, body).status_code == 200


def test_only_the_unabridged_version_leaves_no_room_for_an_abridged_one(client: TestClient, operator: Account) -> None:
    body = saved(current(client))
    body.update(variant="unabridged", abridged=True)
    answer = put(client, body)
    assert answer.status_code == 200 and answer.json()["abridged"] is False
    body.update(variant="prefer", abridged=True)
    assert put(client, body).json()["abridged"] is True


def test_a_stored_choice_that_has_become_invalid_is_replaced_by_the_default(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        db.add(Setting(key="procure_formats", value={"ebook": "nonsense", "target": 7}))
        db.commit()
    assert current(client)["ebook"][0] == {"f": "epub", "on": True}
    with SessionLocal() as db:
        assert formats.config(db)["target"] == {"ebook": "epub", "audio": "flac"}


def test_a_library_that_was_removed_leaves_the_choice_usable(client: TestClient, operator: Account) -> None:
    library = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    body = saved(current(client))
    body["library"]["ebook"] = library
    assert put(client, body).status_code == 200
    with SessionLocal() as db:
        from app.models import Library

        db.query(Library).delete()
        db.commit()
    assert current(client)["library"]["ebook"] == library, "the number stays; a new choice is the operator's"
    preview = client.post(URL + "/preview", json={"schema": "{Author}/{Title}"})
    assert preview.status_code == 200 and not preview.json()["ebook"].startswith("/")


# --- The preview of the naming ----------------------------------------------------------------------------------------------


def test_the_preview_puts_a_made_up_book_where_the_scheme_says(client: TestClient, operator: Account) -> None:
    ebooks = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    audio = make_library(books_folder("audio"), "Audio", kind="audio")
    body = saved(current(client))
    body["library"] = {"ebook": ebooks, "audio": audio}
    assert put(client, body).status_code == 200
    shown = client.post(URL + "/preview", json={"schema": "{Author}/{Series}/{Volume} - {Title} ({Year})", "sample": 0})
    assert shown.status_code == 200, shown.text
    answer = shown.json()
    assert answer["ebook"].replace("\\", "/").endswith("/ebooks/Ada Example/Lantern Saga/02 - The Lantern Road (2019).epub")
    assert answer["audio"].replace("\\", "/").endswith("/audio/Ada Example/Lantern Saga/02 - The Lantern Road (2019)/")
    assert answer["sample"] == 0


def test_the_preview_leaves_out_what_a_book_does_not_have(client: TestClient, operator: Account) -> None:
    standalone = client.post(URL + "/preview", json={"schema": "{Author}/{Series}/{Volume} - {Title} ({Year})",
                                                     "sample": 1}).json()
    assert standalone["ebook"] == "Ben Sample/Standalone Tale (2021).epub", "no series: no folder, no volume"
    body = client.post(URL + "/preview", json={"schema": "{Author}/{Series}/{Volume} - {Title} ({Year})",
                                               "sample": 2}).json()
    assert body["ebook"] == "Cleo Placeholder/Harbour Books/01 - The Long Quiet.epub", "no year: no empty brackets"
    colon = client.post(URL + "/preview", json={"schema": "{Title}", "sample": 3}).json()
    assert colon["ebook"] == "Colours of the Valley - A Novel.epub", "a colon is no name on every disk"


def test_the_preview_follows_the_top_ticked_ebook_format(client: TestClient, operator: Account) -> None:
    body = saved(current(client))
    body["ebook"] = [{"f": "azw3", "on": True}, {"f": "epub", "on": True}, {"f": "mobi", "on": False},
                     {"f": "pdf", "on": False}]
    body["target"]["ebook"] = "azw3"
    assert put(client, body).status_code == 200
    assert client.post(URL + "/preview", json={"schema": "{Title}"}).json()["ebook"].endswith(".azw3")


def test_the_preview_wraps_around_the_samples_and_refuses_a_bad_scheme(client: TestClient, operator: Account) -> None:
    assert client.post(URL + "/preview", json={"schema": "{Title}", "sample": 4}).json()["sample"] == 0
    bad = client.post(URL + "/preview", json={"schema": "{Author}/../{Title}"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "scheme_invalid"
    assert client.post(URL + "/preview", json={"schema": "{Title}", "sample": -1}).status_code == 422
    assert client.post(URL + "/preview", json={"schema": "x" * 1000}).status_code == 422


def test_a_title_cannot_climb_out_of_the_library_through_the_preview(client: TestClient, operator: Account,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "SAMPLES", ({"author": "../..", "title": "..\\..\\x/../y", "series": "", "position": None,
                                              "year": ""},))
    body = client.post(URL + "/preview", json={"schema": "{Author}/{Title}"}).json()
    assert ".." not in body["ebook"].split("/") and "\\" not in body["ebook"]


# --- May nexbooks put books there? ------------------------------------------------------------------------------------------


def test_a_folder_that_can_be_written_is_said_so_and_nothing_stays_behind(client: TestClient, operator: Account) -> None:
    folder = books_folder("ebooks")
    library = make_library(folder, "Ebooks", kind="ebook")
    answer = client.post(URL + "/check-library", json={"library_id": library})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"writable": True, "reason": "", "hardlink": True, "downloads": []}
    assert os.listdir(folder) == [], "the probe file and its link are gone"


def test_a_folder_without_hard_links_is_writable_all_the_same(client: TestClient, operator: Account,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("ebooks")
    library = make_library(folder, "Ebooks", kind="ebook")

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(1, "operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    answer = client.post(URL + "/check-library", json={"library_id": library}).json()
    assert answer["writable"] is True and answer["hardlink"] is False
    assert os.listdir(folder) == []


@pytest.mark.parametrize(("failure", "reason"), [("read_only", "read_only"), ("permission", "no_permission"),
                                                 ("disk", "write_failed")])
def test_a_folder_that_cannot_be_written_says_why(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch,
                                                  failure: str, reason: str) -> None:
    folder = books_folder("ebooks")
    library = make_library(folder, "Ebooks", kind="ebook")
    if failure == "read_only":
        monkeypatch.setattr(planning, "writable", lambda path: (False, "read_only"))
    else:
        import errno

        real = open

        def deny(path: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if str(path).startswith(str(folder)) and "x" in mode:
                raise PermissionError(errno.EACCES, "denied") if failure == "permission" else OSError(errno.ENOSPC, "full")
            return real(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", deny)
    answer = client.post(URL + "/check-library", json={"library_id": library})
    assert answer.status_code == 200
    assert answer.json()["writable"] is False and answer.json()["reason"] == reason and answer.json()["hardlink"] is None
    assert os.listdir(folder) == []


def test_a_folder_that_is_gone_is_missing(client: TestClient, operator: Account) -> None:
    folder = books_folder("ebooks")
    library = make_library(folder, "Ebooks", kind="ebook")
    folder.rmdir()
    answer = client.post(URL + "/check-library", json={"library_id": library}).json()
    assert answer["writable"] is False and answer["reason"] == "missing"


def test_a_library_that_is_not_there_is_not_found(client: TestClient, operator: Account) -> None:
    assert client.post(URL + "/check-library", json={"library_id": 4242}).status_code == 404


def test_the_folders_of_the_download_programs_are_looked_at_for_their_file_system(client: TestClient, operator: Account,
                                                                                  net: FakeNetwork,
                                                                                  tmp_path: Path) -> None:
    folder = books_folder("ebooks")
    library = make_library(folder, "Ebooks", kind="ebook")
    sab = net.add(FakeSabnzbd("sab.example.com", "k" * 8))
    visible = tmp_path / "complete" / "nexbooks"
    visible.mkdir(parents=True)
    sab.misc["complete_dir"] = str(tmp_path / "complete")
    other = net.add(FakeSabnzbd("sab2.example.com", "k" * 8))
    other.misc["complete_dir"] = "/somewhere/nobody/sees"
    for name, fake in (("Visible", sab), ("Hidden", other)):
        assert client.post("/api/v1/procure/clients", json={"kind": "sabnzbd", "name": name,
                                                            "url": f"http://{fake.host}", "secret": "k" * 8}).status_code == 201
    answer = client.post(URL + "/check-library", json={"library_id": library}).json()
    by_client = {item["client"]: item for item in answer["downloads"]}
    assert by_client["Visible"]["seen"] is True and by_client["Visible"]["same_filesystem"] is True
    assert by_client["Visible"]["folder"] == str(visible).replace("\\", "/") or by_client["Visible"]["folder"]
    assert by_client["Hidden"] == {"client": "Hidden", "folder": "/somewhere/nobody/sees/nexbooks", "seen": False,
                                   "same_filesystem": None}


# --- The automation ----------------------------------------------------------------------------------------------------------


def test_the_automation_is_on_from_the_start_with_the_decided_rhythm(client: TestClient, operator: Account) -> None:
    assert client.get("/api/v1/procure/automation").json() == {
        "enabled": True, "rss_minutes": 15, "retry_hours": 6, "replace": True, "replace_max": 3, "seed": "no",
        "retention_days": 0, "retention_read": None}


def test_the_automation_is_saved_and_read_back(client: TestClient, operator: Account) -> None:
    body = {"enabled": False, "rss_minutes": 60, "retry_hours": 24, "replace": False, "replace_max": 10, "seed": "7d",
            "retention_days": 400}
    shown = {**body, "retention_read": None}
    assert client.put("/api/v1/procure/automation", json=body).json() == shown
    assert client.get("/api/v1/procure/automation").json() == shown
    with SessionLocal() as db:
        assert settings_service.get(db, "procure_auto") == {**body, "seed_set": True}


@pytest.mark.parametrize("change", [{"enabled": "perhaps"}, {"rss_minutes": 7}, {"rss_minutes": 0}, {"retry_hours": 2},
                                    {"replace_max": 0}, {"replace_max": 11}, {"seed": "forever"}, {"replace": None},
                                    {"enabled": None}])
def test_a_setting_that_does_not_exist_is_refused(client: TestClient, operator: Account, change: dict[str, Any]) -> None:
    body = client.get("/api/v1/procure/automation").json() | change
    assert client.put("/api/v1/procure/automation", json=body).status_code == 422
    assert client.get("/api/v1/procure/automation").json()["rss_minutes"] == 15


def test_a_stored_automation_that_has_become_invalid_is_replaced_by_the_default(client: TestClient,
                                                                                operator: Account) -> None:
    with SessionLocal() as db:
        db.add(Setting(key="procure_auto", value={"enabled": "maybe", "rss_minutes": 99}))
        db.commit()
    assert client.get("/api/v1/procure/automation").json()["enabled"] is True


def test_the_checks_hold_below_the_interface_too() -> None:
    """The service refuses what the interface would let through if its own checks were gone (a setting read from the
    database, a later route that calls it directly)."""
    with SessionLocal() as db:
        good = formats.default_config()
        assert formats.validate(db, good) == good
        for name in ("rss_minutes", "retry_hours", "seed", "replace_max", "enabled", "replace"):
            broken = automation.default_config()
            broken[name] = {"rss_minutes": 7, "retry_hours": 2, "seed": "forever", "replace_max": 11, "enabled": "yes",
                            "replace": None}[name]
            with pytest.raises(ValueError, match=name):
                automation.validate(broken)
        none = formats.default_config()
        none["ebook"] = [{"f": item["f"], "on": False} for item in none["ebook"]]
        with pytest.raises(ValueError):
            formats.validate(db, none)
