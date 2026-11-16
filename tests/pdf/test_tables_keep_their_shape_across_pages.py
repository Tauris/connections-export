"""Tables and their pictures keep a readable size on every page.

Screenshots side by side in a table -- a German column beside an English one
-- would each be a fraction of the page; such a table prints its cells one
after another at the full width. A row that continues on the next page keeps
the column widths it started with: paged.js works the columns of each copy of
the table out again from what that copy holds, and a column whose text had
ended collapsed while another took the whole width. And the first page of
such a row is not mistaken for something too wide: what paged.js leaves past
the page's foot sits in a column out of sight to the right, printed on the
next page, and counting it shrank the page to a quarter.
"""

from __future__ import annotations

import pytest

from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import render_html, serving_images

from .test_nothing_is_lost_at_page_breaks import ENGLISH, GERMAN, _shot
from .test_wide_content_fits_the_page import _blobs, _model

needs_browser = pytest.mark.skipif(
    not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium"
)

_PAGES_JS = """
() => Array.from(document.querySelectorAll('.pagedjs_page')).map((page) => {
  const area = page.querySelector('.pagedjs_page_content').getBoundingClientRect();
  const width = (el) => {
    const r = Array.from(el.getClientRects()).find((r) => r.left < area.right - 1);
    return r ? r.width / area.width : 0;
  };
  return {
    images: Array.from(page.querySelectorAll('.page-body img')).map(width),
    cells: Array.from(page.querySelectorAll('.page-body td')).map(width),
    stacked: page.querySelectorAll('[data-hcl-stacked]').length,
    scaled: page.querySelectorAll('[data-hcl-fit-page]').length,
  };
})
"""


def _pages(content: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    from connections_export.pdf import paged
    from connections_export.pdf.browser import launch_browser

    with serving_images() as served:
        html = render_html(_model(content), blob_bytes=_blobs)
    with sync_playwright() as p:
        browser = launch_browser(p)
        guard = paged.PrintPage(browser, served=served, pdf_timeout=60)
        paged.lay_out(guard.page, html, pdf_timeout=60)
        found = guard.page.evaluate(_PAGES_JS)
        browser.close()
    return found


@needs_browser
def test_screenshots_side_by_side_are_printed_at_the_full_width():
    content = (
        '<table border=1 style="width:100%"><tr>'
        f'<td style="width:50%">{GERMAN}<br>{_shot(800, 450)}</td>'
        f'<td style="width:50%">{ENGLISH}<br>{_shot(800, 450)}</td>'
        "</tr></table>"
    )
    pages = _pages(content)
    images = [w for page in pages for w in page["images"]]
    assert len(images) == 2 and all(w > 0.95 for w in images), images
    assert any(page["stacked"] for page in pages)


@needs_browser
def test_small_pictures_leave_a_table_as_it_is():
    icon = '<img src="cid:wide.png" width="32" height="32">'
    content = f"<table border=1><tr><td>{icon} Deutsch</td><td>{icon} English</td></tr></table>"
    pages = _pages(content)
    assert not any(page["stacked"] for page in pages)
    cells = [w for page in pages for w in page["cells"]]
    assert len(cells) == 2


@needs_browser
def test_a_row_continued_over_pages_keeps_its_columns_and_its_size():
    content = f"<table border=1><tr><td>{GERMAN * 40}</td><td>{ENGLISH * 12}</td></tr></table>"
    pages = [page for page in _pages(content) if page["cells"]]
    assert len(pages) >= 3
    first = [round(w, 2) for w in pages[0]["cells"]]
    for page in pages:
        assert [round(w, 2) for w in page["cells"]] == first
        # Nothing on these pages is too wide, so nothing is shrunk.
        assert page["scaled"] == 0
    assert all(w > 0.1 for w in first)
