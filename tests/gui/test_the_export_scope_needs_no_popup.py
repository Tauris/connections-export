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


def _open_menu(page, url: str) -> None:
    _archives_screen(page, url)
    page.click('.recent-archive-row:has(.archive-select[data-name="wiki-run"]) .recent-archive')
    page.wait_for_selector("#r-export-menu", state="visible", timeout=30_000)
    page.click("#r-export-menu > summary")


def test_every_scope_choice_is_visible_without_a_popup(console):  # noqa: F811
    from playwright.sync_api import sync_playwright

    url, _ = console
    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _open_menu(page, url)

        assert page.locator("#r-export-menu select").count() == 0
        choices = page.locator('#r-pdf-scope input[type="radio"]')
        assert choices.count() == 3
        for index in range(3):
            assert choices.nth(index).is_visible()
        assert page.is_checked('#r-pdf-scope input[value="all"]')
        # A wiki page is open, so "Current item" names it and can be chosen.
        assert page.is_enabled('#r-pdf-scope input[value="item"]')
        assert "This page" in page.inner_text("#r-pdf-scope")

        page.click('#r-pdf-scope label:has(input[value="choose"])')
        assert page.is_visible("#pdf-pick")
        assert page.is_visible("#r-export-menu .menu-panel"), "choosing closed the menu"
        assert page.is_visible("#r-pdf-open-bookmarks")

        page.click('#r-pdf-scope label:has(input[value="all"])')
        assert page.is_hidden("#pdf-pick")
        browser.close()
