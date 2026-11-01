"""The notes of how the project was worked on do not belong in the repository.

A comment says what the code does and why, never who found it, in which round of the work or on which day. This module
knows the marks such notes leave: the names of the building blocks and of the rounds of checking, the numbers of
findings, ticket numbers, the prototype the pages were drawn from, the private blueprints, other projects of the same
family named as a source, and file names that carry any of them.

It is used by ``test_no_working_notes.py`` and runs on its own as well, over the files named on the command line (a
commit hook can call it with the files of the commit) or, with none, over every file git would take:

    python tests/working_notes.py [file ...]

The exit code is 1 when something was found. Words that are product terms stay allowed on purpose: the *review* list of
books, the *probe* of a file, the *blocklist*, ``require_reviewer``, a docker *stage*.

The marks are put together from pieces where they would be written out, so that this file does not trip itself.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Copied from elsewhere and kept unchanged (the shared sign-in module and its tests), lock files and generated data
#: of other tools. What differs from app to app is checked.
EXEMPT = (
    "backend/app/vendor/",
    "backend/tests/nexoidc/",
    "frontend/src/vendor/",
    "frontend/package-lock.json",
    "LICENSE",
)
#: This module and its test name the marks to look for.
OWN = ("backend/tests/working_notes.py", "backend/tests/test_no_working_notes.py")
BINARY = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".gif", ".pdf", ".zip", ".epub", ".mp3", ".m4b")

_ID = r"(?:[ABX][0-9]|S[12][abc]?(?:-B[0-9])?)"
_KIN = "|".join(("nex" + name) for name in (
    "crate", "lore", "mail", "sift", "paper", "diary", "canvas", "view", "brand", "tasks", "suite", "beat", "pulse",
    "trmnl", "jukebox", "base", "scan"))

#: (what it is, the mark)
MARKS: list[tuple[str, re.Pattern[str]]] = [
    ("the name of a building block", re.compile(r"\b[Bb]locks? " + _ID + r"\b|\b[Bb]locks? Stage\b")),
    ("the name of a building block", re.compile(r"\bS[12][abc]?-B[0-9]\b")),
    ("the name of a building block", re.compile(r"\((?:the )?(?:review )?(?:[ABX][0-9]|S[12][abc]?)\)")),
    ("the name of a building block", re.compile(r"\b(?:the work after|the review of|review of|reviews of|correction of) " + _ID + r"\b")),
    ("a later or earlier building block", re.compile(r"\b(?:later|next|following|earlier|last|first) blocks?\b|\bthe blocks? (?:after|before|that|which)\b|\bthis block\b")),
    ("a number of the work notes", re.compile(r"#job-[0-9]+")),
    ("the number of a finding", re.compile(r"\b[Ff]indings? (?:[A-Z]-?[0-9]+|[0-9]+)\b|\b[Ff]indings? of the\b|\b(?:probe|trial) [0-9]+, finding\b|\bFund [0-9]+\b|\b[Bb]efund")),
    ("the number of a finding", re.compile(r"#{1,4} -{2,}.*\([A-Z][0-9]\) -{2,}|\bfinding [A-Z][0-9]\b")),
    ("a round of checking", re.compile(r"\b[Rr]eviewers?(?:'s)?\b|\bexaminers?\b|\bexamination\b|\b(?:security )?review (?:before|of|found|at)\b|\bthe review found\b|\bcorrections? (?:of|after) (?:the )?(?:block|review|examiner|first round|[ABS][0-9])|\bthe first round\b")),
    ("a note of a day of the work", re.compile(r"\b(?:decided|measured|recorded|found) (?:on |at )?[0-9]{1,2}\.[0-9]{1,2}\.20[0-9]{2}\b|\([0-9]{1,2}\.[0-9]{1,2}\.20[0-9]{2}\)")),
    ("the prototype", re.compile(r"\bmock-?ups?\b|\bAttrappe\b|\battrappe[/\-]|\bthe mock\b|\bmock's\b", re.IGNORECASE)),
    ("a private blueprint", re.compile(r"\bBauplan|\bbauplaene\b|\bPflichttest|\bDurchlauf\b|\bthe shared blueprint\b|\bStufe [0-9]")),
    ("the notes of the work", re.compile(r"(?<![A-Za-z./-])tools/(?!make_|seed_)|\b(?:docker-dev|docker-live)\b|\btest instance\b|\bstage [0-9]\b", re.IGNORECASE)),
    ("another project named as the source", re.compile(r"\b(?:after|from|as in|like|taken over from|built after|copied from|by the tests of|as) (?:the )?(?:" + _KIN + r")(?:'s)?\b", re.IGNORECASE)),
    ("another project named as the source", re.compile(r"\b(?:" + _KIN + r")'s (?:measurements|accounts|backup|update check|test bench|code)\b", re.IGNORECASE)),
]
#: A file name that carries the name of a block or a round of checking.
NAME_MARKS = re.compile(r"(?:^|[/_.-])(?:b[0-8]|s[12][abc]?|a[123])(?=[/_.-]|$)|(?:^|[/_])(?:stufe|attrappe|auftrag)(?=[/_.]|$)")
#: Files that are allowed to say what the marks look for (an exact line is allowed by listing the file and the text).
ALLOWED: dict[str, tuple[str, ...]] = {
    # The docker build has stages; ``docker-live`` etc. are not named there.
    "Dockerfile": ("stage",),
}


def exempt(relative: str) -> bool:
    return relative.startswith(EXEMPT) or relative in OWN


def scan(relative: str, text: str) -> list[str]:
    """The marks in one file, as ``path:line: what``."""
    if exempt(relative):
        return []
    found: list[str] = []
    if NAME_MARKS.search(relative):
        found.append(f"{relative}: the file name carries the name of a block or a round of checking")
    skipped = ALLOWED.get(relative, ())
    for number, line in enumerate(text.splitlines(), 1):
        for what, mark in MARKS:
            if mark.search(line) and not any(word in line.lower() for word in skipped):
                found.append(f"{relative}:{number}: {what}")
                break
    return found


def files_in_git() -> list[str]:
    listing = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    return [name for name in sorted(set(listing.split("\0"))) if name and (ROOT / name).is_file()]


def text_of(path: Path) -> str | None:
    if path.suffix.lower() in BINARY:
        return None
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def scan_files(names: list[str]) -> tuple[list[str], int]:
    """The marks in the files, and how many files were read."""
    found: list[str] = []
    read = 0
    for name in names:
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        text = text_of(path)
        if text is None:
            found += scan(relative, "")
            continue
        read += 1
        found += scan(relative, text)
    return found, read


def main(arguments: list[str]) -> int:
    names = arguments or files_in_git()
    found, _read = scan_files(names)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
