"""When no feed can be read, the answer names what the deployment said.

A lookup session's `get` returns `(status, content, error)` -- an HTTP
status, or the exception that stopped the request reaching one. The
component lookup kept only the content, so a 401 on every feed, a
certificate that would not verify, and a community that genuinely holds
nothing all arrived as the same empty list.

The distinction is the whole answer. "Every request was refused, 401"
points at credentials; "certificate verify failed" points at a trust
store; "no feed named a component" points at the feeds themselves. A
console that cannot say which sends its user to read the wrong code.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.routes import lookup as lookup_routes
from connections_export.gui.routes._lookup import trust_deployment

REAL_BASE = "https://connections.example.corp"
UUID = "9f3a1b2c-0000-4d5e-8f01-abcdef123456"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo_delay=0)
    # As if a URL from it had been dropped: only a chosen deployment is asked.
    trust_deployment(app, REAL_BASE)
    return TestClient(app, base_url="http://127.0.0.1")


class _Session:
    """A lookup session whose every request answers `answer`."""

    def __init__(self, answer):
        self.answer = answer

    def get(self, url):
        return self.answer

    def redirect_location(self, url):
        return None


def _ask(client, monkeypatch, answer):
    """Every lookup answers `answer`, a `(status, content, error)` triple."""
    # Patched where it is USED: `lookup.py` imports the name, so rebinding
    # it on the module it came from would leave this call site untouched.
    monkeypatch.setattr(lookup_routes, "_lookup_session", lambda *a, **k: _Session(answer))
    return client.get(
        f"/api/community-components?community_uuid={UUID}&base_url={REAL_BASE}"
    ).json()


def test_a_refusal_reports_the_status(client, monkeypatch):
    body = _ask(client, monkeypatch, (401, b"", None))

    assert body["components"] == []
    assert "401" in body["detail"], body["detail"]
    assert body["transport"], "no record of what the deployment answered"
    assert all(entry["status"] == 401 for entry in body["transport"])


def test_a_request_that_never_arrived_reports_the_error(client, monkeypatch):
    body = _ask(client, monkeypatch, (None, b"", "certificate verify failed"))

    assert body["components"] == []
    assert "certificate verify failed" in body["detail"], body["detail"]


def test_the_urls_that_failed_are_named(client, monkeypatch):
    """So the reader can try one by hand, which is the fastest way to tell a
    wrong address from a refused one."""
    body = _ask(client, monkeypatch, (403, b"", None))

    urls = [entry["url"] for entry in body["transport"]]
    assert urls, "no URLs recorded"
    assert all(url.startswith(REAL_BASE) for url in urls), urls


def test_a_feed_that_answers_keeps_its_own_finding(client, monkeypatch):
    """A feed that answered and simply named nothing is a different finding
    from one that could not be read, and it must keep its own words -- an
    auxiliary lookup failing alongside it does not get to speak for the
    whole answer."""
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>none</title></feed>'
    )
    body = _ask(client, monkeypatch, (200, empty, None))

    assert body["components"] == []
    assert "every feed answered" in body["detail"], body["detail"]
