"""The author list covers the whole import, not its last component.

Wikis, Blogs and Forums each end with their own `AuthorFilterSummary`, and the
so a picker that re-renders from scratch on each one leaves a community
import showing only the identities of whichever component summarised last --
a fraction of the people in the run, and the rest unreachable.

They matter because the picker is not decoration: each chip re-imports that
person's content with the exact stored form of their name. An identity that is
not in the list is a filter the user cannot construct by hand.

The per-component cap was the second half of it: two of the three summaries
came back holding exactly 15, which is the limit rather than the count.
"""

from __future__ import annotations

import pathlib
import socket
import tempfile
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser


def test_a_component_reports_more_than_fifteen_identities():
    """The cap was low enough to truncate a single demo component, so
    aggregating alone would still have shown a partial list."""
    import inspect

    from connections_export.crawler.author_plan import format_identities

    signature = inspect.signature(format_identities)
    assert signature.parameters["limit"].default > 15


def test_the_identities_are_still_bounded():
    """Uncapped, one event could carry every author in a large deployment."""
    from connections_export.crawler.author_plan import Involvement, format_identities

    many = [Involvement(author=f"Person {n}", author_userid=f"u{n}") for n in range(1000)]
    assert len(format_identities(many)) <= 500


@pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")
def test_the_picker_shows_every_component_s_authors_after_a_community_import(monkeypatch):
    import uvicorn
    from playwright.sync_api import sync_playwright

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(support, "ARCHIVES_BASE", pathlib.Path(tempfile.mkdtemp()))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(
        make_app(demo=True), host="127.0.0.1", port=port, log_level="error", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.skip("the console did not start")

    try:
        with sync_playwright() as play:
            # Through the product's own launcher, not `chromium.launch`:
            # the skip guard above proves a browser by starting one from that
            # same candidate order (a system Edge or Chrome before
            # Playwright's bundled Chromium), so asking here for the bundled
            # one is asking for a different browser than the guard checked --
            # green locally, and "Executable doesn't exist" on a runner that
            # has Chrome.
            browser = launch_browser(play)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            page.wait_for_timeout(1000)
            page.click("#ov-demo")
            page.wait_for_selector(".demo-chip", timeout=10000)
            titles = page.eval_on_selector_all(".demo-chip", "els => els.map(e => e.textContent)")
            index = next(i for i, t in enumerate(titles) if "communit" in t.lower())
            page.locator(".demo-chip").nth(index).click()
            page.wait_for_timeout(3500)
            page.wait_for_selector("#start-ingest:not([disabled])", timeout=15000)
            page.click("#start-ingest")
            page.wait_for_timeout(40000)
            chips = page.eval_on_selector_all(
                "#author-picker .ap-chip", "els => els.map(e => e.textContent)"
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    # Every distinct person the demo credits, each exactly once.
    #
    # A count cannot prove the MERGE on this dataset: the demo credits all
    # eight people in every component, so the union and the last summary are
    # the same size. What it does prove is completeness and deduplication --
    # before the fake gave each person one id, the same human appeared three
    # times ("M. Lindqvist", "M. Lindqvist (uid: M. Lindqvist)",
    # "M. Lindqvist (uid: blogger0)"). The accumulation itself is asserted
    # structurally below.
    assert len(chips) == 8, f"{len(chips)} chips: {chips}"
    assert len(set(chips)) == len(chips), f"the same person offered twice: {chips}"
    assert all("(uid:" in c for c in chips), chips


def test_the_summaries_accumulate_rather_than_replace():
    """Structural, because the demo cannot show it behaviourally -- every
    person appears in every component there, so the union and the last
    summary have the same size. What a regression would look like is the
    picker rendering `evt.identities` directly again."""
    from tests.gui._served_assets import served_console_js

    js = served_console_js()
    assert "authorIdentities" in js
    assert "renderAuthorPicker(author, authorIdentities" in js
    assert "renderAuthorPicker(author, evt.identities" not in js
