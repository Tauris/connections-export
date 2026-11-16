"""Every word of a page reaches the PDF once, wherever the pages break.

paged.js 0.4.3 decides where the next page resumes after a break inside text
by searching the source for the text it rendered -- the first text node that
contains it, the first place in it. Where a paragraph's or a cell's text runs
between screenshots, links or bold words, an earlier run often contains the
same words: the next page resumed there, repeated content, and what followed
was never printed. A German column beside an English one, with screenshots,
lost the English column entirely. `pdf.paged` corrects the polyfill as it
loads it; these tests compare the laid-out text with the source, character
for character.

And author CSS written for print or for a narrow window is in effect while
the layout is measured, not only once Chromium prints it.
"""

from __future__ import annotations

import pytest

from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import render_html, serving_images

from .test_wide_content_fits_the_page import _OVERFLOW_JS, _blobs, _model

needs_browser = pytest.mark.skipif(
    not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium"
)

GERMAN = "Dies ist ein deutscher Absatz mit ziemlich viel Text, der umbrechen sollte. " * 3
ENGLISH = "This is an English paragraph with quite a lot of text that should wrap. " * 3


def _shot(width: int, height: int) -> str:
    return f'<img src="cid:wide.png" style="width:{width}px;height:{height}px">'


#: One table row, three pages tall: each language with its screenshots.
BILINGUAL = (
    "<table border=1><tr>"
    f"<td>{(GERMAN + _shot(900, 700)) * 4}ENDE-DEUTSCH</td>"
    f"<td>{(ENGLISH + _shot(900, 700)) * 4}END-ENGLISH</td>"
    "</tr></table>"
)

#: Text running between links, bold words and screenshots, repeating itself.
INLINE_MIX = (
    "<div>"
    + "".join(
        f'{GERMAN}<a href="https://example.com/">Link</a> und <b>fett</b> {_shot(900, 600)} '
        for _ in range(5)
    )
    + "THE-END</div>"
)

_TEXT_JS = """
() => {
  // Text paged.js left past a page's foot is in a CSS column out of sight to
  // the right of the page: in the DOM, never printed. Counted as present, it
  // would hide exactly the loss these tests are for.
  let hidden = 0;
  const pages = Array.from(document.querySelectorAll('.pagedjs_page'));
  for (const page of pages) {
    const area = page.querySelector('.pagedjs_page_content').getBoundingClientRect();
    const walker = document.createTreeWalker(page, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim() || !node.parentElement.closest('.page-body')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) if (r.width && r.left >= area.right - 1) hidden += 1;
    }
  }
  const text = pages.map((p) => Array.from(p.querySelectorAll('.page-body'))
    .map((body) => body.innerText).join('')).join('');
  return [text, hidden];
}
"""


def _squeeze(text: str) -> str:
    # paged.js adds a hyphen where it breaks inside a word.
    return "".join(text.replace("‑", "").split())


def _source_and_laid_out(content: str) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    from connections_export.pdf import paged
    from connections_export.pdf.browser import launch_browser

    with serving_images() as served:
        html = render_html(_model(content), blob_bytes=_blobs)
    with sync_playwright() as p:
        browser = launch_browser(p)
        guard = paged.PrintPage(browser, served=served, pdf_timeout=60)
        guard.page.set_content(html, wait_until="domcontentloaded")
        source = guard.page.evaluate("() => document.querySelector('.page-body').innerText")
        paged.lay_out(guard.page, html, pdf_timeout=60)
        laid_out, hidden = guard.page.evaluate(_TEXT_JS)
        browser.close()
    assert hidden == 0, f"{hidden} pieces of text are past a page's foot, never printed"
    return _squeeze(source), _squeeze(laid_out)


@needs_browser
@pytest.mark.parametrize("content", [BILINGUAL, INLINE_MIX], ids=["bilingual", "inline-mix"])
def test_the_laid_out_text_is_the_source_text_exactly(content):
    source, laid_out = _source_and_laid_out(content)
    assert laid_out == source


@needs_browser
def test_the_second_column_of_a_row_taller_than_a_page_is_printed():
    _, laid_out = _source_and_laid_out(BILINGUAL)
    assert "ENDE-DEUTSCH" in laid_out and "END-ENGLISH" in laid_out
    assert laid_out.count("Thisisan") == ENGLISH.count("This is an") * 4


def test_the_corrections_apply_to_the_vendored_polyfill():
    from connections_export.pdf import paged

    source = paged.polyfill_source()
    assert source.count("/* hcl") == 2
    # The published file itself is untouched.
    assert "/* hcl" not in paged.POLYFILL_PATH.read_text(encoding="utf-8")


def test_a_different_polyfill_is_refused_rather_than_used_uncorrected(monkeypatch, tmp_path):
    from connections_export.pdf import paged

    other = tmp_path / "paged.polyfill.js"
    other.write_text("/** some other version */", encoding="utf-8")
    monkeypatch.setattr(paged, "POLYFILL_PATH", other)
    paged.polyfill_source.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="not the version"):
            paged.polyfill_source()
    finally:
        paged.polyfill_source.cache_clear()


@needs_browser
@pytest.mark.parametrize(
    "rule",
    [
        "@media print { .bi { width: 1400px !important } .bi td { min-width: 700px !important } }",
        "@media (max-width: 900px) { .bi td { white-space: nowrap } }",
    ],
    ids=["print-only", "narrow-window"],
)
def test_author_css_for_print_is_in_effect_when_the_page_is_fitted(rule):
    from playwright.sync_api import sync_playwright

    from connections_export.pdf import paged
    from connections_export.pdf.browser import launch_browser

    content = (
        f'<style>{rule}</style><table class="bi">'
        f"<tr><td>{GERMAN}</td><td>{ENGLISH}</td></tr></table>"
    )
    with serving_images() as served:
        html = render_html(_model(content), blob_bytes=_blobs)
    with sync_playwright() as p:
        browser = launch_browser(p)
        guard = paged.PrintPage(browser, served=served, pdf_timeout=60)
        paged.lay_out(guard.page, html, pdf_timeout=60)
        found = guard.page.evaluate(_OVERFLOW_JS)
        # What the fitting measured is what gets printed: print media, and a
        # window as wide as the sheet.
        media = guard.page.evaluate("() => [matchMedia('print').matches, window.innerWidth]")
        browser.close()
    assert found["overflow"] == []
    assert media == [True, paged.PRINT_VIEWPORT["width"]]
