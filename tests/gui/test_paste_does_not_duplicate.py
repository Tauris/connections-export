"""Pasting a community URL renders its components once.

every component listed twice, with two
"Select all / Clear all" rows. Reproduced here, and it needs three things at
once -- which is why nothing in the suite caught it and the demo never showed
it:

1. A paste fires BOTH handlers on the field: `paste` identifies immediately,
   and `input` identifies again after its debounce. Two identifications.
2. Each one re-renders the component list, clearing the container and starting
   its own lookup.
3. The lookup is slow enough that the two overlap -- true of a real
   deployment, never of the in-process demo, which answers instantly.

Then the second render's clear detaches the first render's controls, and the
first lookup finishing puts them BACK with `insertBefore`, followed by its own
copy of every component.

This runs a real browser against a real server because that is the only place
the bug exists: every piece of it is timing between two event handlers.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

URL = (
    "https://demo.connections.example/communities/service/html/"
    "communitystart?communityUuid=b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
)


@pytest.fixture
def console_url():
    """The console, served over real HTTP on a free port."""
    import uvicorn

    from connections_export.gui.app import make_app

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
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_pasted_community_url_lists_its_components_once(console_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher, not `chromium.launch`: the
        # skip guard above proves a browser by starting one from that same
        # candidate order (a system Edge or Chrome before Playwright's bundled
        # Chromium), so asking here for the bundled one is asking for a
        # different browser than the guard checked -- green locally, and
        # "Executable doesn't exist" on a runner that has Chrome.
        browser = launch_browser(play)
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # A deployment that takes its time, which is what makes the two
        # identifications overlap.
        def slow(route):
            time.sleep(1.2)
            route.continue_()

        page.route("**/api/community-components*", slow)
        # The sub-community offer is rendered by the same code path and clears
        # and refills its own list in a callback -- the identical hazard.
        page.route("**/api/subcommunities*", slow)
        page.goto(console_url, wait_until="load")
        page.wait_for_timeout(800)
        page.click("#ov-demo")
        page.wait_for_timeout(600)

        # Exactly what a paste does: both events, in that order.
        page.evaluate(
            """(url) => {
              const field = document.getElementById('setup-url-input');
              field.focus();
              const data = new DataTransfer();
              data.setData('text', url);
              field.dispatchEvent(new ClipboardEvent('paste', {
                clipboardData: data, bubbles: true, cancelable: true,
              }));
              field.value = url;
              field.dispatchEvent(new Event('input', {bubbles: true}));
            }""",
            URL,
        )
        page.wait_for_timeout(7000)

        titles = page.eval_on_selector_all(
            "#community-component-options .component-option b", "els => els.map(e => e.textContent)"
        )
        controls = page.eval_on_selector_all(
            "#community-component-options .component-controls", "els => els.length"
        )
        candidates = page.eval_on_selector_all(
            ".subcommunity-candidate .subcommunity-candidate-name",
            "els => els.map(e => e.textContent)",
        )
        browser.close()

    assert titles, "no components were listed at all"
    assert len(titles) == len(set(titles)), f"components listed more than once: {titles}"
    assert controls == 1, f"{controls} Select all / Clear all rows"
    assert len(candidates) == len(set(candidates)), f"sub-communities offered twice: {candidates}"
    assert errors == []
