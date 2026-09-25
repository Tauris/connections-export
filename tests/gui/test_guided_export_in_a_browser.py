"""The guided export, driven in a real browser.

Tick two archives, open "Export combined", step through Format, Check and
Export: the check names the combining and the folder, the result names the
folder it wrote and what to do next, and the folder is there. The Reader's
developer-format buttons open the same dialog for the archive being read. A
claim about wiring -- which handler runs, which button unlocks when, what
fits on a laptop screen -- so it is checked where the wiring runs.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

#: The laptop screen every step has to fit without scrolling.
LAPTOP = {"width": 1280, "height": 800}
HUGO = shutil.which("hugo")


@pytest.fixture
def console(tmp_path, monkeypatch):
    import uvicorn

    from connections_export.gui import support as gui_support
    from connections_export.gui.app import make_app
    from connections_export.gui.demo import run_demo

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    run_demo(lambda _e: None, archive_dir=tmp_path / "wiki-run", delay=0, app_filter="wiki")
    run_demo(lambda _e: None, archive_dir=tmp_path / "blog-run", delay=0, app_filter="blog")

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
        yield f"http://127.0.0.1:{port}/", tmp_path
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _archives_screen(page, url: str) -> None:
    page.goto(url, wait_until="load")
    page.click('[data-section="archives"]')
    page.wait_for_selector('.archive-select[data-name="wiki-run"]', timeout=20_000)


def _step(page) -> str:
    return page.get_attribute("#devx-stepper li[aria-current=step]", "data-step")


def _check(page) -> str:
    page.click("#devx-check-btn")
    page.wait_for_function(
        "document.querySelector('#devx-check').textContent.includes('Written to')",
        timeout=30_000,
    )
    assert _step(page) == "3"
    return page.inner_text("#devx-check")


def _fits(page) -> dict:
    """Where the dialog's footer and step body sit in the window."""
    return page.evaluate(
        """() => {
          const foot = document.querySelector('.devx-foot').getBoundingClientRect();
          const dialog = document.querySelector('.modal.devx').getBoundingClientRect();
          const body = document.querySelector('#devx-body');
          return {
            footTop: foot.top, footBottom: foot.bottom, dialogTop: dialog.top,
            height: innerHeight, overflow: body.scrollHeight - body.clientHeight,
          };
        }"""
    )


def test_two_archives_are_checked_then_exported_as_one(console):
    from playwright.sync_api import sync_playwright

    url, base = console
    with sync_playwright() as play:
        # Through the product's own launcher, the browser the skip guard proved.
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _archives_screen(page, url)
        page.check('.archive-select[data-name="wiki-run"]')
        page.check('.archive-select[data-name="blog-run"]')
        assert page.inner_text("#export-selected") == "Export combined (2)…"

        page.click("#export-selected")
        page.wait_for_selector("#devx-scrim:not([hidden])")
        assert _step(page) == "1"
        assert page.locator("#devx-archives li").count() == 2
        assert page.is_hidden("#devx-export-btn") and page.is_disabled("#devx-export-btn")

        page.click("#devx-next")
        assert _step(page) == "2"
        page.check('input[name="devx-format"][value="jekyll"]')
        check = _check(page)
        assert "2 archives combined" in check and "Jekyll site" in check
        assert page.is_enabled("#devx-export-btn"), "checked: Export unlocks"

        page.click("#devx-export-btn")
        assert _step(page) == "4"
        page.wait_for_selector("#devx-result.is-done", timeout=60_000)
        result = page.inner_text("#devx-result")
        assert "jekyll serve" in result
        written = [p for p in base.iterdir() if p.name.startswith("combined-")]
        assert len(written) == 1 and written[0].name.endswith("-jekyll-site")
        assert str(written[0]) in result
        assert (written[0] / "sources.md").is_file()

        # Changing the format afterwards needs a new check before writing.
        page.click("#devx-back")
        page.click("#devx-back")
        assert _step(page) == "2"
        page.check('input[name="devx-format"][value="hugo"]')
        assert page.is_disabled("#devx-export-btn")
        assert "Choices changed" in page.inner_text("#devx-check")
        browser.close()


def test_every_step_fits_a_laptop_screen(console):
    """Each step of a normal export fits 1280x800: the footer is inside the
    window and the step body does not need to scroll."""
    from playwright.sync_api import sync_playwright

    url, _base = console
    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _archives_screen(page, url)
        page.check('.archive-select[data-name="wiki-run"]')
        page.check('.archive-select[data-name="blog-run"]')
        page.click("#export-selected")
        page.wait_for_selector("#devx-scrim:not([hidden])")

        def assert_fits(step: str) -> None:
            fit = _fits(page)
            assert fit["dialogTop"] >= 0, step
            assert fit["footBottom"] <= fit["height"], f"step {step}: footer below the window"
            assert fit["overflow"] <= 1, f"step {step}: body scrolls by {fit['overflow']}px"
            assert page.is_visible("#devx-close"), step

        assert_fits("1")
        page.click("#devx-next")
        page.wait_for_function("document.querySelector('#devx-hugo-status').textContent !== ''")
        assert_fits("2")
        _check(page)
        assert_fits("3")
        page.click("#devx-export-btn")
        page.wait_for_selector("#devx-result.is-done", timeout=60_000)
        page.wait_for_timeout(500)  # the Hugo block, when there is one
        assert_fits("4")
        browser.close()


@pytest.mark.skipif(HUGO is None, reason="no hugo binary on PATH")
def test_a_combined_hugo_export_is_previewed_in_a_new_tab(console):
    """Hugo chosen: the format step says at once that Hugo is here; the
    starter site is on by default; after Check and Export the result's
    primary action is Preview with Hugo -- which opens Hugo's own server, on
    another port, in a new tab -- and it stops again."""
    from playwright.sync_api import sync_playwright

    url, _base = console
    with sync_playwright() as play:
        browser = launch_browser(play)
        context = browser.new_context(viewport=LAPTOP)
        page = context.new_page()
        _archives_screen(page, url)
        page.check('.archive-select[data-name="wiki-run"]')
        page.check('.archive-select[data-name="blog-run"]')
        page.click("#export-selected")
        page.wait_for_selector("#devx-scrim:not([hidden])")
        page.click("#devx-next")

        page.check('input[name="devx-format"][value="jekyll"]')
        assert page.is_hidden("#devx-starter-row"), "the starter site is Hugo's alone"
        page.check('input[name="devx-format"][value="hugo"]')
        assert page.is_visible("#devx-starter-row") and page.is_checked("#devx-starter")
        # The front page's layout sits under the starter site, list by
        # default, and goes with it.
        assert page.is_visible("#devx-starter-layout")
        assert page.input_value("#devx-starter-layout") == "list"
        page.uncheck("#devx-starter")
        assert page.is_hidden("#devx-starter-layout")
        page.check("#devx-starter")
        assert page.is_visible("#devx-starter-layout")
        page.wait_for_function(
            "document.querySelector('#devx-hugo-status').textContent.includes(' found')"
        )
        status = page.inner_text("#devx-hugo-status")
        assert status.startswith("Hugo ") and "you can preview it here" in status

        _check(page)
        page.click("#devx-export-btn")
        page.wait_for_selector("#devx-result.is-done", timeout=60_000)
        assert "hugo server" in page.inner_text("#devx-result")

        page.wait_for_selector("#devx-hugo-preview:not([hidden])", timeout=20_000)
        assert "primary" in page.get_attribute("#devx-hugo-preview", "class")
        with context.expect_page(timeout=90_000) as opened:
            page.click("#devx-hugo-preview")
        preview = opened.value
        preview.wait_for_url("http://127.0.0.1:*/", timeout=90_000)
        preview.wait_for_load_state()
        assert not preview.url.startswith(url), "never served from the console's origin"
        assert preview.evaluate("window.opener") is None

        page.wait_for_selector("#devx-hugo-stop:not([hidden])")
        assert preview.url in page.inner_text("#devx-hugo-note")
        page.click("#devx-hugo-stop")
        page.wait_for_selector("#devx-hugo-stop[hidden]", state="attached")
        browser.close()


@pytest.mark.skipif(HUGO is None, reason="no hugo binary on PATH")
def test_the_readers_hugo_button_opens_the_dialog_for_the_open_archive(console):
    """Reader: open an archive, press Export Hugo content -- the dialog opens
    at the format step with Hugo chosen and the starter site ticked, and the
    export offers Preview with Hugo."""
    from playwright.sync_api import sync_playwright

    url, base = console
    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _archives_screen(page, url)
        page.click('.recent-archive-row:has(.archive-select[data-name="wiki-run"]) .recent-archive')
        page.wait_for_selector("#r-export-menu", state="visible", timeout=30_000)
        page.click("#r-export-menu > summary")
        page.click("#r-dev-export > summary")
        page.click("#r-export-hugo")

        page.wait_for_selector("#devx-scrim:not([hidden])")
        assert _step(page) == "2"
        assert page.is_checked('input[name="devx-format"][value="hugo"]')
        assert page.is_checked("#devx-starter")
        page.click("#devx-back")
        assert page.inner_text("#devx-archives").startswith("wiki-run")
        page.click("#devx-next")

        check = _check(page)
        assert str(base / "wiki-run-hugo-content") in check
        page.click("#devx-export-btn")
        page.wait_for_selector("#devx-result.is-done", timeout=60_000)
        assert (base / "wiki-run-hugo-content" / "hugo.toml").is_file()
        page.wait_for_selector("#devx-hugo-preview:not([hidden])", timeout=20_000)
        assert page.inner_text("#devx-hugo-preview") == "Preview with Hugo"
        browser.close()


def test_the_reader_exports_an_archive_from_outside_the_archives_folder(console, tmp_path_factory):
    """An archive opened from elsewhere is the dialog's single source,
    exported beside where it lies, with the format the button named."""
    from playwright.sync_api import sync_playwright

    from connections_export.gui.demo import run_demo

    url, _base = console
    elsewhere = tmp_path_factory.mktemp("dropped") / "dropped-run"
    run_demo(lambda _e: None, archive_dir=elsewhere, delay=0, app_filter="blog")
    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport=LAPTOP)
        _archives_screen(page, url)
        page.fill("#archive-path", str(elsewhere))
        page.click("#archive-open-path")
        page.wait_for_selector("#r-export-menu", state="visible", timeout=30_000)
        # The "opening" notice, if it is still up, would sit over the menu.
        page.wait_for_timeout(1000)
        if page.is_visible("#modal-ok"):
            page.click("#modal-ok")
        page.click("#r-export-menu > summary")
        page.click("#r-dev-export > summary")
        page.click("#r-export-jekyll")

        page.wait_for_selector("#devx-scrim:not([hidden])")
        assert _step(page) == "2"
        assert page.is_checked('input[name="devx-format"][value="jekyll"]')
        page.click("#devx-back")
        assert "open in the Reader" in page.inner_text("#devx-archives")
        assert page.is_hidden("#devx-add-select"), "nothing to combine it with by name"
        page.click("#devx-next")

        check = _check(page)
        folder = elsewhere.parent / "dropped-run-jekyll-site"
        assert str(folder) in check
        page.click("#devx-export-btn")
        page.wait_for_selector("#devx-result.is-done", timeout=60_000)
        assert folder.is_dir()
        browser.close()
