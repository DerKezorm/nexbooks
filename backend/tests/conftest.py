"""Every test run gets its own empty data directory; nothing touches ``data/`` of the project."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from typing import Any

_DATA = tempfile.mkdtemp(prefix="nexbooks-tests-")
#: Where the libraries of a test lie: outside the data folder (a library may not lie in it), and the only place libraries
#: may be made (``NEXBOOKS_LIBRARY_ROOTS``).
_BOOKS = tempfile.mkdtemp(prefix="nexbooks-tests-books-")
os.environ["NEXBOOKS_DATA_DIR"] = _DATA
os.environ["NEXBOOKS_LIBRARY_ROOTS"] = _BOOKS
os.environ["NEXBOOKS_DISABLE_BACKGROUND"] = "1"
os.environ["NEXBOOKS_FRONTEND_DIST"] = os.path.join(_DATA, "no-frontend")
# Set, not removed: a value in the environment wins over a .env file in the project, a removed one does not.
os.environ["NEXBOOKS_LOCALES_DIR"] = os.path.join(_DATA, "locales")
os.environ["NEXBOOKS_LOG_LEVEL"] = ""
# Argon2 as cheap as it goes: the tests make many accounts. The strength itself is Argon2's business.
os.environ["NEXBOOKS_ARGON2_TIME"] = "1"
os.environ["NEXBOOKS_ARGON2_MEMORY_KIB"] = "1024"
os.environ["NEXBOOKS_ARGON2_PARALLELISM"] = "1"
# Never GitHub from a test: port 9 refuses at once.
os.environ["NEXBOOKS_UPDATE_URL"] = "http://127.0.0.1:9/releases/latest"
os.environ["NEXBOOKS_SECRET_KEY"] = "test-secret-key-for-the-test-run-only"
SETUP_CODE = "test-setup-code"
os.environ["NEXBOOKS_SETUP_TOKEN"] = SETUP_CODE

import shutil  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app import middleware  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import MEMBER, OPERATOR, Account, AppChanges, AppGone, AppSeen, Base, Setting  # noqa: E402
from app.security import SESSION_COOKIE, brake, hash_password, start_session  # noqa: E402
from app.services import (  # noqa: E402
    apikeys,
    backups,
    brakes,
    covers,
    device_request,
    jobs,
    memory,
    notices,
    oidc_store,
    pairing,
    passkeys,
    probe,
    push,
    scanner,
    settings_service,
    totp,
    updates,
)
from app.services.browse import cache as shelf_cache  # noqa: E402
from app.services.discover import owned  # noqa: E402
from app.services.procure import gate as procure_gate  # noqa: E402
from app.services.procure import indexers as procure_indexers  # noqa: E402
from app.services.procure import rations as procure_rations  # noqa: E402
from app.services.sources import http as sources_http  # noqa: E402
from app.vendor.nexoidc import attempt as nexoidc_attempt  # noqa: E402
from app.vendor.nexoidc import protocol as nexoidc_protocol  # noqa: E402

# The stand-in sign-in provider of the shared contract tests, for nexbooks's own tests around it (``oidc_helpers``).
from .oidc_helpers import provider  # noqa: E402, F401
from .procure_fakes import FakeNetwork  # noqa: E402

DATA_DIR = _DATA
BOOKS_DIR = _BOOKS
TAB = {"X-Nexbooks-Client": "tab-tests000"}


@pytest.fixture(scope="session", autouse=True)
def schema() -> None:
    init_db()


def _remove_book_entry(entry: os.DirEntry[str]) -> None:
    """Removes what a test left in the folder for libraries; a link (or a junction on Windows) goes alone, never with what
    it points to."""
    if entry.is_symlink() or entry.is_junction():
        try:
            os.unlink(entry.path)
        except OSError:
            os.rmdir(entry.path)
    elif entry.is_dir(follow_symlinks=False):
        shutil.rmtree(entry.path, ignore_errors=True)
    else:
        os.unlink(entry.path)


@pytest.fixture(autouse=True)
def clean_db(schema: None) -> Iterator[None]:
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        # Deleting the works wrote what went, and moved the count of changes the app follows: a test starts from none.
        for table in (AppGone, AppSeen, AppChanges):
            db.execute(delete(table))
        db.execute(delete(Setting))
        # nexbooks asks every account for a second factor from the start. Most tests are about something else and
        # sign in with a password alone; the tests of the second factor turn it on (``require_second_factor``).
        db.add(Setting(key="two_factor_required", value=False))
        # No test asks a source: the main switch is off unless a test of the sources turns it on (``sources_on``).
        db.add(Setting(key="sources_enabled", value=False))
        db.commit()
    # Backups of a test before (an automatic one of the same second) must not decide what another test prunes.
    shutil.rmtree(backups.folder(), ignore_errors=True)
    shutil.rmtree(covers.folder(), ignore_errors=True)
    # The other pictures too: a backup takes every picture of the data folder along.
    for leftover in ("people", "foreign-covers"):
        shutil.rmtree(os.path.join(_DATA, leftover), ignore_errors=True)
    # What a test put into the folder for libraries goes before the next one.
    for entry in os.scandir(_BOOKS):
        _remove_book_entry(entry)
    # The shared sign-in module: configured as at start-up, nothing remembered from a test before.
    oidc_store.configure()
    nexoidc_protocol.clear_caches()
    nexoidc_attempt.forget_used_states()
    brake.forget()
    # The themed shelves kept in memory belong to the database of the test before.
    shelf_cache.forget()
    pairing.code_watch.forget()
    device_request.lookup_watch.forget()
    owned.forget()
    brakes.forget()
    procure_indexers.forget()
    procure_rations.forget()
    middleware.forget_gate()
    memory.forget()
    totp.forget()
    updates.forget()
    apikeys.forget()
    notices.forget()
    push.forget()
    passkeys.forget()
    # A test that ran the background loop leaves the flag "the server is stopping" set when the loop ends; a worker
    # started in a later test would take it as its own stop.
    jobs.stopping.clear()
    yield
    notices.settle()
    app.dependency_overrides.clear()


class _BorrowedWorker:
    """The helper process of the scan, lent to one scan: it is not ended with it (starting one costs a second or two, and
    the tests scan a lot)."""

    def __init__(self, worker: probe.Worker) -> None:
        self.worker = worker

    def __enter__(self) -> probe.Worker:
        return self.worker

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture(scope="session")
def probe_worker() -> Iterator[probe.Worker]:
    worker = probe.Worker(stop=jobs.stopping.is_set)
    yield worker
    worker.close()


@pytest.fixture(autouse=True)
def lend_the_probe_worker(monkeypatch: pytest.MonkeyPatch, probe_worker: probe.Worker) -> None:
    """Every scan of a test uses the one helper process of the session (the tests of the helper itself make their own)."""
    monkeypatch.setattr(scanner, "Worker", lambda **_kwargs: _BorrowedWorker(probe_worker))


class _NoRealServices(httpx.BaseTransport):
    """The way out of the sources in every test, unless the test puts its own in (``fake_sources``): a request that gets
    here is a test that reached for a real service. It is answered with an error and noted; the test fails at its end."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url.host))
        return httpx.Response(599, request=request)


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch: pytest.MonkeyPatch) -> Iterator[_NoRealServices]:
    """No test asks a real source: the transport is a stand-in that fails the test, and every name resolves to a public
    address (so that a test with its own transport passes the check of where a request may go)."""
    guard = _NoRealServices()
    monkeypatch.setattr(sources_http, "transport", guard)
    monkeypatch.setattr(sources_http, "resolver", lambda _host, _port: ["93.184.216.34"])
    # The same for the way out to the indexers, Prowlarr and the download programs (``net`` puts stand-ins in).
    monkeypatch.setattr(procure_gate, "transport", guard)
    monkeypatch.setattr(procure_gate, "resolver", lambda _host, _port: ["10.99.0.5"])
    yield guard
    assert guard.asked == [], f"a test asked real services: {sorted(set(guard.asked))}"


PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def the_instance_lives_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """The day of the limits of the indexers begins at midnight of the instance's time zone; a test run does not depend
    on the zone of the machine it runs on. A test that wants another zone sets ``timezones.instance_zone`` itself."""
    from datetime import UTC

    from app.services import timezones

    monkeypatch.setattr(timezones, "instance_zone", lambda: UTC)


def require_second_factor(on: bool = True) -> None:
    """The operator's switch "require a second factor", as it stands from the start (on)."""
    with SessionLocal() as db:
        settings_service.save(db, {"two_factor_required": on})


def make_account(name: str, role: str = MEMBER, password: str = PASSWORD) -> Account:
    """An account with a password, straight into the database."""
    with SessionLocal() as db:
        row = Account(name=name, role=role, password_hash=hash_password(password))
        db.add(row)
        db.commit()
        db.expunge(row)
    return row


def sign_in(client: TestClient, account: Account) -> None:
    """The client carries a session of ``account`` from now on (a real one, as after signing in)."""
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        token = start_session(db, row, "127.0.0.1", "tests")
    client.cookies.set(SESSION_COOKIE, token)


def new_client(account: Account | None = None) -> TestClient:
    """Another browser, signed in as ``account`` when one is given. Close it (``with``) when done."""
    client = TestClient(app, base_url="http://testserver", headers=TAB)
    if account is not None:
        sign_in(client, account)
    return client


def person(name: str) -> TestClient:
    """A browser signed in as a new member ``name``."""
    return new_client(make_account(name))


def _operator(client: TestClient) -> Account:
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name="tester").one_or_none()
        if row is not None:
            db.expunge(row)
    if row is None:
        row = make_account("tester", OPERATOR)
    sign_in(client, row)
    return row


@pytest.fixture
def client() -> Iterator[TestClient]:
    # As the interface does: every request names its tab (changes are refused without it).
    with TestClient(app, base_url="http://testserver", headers=TAB) as test_client:
        yield test_client


@pytest.fixture
def push_service(monkeypatch: pytest.MonkeyPatch) -> object:
    """The stand-in of a push service (``fake_push.py``), added to the operator's list of push services, its name
    resolving to a public address. The check of where a message may go stays as it is."""
    from .fake_push import HOST, FakePushService, public_resolver

    fake = FakePushService()
    monkeypatch.setattr(push, "transport", fake.transport())
    monkeypatch.setattr(push, "resolver", public_resolver)
    with SessionLocal() as db:
        settings_service.save(db, {"push_hosts": [HOST]})
    return fake


@pytest.fixture
def operator(client: TestClient) -> Account:
    """The signed-in operator ``tester``."""
    return _operator(client)


@pytest.fixture
def account(client: TestClient) -> Account:
    """The same as ``operator``, under another name."""
    return _operator(client)


def pytest_unconfigure(config: pytest.Config) -> None:
    """The data folder of this run goes when the run ends: the log file is closed first (Windows keeps open files),
    then the folder with everything in it."""
    import logging

    for logger in (logging.getLogger(), logging.getLogger("uvicorn.error")):
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    from app.db import engine

    engine.dispose()
    shutil.rmtree(_DATA, ignore_errors=True)
    shutil.rmtree(_BOOKS, ignore_errors=True)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> object:
    """The sources switched on with faked services and a clock that jumps (``match_helpers.World``)."""
    from .match_helpers import World

    return World(monkeypatch)


@pytest.fixture
def shelves(monkeypatch: pytest.MonkeyPatch) -> object:
    """Two libraries, three people and the lists of the German market fetched (``discover_shelves.Shelves``)."""
    from .discover_shelves import Shelves

    return Shelves(monkeypatch)


@pytest.fixture
def browse(monkeypatch: pytest.MonkeyPatch) -> object:
    """The world of ``shelves`` with only the open sources on and the shelves of the German market fetched
    (``browse_helpers.Browse``)."""
    from .browse_helpers import Browse

    return Browse(monkeypatch)


class FakeClock:
    """The time of the pacing in a test: a wait is a jump of the clock, not a pause."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def ticks(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def pace(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Indexers are asked at most once in 2 seconds; in a test that wait costs nothing."""
    from app.services.procure import indexers as indexer_service

    fake = FakeClock()
    monkeypatch.setattr(indexer_service, "ticks", fake.ticks)
    monkeypatch.setattr(indexer_service, "sleep", fake.sleep)
    return fake


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch, no_real_services: _NoRealServices, pace: FakeClock) -> Iterator[FakeNetwork]:
    """The way out to indexers, Prowlarr and download programs ends in stand-ins (``procure_fakes``). Every name resolves
    to a private address, which the gate allows on purpose."""
    network = FakeNetwork()
    monkeypatch.setattr(procure_gate, "transport", network)
    yield network


@pytest.fixture
def watchers(monkeypatch: pytest.MonkeyPatch, net: FakeNetwork) -> Iterator[object]:
    """The world of watching an author (``watch_helpers.World``): a library, three people, the catalogue of an
    invented author with a book to come, an indexer that is only there to be switched on, and a clock that jumps."""
    from .watch_helpers import World, close

    found = World(monkeypatch, net)
    yield found
    close(found)


@pytest.fixture
def reading() -> Iterator[Any]:
    """The invented shelf of ``app_helpers`` with more on it, for the tests of e-readers (``ereader_helpers.Reading``)."""
    from .ereader_helpers import make_reading

    yield make_reading()


@pytest.fixture
def ada_browser(reading: Any) -> Iterator[TestClient]:
    """The browser of ``ada``, a member of the library ``Books`` of that shelf."""
    with new_client(reading.shelf.ada) as browser:
        yield browser
