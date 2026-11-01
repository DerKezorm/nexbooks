"""No note of how the project was worked on stays in the repository (``working_notes.py`` knows the marks).

Comments, docstrings, test names, texts and file names say what the code does and why. They do not name the building
block a thing came from, the finding it answers, the round of checking that found it, the prototype it was drawn from, a
private blueprint, a ticket number or the day it was decided. Words that are product terms stay: the review list, the
probe of a file, the blocklist, ``require_reviewer``, a stage of the docker build.

Like the other checks of the repository it reads the files git would take, and it runs on its own as well
(``python tests/working_notes.py``), so a commit hook can call it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from . import working_notes as notes
from .test_no_attribution import repository_files

#: The repository holds more than this; fewer means the listing went wrong.
FILES_FLOOR = 500
#: The marks, put together from pieces: written out, these lines would be what the check objects to.
BLOCK = "bl" + "ock "
FINDING = "find" + "ing "
REVIEWER = "re" + "viewer"
SHOULD_BE_FOUND = {
    "the name of a building block": [f"# ({BLOCK}S2-B4): the check", f"/* {BLOCK}B7 */", f"the {BLOCK}A1 builds", "S2" + "-B3 follows",
                                     "(the review of " + BLOCK + "S1c)", "kept per device (B8)"],
    "a later or earlier building block": ["comes with the next bl" + "ocks", "the bl" + "ocks after it", "in a later " + BLOCK],
    "a number of the work notes": ["#" + "job-215: the sentence", "(#" + "job-9)"],
    "the number of a finding": [FINDING + "11", FINDING + "M3 of its review", "Fu" + "nd 17 of the first trial", "pro" + "be 1, " + FINDING + "7"],
    "a round of checking": [f"the {REVIEWER}'s case", "the exam" + "iner changed the code", "security re" + "view of stage", "after the " + "exam" + "ination"],
    "a note of a day of the work": ["decided 06.10.2026", "(18.10.2026)", "measured on 13.10.2026"],
    "the prototype": ["as the mock" + "-up draws it", "the " + "Attra" + "ppe", "`attra" + "ppe/app.js`", "as the mo" + "ck shows"],
    "a private blueprint": ["(Bau" + "plan 01)", "Pflicht" + "test 35", "Durch" + "lauf point 2", "Stu" + "fe 2", "the shared blue" + "print"],
    "the notes of the work": ["see tools" + "/stufe2.md", "the test " + "instance", "docker" + "-live", "(stage " + "1 of nexbooks)"],
    "another project named as the source": ["after nex" + "crate's measurements", "taken over from nex" + "lore", "as in nex" + "mail", "built after nex" + "trmnl's"],
}
SHOULD_STAY = [
    "the review list of the books the matching is not sure about", "libraries.require_reviewer(db, uid, library_id)",
    "the probe of a file runs in a helper process", "the blocklist is shown to who may acquire",
    "a block of `expect` calls", "block a release for good", "a key named nexdeck", "the stage of a sign-in is setup",
    "an ASIN such as B0" + "ABCDEFGH", "the 2026-10-08 date in a test of the date format", "the corrections by hand of an edition",
    "blocks that outlive a work", "the second factor (stage setup)",
]


def found(line: str, path: str = "backend/app/example.py") -> list[str]:
    return notes.scan(path, line + "\n")


def test_no_file_of_the_repository_holds_a_note_of_the_work() -> None:
    files = repository_files()
    assert len(files) >= FILES_FLOOR, f"only {len(files)} files listed; is the listing looking at the repository?"
    names = [path.relative_to(notes.ROOT).as_posix() for path in files]
    findings, read = notes.scan_files(names)
    assert read >= 400, f"only {read} files read"
    listed = "\n".join(findings[:40])
    assert not findings, f"Notes of how the project was worked on (say what the code does and why, nothing else):\n{listed}"


@pytest.mark.parametrize(("what", "line"), [(what, line) for what, lines in SHOULD_BE_FOUND.items() for line in lines])
def test_the_check_finds_what_it_looks_for(what: str, line: str) -> None:
    result = found(line)
    assert result, f"not found: {line!r}"
    assert result[0].endswith(what), (line, result)


@pytest.mark.parametrize("line", SHOULD_STAY)
def test_the_check_leaves_product_words_alone(line: str) -> None:
    assert found(line) == []


def test_a_file_name_that_carries_a_block_is_found_and_a_plain_one_is_not() -> None:
    assert notes.scan("backend/tests/" + "b7" + "_helpers.py", "x = 1\n")
    assert notes.scan("backend/tests/test_thing_" + "s2" + ".py", "x = 1\n")
    assert notes.scan("backend/tests/test_review_api.py", "x = 1\n") == []
    assert notes.scan("frontend/src/review.css", "a { b: c }\n") == []
    assert notes.scan("backend/tests/reading_helpers.py", "x = 1\n") == []


def test_what_is_copied_unchanged_from_elsewhere_is_left_alone() -> None:
    line = "# " + BLOCK + "B2 and #" + "job-1\n"
    assert notes.scan("backend/app/vendor/nexoidc/flow.py", line) == []
    assert notes.scan("backend/tests/nexoidc/test_flow.py", line) == []
    assert notes.scan("backend/app/services/flow.py", line)


def run_on(folder: Path, text: str) -> subprocess.CompletedProcess[str]:
    """The command line over one file."""
    target = folder / "sample.py"
    target.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(Path(notes.__file__)), str(target)],
                          cwd=notes.ROOT, capture_output=True, text=True, check=False)


def test_the_command_line_says_so_with_its_exit_code(tmp_path: Path) -> None:
    clean = run_on(tmp_path, "# Reads the file and says which book it is.\n")
    assert clean.returncode == 0, clean.stdout
    dirty = run_on(tmp_path, "# (" + BLOCK + "S2-B3): reads the file.\n")
    assert dirty.returncode == 1, dirty.stdout
    assert "the name of a building block" in dirty.stdout


def test_a_docker_stage_is_a_stage() -> None:
    assert notes.scan("Dockerfile", "# --- Stage 2: runtime ---\n") == []
