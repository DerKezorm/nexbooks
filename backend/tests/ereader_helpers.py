"""What the tests of the e-reader connections, mailing to a Kindle and the OPDS catalogue share: the invented shelf of
``app_helpers`` with more on it (an audiobook alone, a book with copy protection, a PDF), and a way to make a connection
the way a person does in the browser."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BookFile

from .app_helpers import Shelf, make_shelf
from .bookmaker import cover_image
from .library_helpers import book, recording, run_jobs, scan

#: An EPUB with an encrypted chapter, as a copy protection leaves it.
ENCRYPTION = (
    '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
    'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod '
    'Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/><enc:CipherData><enc:CipherReference '
    'URI="OEBPS/text/c0.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>'
)


@dataclass
class Reading:
    """The shelf of ``app_helpers`` (``ada`` reads ``Books``, ``ben`` reads ``Books`` and ``Private``) with: a second
    volume of the series, an audiobook that has no e-book, and a book with copy protection."""

    shelf: Shelf

    def file_of(self, title: str, fmt: str = "epub") -> int:
        """The number of the file of the e-book ``title`` in ``fmt``."""
        from app.models import Edition

        with SessionLocal() as db:
            found = db.scalar(select(BookFile.id).join(Edition, Edition.id == BookFile.edition_id)
                              .where(Edition.title == title, BookFile.format == fmt, Edition.kind == "ebook"))
        assert found is not None, (title, fmt)
        return int(found)

    def audio_file_of(self, title: str) -> int:
        from app.models import Edition

        with SessionLocal() as db:
            found = db.scalar(select(BookFile.id).join(Edition, Edition.id == BookFile.edition_id)
                              .where(Edition.title == title, Edition.kind == "audio"))
        assert found is not None, title
        return int(found)


def make_reading() -> Reading:
    shelf = make_shelf()
    book(shelf.folder, "Mara Lindqvist/The Loud Orchard.epub", title="The Loud Orchard", authors=("Mara Lindqvist",),
         language="en", series="Orchard Tales", series_pos=2.0, cover=cover_image((160, 60, 40)))
    book(shelf.folder, "Rune Falkner/Tin Brass Dust.epub", title="Tin & <Brass> \"Dust\"",
         authors=("Rune Falkner",), language="en", description="Cogs <b>& gears</b> in a quiet town.")
    book(shelf.folder, "Rune Falkner/Locked.epub", title="The Locked Door", authors=("Rune Falkner",), language="en",
         extra_entries={"META-INF/encryption.xml": ENCRYPTION.encode()})
    recording(shelf.folder, "Iris Vale/Night Train/night.m4b", 300.0, title="Night Train", album="Night Train",
              artist="Iris Vale", narrator="Iris Vale", chapters=[("One", 0.0)])
    scan(shelf.library)
    run_jobs()
    shelf.reload()
    return Reading(shelf)


def connect(client: TestClient, name: str = "Reader", kind: str = "opds") -> str:
    """A connection the way a person makes it in the browser; its secret."""
    answer = client.post("/api/v1/ereaders/links", json={"name": name, "kind": kind})
    assert answer.status_code == 201, answer.text
    return str(answer.json()["secret"])


def opds_client() -> TestClient:
    """A reading app: no cookie, no tab header, nothing but what it sends."""
    from app.main import app

    return TestClient(app, base_url="http://testserver")


def basic(name: str, secret: str) -> dict[str, str]:
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"{name}:{secret}".encode()).decode()}

