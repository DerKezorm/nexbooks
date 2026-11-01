"""Nothing is loaded from another host: the Content Security Policy, read from real answers, names no source outside the
site itself. (The files of the interface are swept for foreign addresses by ``frontend/src/noForeignSources.test.ts``.)"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

#: The only sources a directive may carry: the site itself, nothing, inline styles, and pictures made in the browser.
ALLOWED = {"'self'", "'none'", "'unsafe-inline'", "data:", "blob:"}


def directives(policy: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            found[words[0]] = words[1:]
    return found


def sources_of(policy: str) -> list[tuple[str, str]]:
    return [(name, source) for name, values in directives(policy).items() for source in values]


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/does-not-exist", "/sw.js", "/"])
def test_the_policy_in_an_answer_names_no_source_but_the_site(client: TestClient, path: str) -> None:
    policy = client.get(path).headers["content-security-policy"]
    sources = sources_of(policy)
    assert sources, "a policy without sources would be read wrong by this test"
    for name, source in sources:
        assert source not in {"*", "http:", "https:", "ws:", "wss:"} and "//" not in source and "*" not in source, (name, source)
        assert source in ALLOWED, (name, source)
        assert source != "'unsafe-inline'" or name == "style-src", (name, source)  # scripts stay strict
    # Every kind of load is covered by a directive, and the fallback is the site itself.
    assert directives(policy)["default-src"] == ["'self'"]
    for name in ("img-src", "style-src", "font-src", "connect-src", "worker-src", "manifest-src"):
        assert name in directives(policy)


def test_the_checks_here_do_catch_a_foreign_source() -> None:
    """The sweep above is only worth something if it can fail: a policy with a foreign host does not pass it."""
    bad = "default-src 'self'; font-src 'self' data: https://fonts.gstatic.com"
    assert [source for _, source in sources_of(bad) if source not in ALLOWED] == ["https://fonts.gstatic.com"]
