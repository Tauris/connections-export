"""The setting has to be operable, not merely present.

`archive_only` was rendered into its checkbox and read back out of it for
the PDF preview, and nothing else: no change handler, and the key absent
from the body the Save buttons send. So ticking it did nothing, unticking
it did nothing, and any other card's save re-rendered it from a stored
value that could never change. A setting you cannot reach is worse than
one that is missing -- it looks like it works.

These drive a real browser, because "the checkbox does something" is a
claim about wiring: asserting on the source text would pass against a
handler that is never attached.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

CAPTURE_NAV = '[data-section="select"]'


@pytest.fixture
def console_url(tmp_path, monkeypatch):
    import uvicorn

    from connections_export.gui import support as gui_support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

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


def _open_settings(page, console_url):
    page.goto(console_url, wait_until="load")
    page.click('[data-section="settings"]')
    page.wait_for_selector("#panel-settings:not([hidden])", timeout=20_000)
    page.wait_for_selector("#settings-archive-only", timeout=20_000)


def test_ticking_it_hides_the_capture_screens_there_and_then(console_url):
    """No Save press: the card's other control is the theme toggle, which
    applies as you press it. A show/hide choice you can already see the
    result of does not need confirming."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher: the skip guard proves a browser
        # by that same candidate order, so asking here for the bundled
        # Chromium would be asking for a different one than was checked.
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)
        assert page.is_visible(CAPTURE_NAV)

        page.check("#settings-archive-only")

        page.wait_for_selector(CAPTURE_NAV, state="hidden", timeout=10_000)
        browser.close()


def test_unticking_it_brings_them_back(console_url):
    """A display choice must be exactly reversible from the same control."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher: the skip guard proves a browser
        # by that same candidate order, so asking here for the bundled
        # Chromium would be asking for a different one than was checked.
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)
        page.check("#settings-archive-only")
        page.wait_for_selector(CAPTURE_NAV, state="hidden", timeout=10_000)

        page.uncheck("#settings-archive-only")

        page.wait_for_selector(CAPTURE_NAV, state="visible", timeout=10_000)
        browser.close()


def test_the_choice_is_still_there_after_a_reload(console_url):
    """It is kept as it is made. Without that the console forgets between
    visits, and the setting reads as broken rather than as unsaved."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher: the skip guard proves a browser
        # by that same candidate order, so asking here for the bundled
        # Chromium would be asking for a different one than was checked.
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)
        page.check("#settings-archive-only")
        page.wait_for_selector(CAPTURE_NAV, state="hidden", timeout=10_000)

        _open_settings(page, console_url)

        assert page.is_checked("#settings-archive-only")
        assert page.is_hidden(CAPTURE_NAV)
        browser.close()


def test_it_is_in_force_from_the_first_screen(console_url):
    """Applied at start-up, not on first visit to Settings. Otherwise the
    console opens showing the very screens the setting hides, and only tidies
    itself once you happen to go and look at the setting -- so the console
    contradicts its own saved preference for as long as you stay away from
    it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)
        page.check("#settings-archive-only")
        page.wait_for_selector(CAPTURE_NAV, state="hidden", timeout=10_000)

        # A fresh load, left where it lands. Nothing is clicked.
        page.goto(console_url, wait_until="load")
        page.wait_for_selector("#panel-overview:not([hidden])", timeout=20_000)
        page.wait_for_timeout(1500)

        assert page.is_hidden(CAPTURE_NAV)
        browser.close()


def test_it_says_that_it_kept_the_choice(console_url):
    """There is no Save button on this card, so the saving has to be visible
    some other way. Screens appearing and disappearing shows that something
    happened; it does not say whether it outlives the tab, and that is the
    one question a setting with no Save leaves open."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)

        page.check("#settings-archive-only")

        note = page.locator("#settings-appearance-status")
        note.wait_for(state="visible", timeout=10_000)
        page.wait_for_function(
            "() => /Saved/.test(document.getElementById('settings-appearance-status').textContent)",
            timeout=10_000,
        )
        # Beside the control it is about, not in another card further up.
        assert page.locator("#panel-settings").locator("#settings-appearance-status").count() == 1
        browser.close()


def test_saving_another_card_does_not_undo_it(console_url):
    """The Archives card has its own Save, and pressing it re-reads the
    settings. Ticking this and then saving that must not put the tick back
    where it was."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher: the skip guard proves a browser
        # by that same candidate order, so asking here for the bundled
        # Chromium would be asking for a different one than was checked.
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open_settings(page, console_url)
        page.check("#settings-archive-only")
        page.wait_for_selector(CAPTURE_NAV, state="hidden", timeout=10_000)

        page.click("#set-archives-apply")
        page.wait_for_timeout(1500)

        assert page.is_checked("#settings-archive-only")
        assert page.is_hidden(CAPTURE_NAV)
        browser.close()
