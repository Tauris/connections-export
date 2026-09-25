"""Discovering a community's components: fast enough, and visibly at work.

On a real deployment the discovery timed out, and the timeout was the
console's: it gave up at 35 seconds while the answer was still on its way.
Three things made the answer that slow, and each looked like the others:

- every one of discovery's ten to fifteen requests built its own client, so
  each was a full integrated-auth handshake before the request it was for;
- that client was paced like a crawl, waiting a second between requests
  that a lookup has no reason to space out;
- and nothing reported progress, so the only signal available to the
  console was silence -- which it read, wrongly, as failure.

These pin the three fixes: one session for the whole discovery, unpaced;
progress reported phase by phase and request by request; and a stream that
carries it, ending in exactly the answer the plain route gives.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from connections_export.crawler.community import discover_components
from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.routes import _lookup as lookup_support
from connections_export.gui.routes import lookup as lookup_routes
from connections_export.gui.routes._lookup import trust_deployment

REAL_BASE = "https://connections.example.corp"
UUID = "9f3a1b2c-0000-4d5e-8f01-abcdef123456"

EMPTY_FEED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom"><title>none</title></feed>'
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo_delay=0)
    # As if a URL from it had been dropped: only a chosen deployment is asked.
    trust_deployment(app, REAL_BASE)
    return TestClient(app, base_url="http://127.0.0.1")


# --- one session, unpaced -------------------------------------------------


def test_the_whole_discovery_opens_one_session_not_one_per_request(client, monkeypatch):
    """Ten to fifteen handshakes became one. Counted at the seam that opens
    a session, because that is what a handshake costs."""
    opened = []

    class _Session:
        def get(self, url):
            return 200, EMPTY_FEED, None

        def redirect_location(self, url):
            return None

    def open_session(*a, **k):
        opened.append(1)
        return _Session()

    monkeypatch.setattr(lookup_routes, "_lookup_session", open_session)
    client.get(f"/api/community-components?community_uuid={UUID}&base_url={REAL_BASE}")

    assert len(opened) == 1


def test_a_lookup_session_is_unpaced_and_patient(monkeypatch):
    """A lookup is a handful of reads, not a crawl. It must not wait a second
    between them, and it may wait longer than a crawl for each one -- a slow
    deployment answering in 40 seconds is answering."""
    built = {}

    class _Client:
        pass

    def capture(config, env, **overrides):
        built.update(overrides)
        return _Client()

    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", capture)
    app = make_app()
    app.state.live_cookies = None
    trust_deployment(app, REAL_BASE)

    lookup_support._lookup_session(app, REAL_BASE, "kerberos")

    assert built["min_interval"] == 0.0
    assert built["timeout"] == lookup_support.LOOKUP_TIMEOUT_SECONDS
    assert lookup_support.LOOKUP_TIMEOUT_SECONDS >= 120


def test_one_shot_lookups_keep_their_shorter_patience(monkeypatch):
    """`_authed_lookup` is one request; it need not wait two minutes for
    it. The longer timeout belongs to the session a discovery holds."""
    built = {}

    def capture(config, env, **overrides):
        built.update(overrides)
        raise RuntimeError("stop: only the overrides are being checked")

    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", capture)
    app = make_app()
    app.state.live_cookies = None
    trust_deployment(app, REAL_BASE)

    lookup_support._authed_lookup(app, f"{REAL_BASE}/x", REAL_BASE, "kerberos")

    assert built["timeout"] == 30.0


# --- progress -------------------------------------------------------------


def test_discovery_reports_each_phase_as_it_begins():
    steps = []
    discover_components(
        community_uuid=UUID,
        base_url=REAL_BASE,
        fetch=lambda url: EMPTY_FEED,
        progress=steps.append,
    )

    assert steps, "no progress reported at all"
    assert steps[0].startswith("reading the blogs, forums and wikis lists")
    # The fallbacks ran (every feed was empty), and said so.
    assert any("another way" in step for step in steps)
    assert any("front page" in step for step in steps)


def test_a_listener_that_raises_does_not_stop_the_discovery():
    """Progress is a courtesy to whoever is watching. The answer is not."""

    def explode(_):
        raise RuntimeError("the listener is broken")

    answer = discover_components(
        community_uuid=UUID,
        base_url=REAL_BASE,
        fetch=lambda url: EMPTY_FEED,
        progress=explode,
    )

    assert "components" in answer


# --- the stream -----------------------------------------------------------


def _stream(client, monkeypatch, session) -> list[dict]:
    monkeypatch.setattr(lookup_routes, "_lookup_session", lambda *a, **k: session)
    events = []
    with client.stream(
        "GET", f"/api/community-components/stream?community_uuid={UUID}&base_url={REAL_BASE}"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)
                for line in raw.split("\n"):
                    field, _, value = line.partition(": ")
                    if field == "data":
                        events.append(json.loads(value))
    return events


class _EmptyFeeds:
    def get(self, url):
        return 200, EMPTY_FEED, None

    def redirect_location(self, url):
        return None


def test_the_stream_shows_its_working_and_ends_in_the_answer(client, monkeypatch):
    events = _stream(client, monkeypatch, _EmptyFeeds())

    kinds = [event["type"] for event in events]
    assert "step" in kinds, "no phase was reported"
    assert "request" in kinds, "no request was reported"
    assert kinds[-1] == "result", "the stream did not end in the answer"
    assert kinds.count("result") == 1

    requests = [event for event in events if event["type"] == "request"]
    # Numbered, so a watcher can see the count climb -- activity, not silence.
    assert [event["n"] for event in requests] == list(range(1, len(requests) + 1))
    assert all(event["url"].startswith(REAL_BASE) for event in requests)


def test_the_streamed_answer_is_the_plain_routes_answer(client, monkeypatch):
    """One discovery, two ways of delivering it. If they could disagree, the
    one a person reads in a browser to diagnose would not describe the one
    the console acted on."""
    monkeypatch.setattr(lookup_routes, "_lookup_session", lambda *a, **k: _EmptyFeeds())
    plain = client.get(
        f"/api/community-components?community_uuid={UUID}&base_url={REAL_BASE}"
    ).json()

    result = [e for e in _stream(client, monkeypatch, _EmptyFeeds()) if e["type"] == "result"][0]
    result.pop("type")

    assert result == plain


def test_a_session_that_cannot_open_is_an_answer_not_a_crash(client, monkeypatch):
    """Authentication failing to prepare -- the optional package missing, no
    domain to authenticate against -- is the same finding every feed would
    then have produced, and is reported as one."""

    def cannot(*a, **k):
        raise RuntimeError("SspiAuth requires the 'requests-negotiate-sspi' package")

    monkeypatch.setattr(lookup_routes, "_lookup_session", cannot)
    body = client.get(
        f"/api/community-components?community_uuid={UUID}&base_url={REAL_BASE}"
    ).json()

    assert body["components"] == []
    assert "requests-negotiate-sspi" in json.dumps(body)


def test_the_stream_carries_a_failure_to_its_end(client, monkeypatch):
    """A stream that stops without a `result` leaves the console waiting on
    its inactivity timer for two minutes to learn something the server knew
    at once."""

    class _Explodes:
        def get(self, url):
            raise RuntimeError("boom")

        def redirect_location(self, url):
            return None

    events = _stream(client, monkeypatch, _Explodes())

    assert events[-1]["type"] == "result"
    assert events[-1]["components"] == []
