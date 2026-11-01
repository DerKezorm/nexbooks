"""The sources: which of them nexbooks may ask about books, Hardcover's token, and "match everything again". The
operator's,
in a signed-in browser.

What goes out to a source is a title, an author and an ISBN, never a file. The token of Hardcover is sealed when it is
saved and never read back: the answers say only whether one is set.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession, OperatorAccount
from ..errors import error
from ..models import SOURCES, MatchEntry
from ..security import encrypt_secret
from ..services import settings_service
from ..services.match import run as matching
from ..services.sources import config, hardcover, http, throttle

logger = logging.getLogger("nexbooks.sources")

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])

SourceId = Literal["hardcover", "openlibrary", "audible", "dnb", "apple", "wikidata", "bnf", "libris", "kb", "nb"]
TOKEN_MAX = 4000


class SourceOut(BaseModel):
    id: SourceId
    #: The operator's switch for this source.
    enabled: bool
    #: ``ready``, ``main_off``, ``off``, ``not_connected`` (Hardcover without a token), ``token_invalid``, ``resting``.
    state: Literal["ready", "main_off", "off", "not_connected", "token_invalid", "resting"]
    requests_today: int
    requests_per_day: int
    last_ok_at: datetime | None
    #: The code of the last failure (``timeout``, ``rate_limited``, ``server`` ...), empty when it went well.
    last_error: str
    last_error_at: datetime | None
    #: Until when the source is left alone after errors.
    resting_until: datetime | None
    #: Books that wait for this source: the last look at them found it away (failed, resting, at its limit).
    waiting: int = 0


class SourcesOut(BaseModel):
    #: The main switch: "nexbooks may ask sources on the internet".
    enabled: bool
    #: The markets of Audible's catalogue that are asked, in order, and every market there is.
    markets: list[str]
    available_markets: list[str]
    hardcover_connected: bool
    #: Whether the operator has chosen about Audible (it is off from the start; until then the settings ask once).
    audible_decided: bool
    #: Whether authors are looked up on the internet for a photo and a short biography.
    author_info: bool
    sources: list[SourceOut]
    #: Books waiting to be matched, and books that need a person's look.
    waiting: int
    to_look_at: int


class SourcesIn(BaseModel):
    enabled: bool | None = None
    author_info: bool | None = None
    markets: list[str] | None = Field(default=None, min_length=1, max_length=config.MAX_MARKETS)
    sources: dict[SourceId, bool] | None = None


class TokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=TOKEN_MAX + 10)


class Queued(BaseModel):
    books: int


def _view(db: DbSession) -> SourcesOut:
    values = settings_service.get_all(db)
    out = []
    waiting_for = matching.waiting_per_source()
    for source in SOURCES:
        available = config.availability(db, source)
        state = throttle.state_of(source)
        resting = state.blocked_until if state.blocked_until and state.blocked_until > throttle.now() else None
        out.append(SourceOut(
            id=source, enabled=bool(values.get(f"source_{source}")), state=available.state,  # type: ignore[arg-type]
            requests_today=state.day_count, requests_per_day=config.LIMITS[source].per_day, last_ok_at=state.last_ok_at,
            last_error=state.last_error, last_error_at=state.last_error_at, resting_until=resting,
            waiting=waiting_for.get(source, 0)))
    waiting = db.scalar(select(func.count(MatchEntry.id)).where(MatchEntry.state == "pending",
                                                                MatchEntry.decision == "")) or 0
    look = db.scalar(select(func.count(MatchEntry.id)).where(MatchEntry.state.in_(("maybe", "open")),
                                                             MatchEntry.decision == "")) or 0
    return SourcesOut(enabled=bool(values["sources_enabled"]), markets=config.chosen_markets(db),
                      available_markets=list(config.AUDIBLE_MARKETS),
                      hardcover_connected=bool(config.hardcover_token(db)), audible_decided=config.audible_decided(db),
                      author_info=bool(values["author_info"]),
                      sources=out, waiting=int(waiting), to_look_at=int(look))


@router.get("", response_model=SourcesOut, summary="The sources, their switches and how they fare (the operator)")
def read(_operator: OperatorAccount, db: DbSession) -> SourcesOut:
    return _view(db)


@router.put("", response_model=SourcesOut, summary="Switch the sources and choose the markets (the operator)")
def save(payload: SourcesIn, operator: OperatorAccount, db: DbSession) -> SourcesOut:
    changes: dict[str, object] = {}
    if payload.enabled is not None:
        changes["sources_enabled"] = payload.enabled
    if payload.author_info is not None:
        changes["author_info"] = payload.author_info
    for source, value in (payload.sources or {}).items():
        changes[f"source_{source}"] = value
    if payload.markets is not None:
        markets = list(dict.fromkeys(payload.markets))
        if any(market not in config.AUDIBLE_MARKETS for market in markets):
            raise error("invalid_input", "That market is not one of the markets.", 422, field="markets")
        changes["source_markets"] = markets
    settings_service.save(db, changes)
    logger.info("Sources changed keys=%s by=%s", ",".join(sorted(changes)), operator.name)
    # What waited for a source that is ready now is picked up.
    matching.enqueue_pending(db)
    return _view(db)


@router.put("/hardcover/token", response_model=SourcesOut,
            summary="Connect Hardcover with a read-only token of the instance (the operator)")
def connect(payload: TokenIn, operator: OperatorAccount, db: DbSession) -> SourcesOut:
    token = payload.token.strip()
    if token.lower() == "bearer":
        token = ""
    elif token.lower().startswith("bearer "):
        # Hardcover shows the token with the word in front of it.
        token = token[7:].strip()
    if not token or len(token) > TOKEN_MAX or any(ord(char) <= 32 or ord(char) == 127 for char in token):
        raise error("source_token_invalid", "That is not a token.", 422, field="token")
    previous = str(settings_service.get(db, "hardcover_token_enc") or "")
    settings_service.save(db, {"hardcover_token_enc": encrypt_secret(token, config.TOKEN_CONTEXT)})
    throttle.set_state("hardcover", "")
    try:
        accepted = hardcover.check_token(db)
    except http.SourceOff:
        # A switch is off: the token is kept, it cannot be tried now.
        accepted = True
    except http.SourceError:
        # Hardcover could not be reached: the token is kept; the card shows the error.
        accepted = True
    if not accepted:
        settings_service.save(db, {"hardcover_token_enc": previous})
        throttle.set_state("hardcover", "token_invalid" if previous else "")
        raise error("source_token_invalid", "Hardcover does not accept that token.", 422, field="token")
    logger.info("Hardcover connected by=%s", operator.name)
    matching.enqueue_pending(db)
    return _view(db)


@router.delete("/hardcover/token", response_model=SourcesOut, summary="Disconnect Hardcover (the operator)")
def disconnect(operator: OperatorAccount, db: DbSession) -> SourcesOut:
    settings_service.save(db, {"hardcover_token_enc": ""})
    throttle.set_state("hardcover", "")
    logger.info("Hardcover disconnected by=%s", operator.name)
    return _view(db)


@router.post("/rematch", response_model=Queued, status_code=202,
             summary="Match every book again; what a person decided stays (the operator)")
def rematch(operator: OperatorAccount, db: DbSession) -> Queued:
    count = matching.rematch_all(db)
    logger.info("Matching everything again books=%s by=%s", count, operator.name)
    return Queued(books=count)
