"""Content written for a scrolling window fits the printed page, whole.

On the web a wide table or a fixed-width box scrolls; a page box clips it, and
what sticks out is lost from the PDF for good. So, before layout, content is
made to reflow -- text allowed to wrap, positioned boxes put back into the
flow, tables freed from the widths authors gave their cells -- and what still
cannot fit is scaled down as a whole. After layout, a last check scales down
anything that still reaches past its page. Nothing may be cut off.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange, ResolvedAsset
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import render_html, serving_images


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x60\xa0\xe0" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


#: A real archived image, 1600 pixels wide. (A `data:` image in captured
#: content is refused by the sanitizer, so it would never reach the page.)
WIDE_PNG = _png(1600, 100)
DIGEST = "d" * 64


def _blobs(digest: str) -> bytes | None:
    return WIDE_PNG if digest == DIGEST else None


WIDE = (
    '<div style="min-width:900px;border:1px solid #999">An author box at least '
    "900 pixels wide, with text that must reflow onto the page.</div>"
    '<table style="white-space:nowrap"><tr>'
    + "".join(f"<td>Column{c} value-that-does-not-wrap</td>" for c in range(14))
    + "</tr></table>"
    '<img src="cid:wide.png" width="1600" height="100">'
    '<div style="white-space:pre;font-family:monospace">'
    + "x = "
    + "very_long_identifier_" * 12
    + "</div>"
    '<div style="position:relative;height:40px"><div style="position:absolute;left:700px;'
    'width:400px">A positioned box far to the right</div></div>'
    "<p>https://example.com/" + "a" * 160 + "</p>"
)

#: Tables whose cells were given more width than the table itself -- by
#: attribute, by style, through <col> -- an image in a narrow table's cell, a
#: nested table, and a box placed far beyond the right edge.
TABLES = (
    '<table width="500" border="1"><tr><td width="900">Cell asked for 900 pixels '
    'in a 500 pixel table</td><td style="width:1200px">Cell asked for 1200 pixels '
    "with its own text</td><td>last column</td></tr></table>"
    '<table style="width:400px"><colgroup><col style="width:700px"><col style="width:700px">'
    "</colgroup><tr><td>first column wanted 700</td><td>second column wanted 700</td></tr></table>"
    '<table style="width:300px"><tr><td><img src="cid:wide.png" '
    'width="1500" height="80" style="max-width:none !important"></td></tr></table>'
    '<table width="100%"><tr><td><table width="1400"><tr><td width="700">nested a</td>'
    '<td width="700">nested b</td></tr></table></td></tr></table>'
    '<div style="position:relative;height:30px"><div style="position:absolute;left:3000px;'
    'white-space:nowrap">Far right, at 3000 pixels</div></div>'
)


def _model(content: str = WIDE) -> Interchange:
    asset = ResolvedAsset(
        original_href="cid:wide.png",
        resolved_url="https://hcl.example.invalid/wide.png",
        blob_hash=f"sha256:{DIGEST}",
        present=True,
        scope="same",
    )
    page = DerivedPage(id="p1", label="p1", title="Wide page", content_html=content, assets=[asset])
    return Interchange(
        wikis=[DerivedWiki(id="w", label="w", title="W", root_page_ids=["p1"], pages={"p1": page})]
    )


#: After layout: every element on a page against its page's content area.
_OVERFLOW_JS = """
() => {
  const out = [];
  document.querySelectorAll('.pagedjs_page').forEach((page) => {
    const area = page.querySelector('.pagedjs_page_content').getBoundingClientRect();
    page.querySelectorAll('.pagedjs_page_content .page-body *').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > area.right + 1) {
        out.push(el.tagName + ' ' + Math.round(r.right - area.right) + 'px');
      }
    });
  });
  return {
    overflow: out,
    scaled: Array.from(document.querySelectorAll('[data-hcl-fit]')).map(
      (e) => e.tagName + ':' + e.getAttribute('data-hcl-fit')),
    page_scaled: document.querySelectorAll('[data-hcl-fit-page]').length,
    text: document.querySelector('.pagedjs_pages').innerText,
    images: Array.from(document.querySelectorAll('.pagedjs_page_content img')).map((img) => {
      const area = img.closest('.pagedjs_page').querySelector('.pagedjs_page_content');
      return img.getBoundingClientRect().right <= area.getBoundingClientRect().right + 1;
    }),
  };
}
"""


def _laid_out(
    monkeypatch=None,
    *,
    content: str = WIDE,
    without_fit: bool = False,
    without_page_fit: bool = False,
) -> dict:
    from playwright.sync_api import sync_playwright

    from connections_export.pdf import paged
    from connections_export.pdf.browser import launch_browser

    if without_fit:
        monkeypatch.setattr(paged, "_FIT_JS", "() => 0")
    if without_page_fit:
        monkeypatch.setattr(paged, "_FIT_PAGES_JS", "() => 0")
    with serving_images() as served:
        html = render_html(_model(content), blob_bytes=_blobs)
    with sync_playwright() as p:
        browser = launch_browser(p)
        guard = paged.PrintPage(browser, served=served, pdf_timeout=60)
        paged.lay_out(guard.page, html, pdf_timeout=60)
        found = guard.page.evaluate(_OVERFLOW_JS)
        browser.close()
    return found


needs_browser = pytest.mark.skipif(
    not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium"
)


@needs_browser
def test_without_the_fitting_passes_this_content_is_cut_off(monkeypatch):
    """The control: the print CSS alone does not tame this content -- the
    no-wrap table and the cells wider than their table stick out by hundreds
    of pixels."""
    found = _laid_out(monkeypatch, content=WIDE + TABLES, without_fit=True, without_page_fit=True)
    assert any(o.startswith("TABLE") for o in found["overflow"])


@needs_browser
def test_everything_reflows_onto_the_page_at_full_size():
    """Fixed before layout by reflowing alone: nothing sticks out, nothing
    needed shrinking -- the no-wrap table wraps, the positioned boxes are back
    in the flow, the code line wraps -- and every word and image is there."""
    found = _laid_out(content=WIDE + TABLES)
    assert found["overflow"] == []
    assert found["scaled"] == [] and found["page_scaled"] == 0
    for words in (
        "Cell asked for 1200 pixels",
        "second column wanted 700",
        "nested b",
        "Far right, at 3000 pixels",
        "A positioned box far to the right",
        "Column13",
    ):
        assert words in found["text"]
    # Both 1500-1600 pixel images are on the page, whole -- one of them in the
    # cell of a 300 pixel table, with an author's `max-width: none`.
    assert len(found["images"]) == 2 and all(found["images"])


@needs_browser
def test_what_cannot_reflow_is_scaled_without_a_lower_limit():
    """A block that stays wider than the page however it wraps is scaled down
    as a whole, as far as it takes: smaller and complete beats cut off."""
    rigid = (
        '<div style="display:flex;flex-wrap:nowrap;gap:0">'
        + "".join(
            f'<div style="flex:0 0 400px;width:400px;white-space:nowrap">Panel {n}</div>'
            for n in range(8)
        )
        + "</div>"
    )
    found = _laid_out(content=rigid)
    assert found["overflow"] == []
    assert found["scaled"] and all(0 < float(s.split(":")[1]) < 1 for s in found["scaled"])
    assert "Panel 7" in found["text"]


@needs_browser
def test_the_last_guarantee_alone_keeps_everything_on_the_page(monkeypatch):
    """With everything before layout disabled, the check on the laid-out
    pages still leaves nothing outside them: whatever reaches past a page's
    content area is scaled down within its page."""
    found = _laid_out(monkeypatch, content=WIDE + TABLES, without_fit=True)
    assert found["overflow"] == []
    assert found["page_scaled"] > 0


def test_highlights_bodies_get_the_print_rules_too():
    css = render_html(_model(), blob_bytes=_blobs)
    assert ".rc-body *" in css and "min-width: 0 !important" in css
