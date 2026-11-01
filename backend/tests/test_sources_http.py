"""The walls every request to a source passes: the switches, the address, the way out, the turn, the answer.

Nothing here reaches a real service: the transport is ``fake_sources.FakeSources``, the names resolve to a public address
the test chooses, and time is a clock that jumps when the code waits for its turn.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import SourceCache, SourceState
from app.services.sources import audible, cache, config, hardcover, http, openlibrary, throttle
from app.services.sources.model import Query

from .fake_sources import host_of
from .match_helpers import World

QUERY = Query(kind="ebook", title="The Quiet Orchard", authors=["Mara Lindqvist"], language="en")


def ask_openlibrary(query: Query = QUERY) -> list:  # type: ignore[type-arg]
    with SessionLocal() as db:
        return openlibrary.search(db, query)


# ---- The switches -----------------------------------------------------------------------------------------------


def test_with_the_main_switch_off_no_source_is_asked(world: World) -> None:
    world.settings(sources_enabled=False)
    for call in (lambda db: openlibrary.search(db, QUERY), lambda db: audible.search(db, QUERY, "de"),
                 lambda db: hardcover.search(db, QUERY)):
        with SessionLocal() as db, pytest.raises(http.SourceOff) as raised:
            call(db)
        assert raised.value.state == "main_off"
    assert world.fake.log == [], "the main switch off means no request at all"


def test_a_source_that_is_switched_off_is_not_asked_and_the_others_are(world: World) -> None:
    world.only("audible")
    with SessionLocal() as db, pytest.raises(http.SourceOff) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.state == "off"
    assert world.fake.log == []
    with SessionLocal() as db:
        audible.search(db, QUERY, "de")
    # (Nothing is found by the free text, so the fields title and author are asked once more.)
    assert world.fake.count("api.audible.de") == 2


def test_hardcover_waits_for_a_token_and_then_works(world: World) -> None:
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").state == "not_connected"
        with pytest.raises(http.SourceOff):
            hardcover.search(db, QUERY)
    assert world.fake.log == []
    token = world.connect_hardcover()
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").ready
        hardcover.search(db, QUERY)
    request = world.fake.log[0]
    # The token goes to Hardcover as a bearer token, in no address, and to nobody else.
    assert request.headers["authorization"] == f"Bearer {token}"
    assert token not in str(request.url)


def test_the_default_sources_are_open_library_and_the_german_library_on_and_audible_off() -> None:
    from app.services import settings_service

    with SessionLocal() as db:
        values = settings_service.get_all(db)
    assert (values["source_openlibrary"], values["source_audible"], values["source_dnb"]) == (True, False, True)
    assert values["source_hardcover"] is True and values["hardcover_token_enc"] == ""
    assert values["source_markets"] == ["de", "us"]


# ---- The address ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://evil.example.com/search.json",
    "http://openlibrary.org/search.json?q=x",
    "https://user:secret" + chr(64) + "openlibrary.org/search.json",
    "https://openlibrary.org:8443/search.json",
    "https://openlibrary.org.evil.example.com/search.json",
    "https://sub.openlibrary.org/search.json",
    "https://openlibrary.org/search.json?q=a b",
    "ftp://openlibrary.org/x",
])
def test_a_request_goes_only_to_the_hosts_of_its_source(world: World, url: str) -> None:
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        http.request(db, "openlibrary", url)
    assert raised.value.code == "refused"
    assert world.fake.log == []


def test_the_host_of_a_request_is_never_made_from_what_a_person_typed(world: World) -> None:
    hostile = Query(kind="ebook", title="x/../../@evil.example.com#", authors=["//evil.example.com\\a"], isbn="")
    with SessionLocal() as db:
        openlibrary.search(db, hostile)
        audible.search(db, hostile, "de")
    assert set(world.fake.hosts()) <= {"openlibrary.org", "api.audible.de"}


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.20", "127.0.0.1", "169.254.169.254", "::1", "fe80::1",
                                     "100.64.0.1", "0.0.0.0", "::ffff:10.0.0.1", "64:ff9b::a00:1"])
def test_a_name_that_leads_into_an_own_network_is_never_reached(world: World, monkeypatch: pytest.MonkeyPatch,
                                                                 address: str) -> None:
    monkeypatch.setattr(http, "resolver", lambda _host, _port: [address])
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "refused"
    assert world.fake.log == [], "nothing was sent to the address"


def test_one_private_address_among_public_ones_stops_the_request(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "resolver", lambda _host, _port: ["93.184.216.34", "10.0.0.5"])
    with SessionLocal() as db, pytest.raises(http.SourceError):
        openlibrary.search(db, QUERY)
    assert world.fake.log == []


def test_a_name_that_does_not_resolve_is_an_error_and_rests_the_source(world: World,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "resolver", lambda _host, _port: [])
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "unreachable"
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").state == "resting"


def test_a_redirect_to_a_private_address_or_a_foreign_host_is_not_followed(world: World) -> None:
    for target in ("https://evil.example.com/x.jpg", "http://127.0.0.1/x.jpg", "https://10.0.0.5/x.jpg",
                   "https://openlibrary.org.evil.example.com/x.jpg", "file:///etc/hostname"):
        world.fake.log.clear()
        original = world.fake.handle_request

        def redirect(request: httpx.Request, target: str = target, original=original) -> httpx.Response:  # type: ignore[no-untyped-def]
            if request.headers["host"].startswith("covers."):
                world.fake.log.append(request)
                return httpx.Response(302, headers={"Location": target}, request=request)
            return original(request)

        world.fake.handle_request = redirect  # type: ignore[method-assign]
        with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
            http.request(db, "openlibrary", "https://covers.openlibrary.org/b/id/1-L.jpg")
        world.fake.handle_request = original  # type: ignore[method-assign]
        assert raised.value.code == "redirect", target
        assert world.fake.hosts() == ["covers.openlibrary.org"], f"nothing was asked at {target}"
        throttle.set_state("openlibrary", "")


def test_a_redirect_to_the_archive_is_followed_but_only_twice(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with SessionLocal() as db:
        answer = http.request(db, "openlibrary", "https://covers.openlibrary.org/b/id/1-L.jpg")
    assert answer.status_code == 200
    assert world.fake.hosts() == ["covers.openlibrary.org", "ia800000.us.archive.org"]
    # An endless chain stops.
    world.fake.log.clear()

    def endless(request: httpx.Request) -> httpx.Response:
        world.fake.log.append(request)
        return httpx.Response(302, headers={"Location": "https://ia800000.us.archive.org/next"}, request=request)

    monkeypatch.setattr(world.fake, "handle_request", endless)
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        http.request(db, "openlibrary", "https://covers.openlibrary.org/b/id/1-L.jpg")
    assert raised.value.code == "redirect"
    assert len(world.fake.log) == 3, "the first request and two redirects, and not one more"


def test_a_cover_follows_the_real_chain_over_archive_org_to_its_storage_host(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Open Library sends a cover to archive.org first and from there to a storage host: two redirects, both allowed."""
    original = world.fake.handle_request
    steps = {
        "covers.openlibrary.org": "https://archive.org/download/m_covers_0001/m_covers_0001_01.zip/0000000001-L.jpg",
        "archive.org": "https://ia800000.us.archive.org/view_archive.php?archive=/1/items/m_covers_0001.zip&file=1-L.jpg",
    }

    def chain(request: httpx.Request) -> httpx.Response:
        target = steps.get(host_of(request))
        if target is None:
            return original(request)
        world.fake.log.append(request)
        return httpx.Response(302, headers={"Location": target}, request=request)

    monkeypatch.setattr(world.fake, "handle_request", chain)
    with SessionLocal() as db:
        answer = http.request(db, "openlibrary", "https://covers.openlibrary.org/b/id/1-L.jpg")
    assert answer.status_code == 200
    assert world.fake.hosts() == ["covers.openlibrary.org", "archive.org", "ia800000.us.archive.org"]


def test_the_token_of_hardcover_does_not_go_where_a_redirect_leads(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardcover's redirects stay on its own hosts (none may lead elsewhere), so the token never leaves them."""
    token = world.connect_hardcover()
    seen: list[httpx.Request] = []

    def redirecting(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example.com/steal"}, request=request)

    monkeypatch.setattr(world.fake, "handle_request", redirecting)
    with SessionLocal() as db, pytest.raises(http.SourceError):
        hardcover.search(db, QUERY)
    assert [r.headers["host"] for r in seen] == ["api.hardcover.app"]
    assert all(token not in str(r.url) for r in seen)


def test_the_token_of_hardcover_is_sent_to_its_api_only_not_to_its_own_picture_host(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect to another host of Hardcover is followed, and the token does not follow it: the hosts it may go to
    are named by their own table, not by the table of the hosts a request may reach."""
    token = world.connect_hardcover()
    seen: list[tuple[str, str | None]] = []

    def redirecting(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["host"], request.headers.get("authorization")))
        if request.headers["host"] == "api.hardcover.app":
            return httpx.Response(307, headers={"Location": "https://assets.hardcover.app/x"}, request=request)
        return httpx.Response(200, json={"data": {}}, request=request)

    monkeypatch.setattr(world.fake, "handle_request", redirecting)
    with SessionLocal() as db:
        answer = http.request(db, "hardcover", hardcover.URL, method="POST", json_body={"query": "query{ me { id } }"},
                              headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 200
    assert seen == [("api.hardcover.app", f"Bearer {token}"), ("assets.hardcover.app", None)]


def test_a_request_to_a_host_that_is_not_the_api_never_carries_the_token(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    token = world.connect_hardcover()
    seen: list[str | None] = []

    def watching(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, content=b"x", request=request)

    monkeypatch.setattr(world.fake, "handle_request", watching)
    with SessionLocal() as db:
        http.request(db, "hardcover", "https://assets.hardcover.app/x.jpg", headers={"Authorization": f"Bearer {token}"})
    assert seen == [None]


# ---- The answer -------------------------------------------------------------------------------------------------


def test_an_answer_that_is_too_large_is_an_error(world: World) -> None:
    assert http.JSON_MAX < 3 * 1024 * 1024
    world.fake.bodies["openlibrary.org"] = b"x" * (3 * 1024 * 1024)
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "too_large"


def test_an_answer_that_is_no_json_is_an_error_and_counts_against_the_source(world: World) -> None:
    world.fake.bodies["openlibrary.org"] = b"<html>an error page</html>"
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "bad_answer"
    assert throttle.state_of("openlibrary").last_error == "bad_answer"


def test_an_answer_nested_too_deep_to_read_is_an_error_like_any_other_bad_answer(world: World) -> None:
    world.fake.bodies["openlibrary.org"] = b"[" * 200_000
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "bad_answer"
    assert throttle.state_of("openlibrary").last_error == "bad_answer"


def test_a_request_that_takes_too_long_is_cut_off(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    monkeypatch.setattr(world.fake, "handle_request", slow)
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "timeout"


def test_the_deadline_is_one_for_the_whole_request(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The time of the request is bounded in the socket (``outbound.DeadlineBackend``); here the budget is the number
    ``http.SECONDS`` hands to ``outbound.send``."""
    seen: list[float] = []
    original = http.outbound.send

    def spy(session, method, place, headers, *, deadline, ticks, **rest):  # type: ignore[no-untyped-def]
        seen.append(deadline - ticks())
        return original(session, method, place, headers, deadline=deadline, ticks=ticks, **rest)

    monkeypatch.setattr(http.outbound, "send", spy)
    ask_openlibrary()
    assert seen and 0 < seen[0] <= http.SECONDS


def test_429_makes_the_source_rest_for_as_long_as_it_asks(world: World) -> None:
    world.fake.status["openlibrary.org"] = 429
    world.fake.headers["openlibrary.org"] = {"Retry-After": "300"}
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        openlibrary.search(db, QUERY)
    assert raised.value.code == "rate_limited"
    asked = world.fake.count("openlibrary.org")
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").state == "resting"
        with pytest.raises(http.SourceOff):
            openlibrary.search(db, QUERY)
    assert world.fake.count("openlibrary.org") == asked, "a source that rests is not asked"
    world.advance(301)
    del world.fake.status["openlibrary.org"]
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").ready
        openlibrary.search(db, QUERY)


def test_429_without_a_wait_rests_the_source_a_minute_and_a_moment_named_is_kept_to(world: World) -> None:
    """A source that says 429 without ``Retry-After`` rests a minute (not two); one that names a moment rests until
    then (``Retry-After`` may be a date)."""
    world.fake.status["openlibrary.org"] = 429
    with SessionLocal() as db, pytest.raises(http.SourceError):
        openlibrary.search(db, QUERY)
    state = throttle.state_of("openlibrary")
    assert round((state.blocked_until - clock.now()).total_seconds()) == http.RETRY_AFTER_DEFAULT == 60
    world.advance(61)
    world.fake.headers["openlibrary.org"] = {"Retry-After": format_datetime(clock.now() + timedelta(seconds=600),
                                                                            usegmt=True)}
    with SessionLocal() as db, pytest.raises(http.SourceError):
        openlibrary.search(db, QUERY)
    state = throttle.state_of("openlibrary")
    assert 598 <= (state.blocked_until - clock.now()).total_seconds() <= 600


def test_one_answer_that_runs_out_of_time_does_not_rest_the_source_a_second_one_soon_does(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow answer now and then is how the catalogues are: one timeout leaves the source to be asked (by everybody), a
    second within ``SOFT_WINDOW`` makes it rest; one long after counts as a first again."""
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    monkeypatch.setattr(world.fake, "handle_request", slow)

    def once() -> str:
        with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
            http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=x")
        return raised.value.code

    assert once() == "timeout"
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").ready, "one timeout is no reason to rest"
    world.advance(throttle.SOFT_WINDOW + 1)
    assert once() == "timeout"
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").ready, "a timeout long after the last one is a first again"
    world.advance(10)
    assert once() == "timeout"
    with SessionLocal() as db:
        assert config.availability(db, "openlibrary").state == "resting"
    state = throttle.state_of("openlibrary")
    assert round((state.blocked_until - clock.now()).total_seconds()) == throttle.REST_FIRST


def test_errors_of_the_server_make_the_rest_longer_each_time(world: World) -> None:
    world.fake.status["openlibrary.org"] = 503
    rests = []
    for _ in range(4):
        with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
            http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=x")
        assert raised.value.code == "server"
        state = throttle.state_of("openlibrary")
        rests.append(round((state.blocked_until - clock.now()).total_seconds()))
        world.advance(rests[-1] + 1)
    assert rests == [30, 60, 120, 240]


def test_an_answer_that_went_well_ends_the_rest(world: World) -> None:
    world.fake.status["openlibrary.org"] = 500
    with SessionLocal() as db, pytest.raises(http.SourceError):
        openlibrary.search(db, QUERY)
    world.advance(31)
    del world.fake.status["openlibrary.org"]
    ask_openlibrary()
    state = throttle.state_of("openlibrary")
    assert state.fail_count == 0 and state.blocked_until is None and state.last_ok_at is not None


def test_a_404_is_an_answer_not_an_error(world: World) -> None:
    world.fake.status["openlibrary.org"] = 404
    assert ask_openlibrary() == []
    assert throttle.state_of("openlibrary").fail_count == 0


def test_hardcover_refusing_the_token_stops_the_source_until_it_is_connected_again(world: World) -> None:
    world.connect_hardcover()
    world.fake.status["api.hardcover.app"] = 401
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        hardcover.search(db, QUERY)
    assert raised.value.code == "token_invalid"
    asked = world.fake.count("api.hardcover.app")
    world.advance(86400 * 3)
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").state == "token_invalid", "days later it is still stopped"
        with pytest.raises(http.SourceOff):
            hardcover.search(db, QUERY)
    assert world.fake.count("api.hardcover.app") == asked
    # Connecting anew ends it.
    throttle.set_state("hardcover", "")
    del world.fake.status["api.hardcover.app"]
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").ready


def test_hardcover_saying_in_its_answer_that_the_token_expired_is_taken_the_same_way(world: World,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    world.connect_hardcover()
    monkeypatch.setattr(world.fake, "handle_request", lambda r: httpx.Response(
        200, json={"errors": [{"message": "Unable to verify token: JWT expired"}]}, request=r))
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        hardcover.search(db, QUERY)
    assert raised.value.code == "token_invalid"
    assert throttle.state_of("hardcover").state == "token_invalid"


@pytest.mark.parametrize("error", [
    {"message": "Could not verify JWT: JWTExpired"},
    {"message": "x", "extensions": {"code": "invalid-jwt", "path": "$"}},
    {"message": "Unable to verify token: expired"},
])
def test_hardcover_naming_the_token_as_refused_in_an_error_stops_the_source(
        world: World, monkeypatch: pytest.MonkeyPatch, error: dict[str, object]) -> None:
    world.connect_hardcover()
    monkeypatch.setattr(world.fake, "handle_request", lambda r: httpx.Response(200, json={"errors": [error]}, request=r))
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        hardcover.search(db, QUERY)
    assert raised.value.code == "token_invalid"
    assert throttle.state_of("hardcover").state == "token_invalid"


@pytest.mark.parametrize("error", [
    {"message": "field \"token\" not found in type: 'books'"},
    {"message": "The request expired before it could be answered"},
    {"message": "Authentication hint: nothing", "extensions": {"code": "validation-failed"}},
    {"message": "query depth exceeded"},
])
def test_hardcover_complaining_of_something_else_is_a_bad_answer_and_the_token_stays_good(
        world: World, monkeypatch: pytest.MonkeyPatch, error: dict[str, object]) -> None:
    world.connect_hardcover()
    monkeypatch.setattr(world.fake, "handle_request", lambda r: httpx.Response(200, json={"errors": [error]}, request=r))
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        hardcover.search(db, QUERY)
    assert raised.value.code == "bad_answer"
    assert throttle.state_of("hardcover").state != "token_invalid"


def test_the_request_names_nexbooks_and_sends_nothing_of_the_visitor(world: World) -> None:
    ask_openlibrary()
    request = world.fake.log[0]
    assert request.headers["user-agent"].startswith("nexbooks/") and "github.com/DerKezorm/nexbooks" in request.headers["user-agent"]
    for name in ("cookie", "authorization", "referer", "x-forwarded-for"):
        assert name not in request.headers
    assert request.method == "GET"


# ---- The turn ---------------------------------------------------------------------------------------------------


def test_open_library_is_asked_politely_a_burst_and_then_one_a_second(world: World) -> None:
    for number in range(10):
        with SessionLocal() as db:
            http.request(db, "openlibrary", f"https://openlibrary.org/search.json?q=n{number}")
    # A burst of 3, less the one the background keeps for a person's search, goes at once; the other 8 wait a second
    # each.
    assert 7.9 <= world.slept <= 8.1


def test_a_persons_search_takes_the_token_the_background_keeps(world: World) -> None:
    """The reading of a library asks a source as fast as it may and keeps one token of its bucket back: a person who
    searches in the meantime is asked at once, not after the background (a bucket of 3 for Open Library)."""
    for number in range(3):
        with SessionLocal() as db:
            http.request(db, "openlibrary", f"https://openlibrary.org/search.json?q=n{number}")
    assert 0.9 <= world.slept <= 1.1, "the third waits for a token: one stays for a person"
    slept = world.slept
    with SessionLocal() as db:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=person", interactive=True)
    assert world.slept == slept, "the token kept for a person is there"


def test_hardcover_keeps_to_sixty_a_minute_with_a_burst_of_ten(world: World) -> None:
    world.connect_hardcover()
    for number in range(30):
        with SessionLocal() as db:
            http.request(db, "hardcover", "https://api.hardcover.app/v1/graphql", method="POST",
                         json_body={"query": f"query{{ me {{ id }} }} # {number}"})
    # (A burst of 10 less the one kept for a person's search, then one a second.)
    assert 20.9 <= world.slept <= 21.1


def test_the_bucket_is_kept_in_the_database_so_a_restart_gives_no_free_burst(world: World) -> None:
    for number in range(3):
        with SessionLocal() as db:
            http.request(db, "openlibrary", f"https://openlibrary.org/search.json?q=n{number}", interactive=True)
    with SessionLocal() as db:
        row = db.get(SourceState, "openlibrary")
        assert row is not None and row.tokens < 1.0 and row.day_count == 3


def test_the_count_of_the_day_is_kept_and_a_new_day_starts_at_zero(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.LIMITS, "openlibrary", config.Limits(rate=1000.0, burst=1000, per_day=5))
    for number in range(5):
        with SessionLocal() as db:
            http.request(db, "openlibrary", f"https://openlibrary.org/search.json?q=n{number}")
    assert throttle.state_of("openlibrary").day_count == 5
    asked = world.fake.count("openlibrary.org")
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=again")
    assert raised.value.code == "daily_limit"
    assert world.fake.count("openlibrary.org") == asked, "past the limit of the day nothing is sent"
    world.days(1)
    assert throttle.state_of("openlibrary").day_count == 0
    with SessionLocal() as db:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=tomorrow")


def test_a_persons_request_does_not_wait_longer_than_a_few_seconds(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.LIMITS, "openlibrary", config.Limits(rate=0.1, burst=1, per_day=1000))
    with SessionLocal() as db:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=a", interactive=True)
        with pytest.raises(http.SourceError) as raised:
            http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=b", interactive=True)
    assert raised.value.code == "busy" and raised.value.values["retry_after"] >= 5
    assert world.slept == 0, "it did not wait"
    # The background waits as long as it takes.
    with SessionLocal() as db:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=c")
    assert world.slept >= 9


def test_two_requests_at_the_same_moment_do_not_take_the_same_token(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.LIMITS, "openlibrary", config.Limits(rate=0.0001, burst=3, per_day=1000))
    taken: list[float] = []
    barrier = threading.Barrier(12)

    def go() -> None:
        barrier.wait()
        taken.append(throttle.take("openlibrary"))

    threads = [threading.Thread(target=go) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for wait in taken if wait == 0) == 3, "exactly the three tokens of the burst were given out"
    assert throttle.state_of("openlibrary").day_count == 3


def test_the_server_stopping_ends_a_wait(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import jobs

    monkeypatch.setitem(config.LIMITS, "openlibrary", config.Limits(rate=0.1, burst=1, per_day=1000))
    with SessionLocal() as db:
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=a")
    jobs.stopping.set()
    try:
        with SessionLocal() as db, pytest.raises(jobs.Interrupted):
            http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=b")
    finally:
        jobs.stopping.clear()


# ---- The cache --------------------------------------------------------------------------------------------------


def test_a_question_is_asked_once_and_then_kept(world: World) -> None:
    world.fake.ol = [{"key": "/works/OL1W", "title": "The Quiet Orchard", "author_name": ["Mara Lindqvist"]}]
    first = ask_openlibrary()
    again = ask_openlibrary()
    assert [c.key for c in first] == [c.key for c in again] == ["OL1W"]
    assert world.fake.count("openlibrary.org") == 1


def test_an_answer_with_nothing_in_it_is_kept_for_a_day_and_one_with_a_book_for_a_month(world: World) -> None:
    ask_openlibrary()  # nothing found
    ask_openlibrary()
    assert world.fake.count("openlibrary.org") == 1
    world.advance(23 * 3600)
    ask_openlibrary()
    assert world.fake.count("openlibrary.org") == 1, "still good at 23 hours"
    world.advance(2 * 3600)
    world.fake.ol = [{"key": "/works/OL1W", "title": "The Quiet Orchard", "author_name": ["Mara Lindqvist"]}]
    assert ask_openlibrary() and world.fake.count("openlibrary.org") == 2, "the empty answer ran out after a day"
    world.days(29)
    ask_openlibrary()
    assert world.fake.count("openlibrary.org") == 2, "a book found is good for a month"
    world.days(2)
    ask_openlibrary()
    assert world.fake.count("openlibrary.org") == 3


def test_a_failed_request_is_not_kept(world: World) -> None:
    world.fake.status["openlibrary.org"] = 500
    with pytest.raises(http.SourceError):
        ask_openlibrary()
    del world.fake.status["openlibrary.org"]
    world.advance(31)
    world.fake.ol = [{"key": "/works/OL1W", "title": "The Quiet Orchard", "author_name": ["Mara Lindqvist"]}]
    assert ask_openlibrary()
    assert world.fake.count("openlibrary.org") == 2


def test_the_cache_is_bounded_the_expired_go_first_then_the_oldest(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_ROWS", 5)
    for number in range(4):
        cache.put("openlibrary", f"old-{number}", [], hit=False)
    world.advance(2 * 86400)
    for number in range(8):
        world.advance(1)
        cache.put("openlibrary", f"new-{number}", [{"n": number}], hit=True)
    cache.prune()
    with SessionLocal() as db:
        keys = sorted(db.scalars(select(SourceCache.key)))
    assert keys == ["new-3", "new-4", "new-5", "new-6", "new-7"]


def test_what_the_cache_holds_is_the_cut_down_answer_not_the_page(world: World) -> None:
    world.fake.ol = [{"key": "/works/OL1W", "title": "The Quiet Orchard", "author_name": ["Mara Lindqvist"],
                      "ratings_sortable": 4.5, "text": ["a long text"] * 50, "ia": ["x"] * 80}]
    ask_openlibrary()
    with SessionLocal() as db:
        value = db.scalars(select(SourceCache.value)).one()
    assert set(value[0]) <= set(openlibrary.Candidate.__dataclass_fields__)
    assert "ratings_sortable" not in value[0] and "text" not in value[0] and "ia" not in value[0]


def test_the_clients_send_only_titles_authors_and_isbns(world: World) -> None:
    """What goes out to the sources: the words of the question and nothing about the files, the library or the person."""
    world.connect_hardcover()
    secret = "Private Folder Name 4711"
    query = Query(kind="ebook", title="The Quiet Orchard", authors=["Mara Lindqvist"], language="en",
                  isbn="9783999900018")
    with SessionLocal() as db:
        openlibrary.search(db, query)
        audible.search(db, query, "us")
        hardcover.search(db, query)
        from app.services.sources import dnb

        world.settings(source_dnb=True)
        dnb.search(db, query)
    for request in world.fake.log:
        text = str(request.url) + request.content.decode("utf-8", "replace")
        assert secret not in text and "/books" not in text and ".epub" not in text
    for url in world.fake.urls("openlibrary.org"):
        assert url.split("?")[0].endswith("/search.json")
    # The ISBN goes as the ISBN, to the sources that search by it.
    assert any("isbn" in url and "9783999900018" in url for url in world.fake.urls("openlibrary.org"))
    assert any("9783999900018" in url for url in world.fake.urls("services.dnb.de"))
