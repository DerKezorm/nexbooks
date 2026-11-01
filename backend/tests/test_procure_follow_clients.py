"""Following the jobs in the five download programs: the state of a job as the program says it, in
nexbooks's words; every job of the category, whoever put it there; taking a job away; the seed goal of a torrent. The
stand-ins keep the jobs in the program's own words (``procure_fakes``)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.procure.clients import ClientError, Target, open_client

from .procure_fakes import FakeDeluge, FakeNetwork, FakeNzbget, FakeQbittorrent, FakeSabnzbd, FakeTransmission

SECRET = secrets.token_hex(12)
USER = "reader" + secrets.token_hex(2)
HASH = "ab" * 20
OTHER_HASH = "cd" * 20
NAME = "Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"


@dataclass
class Kit:
    kind: str
    fake: Any
    target: Target
    ref: str
    #: Put a job in the program: working at ``percent``.
    working: Callable[[int], None]
    #: The job is done and lies at this path.
    done: Callable[[str], None]
    #: A job in the category nobody asked for; the program's name for it.
    foreign: Callable[[str], str]
    #: A job in another category.
    elsewhere: Callable[[], None]
    failed: Callable[[], None] | None
    #: What the program says the folder of a finished job is called.
    folder: str


def make(kind: str, net: FakeNetwork) -> Kit:
    host = f"{kind}.example.com"
    if kind == "sabnzbd":
        fake = net.add(FakeSabnzbd(host, SECRET))
        fake.categories.append({"name": "nexbooks", "dir": "nexbooks", "pp": "3"})
        ref = "SABnzbd_nzo_abc"

        def working(percent: int) -> None:
            fake.jobs[ref] = ("nexbooks", NAME, b"")
            fake.progress(ref, percent)
            fake.put(ref, filename=NAME, cat="nexbooks")

        def done(path: str) -> None:
            fake.jobs[ref] = ("nexbooks", NAME, b"")
            fake.finish(ref, path)

        def elsewhere() -> None:
            fake.jobs["other"] = ("movies", "Some.Movie", b"")
            fake.put("other", where="queue", filename="Some.Movie", cat="movies", status="Downloading",
                     percentage="3", mb="5", mbleft="4")

        return Kit(kind, fake, Target(kind, f"http://{host}:8080", "", SECRET, "nexbooks"), ref, working, done,
                   lambda name: fake.foreign_job(name), elsewhere, lambda: fake.fail(ref),
                   "/downloads/complete/nexbooks")
    if kind == "nzbget":
        fake = net.add(FakeNzbget(host, USER, SECRET))

        def working(percent: int) -> None:
            fake.put(7, NZBName=NAME, Status="DOWNLOADING", RemainingSizeLo=10_000_000 * (100 - percent) // 100)

        def done(path: str) -> None:
            fake.put(7, where="history", Name=NAME, Status="SUCCESS/ALL", FinalDir=path)

        def foreign(name: str) -> str:
            fake.put(8, NZBName=name, Status="DOWNLOADING")
            return "8"

        def failed() -> None:
            fake.put(7, where="history", Name=NAME, Status="FAILURE/PAR", FinalDir="/downloads/dst/nexbooks/x")

        return Kit(kind, fake, Target(kind, f"http://{host}:6789", USER, SECRET, "nexbooks"), "7", working, done,
                   foreign, lambda: fake.put(9, NZBName="Other", Category="movies"), failed,
                   "/downloads/dst/nexbooks")
    if kind == "qbittorrent":
        fake = net.add(FakeQbittorrent(host, USER, SECRET))

        def foreign(name: str) -> str:
            fake.put(OTHER_HASH, name=name)
            return OTHER_HASH

        return Kit(kind, fake, Target(kind, f"http://{host}:8081", USER, SECRET, "nexbooks"), HASH,
                   lambda percent: fake.put(HASH, name=NAME, progress=percent / 100, state="downloading"),
                   lambda path: fake.finish(HASH, path), foreign,
                   lambda: fake.put("ef" * 20, name="Other", category="movies"), None, "/downloads/nexbooks")
    if kind == "transmission":
        fake = net.add(FakeTransmission(host, USER, SECRET))

        def working(percent: int) -> None:
            fake.put(HASH, name=NAME, status=4, percentDone=percent / 100, leftUntilDone=100 - percent)

        def done(path: str) -> None:
            fake.put(HASH, name=NAME, status=6, percentDone=1.0, leftUntilDone=0, downloadDir=path)

        def foreign(name: str) -> str:
            fake.put(OTHER_HASH, name=name)
            return OTHER_HASH

        return Kit(kind, fake, Target(kind, f"http://{host}:9091", USER, SECRET, "nexbooks"), HASH, working, done,
                   foreign, lambda: fake.put("ef" * 20, name="Other", labels=["movies"],
                                             downloadDir="/data/torrents/movies"), None, "/data/torrents")
    fake = net.add(FakeDeluge(host, SECRET))
    fake.plugins.append("Label")
    fake.labels.append("nexbooks")

    def working(percent: int) -> None:
        fake.put(HASH, name=NAME, progress=float(percent), state="Downloading")

    def done(path: str) -> None:
        fake.put(HASH, name=NAME, progress=100.0, state="Seeding", save_path=path, is_finished=True)

    def foreign(name: str) -> str:
        fake.put(OTHER_HASH, name=name)
        return OTHER_HASH

    return Kit(kind, fake, Target(kind, f"http://{host}:8112", "", SECRET, "nexbooks"), HASH, working, done, foreign,
               lambda: fake.put("ef" * 20, name="Other", label="movies"), None, "/downloads/deluge")


KINDS = ["sabnzbd", "nzbget", "qbittorrent", "transmission", "deluge"]
USENET = ["sabnzbd", "nzbget"]
TORRENT = ["qbittorrent", "transmission", "deluge"]


def jobs_of(kit: Kit) -> dict[str, Any]:
    with open_client(kit.target) as client:
        return client.jobs([kit.ref])  # type: ignore[no-any-return]


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_that_works_is_downloading_with_its_progress(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.working(40)
    job = jobs_of(kit)[kit.ref]
    assert job.state == "downloading" and job.ref == kit.ref
    assert job.progress is not None and 39 <= job.progress <= 41, job
    assert job.path is None


@pytest.mark.parametrize("kind", KINDS)
def test_a_finished_job_is_completed_with_the_path_the_program_names(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.done(f"{kit.folder}/{NAME}")
    job = jobs_of(kit)[kit.ref]
    assert job.state == "completed" and job.progress == 100.0
    assert job.path is not None and job.path.replace("\\", "/").endswith(NAME), job


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_the_program_does_not_know_is_missing_not_failed(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    assert jobs_of(kit) == {}


@pytest.mark.parametrize("kind", KINDS)
def test_the_category_lists_every_job_in_it_and_only_those(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.working(10)
    stranger = kit.foreign("Somebody.Elses.Book.EPUB")
    kit.elsewhere()
    with open_client(kit.target) as client:
        listed = client.category_jobs()
    refs = sorted(item.job.ref for item in listed)
    assert refs == sorted([kit.ref, stranger]), listed
    assert {item.name for item in listed} >= {"Somebody.Elses.Book.EPUB"}


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_is_taken_away_and_a_job_that_is_gone_is_no_failure(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.working(10)
    with open_client(kit.target) as client:
        client.remove(kit.ref, delete_files=True)
        assert client.jobs([kit.ref]) == {}
        client.remove(kit.ref, delete_files=True)


@pytest.mark.parametrize("kind", USENET)
def test_a_failed_job_of_a_usenet_program_says_why(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.working(90)
    assert kit.failed is not None
    kit.failed()
    job = jobs_of(kit)[kit.ref]
    assert job.state == "failed" and job.reason == "client_failed"
    assert job.detail in ("incomplete", "repair_failed"), job


def test_sabnzbd_reports_unpacking_as_work_and_a_full_disk_as_a_problem(net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.done("/x")
    kit.fake.put(kit.ref, status="Extracting")
    assert jobs_of(kit)[kit.ref].state == "downloading"
    kit.fake.fail(kit.ref, "Unpacking failed, write error or disk is full?")
    job = jobs_of(kit)[kit.ref]
    assert job.state == "problem" and job.problem == "no_space"
    kit.fake.fail(kit.ref, "Unpacking failed, archive requires a password")
    assert jobs_of(kit)[kit.ref].detail == "password"


def test_sabnzbd_keeps_the_folders_it_may_have_left_behind(net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.working(5)
    kit.fake.fail(kit.ref)
    kit.fake.put(kit.ref, storage="/downloads/complete/nexbooks/_FAILED_" + NAME)
    assert jobs_of(kit)[kit.ref].leftovers == ("/downloads/complete/nexbooks/_FAILED_" + NAME,)


def test_a_queue_that_is_paused_shows_its_jobs_as_paused(net: FakeNetwork) -> None:
    kit = make("nzbget", net)
    kit.working(10)
    kit.fake.paused = True
    assert jobs_of(kit)[kit.ref].state == "paused"


def test_qbittorrent_without_a_content_path_is_a_path_problem_and_a_stalled_one_is_a_hint(net: FakeNetwork) -> None:
    kit = make("qbittorrent", net)
    kit.fake.put(HASH, state="uploading", progress=1.0, content_path="/downloads", save_path="/downloads")
    job = jobs_of(kit)[HASH]
    assert job.state == "problem" and job.problem == "path_not_found"
    kit.fake.put(HASH, state="stalledDL", progress=0.3, content_path="")
    job = jobs_of(kit)[HASH]
    assert job.state == "downloading" and job.problem == "stalled"
    kit.fake.put(HASH, state="error")
    assert jobs_of(kit)[HASH].problem == "client_error"


def test_a_job_name_that_is_not_a_job_is_not_trusted(net: FakeNetwork) -> None:
    kit = make("qbittorrent", net)
    kit.fake.put(HASH, progress="soon", size="big", eta="never", state=5, content_path=["x"])
    # Garbage in every field: no crash, a job that is working.
    job = jobs_of(kit).get(HASH)
    assert job is None or job.state in ("downloading", "queued", "paused")


@pytest.mark.parametrize("kind", TORRENT)
def test_the_seed_goal_of_a_filed_torrent_is_read(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.done(f"{kit.folder}/{NAME}")
    if kind == "qbittorrent":
        kit.fake.put(HASH, ratio=1.4, seeding_time=7200, state="stoppedUP", max_ratio=1.0)
    elif kind == "transmission":
        kit.fake.put(HASH, uploadRatio=1.4, secondsSeeding=7200, status=0, isFinished=True)
    else:
        kit.fake.put(HASH, ratio=1.4, seeding_time=7200, state="Paused", stop_at_ratio=True, stop_ratio=1.0)
    with open_client(kit.target) as client:
        info = client.seeding([HASH])[HASH]
    assert info.done and info.stopped and info.goal_reached
    assert info.ratio == pytest.approx(1.4) and info.seeding_seconds == 7200


@pytest.mark.parametrize("kind", ["transmission", "deluge"])
def test_transmission_and_deluge_are_stopped_by_nexbooks(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.done(f"{kit.folder}/{NAME}")
    with open_client(kit.target) as client:
        client.stop(HASH)
    assert (HASH in kit.fake.stopped) if kind == "transmission" else (HASH in kit.fake.paused)


def test_an_unreachable_program_is_an_error_not_an_empty_answer(net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    net.down.add(kit.fake.host)
    with pytest.raises(ClientError) as raised, open_client(kit.target) as client:
        client.jobs([kit.ref])
    assert raised.value.code == "client_unreachable"


def test_the_news_servers_tell_how_long_they_keep_articles(net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.fake.servers = [{"enable": 1, "retention": 3000}, {"enable": 1, "retention": 1500}, {"enable": 0, "retention": 0}]
    with open_client(kit.target) as client:
        assert client.usenet_retention() == 3000
    kit.fake.servers = [{"enable": 1, "retention": 0}, {"enable": 1, "retention": 800}]
    with open_client(kit.target) as client:
        assert client.usenet_retention() is None
    nz = make("nzbget", net)
    nz.fake.config += [{"Name": "Server1.Active", "Value": "yes"}, {"Name": "Server1.Retention", "Value": "2500"}]
    with open_client(nz.target) as client:
        assert client.usenet_retention() == 2500
