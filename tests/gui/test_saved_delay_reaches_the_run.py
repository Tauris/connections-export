"""The delay you saved is the delay the run uses.

Settings had 0.2s, saved; the Ingest screen
showed 1s; the crawl paced at 1s.

Two controls set one value. Settings populated its own field on load and the
Select screen's field was left at the markup default of 1 -- and the run body
carries that field, which the server prefers over the saved setting. So the
saved value was overwritten by a control the user had never touched, and the
screen showing 1 was telling the truth about what would happen.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")


@pytest.fixture
def console_url(tmp_path, monkeypatch):
    import uvicorn

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(support, "ARCHIVES_BASE", tmp_path / "archives")
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


def test_a_saved_delay_shows_on_the_ingest_screen_and_is_what_gets_sent(console_url):
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

        page.goto(console_url, wait_until="load")
        page.wait_for_timeout(600)
        saved = page.evaluate(
            """async () => {
              const r = await fetch('/api/settings', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({min_interval: 0.2}),
              });
              return r.status;
            }"""
        )
        assert saved < 400, f"saving the setting failed: {saved}"

        # A fresh visit, as the user had.
        page.goto(console_url, wait_until="load")
        page.wait_for_timeout(1500)
        shown = page.eval_on_selector("#field-delay", "e => e.value")

        # ...and what a run would actually be told to use.
        posted: dict = {}

        def capture(route):
            posted.update(json.loads(route.request.post_data or "{}"))
            route.abort()

        page.route("**/api/start", capture)
        page.click("#ov-demo")
        page.wait_for_selector(".demo-chip", timeout=10000)
        titles = page.eval_on_selector_all(".demo-chip", "els => els.map(e => e.textContent)")
        index = next(i for i, t in enumerate(titles) if "communit" in t.lower())
        page.locator(".demo-chip").nth(index).click()
        page.wait_for_timeout(3500)
        page.wait_for_selector("#start-ingest:not([disabled])", timeout=15000)
        page.click("#start-ingest")
        page.wait_for_timeout(1200)
        browser.close()

    assert shown == "0.2", f"the ingest screen showed {shown!r}, not the saved 0.2"
    assert posted.get("min_interval") == 0.2, f"the run was sent {posted.get('min_interval')!r}"
    assert errors == []
