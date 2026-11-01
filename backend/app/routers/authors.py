"""The authors of the library: the grid of photos, and the photo of one author.

Everything here only reads, and is open to a signed-in browser, a device and a program with a key (``Reader``).

**An author is seen through their books**: the list holds the authors with at least one book in a library the
caller is a member of, counts only those books, and an author, a photo or a page of an author the caller sees no
book of answers 404, like one that does not exist (``services/authors.py``). Looking the authors up on the
internet is done in the background (``services/people_info.py``) and begins when a page of authors is read.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..deps import DbSession, Reader
from ..services import authors as service
from ..services import people_info
from .works import GenreName

router = APIRouter(prefix="/api/v1", tags=["people"])


class AuthorCard(BaseModel):
    id: int
    name: str
    #: How the authors are put in order: ``Last, First``.
    sort_name: str
    #: The works of theirs the caller sees, and how many of those have a recording.
    works: int
    audio: int
    #: The genre most of their books are (empty for none), and when the newest of those books came in.
    genre: str
    added_at: str | None
    #: The photo, from the author's folder or from a source; null: the interface draws a circle with the initials.
    photo_url: str | None


class AuthorPage(BaseModel):
    items: list[AuthorCard]
    total: int
    page: int
    per_page: int
    #: The authors for each genre key (``none``: no genre), for all authors the caller sees, whatever genre is chosen.
    genres: dict[str, int]


@router.get("/authors", response_model=AuthorPage,
            summary="The authors of the books the caller sees, a page at a time")
def authors(account: Reader, db: DbSession,
            sort: Annotated[Literal["name", "count", "recent"], Query(
                description="name: by last name; count: most works first; recent: the newest book first")] = "name",
            genre: Annotated[GenreName | None, Query(description="Only authors with a book of this genre")] = None,
            q: Annotated[str, Query(max_length=100, description="Only authors whose name holds these words")] = "",
            page: Annotated[int, Query(ge=1, le=100_000)] = 1,
            per_page: Annotated[int, Query(ge=1, le=service.MAX_PER_PAGE)] = 60) -> AuthorPage:
    found, total, by_genre = service.listing(db, account.id, sort=sort, genre=genre, query=q, page=page,
                                             per_page=per_page)
    # The authors on this page that were not looked up yet are looked up in the background.
    people_info.enqueue_missing(db, [card.person.id for card in found if not card.photo])
    return AuthorPage(
        items=[AuthorCard(id=c.person.id, name=c.person.name, sort_name=c.person.sort_name, works=c.works,
                          audio=c.audio, genre=c.genre, added_at=c.added.isoformat() if c.added else None,
                          photo_url=service.photo_url(c.person.id, c.photo) if c.photo else None) for c in found],
        total=total, page=page, per_page=per_page, genres=by_genre)


@router.get("/people/{person_id}/photo", summary="The photo of an author, as a WebP picture", response_model=None,
            responses={200: {"content": {"image/webp": {}}}, 304: {"description": "Not changed"}})
def photo(person_id: int, request: Request, account: Reader, db: DbSession,
          v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    name, path = service.photo_file(db, account.id, person_id)
    etag = f'"{name[:20]}"'
    # The address carries the start of the picture's name: while it is the one asked for, the picture cannot change.
    stable = v is not None and name.startswith(v) and len(v) >= 12
    headers = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable" if stable else "private, no-cache",
               "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)
