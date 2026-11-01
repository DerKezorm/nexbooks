"""E-readers through the interface: the connections an account made for its reading apps, the address of its Kindle,
mailing a book there, and the list of books for the Kobo. The operator sees every connection and may block one.

Only a signed-in browser makes, lists and deletes connections, sets the Kindle address and puts books on the list for
the Kobo: a key or a device token never does (``BrowserAccount``). Sending a book is open to a device too (the app
sends what the person may download), never to a key (a key only reads).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import Account, BrowserAccount, DbSession, OperatorAccount
from ..errors import error
from ..models import Account as AccountRow
from ..models import EreaderLink, utcnow
from ..services import ereaders, kindle, kobo, mailer, notices
from ..services.ereaders import LinkProblem

logger = logging.getLogger("nexbooks.ereaders")

router = APIRouter(prefix="/api/v1", tags=["e-readers"])


class LinkOut(BaseModel):
    id: int
    #: ``opds`` (the catalogue for reading apps), ``kosync`` (the progress sync of KOReader) or ``kobo`` (the sync of a
    #: Kobo, whose secret is part of its address).
    kind: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    #: The operator blocked it: it answers like none, for good.
    blocked: bool = False


class KindleOut(BaseModel):
    #: The address of the Kindle at Amazon; empty: none set.
    address: str
    #: Whether a mail server is set up (else the book is downloaded and sent from Amazon's web page).
    mail_ready: bool
    #: The address the mails come from, which has to be approved in the Amazon account; empty without a mail server.
    sender: str
    #: The most a mail can carry.
    max_megabytes: int


class EreadersOut(BaseModel):
    #: Whether the operator has switched e-readers on.
    allowed: bool
    links: list[LinkOut]
    kindle: KindleOut
    #: The editions on the caller's list for the Kobo that they still see, oldest first.
    kobo: list[int] = []
    #: The address nexbooks hands a Kobo for everything else it asks (the public address, else the one this request came
    #: to): a page seen over ``https`` that shows ``http`` here sits behind a proxy nexbooks does not know about.
    kobo_base: str = ""


class LinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["opds", "kosync", "kobo"] = "opds"


class MadeOut(BaseModel):
    link: LinkOut
    #: The name to sign in with next to the secret: the account's own.
    username: str
    #: Shown once, never again.
    secret: str


class KindleIn(BaseModel):
    #: An address at kindle.com; empty removes it.
    address: str = Field(max_length=255)


class QueuedOut(BaseModel):
    queued: bool = True


class SentOut(BaseModel):
    sent: bool = True


def _out(row: EreaderLink) -> LinkOut:
    return LinkOut(id=row.id, kind=row.kind, name=row.name, prefix=row.prefix, created_at=row.created_at,
                   last_used_at=row.last_used_at, blocked=row.blocked_at is not None)


def _kindle(db: DbSession, address: str) -> KindleOut:
    return KindleOut(address=address, mail_ready=mailer.configured(db), sender=kindle.sender(db),
                     max_megabytes=kindle.MAX_MEGABYTES)


@router.get("/ereaders", response_model=EreadersOut, summary="The own connections for e-readers and the Kindle address")
def own(request: Request, account: BrowserAccount, db: DbSession) -> EreadersOut:
    rows = db.scalars(select(EreaderLink).where(EreaderLink.account_id == account.id).order_by(EreaderLink.id)).all()
    return EreadersOut(allowed=ereaders.allowed(db), links=[_out(row) for row in rows],
                       kindle=_kindle(db, account.kindle_address), kobo=kobo.listed_ids(db, account.id),
                       kobo_base=ereaders.base_of(db, request))


@router.post("/ereaders/links", response_model=MadeOut, status_code=201,
             summary="Make a connection for a reading app; the secret is shown once")
def make_link(body: LinkIn, account: BrowserAccount, db: DbSession) -> MadeOut:
    if not ereaders.allowed(db):
        raise error("ereaders_off", "The operator has switched e-readers off.", 403)
    try:
        row, secret = ereaders.make(db, account, body.name, body.kind)
    except LinkProblem as exc:
        raise error(exc.code, exc.text, exc.status) from exc
    notices.connection_made(account, body.kind)
    return MadeOut(link=_out(row), username=account.name, secret=secret)


@router.delete("/ereaders/links/{link_id}", status_code=204, summary="Delete an own connection")
def delete_link(link_id: Annotated[int, Path(ge=1)], account: BrowserAccount, db: DbSession) -> None:
    row = db.get(EreaderLink, link_id)
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    if row.blocked_at is not None:
        # A block is the operator's: deleting the connection and making another would lift it.
        raise error("link_blocked", "The operator blocked this connection. Only the operator can delete it.", 409)
    db.delete(row)
    db.commit()
    logger.info("E-reader connection deleted link_id=%s", link_id)


@router.put("/ereaders/kindle", response_model=KindleOut,
            summary="Set the address of the own Kindle (an address at kindle.com; empty removes it)")
def set_kindle(body: KindleIn, account: BrowserAccount, db: DbSession) -> KindleOut:
    address = ""
    if body.address.strip():
        found = kindle.normalize(body.address)
        if found is None:
            raise error("invalid_kindle_address", "This is not a Kindle address. It ends in @kindle.com.", 422)
        address = found
    row = db.get(AccountRow, account.id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    row.kindle_address = address
    db.commit()
    return _kindle(db, address)


@router.post("/ereaders/kindle/test", response_model=SentOut,
             summary="Mail a small test book to the own Kindle address now")
def test_kindle(account: BrowserAccount, db: DbSession) -> SentOut:
    row = db.get(AccountRow, account.id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    kindle.send_test(db, row)
    return SentOut()


@router.post("/files/{file_id}/kindle", response_model=QueuedOut, status_code=202,
             summary="Mail an EPUB the caller may download to their Kindle address, in the background")
def send_to_kindle(file_id: Annotated[int, Path(ge=1)], account: Account, db: DbSession) -> QueuedOut:
    """The rights are those of the download: a file of a library the caller is not a member of does not exist (404). An
    EPUB only, without copy protection, at most 50 MB (413). Needs the address of the Kindle (409), a mail server (409)
    and the operator's switch. Too many books at once, or within an hour, are ``429`` with ``Retry-After``."""
    row = db.get(AccountRow, account.id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    kindle.queue(db, row, file_id)
    return QueuedOut()


class ListedOut(BaseModel):
    #: Whether the edition is on the list for the Kobo now.
    listed: bool


@router.put("/editions/{edition_id}/kobo", response_model=ListedOut,
            summary="Put an e-book on the own list for the Kobo (the Kobos of the account fetch it at their next sync)")
def list_for_kobo(edition_id: Annotated[int, Path(ge=1)], account: BrowserAccount, db: DbSession) -> ListedOut:
    """The rights are those of the download: an edition of a library the caller is not a member of does not exist (404).
    An e-book with an EPUB without copy protection only (422). The operator's switch must be on (403). Putting it there
    twice is one entry; a book the person took off a Kobo by hand is sent again. At most 1000 books (409). Too many
    requests are ``429`` with ``Retry-After``."""
    kobo.add(db, account.id, edition_id)
    return ListedOut(listed=True)


@router.delete("/editions/{edition_id}/kobo", response_model=ListedOut,
               summary="Take an e-book off the own list for the Kobo (the Kobos are told at their next sync)")
def unlist_for_kobo(edition_id: Annotated[int, Path(ge=1)], account: BrowserAccount, db: DbSession) -> ListedOut:
    """Works for an edition that is not on the list too (the answer is the same), and while e-readers are off."""
    kobo.remove(db, account.id, edition_id)
    return ListedOut(listed=False)


# --- The operator ----------------------------------------------------------------------------------------------------


class AnyLinkOut(LinkOut):
    account: str


def _any(row: EreaderLink, owner: str) -> AnyLinkOut:
    return AnyLinkOut(**_out(row).model_dump(), account=owner)


@router.get("/admin/ereaders/links", response_model=list[AnyLinkOut],
            summary="Every connection for e-readers (operator)")
def every_link(_operator: OperatorAccount, db: DbSession) -> list[AnyLinkOut]:
    names = dict(db.execute(select(AccountRow.id, AccountRow.name)).all())
    rows = db.scalars(select(EreaderLink).order_by(EreaderLink.account_id, EreaderLink.id)).all()
    return [_any(row, names.get(row.account_id, "")) for row in rows]


@router.post("/admin/ereaders/links/{link_id}/block", response_model=AnyLinkOut,
             summary="Block a connection for good (operator); its account sees it blocked")
def block_link(link_id: Annotated[int, Path(ge=1)], _operator: OperatorAccount, db: DbSession) -> AnyLinkOut:
    row = db.get(EreaderLink, link_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    if row.blocked_at is None:
        row.blocked_at = utcnow()
        db.commit()
        logger.info("E-reader connection blocked by the operator link_id=%s", link_id)
    owner = db.get(AccountRow, row.account_id)
    return _any(row, owner.name if owner else "")


@router.delete("/admin/ereaders/links/{link_id}", status_code=204,
               summary="Delete a connection of any account (operator); the only way to lift a block")
def delete_any_link(link_id: Annotated[int, Path(ge=1)], _operator: OperatorAccount, db: DbSession) -> None:
    row = db.get(EreaderLink, link_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    db.delete(row)
    db.commit()
    logger.info("E-reader connection deleted by the operator link_id=%s", link_id)
