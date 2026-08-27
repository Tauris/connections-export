"""Identifying something this archive directory already holds.

Re-capturing what you already captured is one of the few ways to waste hours
here, and Archives only notices afterwards -- it has a "select superseded"
button, which is the same problem solved after the fact. Better to say so
before the run.

The offer must be an offer, never a redirect: someone may genuinely want a
fresh capture alongside the old one, and quietly turning their fresh capture
into an update would be worse than not noticing at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    return TestClient(make_app(demo=True, demo_delay=0), base_url="http://127.0.0.1"), tmp_path


def test_nothing_matches_an_empty_archives_directory(client):
    http, _ = client

    body = http.get("/api/archive-match?community_uuid=anything").json()

    assert body["matches"] == []


def test_a_community_already_captured_is_recognised(client):
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.gui.demo import run_demo

    http, base = client
    run_demo(lambda _e: None, delay=0, archive_dir=base / "export-atlas", wave=0)

    body = http.get(f"/api/archive-match?community_uuid={DEMO_COMMUNITY_UUID}").json()

    assert [m["archive"] for m in body["matches"]] == ["export-atlas"]


def test_the_match_says_enough_to_decide_with(client):
    """ "You captured this before" is not actionable. When, and how much, is."""
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.gui.demo import run_demo

    http, base = client
    run_demo(lambda _e: None, delay=0, archive_dir=base / "export-atlas", wave=0)

    body = http.get(f"/api/archive-match?community_uuid={DEMO_COMMUNITY_UUID}").json()
    match = body["matches"][0]

    assert match["captured_at"]
    assert match["items"] > 0
    assert match["components"] > 0


def test_a_different_community_is_not_a_match(client):
    """A false match would push someone toward updating an archive of
    something else entirely."""
    from connections_export.gui.demo import run_demo

    http, base = client
    run_demo(lambda _e: None, delay=0, archive_dir=base / "export-atlas", wave=0)

    body = http.get("/api/archive-match?community_uuid=some-other-community").json()

    assert body["matches"] == []


def test_matching_needs_something_to_match_on(client):
    http, _ = client

    assert http.get("/api/archive-match").json()["matches"] == []
