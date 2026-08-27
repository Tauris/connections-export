"""`/api/archive-ledger` -- what the screen needs to offer extend and update.

One request answers the whole archive-mode screen: what this archive holds,
what it would cost to bring each part up to date, what the live system has now,
and what is there that was never captured (which is what "extend" offers).

The live half is deliberately in the same response. Two round trips would let
the screen render a half-answer -- holdings without live counts -- and a user
would be looking at "8 items" with no idea whether that is current.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    # `127.0.0.1`, not TestClient's default `testserver`: the console refuses
    # requests from hosts it does not recognise, which is a real guard and not
    # something a test should switch off.
    return TestClient(make_app(demo=True, demo_delay=0), base_url="http://127.0.0.1"), tmp_path


@pytest.fixture
def archived(client):
    """A demo capture sitting in the archives directory."""
    from connections_export.gui.demo import run_demo

    http, base = client
    result = run_demo(lambda _e: None, delay=0, archive_dir=base / "export-demo", wave=0)
    return http, result


def test_an_unknown_archive_is_a_404_not_an_empty_ledger(client):
    """An empty ledger would render as "this archive holds nothing", which is
    a very different statement from "no such archive"."""
    http, _ = client

    assert http.get("/api/archive-ledger?name=nope").status_code == 404


def test_the_ledger_reports_the_archives_holdings(archived):
    http, _ = archived

    body = http.get("/api/archive-ledger?name=export-demo").json()

    assert body["status"] == "ok"
    kinds = {row["kind"] for row in body["rows"]}
    assert {"wiki", "blog", "forum"} <= kinds


def test_each_row_carries_what_the_screen_must_show(archived):
    http, _ = archived

    rows = http.get("/api/archive-ledger?name=export-demo").json()["rows"]

    for row in rows:
        assert row["title"]
        assert row["captured_at"], "a row with no date cannot explain its cutoff"
        assert row["update_method"] in ("since", "rescan", "reread")
        assert row["floor_requests"] >= 0


def test_the_cutoff_and_its_anchor_travel_together(archived):
    """The screen says "one minute before the last capture began". It can only
    say that if it has both numbers."""
    http, _ = archived

    body = http.get("/api/archive-ledger?name=export-demo").json()

    assert body["since"] and body["anchor"]
    assert body["since"] < body["anchor"]


def test_the_ledger_says_what_is_live_but_never_captured(archived):
    """The extend half. A component on the deployment that this archive has
    never held is what the screen offers as "Add now"."""
    http, _ = archived

    body = http.get("/api/archive-ledger?name=export-demo").json()

    assert "available" in body, "the ledger cannot offer anything to extend with"


def test_a_partial_capture_offers_what_it_left_out(client):
    """The extend half, end to end: capture two apps out of a community, and
    the ledger names the rest as things that could be added."""
    from connections_export.gui.demo import run_demo

    http, base = client
    run_demo(
        lambda _e: None,
        delay=0,
        archive_dir=base / "export-partial",
        wave=0,
        app_filter=("wiki", "blog"),
    )

    body = http.get("/api/archive-ledger?name=export-partial").json()

    held = {row["kind"] for row in body["rows"]}
    offered = {component["kind"] for component in body["available"]}
    assert held == {"wiki", "blog"}
    assert {"forum", "files", "rich_content"} <= offered
    assert not (held & offered), "something already captured was offered as new"


def test_a_complete_capture_offers_nothing_to_add(client):
    """The counterweight: "add now" must not appear for things the archive
    already holds, or the screen would invite re-capturing what it has."""
    from connections_export.gui.demo import run_demo

    http, base = client
    run_demo(lambda _e: None, delay=0, archive_dir=base / "export-whole", wave=0)

    body = http.get("/api/archive-ledger?name=export-whole").json()

    assert body["available"] == []
