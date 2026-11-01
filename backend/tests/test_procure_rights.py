"""The right to acquire: the third right of a library, between reading and managing. The operator gives it
to the members of a library; ``require_acquirer`` is the check the routes that fetch books use."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import ACQUIRE, LIBRARY_RIGHTS, MANAGE, READ, Account
from app.services import libraries

from .conftest import make_account, new_client
from .library_helpers import books_folder, join, make_library


def admin_members(client: TestClient, library: int) -> list[dict[str, str]]:
    """The members of a library as the operator sees them."""
    shown = [item for item in client.get("/api/v1/libraries/admin").json() if item["id"] == library]
    assert len(shown) == 1
    return shown[0]["members"]  # type: ignore[no-any-return]


def test_the_rights_are_three_and_each_includes_the_one_before() -> None:
    assert LIBRARY_RIGHTS == (READ, ACQUIRE, MANAGE) == ("read", "acquire", "manage")
    assert libraries.at_least(MANAGE, ACQUIRE) and libraries.at_least(MANAGE, READ)
    assert libraries.at_least(ACQUIRE, READ) and libraries.at_least(ACQUIRE, ACQUIRE)
    assert not libraries.at_least(READ, ACQUIRE) and not libraries.at_least(ACQUIRE, MANAGE)
    assert not libraries.at_least(None, READ)


def test_the_check_for_an_acquirer_lets_acquire_and_manage_through_and_names_the_rest(operator: Account) -> None:
    library = make_library(books_folder("shelf"), "Shelf")
    people = {name: make_account(name) for name in ("reader", "acquirer", "manager", "stranger")}
    join(library, people["reader"].id, "read")
    join(library, people["acquirer"].id, "acquire")
    join(library, people["manager"].id, "manage")
    with SessionLocal() as db:
        assert libraries.require_acquirer(db, people["acquirer"].id, library).id == library
        assert libraries.require_acquirer(db, people["manager"].id, library).id == library
        with pytest.raises(HTTPException) as reader:
            libraries.require_acquirer(db, people["reader"].id, library)
        assert reader.value.status_code == 403
        # Somebody who does not see the library is told it is not there, not that they may not.
        with pytest.raises(HTTPException) as stranger:
            libraries.require_acquirer(db, people["stranger"].id, library)
        assert stranger.value.status_code == 404
        with pytest.raises(HTTPException) as nothing:
            libraries.require_acquirer(db, people["manager"].id, library + 1000)
        assert nothing.value.status_code == 404
        # The operator is no exception: a right comes from being in the library (this operator is not in it).
        with pytest.raises(HTTPException) as boss:
            libraries.require_acquirer(db, operator.id, library)
        assert boss.value.status_code == 404
        assert set(db.scalars(libraries.acquirable_library_ids(people["acquirer"].id))) == {library}
        assert set(db.scalars(libraries.acquirable_library_ids(people["manager"].id))) == {library}
        assert set(db.scalars(libraries.acquirable_library_ids(people["reader"].id))) == set()
        assert set(db.scalars(libraries.acquirable_library_ids(operator.id))) == set()


def test_the_operator_gives_the_right_with_the_members_of_a_library(client: TestClient, operator: Account) -> None:
    library = make_library(books_folder("shelf"), "Shelf")
    tom, rita = make_account("tom"), make_account("rita")
    answer = client.put(f"/api/v1/libraries/{library}/members", json={"members": [
        {"user_id": operator.id, "right": "manage"}, {"user_id": tom.id, "right": "acquire"},
        {"user_id": rita.id, "right": "read"}]})
    assert answer.status_code == 200, answer.text
    assert {m["name"]: m["right"] for m in answer.json()["members"]} == {"tester": "manage", "tom": "acquire",
                                                                          "rita": "read"}
    shown = {m["name"]: m["right"] for m in admin_members(client, library)}
    assert shown == {"tester": "manage", "tom": "acquire", "rita": "read"}
    # Moving a person up or down is saying it again.
    again = client.put(f"/api/v1/libraries/{library}/members", json={"members": [
        {"user_id": operator.id, "right": "manage"}, {"user_id": tom.id, "right": "read"},
        {"user_id": rita.id, "right": "acquire"}]})
    assert {m["name"]: m["right"] for m in again.json()["members"]}["rita"] == "acquire"
    assert {m["name"]: m["right"] for m in again.json()["members"]}["tom"] == "read"


@pytest.mark.parametrize("right", ["procure", "write", "Acquire", "", "admin"])
def test_a_right_that_does_not_exist_is_refused_and_changes_nothing(client: TestClient, operator: Account,
                                                                    right: str) -> None:
    library = make_library(books_folder("shelf"), "Shelf")
    before = [m["name"] for m in admin_members(client, library)]
    tom = make_account("tom")
    answer = client.put(f"/api/v1/libraries/{library}/members", json={"members": [
        {"user_id": operator.id, "right": "manage"}, {"user_id": tom.id, "right": right}]})
    assert answer.status_code == 422
    assert [m["name"] for m in admin_members(client, library)] == before


def test_a_member_sees_their_own_right_in_the_list_of_libraries(client: TestClient, operator: Account) -> None:
    library = make_library(books_folder("shelf"), "Shelf")
    tom = make_account("tom")
    join(library, tom.id, "acquire")
    with new_client(tom) as as_tom:
        shown = as_tom.get("/api/v1/libraries").json()
    assert [(item["name"], item["my_right"]) for item in shown] == [("Shelf", "acquire")]
