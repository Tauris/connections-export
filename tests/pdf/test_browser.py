"""`html_to_pdf`/`render_pdf` (html_to_pdf (thin)): browser-gated. `CHROMIUM_AVAILABLE` is decided
once, at
import time, by `connections_export.pdf.browser`'s own probe (an actual launch
attempt) -- this suite must be fully green whether or not the Chromium
*browser binary* happens to be installed (`playwright install
chromium` is never run as part of the test suite itself).
"""

from __future__ import annotations

import pytest

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
from connections_export.pdf import render_pdf_browser
from connections_export.pdf.browser import CHROMIUM_AVAILABLE, render_pdf


def _small_interchange() -> Interchange:
    page = DerivedPage(id="p1", label="p1", title="Page One", content_html="<p>Hello, PDF.</p>")
    page2 = DerivedPage(id="p2", label="p2", title="Page Two", content_html="<p>Second page.</p>")
    wiki = DerivedWiki(
        id="w1",
        label="w1",
        title="Wiki One",
        root_page_ids=["p1", "p2"],
        pages={"p1": page, "p2": page2},
    )
    return Interchange(wikis=[wiki])


@pytest.mark.skipif(
    not CHROMIUM_AVAILABLE,
    reason="no usable Chromium browser binary (run `playwright install chromium` to enable)",
)
def test_render_pdf_of_a_small_model_returns_pdf_bytes():
    interchange = _small_interchange()

    pdf_bytes = render_pdf(interchange, lambda _digest: None)

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.skipif(
    not CHROMIUM_AVAILABLE,
    reason="no usable Chromium browser binary (run `playwright install chromium` to enable)",
)
def test_render_pdf_browser_prints_one_combined_document():
    """Path B end to end: the single combined document (cover + TOC + scoped
    pages) prints to one PDF, with a generated outline."""
    interchange = _small_interchange()

    pdf_bytes = render_pdf_browser(interchange, lambda _digest: None)

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")
    # A cover + TOC + two pages is comfortably more than a trivial PDF.
    assert len(pdf_bytes) > 1000


def test_chromium_available_is_a_bool_decided_without_raising():
    # The module-level probe must never itself blow up import of the
    # package (an environment with no browser binary is the expected,
    # supported case) -- just resolve to a plain bool either way.
    assert isinstance(CHROMIUM_AVAILABLE, bool)
