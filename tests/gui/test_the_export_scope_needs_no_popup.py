"""The export menu's "What to export" choices are on the panel itself.

They were a native dropdown inside the menu panel. A native dropdown opens as
a popup of its own, and on WSLg (a Linux browser shown on Windows) that popup
flashes and vanishes, so all anyone saw was "Whole archive"; a scrolling
panel closes it too. As radio buttons all three choices are simply there.
"""

from __future__ import annotations

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

from .test_guided_export_in_a_browser import LAPTOP, _archives_screen, console  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")


def _open_dialog(page, url: str) -> None:
    _archives_screen(page, url)
    page.click('.recent-archive-row:has(.archive-select[data-name="wiki-run"]) .recent-archive')
    page.wait_for_selector("#r-export-menu", state="visible", timeout=30_000)
    page.click("#r-export-menu > summary")
    page.click("#r-export-open")
    page.wait_for_selector("#export-scrim:not([hidden])")


def test_every_scope_choice_is_visible_without_a_popup(console):  # noqa: F811
    from playwright.sync_api import sync_playwright

    url, _ = console
    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _open_dialog(page, url)

        choices = page.locator('#xd-what input[type="radio"]')
        assert choices.count() == 3
        assert page.is_checked('input[name="xd-scope"][value="all"]')
        # A wiki page is open, so the open item can be chosen and is named.
        assert page.is_visible('input[name="xd-scope"][value="item"]')
        assert "page" in page.inner_text("#xd-item-row").lower()

        page.click('#xd-what label:has(input[value="choose"])')
        assert page.is_visible("#xd-pick")
        assert page.is_visible("#xd-bookmarks")

        page.click('#xd-what label:has(input[value="all"])')
        assert page.is_hidden("#xd-pick")
        browser.close()
