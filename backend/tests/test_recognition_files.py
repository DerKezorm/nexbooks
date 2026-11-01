"""What a book's files say, and what the names of its folders and its file say where they disagree: the date Calibre writes
for a date it does not know, the year in the name of a folder, the author and the title of a PDF against its path, several
authors in one field, the kind of book at the end of a title. All invented books."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AUTHOR, Account, Credit, Edition, LibraryMember, Person

from .bookmaker import make_pdf
from .library_helpers import age, book, books_folder, make_library


def see(library_id: int) -> None:
    with SessionLocal() as db:
        tester = db.scalar(select(Account).where(Account.name == "tester"))
        assert tester is not None
        db.add(LibraryMember(library_id=library_id, user_id=tester.id, right="manage"))
        db.commit()


def edition_of(title: str) -> Edition:
    with SessionLocal() as db:
        row = db.scalar(select(Edition).where(Edition.title == title))
        assert row is not None, title
        db.expunge(row)
        return row


def authors_of(title: str) -> list[str]:
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.title == title))
        assert edition is not None
        return list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
            Credit.edition_id == edition.id, Credit.role == AUTHOR).order_by(Credit.position)))


# ---- Years -------------------------------------------------------------------------------------------------------------


def test_the_date_calibre_writes_for_a_date_it_does_not_know_is_no_year(client: TestClient, operator: Account) -> None:
    folder = books_folder("years")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",), language="de", date="0101-01-01T00:00:00+00:00")
    book(folder, "b.epub", title="Beta", authors=("Ada Feldkamp",), language="de", date="2017-05-24T00:00:00+00:00")
    book(folder, "c.epub", title="Gamma", authors=("Ada Feldkamp",), language="de", date="2105-01-01")
    book(folder, "d.epub", title="Delta", authors=("Ada Feldkamp",), language="de", date="1449-01-01")
    see(make_library(folder, "Years", scan=True))
    assert edition_of("Alpha").published == "" and edition_of("Gamma").published == ""
    assert edition_of("Delta").published == ""
    assert edition_of("Beta").published == "2017-05-24"
    items = {i["title"]: i for i in client.get("/api/v1/works?per_page=50").json()["items"]}
    assert items["Alpha"]["year"] is None and items["Gamma"]["year"] is None and items["Delta"]["year"] is None
    assert items["Beta"]["year"] == 2017


def test_a_book_without_a_year_in_its_file_has_the_year_its_folder_names(client: TestClient, operator: Account) -> None:
    folder = books_folder("years-folder")
    book(folder, "Ada Feldkamp/Alpha (2015)/alpha.epub", title="Alpha", authors=("Ada Feldkamp",), language="de",
         date="0101-01-01T00:00:00+00:00")
    book(folder, "Ada Feldkamp/Beta (2019)/beta.epub", title="Beta", authors=("Ada Feldkamp",), language="de",
         date="2011-03-04")
    book(folder, "Ada Feldkamp/Gamma/gamma.epub", title="Gamma", authors=("Ada Feldkamp",), language="de")
    see(make_library(folder, "Years", scan=True))
    items = {i["title"]: i for i in client.get("/api/v1/works?per_page=50").json()["items"]}
    assert items["Alpha"]["year"] == 2015
    # The year of the file is the one that is shown; the name of the folder is a sign for the matching only.
    assert items["Beta"]["year"] == 2011
    assert items["Gamma"]["year"] is None


# ---- A PDF against its path --------------------------------------------------------------------------------------------


def pdf(folder, relative: str, **kwargs):  # type: ignore[no-untyped-def]
    path = make_pdf(folder / relative, **kwargs)
    age(path)
    return path


def test_the_author_of_a_pdf_that_the_path_contradicts_is_the_author_the_path_names(client: TestClient,
                                                                                    operator: Account) -> None:
    folder = books_folder("pdf")
    pdf(folder, "Dan Example/Das verlorene Zeichen (2009)/Das verlorene Zeichen - Dan Example.pdf",
        title="Das verlorene Zeichen", author="Martin Wegener")
    pdf(folder, "Eva Nordstrom/Sommerfrost/Sommerfrost.pdf", title="Sommerfrost", author="Eva Nordstrom")
    pdf(folder, "lose/Nebelfeld.pdf", title="Nebelfeld", author="Jonas Brandauer")
    see(make_library(folder, "Pdf", scan=True))
    assert authors_of("Das verlorene Zeichen") == ["Dan Example"]
    # Where the PDF and the path agree, or the path names nobody, the PDF is believed.
    assert authors_of("Sommerfrost") == ["Eva Nordstrom"]
    assert authors_of("Nebelfeld") == ["Jonas Brandauer"]


@pytest.mark.parametrize(("pdf_title", "title"), [
    ("Microsoft Word - Dokument1.doc", "Lichtung im Nebel"),
    ("Layout 1", "Lichtung im Nebel"),
    ("Ein ganz anderer Name", "Lichtung im Nebel"),
    ("Lichtung im Nebel (Probe)", "Lichtung im Nebel (Probe)"),
    (None, "Lichtung im Nebel"),
])
def test_the_title_of_a_pdf_that_has_nothing_to_do_with_its_name_is_the_title_of_the_name(
        client: TestClient, operator: Account, pdf_title: str | None, title: str) -> None:
    folder = books_folder("pdf-title")
    pdf(folder, "Eva Nordstrom/Lichtung im Nebel/Lichtung im Nebel - Eva Nordstrom.pdf", title=pdf_title,
        author="Eva Nordstrom")
    see(make_library(folder, "Pdf", scan=True))
    assert authors_of(title) == ["Eva Nordstrom"]


# ---- Authors and titles in one field -----------------------------------------------------------------------------------


def test_two_authors_in_one_field_are_two_people_and_the_one_written_twice_is_one(client: TestClient,
                                                                                  operator: Account) -> None:
    folder = books_folder("authors")
    book(folder, "a.epub", title="Doppelt", authors=("Falkner, Rune | Lindqvist, Mara, Mara Lindqvist",), language="de")
    see(make_library(folder, "Authors", scan=True))
    assert authors_of("Doppelt") == ["Rune Falkner", "Mara Lindqvist"]
    items = client.get("/api/v1/works").json()["items"]
    assert [a["name"] for a in items[0]["authors"]] == ["Rune Falkner", "Mara Lindqvist"]


def test_the_kind_of_book_at_the_end_of_a_title_is_the_subtitle(client: TestClient, operator: Account) -> None:
    folder = books_folder("suffix")
    book(folder, "a.epub", title="Frevel - Kriminalroman", authors=("Ada Feldkamp",), language="de")
    book(folder, "b.epub", title="Frevel Zwei - Die Rueckkehr", authors=("Ada Feldkamp",), language="de")
    see(make_library(folder, "Suffix", scan=True))
    assert edition_of("Frevel").subtitle == "Kriminalroman"
    assert edition_of("Frevel Zwei - Die Rueckkehr").subtitle == ""
