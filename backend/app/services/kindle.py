"""Sending an e-book to a Kindle: the EPUB goes as a mail attachment from the operator's mail server to the address the
person's Kindle has at Amazon. Amazon takes the mail only from a sender the person approved in their Amazon account; the
setup names the sender (``Sender``), and a test mail checks the way.

**Rights are the download's.** The file is found the way ``GET /files/{id}/download`` finds it (``works.file_of``): a
file of a library the person is not a member of does not exist. Only an EPUB without copy protection is sent, and at
most ``MAX_BYTES`` of it (the limit of a mail at Amazon). Nothing is converted.

**In the background.** A request only checks and queues a job (``jobs``); the mail is made and sent there, the rights
and the file checked once more (a membership or the file may have changed in between). A mail server that does not take
the mail is tried again a few times; the log names the kind of failure, never the address.

**A mail that never goes is told.** When the job fails for good (the mail server took it on no try, the file or the
address or the switch of the operator is gone by the time the job runs) the person gets a note under "Braucht dich" with
the reason, which they click away (``ProcureNotice``, code ``kindle_failed``, theirs alone), and a push with the reason:
that page is for people who may ask for books, sending is for everyone who may read. The push names no book, as no push
does; a tap leads to it. A try that will be followed by another, and a job that stops because the server stops, tell
nothing.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, BookFile, Edition, Job, ProcureNotice, utcnow
from . import brakes, ereaders, jobs, mailer, settings_service, tell
from . import works as works_service

logger = logging.getLogger("nexbooks.kindle")

#: What Amazon takes in one mail.
MAX_BYTES = 50 * 1024 * 1024
MAX_MEGABYTES = MAX_BYTES // (1024 * 1024)
#: A Kindle address: a name at kindle.com (nothing that could carry a second address or a line break).
ADDRESS = re.compile(r"^[a-z0-9][a-z0-9._%+-]{0,63}@kindle\.com$")
JOB = "kindle"
#: The number the job is about: the person and the file, so that two people sending the same book are two jobs and one
#: person pressing twice is one.
_STRIDE = 1_000_000_000


def normalize(address: str) -> str | None:
    """The address in lower case, or None when it is not one at kindle.com."""
    found = " ".join(address.split()).casefold()
    return found if ADDRESS.match(found) else None


def sender(db: Session) -> str:
    """The address the mails come from: the one the person has to approve at Amazon. Empty without a mail server."""
    return str(settings_service.get(db, "smtp_from")) if mailer.configured(db) else ""


def _refuse_if_off(db: Session) -> None:
    if not ereaders.allowed(db):
        raise error("ereaders_off", "The operator has switched e-readers off.", 403)


def _file_to_send(db: Session, uid: int, file_id: int) -> tuple[BookFile, Path]:
    """The file the person may send: theirs to download, an EPUB, without copy protection, small enough for a mail."""
    row, path = works_service.file_of(db, uid, file_id)
    if row.format != "epub":
        raise error("not_epub", "Only an EPUB can be sent to a Kindle.", 422)
    if (row.probe or {}).get("drm"):
        raise error("protected_book", "A copy-protected book cannot be sent to a Kindle.", 422)
    try:
        size = path.stat().st_size
    except OSError:
        raise error("not_found", "Not found.", 404) from None
    if size > MAX_BYTES:
        raise error("file_too_large", f"This file is larger than {MAX_MEGABYTES} MB, which is more than a mail to a "
                                      "Kindle can carry. Download it and use Send to Kindle on the web.", 413,
                    max_mb=MAX_MEGABYTES)
    return row, path


def queue(db: Session, account: Account, file_id: int) -> None:
    """Checks everything a request can check and queues the mail. Raises the error the route answers."""
    _refuse_if_off(db)
    if not account.kindle_address:
        raise error("kindle_address_missing", "Set the address of your Kindle first.", 409)
    if not mailer.configured(db):
        raise error("mail_off", "No mail server is set up.", 409)
    _file_to_send(db, account.id, file_id)
    # After the checks that cost nothing, before anything is queued: a script cannot fill the queue.
    brakes.take("kindle", account.id)
    brakes.take("kindle_hour", account.id)
    jobs.enqueue(db, JOB, ref=account.id * _STRIDE + file_id, payload={"account": account.id, "file": file_id})
    db.commit()


def _filename(title: str) -> str:
    """The name of the attachment: the title with nothing but letters, digits and a few signs, never a path."""
    clean = re.sub(r"[^\w .,()'&-]+", " ", title).strip(" .")
    return (" ".join(clean.split())[:80] or "book") + ".epub"


def _message(to: str, subject: str, filename: str, data: bytes) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content("Sent from nexbooks.\n")
    message.add_attachment(data, maintype="application", subtype="epub+zip", filename=filename)
    return message


#: The reasons a job gives up with, by the words it logs, and the code the note tells. A mail server that did not take
#: the mail on any try, or refused it for good, is ``mail``.
REASONS = {"no address": "no_address", "switched off": "off", "file no longer sendable": "not_sendable",
           "file unreadable": "unreadable"}
NOTE = "kindle_failed"
#: What the push says, per language: its title and the words for each reason.
PUSH_TEXTS = {
    "de": ("Ein Buch ist nicht auf deinem Kindle angekommen", {
        "mail": "Der Mailserver hat die Mail nicht angenommen. Versuch es später noch einmal oder sag dem Betreiber "
                "Bescheid.",
        "no_address": "Deine Kindle-Adresse fehlt. Trag sie unter Mein Konto, E-Reader wieder ein.",
        "off": "Der Betreiber hat E-Reader ausgeschaltet.",
        "not_sendable": "Die Datei ließ sich nicht mehr senden. Sie fehlt oder hat sich geändert.",
        "unreadable": "Die Datei ließ sich nicht lesen.",
    }),
    "en": ("A book did not reach your Kindle", {
        "mail": "The mail server did not take the mail. Try again later or tell the operator.",
        "no_address": "Your Kindle address is missing. Enter it again under My account, E-readers.",
        "off": "The operator switched e-readers off.",
        "not_sendable": "The file could not be sent any more. It is missing or has changed.",
        "unreadable": "The file could not be read.",
    }),
}


def _push_of(work_id: int, edition_id: int, reason: str) -> tell.Notice:
    """The push about a mail that did not go. One for a book at a time: a second failure replaces it on the device."""
    return tell.Notice(texts={language: (title, words[reason]) for language, (title, words) in PUSH_TEXTS.items()},
                       url=f"/work/{work_id}", tag=f"kindle-{edition_id}")


def _tell_failure(db: Session, job: Job, reason: str) -> None:
    """The person is told that the mail did not go. One note for a book at a time (a second failure for the same book
    renews it); nothing for a person or a book that is gone. Does not raise: telling must not hide why the job
    failed."""
    try:
        payload = job.payload if isinstance(job.payload, dict) else {}
        file_id = int(payload.get("file") or 0)
        row = db.get(BookFile, file_id) if file_id else None
        account = db.get(Account, int(payload.get("account") or 0))
        if row is None or row.edition_id is None or account is None:
            return
        edition_id, person = row.edition_id, account.id
        work_id = db.scalar(select(Edition.work_id).where(
            Edition.id == edition_id, *works_service.visible_edition_clause(person)))
        if work_id is None:
            # (A book the person does not see any more is not one they can be told about.)
            return
        found = db.scalar(select(ProcureNotice).where(
            ProcureNotice.user_id == person, ProcureNotice.edition_id == edition_id, ProcureNotice.code == NOTE))
        if found is not None:
            found.detail, found.created_at = {"reason": reason}, utcnow()
        else:
            db.add(ProcureNotice(user_id=person, edition_id=edition_id, code=NOTE,
                                 detail={"reason": reason}, created_at=utcnow()))
        db.commit()
        tell.person(person, _push_of(work_id, edition_id, reason))
    except Exception:
        db.rollback()
        logger.exception("Telling a failed mail to a Kindle failed")


def _run(db: Session, job: Job) -> None:
    """The job: the mail with the book, to the address the person has now. When it fails for good, the person is
    told."""
    try:
        _send(db, job)
    except jobs.GiveUp as exc:
        _tell_failure(db, job, REASONS.get(str(exc), "mail"))
        raise
    except jobs.Retry:
        if job.attempts >= jobs.ATTEMPTS:
            _tell_failure(db, job, "mail")
        raise


def _send(db: Session, job: Job) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    account = db.get(Account, int(payload.get("account") or 0))
    file_id = int(payload.get("file") or 0)
    if account is None or account.blocked_at is not None or not account.kindle_address:
        raise jobs.GiveUp("no address")
    if not ereaders.allowed(db) or not mailer.configured(db):
        raise jobs.GiveUp("switched off")
    try:
        row, path = _file_to_send(db, account.id, file_id)
    except Exception as exc:  # the rights or the file changed since the request
        raise jobs.GiveUp("file no longer sendable") from exc
    edition = db.get(Edition, row.edition_id) if row.edition_id else None
    title = edition.title if edition is not None and edition.title else "Book"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise jobs.GiveUp("file unreadable") from exc
    message = _message(account.kindle_address, " ".join(title.split())[:100], _filename(title), data)
    try:
        mailer.send_message(db, message)
    except mailer.MailError as exc:
        # The mail server may be busy for a moment; the next try is a few seconds away.
        if exc.code == "mail_failed":
            raise jobs.Retry("mail server") from exc
        raise jobs.GiveUp(exc.code) from exc
    logger.info("Book mailed to a Kindle account_id=%s file_id=%s bytes=%s", account.id, file_id, len(data))


jobs.register(JOB, _run)


def _test_book() -> bytes:
    """A small EPUB made on the spot, so that a test mail shows up in the Kindle library when the way works."""
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles></container>")
    package = ('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
               'unique-identifier="bookid" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:identifier id="bookid">urn:uuid:6f1f5f0e-0000-4000-8000-000000000001</dc:identifier>'
               "<dc:title>nexbooks test</dc:title><dc:creator>nexbooks</dc:creator><dc:language>en</dc:language>"
               '</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               'properties="nav"/><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
               '<spine><itemref idref="c"/></spine></package>')
    nav = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           '<head><title>nexbooks test</title></head><body><nav epub:type="toc"><ol><li><a href="c.xhtml">Test</a></li>'
           "</ol></nav></body></html>")
    chapter = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body>'
               "<h1>nexbooks test</h1><p>If you can read this on your Kindle, nexbooks can send books to it.</p>"
               "</body></html>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/nav.xhtml", nav)
        archive.writestr("OEBPS/c.xhtml", chapter)
    return buffer.getvalue()


def send_test(db: Session, account: Account) -> None:
    """Mails a small test book to the person's address now, so that they see at once whether the mail server takes it.
    Whether Amazon accepts the sender shows on the Kindle."""
    _refuse_if_off(db)
    if not account.kindle_address:
        raise error("kindle_address_missing", "Set the address of your Kindle first.", 409)
    if not mailer.configured(db):
        raise error("mail_off", "No mail server is set up.", 409)
    brakes.take("kindle_test", account.id)
    brakes.take("kindle_test_hour", account.id)
    try:
        mailer.send_message(db, _message(account.kindle_address, "nexbooks test", "nexbooks test.epub", _test_book()))
    except mailer.MailError as exc:
        raise error(exc.code, str(exc), 502) from exc
