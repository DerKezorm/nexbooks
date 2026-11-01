"""Nothing real in the repository: no address of a home network, no mail address of anybody.

``test_no_attribution.py`` runs the project's own scanner (which knows the real names) where it exists. This file is the
part that holds everywhere, CI included, because it needs no secret list: addresses of the private ranges that a
home network uses in the 10.10 block, and mail addresses outside the reserved placeholder domains. The invented
data of the tests and the documentation uses ``example.com`` and ``127.0.0.1``.
"""

from __future__ import annotations

import hashlib
import re

from app.services.readers.text import fold

from .test_no_attribution import FLOOR, ROOT, repository_files, text_of

HOME_NETWORK = re.compile(r"(?<![0-9.])10\.10\.\d{1,3}\.\d{1,3}(?![0-9])")
MAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
#: Where a mail address may point: the domains reserved for examples, and the address GitHub gives to people who
#: keep theirs private (``<id>+<name>@users.noreply.github.com``).
FINE_DOMAINS = re.compile(r"^(?:[A-Za-z0-9-]+\.)*(?:example\.(?:com|org|net)|invalid|test|localhost|noreply\.github\.com)$", re.IGNORECASE)
#: A file name such as ``icon@2x.png`` looks like an address; so does a package at a version.
IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "webp", "gif", "svg", "avif"}
#: Lock files list packages as ``name@1.2.3`` and carry hashes; they hold no addresses of people.
SKIPPED = {"frontend/package-lock.json"}


def strangers_in(text: str) -> list[str]:
    found = []
    for match in MAIL.finditer(text):
        domain = match.group(1)
        if domain.rsplit(".", 1)[-1].lower() in IMAGE_SUFFIXES or FINE_DOMAINS.match(domain):
            continue
        found.append(match.group(0))
    return found


def test_no_file_holds_an_address_of_a_home_network_or_somebody_s_mail_address() -> None:
    files = repository_files()
    assert len(files) >= FLOOR, f"only {len(files)} files listed; is the listing looking at the repository?"
    found: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = text_of(path)
        if text is None or relative in SKIPPED:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if HOME_NETWORK.search(line):
                found.append(f"{relative}:{number}: address of a home network")
            if strangers_in(line):
                found.append(f"{relative}:{number}: a mail address outside the placeholder domains")
    assert not found, "Something real in the repository:\n" + "\n".join(found[:40])


def test_nothing_in_the_repository_carries_the_name_of_another_app() -> None:
    """The name of another app, its environment variables and its token prefixes do not appear. The
    shared sign-in module and its contract tests (copied unchanged) list the apps they serve."""
    old = "nex" + "paper"
    found: list[str] = []
    for path in repository_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(("backend/app/vendor/nexoidc/", "backend/tests/nexoidc/", "frontend/src/vendor/nexoidc/")):
            continue
        if relative == "frontend/package-lock.json" or old in relative.lower():
            found += [relative] if old in relative.lower() else []
            continue
        text = text_of(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if old in line.lower():
                found.append(f"{relative}:{number}")
    assert not found, "The old name is still there:" + chr(10) + chr(10).join(found[:40])


#: The first 20 hex digits of the SHA-256 of authors, titles, series, ISBNs and ASINs of real books (folded: no case,
#: accents or signs; one to four words in a row). Cut short on purpose: 80 bits tell a name as surely, and a longer run of
#: hex digits is what the secret scanners take for a key. The tests and the documentation invent theirs; a hash tells a name that slipped in without the list being
#: a list of the real names. Word by word, so a name is found in any line, in any spelling of its punctuation.
REAL_BOOKS = frozenset({
    "0cea094bf307b6eca74c",
    "0f5963b27fcaee4f1415",
    "1229b2cac4be39858f86",
    "15c09710ea232f0c9c3e",
    "16c19fe7aadb4679f194",
    "1bc4bdf438b0aea8d215",
    "254e38932fdb9fc27f82",
    "29ffaf8ac4f01ca67d2a",
    "3113ca662e636bbd35f4",
    "37bd6106c461e1aaa9b1",
    "3856586e28ad1fc06ece",
    "40496bebb81944edb543",
    "41fb5c6a5b9d1e4af832",
    "467cf3050e3a8b9abc5f",
    "59cb44578b83b7b878ee",
    "5b089a71362d4a52224d",
    "61c9cb5104c77b3f111a",
    "65d6efc6dcfdcbcca15d",
    "6a7fda0b22565e91533e",
    "6d2a011660148d96e9ff",
    "6db7bf580bfce39d797b",
    "76ff08d6e03f2b4af5ef",
    "8032a1888c9cc84c1059",
    "8097c53d285f85204768",
    "8fccf0a4b7c9d6140e88",
    "9123503b8b863583b362",
    "948762dfb0e496e8955a",
    "962a797ad6b023a9220c",
    "a68c033bdebe28632a66",
    "ae225491a8f1f855bf83",
    "b492dfe3c3a200a05df5",
    "b6ecee2776e4b673e758",
    "b7179675873799ab52f9",
    "bba2339ab2747c5511f7",
    "c807241bac4f8cb50e13",
    "cbbf241eebedba6fd5e6",
    "cd9957f87d1bae26d8d0",
    "ce237884d9a305c45988",
    "cf2f17930a6cfd92022c",
    "dce5860bd4d14d138a1f",
    "e3a32cbaa99d1d019a90",
    "e5901014384473b0a273",
    "e8717a2ebe1744017439",
    "ecd073ddce9fe50a3c0b",
    "ed0b5418ae549d8b88e3",
    "f2e5d762060797916b7d",
    "f531b8c312c7334a6b9b",
    "f62289c7824455ea1beb",
    "f8a8f21a3505b68568af",
    "fc953bfd4f7b475f7758",
})
LONGEST = 4
HYPHEN_IN_NUMBER = re.compile(r"(?<=\d)-(?=\d)")


def real_books_in(line: str) -> list[int]:
    """The place (word number) of every run of words in ``line`` that is one of the hashed names."""
    words = fold(HYPHEN_IN_NUMBER.sub("", line)).split()
    found = []
    for start in range(len(words)):
        for length in range(1, LONGEST + 1):
            run = words[start : start + length]
            if len(run) == length and hashlib.sha256(" ".join(run).encode("utf-8")).hexdigest()[:20] in REAL_BOOKS:
                found.append(start)
                break
    return found


def test_no_file_names_a_real_author_a_real_book_or_a_real_isbn() -> None:
    files = repository_files()
    assert len(files) >= FLOOR, f"only {len(files)} files listed; is the listing looking at the repository?"
    found: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = text_of(path)
        if text is None or relative in SKIPPED:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if real_books_in(line):
                found.append(f"{relative}:{number}: a name or number of a real book")
    listed = "\n".join(found[:40])
    assert not found, f"Something real in the repository (invent the names of tests and documentation):\n{listed}"


def test_the_check_for_real_books_finds_them_in_any_spelling() -> None:
    # Put together from pieces: written out, these would be what the check objects to.
    name = "Dan" + " Br" + "own"
    assert real_books_in(f"author = {name}") == [1]
    assert real_books_in("Br" + "own, Dan" + " - x")
    assert real_books_in("the " + "J. K. " + "Row" + "ling book")
    assert real_books_in("isbn 978-3-16-" + "148410-0")
    assert real_books_in("Der T" + chr(0xE4) + "to" + "wierer von " + "Ausch" + "witz")
    assert not real_books_in("Mara Lindqvist wrote The Quiet Orchard, ISBN 978-3-9999-0010-0")
    assert not real_books_in("an Origin header and a red fox")


def test_the_check_knows_what_it_looks_for() -> None:
    # Put together from pieces: written out, these lines would be what the check (and the commit hook) object to.
    net = "10." + "10."
    assert HOME_NETWORK.search(f"ssh {net}11.128")
    assert HOME_NETWORK.search(f"http://{net}10.65:8420")
    assert not HOME_NETWORK.search("version 110.10.1.2 and 10.1.1.1 and 127.0.0.1")
    stranger = "jule@" + "gmail.com"
    assert strangers_in(f"write to {stranger}") == [stranger]
    other = "tom@" + "familie-beispiel.de"
    assert strangers_in(f"mail me: {other}.") == [other]
    hidden = "12345+x@" + "users.noreply." + "github.com"
    assert strangers_in(f"jule@example.com, tom@mail.example.org, {hidden}") == []
    assert strangers_in("icon@2x.png and react@19.2.0 and @milkdown/kit") == []
