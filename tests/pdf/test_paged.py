"""The paged-media portable PDF (`connections_export.pdf.paged`): running
per-page footer (wiki/page name) + TOC page numbers, via the vendored paged.js
polyfill inside Chromium. The content assertions are browser-gated (skip when
no Chromium is present); the CSS/assembly/packaging checks run everywhere.
"""

from __future__ import annotations

import io
import re

import pytest

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
from connections_export.pdf import paged as pdf_paged


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _two_page_wiki() -> Interchange:
    a = DerivedPage(id="pa", label="pa", title="Alpha Page", content_html="<p>Alpha body.</p>")
    b = DerivedPage(id="pb", label="pb", title="Beta Page", content_html="<p>Beta body.</p>")
    wiki = DerivedWiki(
        id="w1",
        label="w1",
        title="Handbook",
        root_page_ids=["pa", "pb"],
        pages={"pa": a, "pb": b},
    )
    return Interchange(wikis=[wiki])


# --- pure checks (no browser) ---------------------------------------------


def test_polyfill_is_vendored_and_present():
    assert pdf_paged.POLYFILL_PATH.is_file()
    assert pdf_paged.POLYFILL_PATH.read_text(encoding="utf-8").lstrip().startswith("/**")


def test_paged_style_declares_the_paged_media_footer_and_toc_numbering():
    style = pdf_paged._PAGED_STYLE
    # A margin box must be declared so paged.js creates it (we stamp into it).
    assert "@bottom-right" in style
    # TOC page numbers come from target-counter on each entry's href.
    assert "target-counter(attr(href), page)" in style
    # Per-page break must be re-declared outside @media print (paged.js
    # paginates in screen context).
    assert ".page-break" in style and "break-before: page" in style


def test_paged_style_reserves_bottom_margin_so_footer_never_overlaps():
    # A generous bottom margin keeps body content off the footer band.
    match = re.search(r"margin:\s*\S+\s+\S+\s+(\d+)mm", pdf_paged._PAGED_STYLE)
    assert match and int(match.group(1)) >= 18


def test_with_paged_assets_injects_the_style_into_head():
    html = "<!DOCTYPE html><html><head><title>x</title></head><body>hi</body></html>"
    out = pdf_paged._with_paged_assets(html)
    assert "@bottom-right" in out
    assert out.index("@bottom-right") < out.index("</head>")


# --- browser-gated content checks ------------------------------------------


@pytest.mark.skipif(
    not pdf_paged.PAGED_AVAILABLE,
    reason="no Chromium-based browser (or vendored polyfill) available",
)
def test_running_footer_and_toc_page_numbers_render():
    from pypdf import PdfReader

    pdf = pdf_paged.render_pdf_paged(_two_page_wiki(), _no_blobs)
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    # title + TOC + two pages (each root page breaks onto its own sheet).
    assert len(reader.pages) >= 4

    texts = [(pg.extract_text() or "") for pg in reader.pages]
    full = " ".join(t.replace("\n", " ") for t in texts)
    # #39: the running footer names the wiki AND the current page, per sheet.
    assert "Handbook · Alpha Page" in full
    assert "Handbook · Beta Page" in full

    # #40: the TOC page carries real page numbers (target-counter), so its text
    # contains digits alongside the entry titles.
    toc_text = next(t for t in texts if "Table of contents" in t)
    assert "Alpha Page" in toc_text and "Beta Page" in toc_text
    assert re.search(r"\d", toc_text), "TOC entries have no page numbers"
