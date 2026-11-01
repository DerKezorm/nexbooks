"""The name of a device is text from outside: a television names itself, and a person decides on it. So it must show something
(a name of blanks is a name that looks like none) and must not draw outside its line (a streak of stacked marks covers the
words around it). One rule (``devices.clean_name``) for all three ways a device gets a name: the pairing with a QR code, the
television's code, and a token made by hand."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models import Account
from app.services import devices

from .pairing_helpers import app_client, ask, asked, claim_code, look_up, make

ACUTE = chr(0x301)
GRAVE = chr(0x300)
DOT_BELOW = chr(0x323)
HANGUL_FILLER = chr(0x3164)
BRAILLE_BLANK = chr(0x2800)
HALFWIDTH_FILLER = chr(0xFFA0)
KHMER_VOWEL = chr(0x17B4)
ENCLOSING_CIRCLE = chr(0x20DD)


def refused(name: str) -> bool:
    try:
        devices.clean_name(name)
    except HTTPException:
        return True
    return False


@pytest.mark.parametrize("name", [
    "Living room TV",
    "Wohnzimmer-Fernseher",
    "Caf" + "e" + ACUTE,
    "Vi" + "e" + DOT_BELOW + ACUTE + "t",
    "a" + ACUTE + GRAVE + DOT_BELOW,
    "テレビ",
    "التلفاز",
    "TV " + BRAILLE_BLANK + " 2",
    ("e" + ACUTE) * 6,
    "A" + HANGUL_FILLER,
    "4",
])
def test_names_that_show_something_and_stack_few_marks_are_kept(name: str) -> None:
    assert devices.clean_name(name) == " ".join(name.split())


@pytest.mark.parametrize("name", [
    "a" + ACUTE * 4,
    "a" + ENCLOSING_CIRCLE * 4,
    "a" + ACUTE + ENCLOSING_CIRCLE + GRAVE + ENCLOSING_CIRCLE,
    "e" + GRAVE * 79,
    "TV " + "x" + ACUTE * 3 + GRAVE,
    ACUTE * 3,
    HANGUL_FILLER,
    HANGUL_FILLER * 40,
    BRAILLE_BLANK * 3,
    HALFWIDTH_FILLER + KHMER_VOWEL,
    " " + HANGUL_FILLER + BRAILLE_BLANK + " ",
    ACUTE + HANGUL_FILLER,
    "",
    "   ",
])
def test_names_that_draw_nothing_or_a_streak_are_refused(name: str) -> None:
    assert refused(name), ascii(name)


def test_the_refusal_is_the_one_of_all_the_other_names(client: TestClient, operator: Account) -> None:
    for bad in (HANGUL_FILLER, "a" + ACUTE * 30):
        answer = ask(app_client(), bad)
        assert (answer.status_code, answer.json()["detail"]["code"]) == (422, "device_name_invalid"), ascii(bad)


def test_the_pairing_with_a_qr_code_and_a_token_by_hand_hold_the_same_rule(client: TestClient, operator: Account) -> None:
    made = make(client)
    box = app_client()
    for bad in (HANGUL_FILLER, "a" + ACUTE * 30):
        answer = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"], "device_name": bad})
        assert (answer.status_code, answer.json()["detail"]["code"]) == (422, "device_name_invalid"), ascii(bad)
        by_code = claim_code(box, made["code"], bad)
        assert (by_code.status_code, by_code.json()["detail"]["code"]) == (422, "device_name_invalid"), ascii(bad)
        by_hand = client.post("/api/v1/devices", json={"name": bad})
        assert (by_hand.status_code, by_hand.json()["detail"]["code"]) == (422, "device_name_invalid"), ascii(bad)
    # The code stays open after a name that cannot be kept, and the name that fits is kept.
    ok = claim_code(box, made["code"], "Ada's iPhone")
    assert ok.status_code == 200, ok.text


def test_a_name_that_fits_is_shown_as_it_is_to_the_person_who_decides(client: TestClient, operator: Account) -> None:
    name = "Caf" + "e" + ACUTE + " " + "TV"
    seen = look_up(client, asked(app_client(), name)["code"])
    assert seen.json()["device_name"] == name
