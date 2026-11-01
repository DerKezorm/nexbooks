"""Devices: a phone, a later native app or a script that speaks for an account with a token of its own.

Making, listing and revoking want a signed-in browser (``BrowserAccount``): a device cannot make a token here, and a
lost phone cannot make itself a new one. The token is shown once.

One device can bring another: the iPhone app may confirm the code of a television with its own token
(``routers/app1.py``). The list says so (``via``: the device it came through), and revoking the phone revokes the
television with it (``services/devices.revoke``): what a lost phone brought does not outlive it.

The same goes for connecting the iPhone app with a QR code (``/pairing``): the browser makes a code, the app fetches its
token with it (``routers/app1.py``, ``services/pairing.py``). And for the other way round (``/pair-requests``), for a
device without a camera: the device shows a code, the person types it here, sees how the device is named and confirms;
only then the device can fetch a token for this account (``services/device_request.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field

from ..deps import BrowserAccount, DbSession, pair_guard, refuse_pairing
from ..errors import error
from ..models import Account, Device
from ..services import device_request, devices, pairing, totp

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

DeviceUid = Annotated[str, Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")]


class DeviceVia(BaseModel):
    id: str
    name: str


class DeviceOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    #: The device of this account that connected this one from the app (it confirmed its code). Revoking that device
    #: revokes this one too. Empty for a device made in the browser.
    via: DeviceVia | None = None


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=devices.NAME_MAX)


class MadeOut(BaseModel):
    device: DeviceOut
    #: The token of the device, shown once and never again.
    token: str


def _out(row: Device, through: Device | None = None) -> DeviceOut:
    return DeviceOut(id=row.uid, name=row.name, prefix=row.prefix, created_at=row.created_at,
                     last_used_at=row.last_used_at,
                     via=DeviceVia(id=through.uid, name=through.name) if through is not None else None)


@router.get("", response_model=list[DeviceOut], summary="The devices of the own account")
def listing(account: BrowserAccount, db: DbSession) -> list[DeviceOut]:
    rows = devices.listing(db, account.id)
    known = {row.id: row for row in rows}
    out = []
    for row in rows:
        through = None
        if row.via_device_id is not None:
            through = known.get(row.via_device_id) or db.get(Device, row.via_device_id)
        out.append(_out(row, through))
    return out


@router.post("", response_model=MadeOut, status_code=201, summary="Make a device token; it is shown once")
def make(body: DeviceIn, account: BrowserAccount, db: DbSession) -> MadeOut:
    row, token = devices.make(db, account, body.name)
    return MadeOut(device=_out(row), token=token)


@router.delete("/{uid}", status_code=204,
               summary="Revoke a device: its token stops working at once, and so do the devices it connected")
def revoke(uid: DeviceUid, account: BrowserAccount, db: DbSession) -> None:
    if not devices.revoke(db, account.id, uid):
        raise error("not_found", "Not found.", 404)


class PairingIn(BaseModel):
    #: The address the browser sees nexbooks at. The public address of the settings goes first when there is one.
    address: str = Field(default="", max_length=300)


class PairingOut(BaseModel):
    id: str
    #: Shown here once, in the link and the QR code. The interface shows the QR code and the code and forgets the rest.
    secret: str
    #: To type by hand, as people read it: ``ABCD-2345``.
    code: str
    expires_at: datetime
    #: The address the app will remember.
    address: str
    link: str
    qr_svg: str


class PairedDevice(BaseModel):
    id: str
    name: str


class PairingState(BaseModel):
    #: ``waiting``, ``connected`` or ``expired``.
    state: str
    device: PairedDevice | None = None


@router.post("/pairing", response_model=PairingOut, status_code=201,
             summary="Make a code that connects the iPhone app; it is shown once")
def pairing_make(body: PairingIn, request: Request, account: BrowserAccount, db: DbSession) -> PairingOut:
    # The address first: without it a code nobody could use would be made.
    address = pairing.choose_address(db, body.address, request.headers.get("origin"))
    made = pairing.prepare(db, account)
    target = pairing.link(address, made.id, made.secret)
    return PairingOut(id=made.id, secret=made.secret, code=made.code, expires_at=made.expires_at, address=address,
                      link=target, qr_svg=totp.qr_svg(target))


@router.get("/pairing/{pairing_id}", response_model=PairingState, summary="Whether the app took the code")
def pairing_state(pairing_id: Annotated[str, Path(min_length=1, max_length=64)], account: BrowserAccount,
                  db: DbSession) -> PairingState:
    found, device = pairing.state(db, account, pairing_id)
    return PairingState(state=found, device=PairedDevice(id=device.uid, name=device.name) if device else None)


@router.delete("/pairing/{pairing_id}", status_code=204, summary="Withdraw a code nobody used")
def pairing_withdraw(pairing_id: Annotated[str, Path(min_length=1, max_length=64)], account: BrowserAccount,
                     db: DbSession) -> None:
    pairing.withdraw(db, account, pairing_id)


# --- A device without a camera: the person types the code it shows ----------------------------------------------------


class PairCodeIn(BaseModel):
    #: What the person typed, in any case, with or without the dash or spaces.
    code: str = Field(min_length=1, max_length=64)


class PairRequestPreview(BaseModel):
    id: str
    #: How the device calls itself. It was typed by whoever made the request, so it proves nothing about the device.
    device_name: str
    requested_at: datetime
    expires_at: datetime


class PairRequestState(BaseModel):
    #: ``confirmed`` (the device has not fetched its token yet), ``connected`` (with the device) or ``expired``.
    state: str
    device: PairedDevice | None = None


_RequestId = Annotated[str, Path(min_length=1, max_length=64)]


def _typing_keys(account: Account) -> str:
    """The count of the person who types codes: a person who rotates addresses is still one person."""
    return f"pair-account:{account.id}"


@router.post("/pair-requests/lookup", response_model=PairRequestPreview,
             summary="Shows how the device is named that shows this code; changes nothing")
def request_lookup(body: PairCodeIn, request: Request, account: BrowserAccount, db: DbSession) -> PairRequestPreview:
    pair_guard(request, _typing_keys(account))
    try:
        row = device_request.look_up(db, body.code)
    except pairing.Refused as refused:
        refuse_pairing(request, refused, _typing_keys(account))
    return PairRequestPreview(id=row.id, device_name=row.device_name, requested_at=row.created_at,
                              expires_at=row.expires_at)


@router.post("/pair-requests/{request_id}/confirm", response_model=PairRequestState,
             summary="Connects the device to this account; the code is needed again")
def request_confirm(request_id: _RequestId, body: PairCodeIn, request: Request, account: BrowserAccount,
                    db: DbSession) -> PairRequestState:
    pair_guard(request, _typing_keys(account))
    try:
        row = device_request.confirm(db, account, request_id, body.code)
    except pairing.Refused as refused:
        refuse_pairing(request, refused, _typing_keys(account))
    found, device = device_request.state(db, account, row.id)
    return PairRequestState(state=found, device=PairedDevice(id=device.uid, name=device.name) if device else None)


@router.get("/pair-requests/{request_id}", response_model=PairRequestState,
            summary="Whether the device fetched its token")
def request_state(request_id: _RequestId, account: BrowserAccount, db: DbSession) -> PairRequestState:
    found, device = device_request.state(db, account, request_id)
    return PairRequestState(state=found, device=PairedDevice(id=device.uid, name=device.name) if device else None)


@router.delete("/pair-requests/{request_id}", status_code=204,
               summary="Takes a confirmation back as long as the device has not fetched its token")
def request_withdraw(request_id: _RequestId, account: BrowserAccount, db: DbSession) -> None:
    device_request.withdraw(db, account, request_id)
