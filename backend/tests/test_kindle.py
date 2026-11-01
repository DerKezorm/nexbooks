"""Sending an e-book to a Kindle: the address of the person, the checks (rights of the download, EPUB only, no copy
protection, size), the mail through the operator's server in the background, and the brakes against sending in bulk."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition, Job, LibraryMember, ProcureNotice, utcnow
from app.services import brakes, jobs, kindle, notices, settings_service

from .conftest import new_client
from .ereader_helpers import Reading
from .fake_push import FakePushService
from .library_helpers import join, run_jobs
from .test_settings import MAIL, FakeSmtp, smtp  # noqa: F401 - fixture

#: The domain of a Kindle address, kept apart from a name so that no address stands written out in the tests.
DOMAIN = "kindle.com"
ADDRESS = f"ada_k4w1@{DOMAIN}"
BEN = f"ben_x@{DOMAIN}"


def mail_on(client: TestClient) -> None:
    assert client.put("/api/v1/settings", json=MAIL).status_code == 200


def set_address(browser: TestClient, address: str = ADDRESS) -> None:
    answer = browser.put("/api/v1/ereaders/kindle", json={"address": address})
    assert answer.status_code == 200, answer.text


def mail_on_directly() -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"smtp_host": "mail.example.com", "smtp_from": "paper@example.com"})


def jobs_of_kind() -> list[Job]:
    with SessionLocal() as db:
        return list(db.scalars(select(Job).where(Job.kind == kindle.JOB).order_by(Job.id)))


@pytest.mark.parametrize("address", [f"ada_k4w1@{DOMAIN}", f"  Ada.K4W1@{DOMAIN.upper()} ", f"a+b-c@{DOMAIN}"])
def test_an_address_at_kindle_dot_com_is_taken_in_lower_case(ada_browser: TestClient, address: str) -> None:
    saved = ada_browser.put("/api/v1/ereaders/kindle", json={"address": address}).json()
    assert saved["address"] == " ".join(address.split()).lower()
    assert ada_browser.get("/api/v1/ereaders").json()["kindle"]["address"] == saved["address"]


@pytest.mark.parametrize("address", [
    "ada@example.com", f"ada@{DOMAIN}.example.com", f"ada@x{DOMAIN}", f"ada@free.{DOMAIN}", f"@{DOMAIN}",
    f"ada@{DOMAIN}\nBcc: other@example.com", f"ada@{DOMAIN},other@{DOMAIN}", f"ada b@{DOMAIN}", f"ada@{DOMAIN}>",
    f"<ada@{DOMAIN}>", "a" * 80 + f"@{DOMAIN}", f".ada@{DOMAIN}", "ada",
])
def test_anything_else_is_not_a_kindle_address(ada_browser: TestClient, address: str) -> None:
    refused = ada_browser.put("/api/v1/ereaders/kindle", json={"address": address})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "invalid_kindle_address"
    assert ada_browser.get("/api/v1/ereaders").json()["kindle"]["address"] == ""


def test_an_empty_address_removes_it_and_each_person_has_their_own(reading: Reading, ada_browser: TestClient) -> None:
    set_address(ada_browser)
    with new_client(reading.shelf.ben) as ben:
        assert ben.get("/api/v1/ereaders").json()["kindle"]["address"] == ""
        set_address(ben, BEN)
    assert ada_browser.get("/api/v1/ereaders").json()["kindle"]["address"] == ADDRESS
    assert ada_browser.put("/api/v1/ereaders/kindle", json={"address": "  "}).json()["address"] == ""


def test_the_setup_names_the_sender_and_whether_a_mail_server_is_set_up(
        client: TestClient, operator: Account, ada_browser: TestClient) -> None:
    before = ada_browser.get("/api/v1/ereaders").json()["kindle"]
    assert before["mail_ready"] is False and before["sender"] == "" and before["max_megabytes"] == 50
    mail_on(client)
    after = ada_browser.get("/api/v1/ereaders").json()["kindle"]
    assert after["mail_ready"] is True and after["sender"] == "paper@example.com"


def test_a_book_is_mailed_in_the_background_as_an_attachment(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    file_id = reading.file_of("The Quiet Orchard")
    queued = ada_browser.post(f"/api/v1/files/{file_id}/kindle")
    assert queued.status_code == 202 and queued.json() == {"queued": True}
    # Nothing goes out in the request: a job does it.
    assert smtp.sent == [] and len(jobs_of_kind()) == 1
    run_jobs()
    assert [job.state for job in jobs_of_kind()] == ["done"]
    assert len(smtp.sent) == 1
    message = smtp.sent[0]
    assert message["To"] == ADDRESS and message["From"] == "paper@example.com"
    assert message["Subject"] == "The Quiet Orchard"
    (attachment,) = [part for part in message.iter_attachments()]
    assert attachment.get_content_type() == "application/epub+zip"
    assert attachment.get_filename() == "The Quiet Orchard.epub"
    from app.services import works as works_service

    with SessionLocal() as db:
        _row, path = works_service.file_of(db, reading.shelf.ada.id, file_id)
    assert attachment.get_content() == path.read_bytes()
    # Neither the folder nor the name on the disk is in the mail.
    assert "hidden7f3a" not in message.as_string() and str(path.parent) not in message.as_string()


def test_pressing_twice_queues_one_mail(client: TestClient, operator: Account, reading: Reading,
                                         ada_browser: TestClient, smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    file_id = reading.file_of("The Quiet Orchard")
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    assert len(jobs_of_kind()) == 1
    run_jobs()
    assert len(smtp.sent) == 1


def test_two_people_sending_the_same_book_are_two_mails(client: TestClient, operator: Account, reading: Reading,
                                                         ada_browser: TestClient,
                                                         smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    with new_client(reading.shelf.ben) as ben:
        set_address(ben, BEN)
        file_id = reading.file_of("The Quiet Orchard")
        assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
        assert ben.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    run_jobs()
    assert sorted(message["To"] for message in smtp.sent) == [ADDRESS, BEN]


def test_it_needs_an_address_a_mail_server_and_the_operators_switch(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient) -> None:
    file_id = reading.file_of("The Quiet Orchard")
    no_address = ada_browser.post(f"/api/v1/files/{file_id}/kindle")
    assert no_address.status_code == 409 and no_address.json()["detail"]["code"] == "kindle_address_missing"
    set_address(ada_browser)
    no_mail = ada_browser.post(f"/api/v1/files/{file_id}/kindle")
    assert no_mail.status_code == 409 and no_mail.json()["detail"]["code"] == "mail_off"
    mail_on(client)
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    off = ada_browser.post(f"/api/v1/files/{file_id}/kindle")
    assert off.status_code == 403 and off.json()["detail"]["code"] == "ereaders_off"
    assert ada_browser.post("/api/v1/ereaders/kindle/test").status_code == 403
    assert jobs_of_kind() == []


def test_a_file_of_a_library_the_person_is_not_in_does_not_exist(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    secret = reading.file_of("A Secret Shelf")
    gone = ada_browser.post(f"/api/v1/files/{secret}/kindle")
    nowhere = ada_browser.post("/api/v1/files/99999/kindle")
    assert gone.status_code == nowhere.status_code == 404 and gone.json() == nowhere.json()
    assert jobs_of_kind() == [] and smtp.sent == []
    # The one who is a member of it may.
    with new_client(reading.shelf.ben) as ben:
        set_address(ben, BEN)
        assert ben.post(f"/api/v1/files/{secret}/kindle").status_code == 202


def test_only_an_epub_without_copy_protection_is_sent(client: TestClient, operator: Account, reading: Reading,
                                                       ada_browser: TestClient) -> None:
    mail_on(client)
    set_address(ada_browser)
    locked = ada_browser.post(f"/api/v1/files/{reading.file_of('The Locked Door')}/kindle")
    assert locked.status_code == 422 and locked.json()["detail"]["code"] == "protected_book"
    audio = ada_browser.post(f"/api/v1/files/{reading.audio_file_of('Night Train')}/kindle")
    assert audio.status_code == 422 and audio.json()["detail"]["code"] == "not_epub"
    assert jobs_of_kind() == []


def test_a_file_over_fifty_megabytes_is_refused_with_a_clear_message(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        monkeypatch: pytest.MonkeyPatch) -> None:
    assert kindle.MAX_BYTES == 50 * 1024 * 1024
    mail_on(client)
    set_address(ada_browser)
    file_id = reading.file_of("The Quiet Orchard")
    monkeypatch.setattr(kindle, "MAX_BYTES", 100)
    big = ada_browser.post(f"/api/v1/files/{file_id}/kindle")
    assert big.status_code == 413 and big.json()["detail"]["code"] == "file_too_large"
    assert "50 MB" in big.json()["detail"]["message"]
    assert jobs_of_kind() == []


def test_the_rights_are_checked_again_when_the_job_runs(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=reading.shelf.ada.id).delete()
        db.commit()
    run_jobs()
    assert smtp.sent == []
    assert [(job.state, job.error) for job in jobs_of_kind()] == [("failed", "file no longer sendable")]


def test_the_address_is_read_when_the_job_runs_and_no_address_sends_nothing(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    set_address(ada_browser, "")
    run_jobs()
    assert smtp.sent == [] and [job.state for job in jobs_of_kind()] == ["failed"]


def test_a_mail_server_that_does_not_answer_is_tried_again_and_then_given_up(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    smtp.fail = True
    run_jobs()
    (job,) = jobs_of_kind()
    assert job.state == "failed" and job.attempts == jobs.ATTEMPTS and job.error == "mail server"
    assert smtp.sent == []
    # The mail server's own words (an address, a name) are not kept in the job.
    assert "example.com" not in job.error


def test_a_mail_that_failed_once_goes_out_on_the_next_try(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    smtp.fail = True
    assert jobs.run_pending(1) == 1
    assert [job.state for job in jobs_of_kind()] == ["queued"]
    smtp.fail = False
    run_jobs()
    assert len(smtp.sent) == 1 and [job.state for job in jobs_of_kind()] == ["done"]


def test_the_brakes_stop_sending_in_bulk(client: TestClient, operator: Account, reading: Reading,
                                          ada_browser: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mail_on(client)
    set_address(ada_browser)
    monkeypatch.setitem(brakes.LIMITS, "kindle", 2)
    files = [reading.file_of(title) for title in ("The Quiet Orchard", "The Loud Orchard", "Kite Season")]
    assert [ada_browser.post(f"/api/v1/files/{file}/kindle").status_code for file in files[:2]] == [202, 202]
    third = ada_browser.post(f"/api/v1/files/{files[2]}/kindle")
    assert third.status_code == 429 and third.json()["detail"]["code"] == "kindle_too_often"
    assert int(third.headers["retry-after"]) >= 1
    assert len(jobs_of_kind()) == 2
    # Another person is not held up by it.
    with new_client(reading.shelf.ben) as ben:
        set_address(ben, BEN)
        assert ben.post(f"/api/v1/files/{files[2]}/kindle").status_code == 202


def test_the_hourly_brake_holds_after_the_minute_has_passed(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app import clock

    mail_on(client)
    set_address(ada_browser)
    monkeypatch.setitem(brakes.LIMITS, "kindle_hour", 1)
    now = [1000.0]
    monkeypatch.setattr(clock, "monotonic", lambda: now[0])
    first = reading.file_of("The Quiet Orchard")
    assert ada_browser.post(f"/api/v1/files/{first}/kindle").status_code == 202
    now[0] += 120.0
    held = ada_browser.post(f"/api/v1/files/{reading.file_of('The Loud Orchard')}/kindle")
    assert held.status_code == 429 and 3000 < int(held.headers["retry-after"]) <= 3600
    now[0] += 3600.0
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Loud Orchard')}/kindle").status_code == 202


def test_a_key_does_not_send_and_a_device_may_for_its_own_account(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    from app.services import apikeys, devices

    mail_on(client)
    set_address(ada_browser)
    with SessionLocal() as db:
        settings_service.save(db, {"api_keys_allowed": True})
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        _key, key = apikeys.make(db, ada, "dash", "read")
        _device, token = devices.make(db, ada, "phone")
    file_id = reading.file_of("The Quiet Orchard")
    with TestClient(ada_browser.app, base_url="http://testserver") as program:
        refused = program.post(f"/api/v1/files/{file_id}/kindle", headers={"Authorization": f"Bearer {key}"})
        assert refused.status_code in (401, 403) and jobs_of_kind() == []
        sent = program.post(f"/api/v1/files/{file_id}/kindle", headers={"Authorization": f"Bearer {token}"})
        assert sent.status_code == 202
        # The address is a browser's to set: a device cannot point the mail anywhere else.
        moved = program.put("/api/v1/ereaders/kindle", headers={"Authorization": f"Bearer {token}"},
                            json={"address": f"other@{DOMAIN}"})
        assert moved.status_code == 403
    run_jobs()
    assert [message["To"] for message in smtp.sent] == [ADDRESS]


def test_a_test_mail_carries_a_small_book_and_is_braked(
        client: TestClient, operator: Account, ada_browser: TestClient, smtp: type[FakeSmtp],  # noqa: F811
        monkeypatch: pytest.MonkeyPatch) -> None:
    assert ada_browser.post("/api/v1/ereaders/kindle/test").json()["detail"]["code"] == "kindle_address_missing"
    set_address(ada_browser)
    assert ada_browser.post("/api/v1/ereaders/kindle/test").json()["detail"]["code"] == "mail_off"
    mail_on(client)
    monkeypatch.setitem(brakes.LIMITS, "kindle_test", 2)
    assert ada_browser.post("/api/v1/ereaders/kindle/test").json() == {"sent": True}
    message = smtp.sent[0]
    assert message["To"] == ADDRESS and message["Subject"] == "nexbooks test"
    (attachment,) = [part for part in message.iter_attachments()]
    assert attachment.get_content_type() == "application/epub+zip"
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(attachment.get_content())) as archive:
        assert archive.namelist()[0] == "mimetype" and archive.read("mimetype") == b"application/epub+zip"
        assert archive.testzip() is None
    assert ada_browser.post("/api/v1/ereaders/kindle/test").status_code == 200
    held = ada_browser.post("/api/v1/ereaders/kindle/test")
    assert held.status_code == 429 and held.json()["detail"]["code"] == "kindle_too_often"
    assert len(smtp.sent) == 2


def test_test_mails_have_a_brake_for_the_hour_too(
        client: TestClient, operator: Account, ada_browser: TestClient, smtp: type[FakeSmtp],  # noqa: F811
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The address can be changed at will, so a minute's brake alone lets a script mail the operator's server for ever:
    the hour's brake holds the test mails of one person to ten, with whatever addresses."""
    from app import clock

    mail_on(client)
    now = [1000.0]
    monkeypatch.setattr(clock, "monotonic", lambda: now[0])
    answers = []
    for number in range(40):
        assert ada_browser.put("/api/v1/ereaders/kindle", json={"address": f"someone-{number}@{DOMAIN}"}
                               ).status_code == 200
        answers.append(ada_browser.post("/api/v1/ereaders/kindle/test").status_code)
        now[0] += 20.0
    assert answers.count(200) == brakes.LIMITS["kindle_test_hour"] == len(smtp.sent)
    held = ada_browser.post("/api/v1/ereaders/kindle/test")
    assert held.status_code == 429 and held.json()["detail"]["code"] == "kindle_too_often"
    assert 0 < int(held.headers["retry-after"]) <= 3600
    now[0] += 3600.0
    assert ada_browser.post("/api/v1/ereaders/kindle/test").status_code == 200


def test_a_mail_server_that_refuses_the_test_says_so_without_its_own_words(
        client: TestClient, operator: Account, ada_browser: TestClient, smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    smtp.fail = True
    failed = ada_browser.post("/api/v1/ereaders/kindle/test")
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "mail_failed"
    assert "example.com" not in failed.text and ADDRESS not in failed.text


@pytest.mark.parametrize(("title", "name"), [
    ("The Quiet Orchard", "The Quiet Orchard.epub"),
    ("../../etc/passwd", "etc passwd.epub"),
    ("Tin & <Brass> \"Dust\"", "Tin & Brass Dust.epub"),
    ("", "book.epub"),
    ("x" * 300, "x" * 80 + ".epub"),
    ("Line\r\nBcc: other@example.com", "Line Bcc other example.com.epub"),
])
def test_the_name_of_the_attachment_holds_no_path_and_no_line_break(title: str, name: str) -> None:
    assert kindle._filename(title) == name


def notices_of_kindle() -> list[ProcureNotice]:
    with SessionLocal() as db:
        return list(db.scalars(select(ProcureNotice).where(ProcureNotice.code == kindle.NOTE).order_by(ProcureNotice.id)))


def test_a_mail_that_never_goes_is_told_under_needs_you_with_the_reason_and_can_be_clicked_away(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    # (The page "Braucht dich" is the page of people who may ask for books.)
    join(reading.shelf.library, reading.shelf.ada.id, "acquire")
    mail_on(client)
    set_address(ada_browser)
    file_id = reading.file_of("The Quiet Orchard")
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    smtp.fail = True
    run_jobs()
    assert [job.state for job in jobs_of_kind()] == ["failed"]
    (note,) = notices_of_kindle()
    assert note.user_id == reading.shelf.ada.id and note.detail == {"reason": "mail"}
    shown = ada_browser.get("/api/v1/procure/problems").json()["notes"]
    assert [(item["code"], item["detail"], item["book"]["title"]) for item in shown] == [
        ("kindle_failed", {"reason": "mail"}, "The Quiet Orchard")]
    # Clicked away: it is gone, and sending again works as before.
    assert ada_browser.post(f"/api/v1/procure/notes/{shown[0]['id']}/acknowledge").status_code == 204
    assert notices_of_kindle() == []
    smtp.fail = False
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    run_jobs()
    assert len(smtp.sent) == 1 and notices_of_kindle() == []


def work_of(file_id: int) -> tuple[int, int]:
    with SessionLocal() as db:
        row = db.get(BookFile, file_id)
        assert row is not None and row.edition_id is not None
        edition = db.get(Edition, row.edition_id)
        assert edition is not None
        return edition.work_id, edition.id


def test_a_mail_that_never_goes_reaches_someone_who_may_only_read_by_push_without_naming_the_book(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient, smtp: type[FakeSmtp],  # noqa: F811
        push_service: FakePushService) -> None:
    assert ada_browser.get("/api/v1/procure/problems").status_code == 403, "the page of the notes is not hers"
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post("/api/v1/push/devices", json=push_service.device().subscription(),
                            headers={"X-Nexbooks-Language": "de"}).status_code == 201
    file_id = reading.file_of("The Quiet Orchard")
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    smtp.fail = True
    for _ in range(jobs.ATTEMPTS - 1):
        assert jobs.run_pending(1) == 1
    notices.settle()
    assert push_service.received == [], "a try that will be followed by another tells nothing"
    assert jobs.run_pending(1) == 1
    notices.settle()
    work_id, edition_id = work_of(file_id)
    [came] = push_service.received
    assert came.message["title"] == "Ein Buch ist nicht auf deinem Kindle angekommen"
    assert came.message["body"] == kindle.PUSH_TEXTS["de"][1]["mail"]
    assert came.message["url"] == f"/work/{work_id}" and came.message["tag"] == f"kindle-{edition_id}"
    assert "Quiet Orchard" not in str(came.message) and DOMAIN not in str(came.message)


def test_the_push_about_a_mail_comes_without_a_mail_of_its_own(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient, smtp: type[FakeSmtp],  # noqa: F811
        push_service: FakePushService) -> None:
    mail_on(client)
    with SessionLocal() as db:
        found = db.get(Account, reading.shelf.ada.id)
        assert found is not None
        found.email, found.email_confirmed_at = "ada@example.com", utcnow()
        db.commit()
    set_address(ada_browser)
    assert ada_browser.post("/api/v1/push/devices", json=push_service.device().subscription(),
                            headers={"X-Nexbooks-Language": "en"}).status_code == 201
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    set_address(ada_browser, "")
    run_jobs()
    notices.settle()
    [came] = push_service.received
    assert came.message["title"] == "A book did not reach your Kindle"
    assert came.message["body"] == kindle.PUSH_TEXTS["en"][1]["no_address"]
    assert smtp.sent == []


def test_a_blocked_account_gets_no_push(client: TestClient, operator: Account, reading: Reading,
                                        ada_browser: TestClient, smtp: type[FakeSmtp],  # noqa: F811
                                        push_service: FakePushService) -> None:
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post("/api/v1/push/devices", json=push_service.device().subscription()).status_code == 201
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    with SessionLocal() as db:
        found = db.get(Account, reading.shelf.ada.id)
        assert found is not None
        found.blocked_at = utcnow()
        db.commit()
    run_jobs()
    notices.settle()
    assert push_service.received == []


def test_every_reason_is_said_in_both_languages() -> None:
    reasons = {*kindle.REASONS.values(), "mail"}
    assert set(kindle.PUSH_TEXTS) == {"de", "en"}
    for title, words in kindle.PUSH_TEXTS.values():
        assert set(words) == reasons
        assert all("–" not in text and "—" not in text for text in (title, *words.values()))


def test_a_try_that_will_be_followed_by_another_and_a_mail_that_went_tell_nothing(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    smtp.fail = True
    for _ in range(jobs.ATTEMPTS - 1):
        assert jobs.run_pending(1) == 1
        assert [job.state for job in jobs_of_kind()] == ["queued"] and notices_of_kindle() == []
    smtp.fail = False
    run_jobs()
    assert [job.state for job in jobs_of_kind()] == ["done"] and notices_of_kindle() == []
    assert len(smtp.sent) == 1


def test_an_address_that_went_before_the_job_ran_is_told_with_its_own_reason_and_once_for_a_book(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    file_id = reading.file_of("The Quiet Orchard")
    for _ in range(2):
        assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
        set_address(ada_browser, "")
        run_jobs()
        set_address(ada_browser)
    (note,) = notices_of_kindle()
    assert note.detail == {"reason": "no_address"} and smtp.sent == []
    # Another reason for the same book renews the one note.
    assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
    smtp.fail = True
    run_jobs()
    (note,) = notices_of_kindle()
    assert note.detail == {"reason": "mail"}


def test_the_operator_switching_e_readers_off_before_the_job_ran_is_told_as_such(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    assert ada_browser.post(f"/api/v1/files/{reading.file_of('The Quiet Orchard')}/kindle").status_code == 202
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    run_jobs()
    (note,) = notices_of_kindle()
    assert note.detail == {"reason": "off"} and smtp.sent == []


def test_a_note_is_the_persons_own_and_a_book_they_no_longer_see_is_not_told(
        client: TestClient, operator: Account, reading: Reading, ada_browser: TestClient,
        smtp: type[FakeSmtp]) -> None:  # noqa: F811
    mail_on(client)
    set_address(ada_browser)
    with new_client(reading.shelf.ben) as ben:
        set_address(ben, BEN)
        file_id = reading.file_of("The Quiet Orchard")
        assert ada_browser.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
        assert ben.post(f"/api/v1/files/{file_id}/kindle").status_code == 202
        smtp.fail = True
        with SessionLocal() as db:
            db.query(LibraryMember).filter_by(user_id=reading.shelf.ada.id).delete()
            db.commit()
        run_jobs()
    # Ada lost the library before the job ran: nothing to tell her about a book she cannot see. Ben is told.
    assert [note.user_id for note in notices_of_kindle()] == [reading.shelf.ben.id]
    assert [job.state for job in jobs_of_kind()] == ["failed", "failed"]
