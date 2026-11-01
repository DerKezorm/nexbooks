"""Deleting an account: the operator's act, from a browser, with the operator's password and the account's name typed once
more. Everything that hangs on the account goes with it in one write; nothing of anybody else is touched, and a refusal
changes nothing."""

from __future__ import annotations

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, ApiKey, AuthSession, Device
from app.services import apikeys, devices

from .conftest import PASSWORD, make_account, new_client


def count(model: type) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def delete(client: TestClient, account: Account, **body: object) -> Response:
    return client.request("DELETE", f"/api/v1/accounts/{account.id}", json=body)


def test_the_name_and_the_password_are_asked_and_a_refusal_changes_nothing(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    wrong_name = delete(client, anna, confirm_name="ann", current_password=PASSWORD)
    assert wrong_name.status_code == 422 and wrong_name.json()["detail"]["code"] == "name_mismatch"
    wrong_password = delete(client, anna, confirm_name="anna", current_password="not the password at all")
    assert wrong_password.status_code == 401
    nothing = delete(client, anna)
    assert nothing.status_code in (401, 422)
    with SessionLocal() as db:
        assert db.scalar(select(Account.id).where(Account.name == "anna")) is not None


def test_what_hangs_on_the_account_goes_with_it_and_nothing_else(client: TestClient, operator: Account) -> None:
    anna, bert = make_account("anna"), make_account("bert")
    with SessionLocal() as db:
        for owner in (anna, bert):
            apikeys.make(db, db.get(Account, owner.id), f"key of {owner.name}", "read")  # type: ignore[arg-type]
            devices.make(db, db.get(Account, owner.id), f"phone of {owner.name}")  # type: ignore[arg-type]
    with new_client(anna) as session:
        assert session.get("/api/v1/auth/me").status_code == 200
    before = (count(ApiKey), count(Device), count(AuthSession))
    done = delete(client, anna, confirm_name="anna", current_password=PASSWORD)
    assert done.status_code == 204
    assert (count(ApiKey), count(Device)) == (before[0] - 1, before[1] - 1)
    assert count(AuthSession) < before[2], "her sessions are gone"
    with SessionLocal() as db:
        assert db.scalar(select(Account.id).where(Account.name == "anna")) is None
        assert db.scalar(select(Account.id).where(Account.name == "bert")) is not None
        assert db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.account_id == bert.id)) == 1


def test_an_old_session_of_the_deleted_account_is_dead(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as session:
        assert delete(client, anna, confirm_name="anna", current_password=PASSWORD).status_code == 204
        assert session.get("/api/v1/auth/me").status_code == 401


def test_the_operator_cannot_delete_themselves(client: TestClient, operator: Account) -> None:
    refused = delete(client, operator, confirm_name="tester", current_password=PASSWORD)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "cannot_delete_self"


def test_only_the_operator_in_a_browser_deletes(client: TestClient, operator: Account) -> None:
    anna, bert = make_account("anna"), make_account("bert")
    with new_client(bert) as member:
        assert delete(member, anna, confirm_name="anna", current_password=PASSWORD).status_code == 403
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    from app.main import app

    with TestClient(app, base_url="http://testserver") as device:
        answer = device.request("DELETE", f"/api/v1/accounts/{anna.id}", headers={"Authorization": f"Bearer {token}"},
                                json={"confirm_name": "anna", "current_password": PASSWORD})
        assert answer.status_code == 403 and answer.json()["detail"]["code"] == "browser_only"
    with new_client() as stranger:
        assert delete(stranger, anna, confirm_name="anna", current_password=PASSWORD).status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(Account.id).where(Account.name == "anna")) is not None


def test_fields_of_the_old_choices_are_refused(client: TestClient, operator: Account) -> None:
    """There is nothing to choose any more where the books go: a body that still names that is not understood."""
    anna = make_account("anna")
    answer = delete(client, anna, confirm_name="anna", current_password=PASSWORD, documents="delete_documents")
    assert answer.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(Account.id).where(Account.name == "anna")) is not None
