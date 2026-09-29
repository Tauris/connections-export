"""The render timeout governs every browser step, not only pagination.

A large export -- a real archive with its images embedded, external images
included -- can take longer than 30 seconds just to load into the browser.
The setting covered only the wait for paged.js; loading the document and
writing the PDF kept Playwright's own 30-second default, so raising the
setting did nothing for exactly the exports that needed it ("Timeout 30000ms
exceeded"). Both renderers now make the setting the page's default.
"""

from __future__ import annotations

import contextlib

import playwright.sync_api
import pytest

from connections_export.pdf import browser, paged


class _Page:
    def __init__(self, log: list):
        self.log = log

    def set_default_timeout(self, ms):
        self.log.append(("default", ms))

    def set_content(self, *_args, **_kwargs):
        self.log.append(("load",))

    def pdf(self, **_kwargs):
        self.log.append(("print",))
        return b"%PDF-"

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Browser:
    def __init__(self, log: list):
        self.log = log

    def new_page(self, **_kwargs):
        return _Page(self.log)

    def close(self):
        pass


@pytest.fixture
def log(monkeypatch):
    calls: list = []
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", contextlib.nullcontext)
    monkeypatch.setattr(browser, "_launch", lambda _pw: _Browser(calls))
    monkeypatch.setattr(paged, "_launch", lambda _pw: _Browser(calls))
    monkeypatch.setattr(browser, "block_network", lambda *_args: None)
    monkeypatch.setattr(paged, "block_network", lambda *_args: None)
    return calls


def _default_before_loading(calls: list) -> int:
    assert ("load",) in calls, calls
    before = calls[: calls.index(("load",))]
    defaults = [entry[1] for entry in before if entry[0] == "default"]
    assert defaults, f"no default timeout before loading: {calls}"
    return defaults[-1]


def test_the_paged_renderer_loads_and_prints_within_the_setting(log, monkeypatch):
    monkeypatch.setattr(paged, "with_outline", lambda pdf, *_a, **_k: pdf)
    with contextlib.suppress(paged.PdfRenderError):
        paged.html_to_pdf_paged("<p>x</p>", pdf_timeout=300)

    assert _default_before_loading(log) == 300_000


def test_the_simple_renderer_loads_and_prints_within_the_setting(log):
    browser.html_to_pdf("<p>x</p>", pdf_timeout=300)

    assert _default_before_loading(log) == 300_000
    assert ("print",) in log


def test_the_simple_renderer_takes_the_setting_end_to_end():
    import inspect

    assert "pdf_timeout" in inspect.signature(browser.render_pdf).parameters


# --- layout: a large export that keeps making pages is not a hang -----------


class _Laying:
    """A page that lays out `steps` more pages, then finishes; each wait
    takes `each_ms` of the stall budget. None for `steps` never finishes."""

    def __init__(self, steps: int | None, each_ms: int = 10):
        self.steps, self.each_ms, self.pages, self.done = steps, each_ms, 0, False
        self.waits: list[int] = []

    def wait_for_selector(self, selector, *, state, timeout):
        self.waits.append(timeout)
        if self.steps is not None and self.pages >= self.steps:
            self.done = True
            return
        if self.steps is None or self.each_ms > timeout:
            raise TimeoutError(f"Timeout {timeout}ms exceeded.")
        self.pages += 1

    def query_selector(self, selector):
        return object() if self.done and "data-paged-done" in selector else None

    def locator(self, selector):
        pages = self.pages
        return type("L", (), {"count": lambda _self: pages})()


def test_layout_runs_on_while_pages_keep_coming():
    page = _Laying(steps=500)

    assert paged._wait_for_layout(page, stall_ms=1000) == 500
    assert set(page.waits) == {1000}, "each new page restarts the stall budget"


def test_layout_that_stops_making_pages_fails_after_the_setting():
    page = _Laying(steps=None)

    with pytest.raises(TimeoutError) as caught:
        paged._wait_for_layout(page, stall_ms=1000)

    assert "0 pages" in str(caught.value) and "1 s" in str(caught.value)


def test_printing_is_allowed_time_for_every_page():
    assert paged._print_timeout_ms(pdf_timeout=120, pages=0) == 120_000
    assert paged._print_timeout_ms(pdf_timeout=120, pages=1000) == 620_000
