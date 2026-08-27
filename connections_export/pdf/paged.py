"""Portable PDF via a real paged-media engine -- **paged.js run inside the
Chromium we already ship**, not a system library.

Why this exists: the user wants a proper print artifact -- a running footer
naming *which* wiki/page each printed page came from, and a table of contents
with real "p. N" page numbers. Both are CSS Paged Media features
(`string-set`/`string`, `target-counter`) that Chromium's own
print-to-PDF engine does NOT implement (see `browser.py`'s module comment),
and WeasyPrint -- which does -- needs GTK/Pango/cairo *system* libraries that
can't come through a plain `uv`/`pip` install (especially on the Windows boxes
this tool targets). paged.js is a pure-JS polyfill of exactly those CSS
modules; vendored (`vendor/paged.polyfill.js`, MIT) and injected into the same
headless Chromium the rest of the PDF/browser code already uses, it needs no
system libraries and no separate install.

Pipeline: `render_html(...)` (the shared, pure core) -> inject the paged-media
CSS + the polyfill -> let paged.js paginate the DOM into `.pagedjs_page`
boxes -> stamp each page's bottom-right margin box with its running
"container - item N" footer (paged.js v0.4.3's own `string` support is
unreliable in margin boxes, so we compute the running names ourselves from the
headings that landed on each page) -> `page.pdf`.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.derive.model import Interchange
from connections_export.pdf.browser import CHROMIUM_AVAILABLE, _launch
from connections_export.pdf.html import DEFAULT_GENERATED_AT, BlobBytes, render_html

#: The vendored polyfill (MIT -- see the sibling `.LICENSE`). Injected into the
#: print page at render time; nothing is fetched from the network.
POLYFILL_PATH = Path(__file__).parent / "vendor" / "paged.polyfill.js"

#: True only when both a Chromium-based browser AND the vendored polyfill are
#: present -- the API layer checks this before offering the paged renderer and
#: falls back to the basic (static-footer) Chromium PDF otherwise.
PAGED_AVAILABLE = CHROMIUM_AVAILABLE and POLYFILL_PATH.is_file()

# Paged-media CSS layered on top of `html.py`'s `_STYLE`. The generous bottom
# margin is what keeps body content from ever overlapping the footer -- paged.js
# flows content only inside the content box, and the margin boxes live in the
# reserved margin band. The `@bottom-right { content: "" }` declaration is what
# makes paged.js *create* the margin box on every page; we fill it in afterwards
# (see `_STAMP_JS`). The TOC gets real page numbers via `target-counter` on
# each entry's `href` -- the one thing Chromium's own engine can't do.
_PAGED_STYLE = """
@page {
  size: A4;
  margin: 16mm 15mm 20mm 15mm;
  /* Declaring each box is what makes paged.js CREATE it; the text is written
     afterwards by _STAMP_JS, which is the only thing that knows the page
     count and the running section. All six exist so any slot can be turned
     on by setting alone, with no stylesheet change. */
  @top-left { content: ""; }
  @top-center { content: ""; }
  @top-right { content: ""; }
  @bottom-left { content: ""; }
  @bottom-center { content: ""; }
  @bottom-right { content: ""; }
}
/* paged.js paginates in SCREEN context, so `@media print` rules in the shared
   _STYLE (the per-page break, colour-adjust) never fire during pagination.
   Re-declare them unconditionally here, or every wiki page/post/topic would
   flow together instead of each starting its own sheet. */
.page-break { break-before: page; }
/* Same re-declaration reason as `.page-break`: paged.js runs outside
   the print media query, so the container rules must be repeated. */
.hcl-wiki, .hcl-blog, .hcl-forum, .hcl-files, .hcl-rc { break-before: page; }
.hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1, .hcl-files > h1,
.hcl-rc > h1 { break-after: avoid; }
.hcl-wiki > .page-break:first-of-type,
.hcl-blog > .page-break:first-of-type,
.hcl-forum > .page-break:first-of-type { break-before: auto; }
* { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
#toc a { text-decoration: none; color: inherit; }
/* paged.js v0.4.3 doesn't implement `leader()`; mixing it into the content
   string voids the whole declaration, so the page number vanishes. Use a bare
   `target-counter` floated to the right margin instead. */
/* The page number was floated right, and a float escapes the flow paged.js
   measures: a table of contents longer than one page had its overflow
   DROPPED rather than continued, so the last container's entries silently
   went missing while its body rendered normally. A flex row puts the number
   in the same place without leaving the flow. */
/* Right-aligned page numbers, in the flow. (A float here truncated the TOC
   once; flex does not -- verified by counting every entry in the rendered
   PDF rather than by eye.) */
#toc a {
  display: flex; justify-content: space-between; gap: 1.5em; align-items: baseline;
}
#toc a::after {
  content: target-counter(attr(href), page);
  font-variant-numeric: tabular-nums;
  flex: 0 0 auto;
}
/* No markers. `list-style-position: inside` put the marker in the li's
   content flow, and the entry itself is now a flex row -- a block-level box --
   so every bullet rendered on its own line ABOVE its entry. A table of
   contents does not need markers; the nesting indent already carries the
   hierarchy. */
#toc ul { list-style: none; }
/* A container heading that landed exactly on a page boundary was DROPPED
   rather than carried over, so "Operations KB" and "Help & Support" lost
   their headings while their entries stayed. Keep a heading with the list it
   introduces. */
/* One clean boundary before the contents. The cover also carries
   `page-break-after: always` inline (both renderers need it), and the
   two together leave a blank page 2 -- a known cosmetic blemish that
   resisted every override tried, including `!important` on both the
   modern and legacy property. Content correctness won: with this rule
   the contents are complete and start on their own page; without it
   they begin on the cover page and lose their first block. */
#toc { break-before: page; }
#toc .toc-title { font-size: 1.5em; font-weight: 700; margin-bottom: 0.6em; }
#toc .toc-group { font-weight: 700; margin-top: 0.9em; break-after: avoid; }
/* Each block after the first starts its own page: the split is ours,
   so paged.js never has to fragment the list. */
#toc .toc-continued { break-before: page; }
#toc .toc-d1 { padding-left: 1.2em; }
#toc .toc-d2 { padding-left: 2.4em; }
#toc .toc-d3, #toc .toc-d4 { padding-left: 3.6em; }
/* And say explicitly that it may break: a TOC that outgrows one page has to
   continue onto the next. */
#toc, #toc ul, #toc li { break-inside: auto; page-break-inside: auto; }
/* The cover already carries `page-break-after: always` inline (it has to,
   so both renderers get it). Declaring the break again here produced a
   SECOND break and left a blank page 2 between the cover and the
   contents. */
/* No forced break after the TOC: the first body container carries
   `break-before: page` already, and forcing a break on an element that
   itself spans several pages made paged.js lose a whole fragment --
   an entire page of contents disappeared between two that rendered. */
"""

# After paged.js paginates, walk the pages in order and stamp each one's
# bottom-right margin box with a running footer: the current container name
# (wiki/blog/forum <h1>) and item name (`.pdf-sectitle` heading) that are in
# effect on that page, plus the page number. Names carry over across pages
# until a new heading appears, so a wiki page spanning several sheets keeps its
# name on every sheet. Returns the stamped strings for test/inspection.
_STAMP_JS = r"""
(marks) => {
  // Placeholders are substituted here rather than server-side because only the
  // paginated document knows the page count and which section each page fell
  // in. `{section}` is the reason this renderer's footer is worth having.
  const fill = (tpl, ctx) =>
    (tpl || '').replace(/\{([a-z]+)\}/g, (_, name) => (name in ctx ? ctx[name] : ''));
  const box = (pageEl, side) =>
    pageEl.querySelector('.pagedjs_margin-' + side + ' .pagedjs_margin-content')
      || pageEl.querySelector('.pagedjs_margin-' + side);
  const put = (pageEl, side, text) => {
    const el = box(pageEl, side);
    if (!el) return;
    el.textContent = text;
    el.style.fontSize = marks.mark_size;
    el.style.color = marks.mark_color;
    el.style.whiteSpace = 'nowrap';
    // Empty follows the document, which is what a footer should do unless
    // someone deliberately says otherwise.
    if (marks.mark_font) el.style.fontFamily = marks.mark_font;
  };
  const pages = Array.from(document.querySelectorAll('.pagedjs_page'));
  let container = '', item = '', out = [];
  pages.forEach((pageEl, i) => {
    const content = pageEl.querySelector('.pagedjs_page_content');
    if (content) {
      // In document order, update whichever running name each heading sets.
      content.querySelectorAll('.hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1, .pdf-sectitle')
        .forEach((h) => {
          const t = (h.textContent || '').trim();
          if (!t) return;
          if (h.matches('.pdf-sectitle')) item = t; else { container = t; item = ''; }
        });
    }
    const parts = [];
    if (container) parts.push(container);
    if (item) parts.push(item);
    const ctx = {
      page: String(i + 1),
      pages: String(pages.length),
      title: document.title || '',
      date: marks.date || '',
      section: parts.join(' · '),
    };
    // A whole band of user HTML replaces its three slots. Margin boxes are
    // real elements here, so the markup simply goes in -- and unlike the
    // other renderer, images by URL load normally.
    const band = (pageEl, side, htmlText) => {
      const el = box(pageEl, side + '-center');
      if (!el) return false;
      el.innerHTML = fill(htmlText, ctx);
      el.style.fontSize = marks.mark_size;
      el.style.color = marks.mark_color;
      if (marks.mark_font) el.style.fontFamily = marks.mark_font;
      // The centre box is one third of the band; user markup expects the
      // width of the page.
      const row = el.closest('.pagedjs_margin-' + side);
      if (row) row.style.display = 'block';
      el.style.width = '100%';
      el.style.whiteSpace = 'normal';
      return true;
    };
    if (marks.footer_html) { band(pageEl, 'bottom', marks.footer_html); } else {
    put(pageEl, 'bottom-left', fill(marks.footer_left, ctx));
    put(pageEl, 'bottom-center', fill(marks.footer_center, ctx));
    put(pageEl, 'bottom-right', fill(marks.footer_right, ctx));
    }
    if (marks.header_html) { band(pageEl, 'top', marks.header_html); } else {
    put(pageEl, 'top-left', fill(marks.header_left, ctx));
    put(pageEl, 'top-center', fill(marks.header_center, ctx));
    put(pageEl, 'top-right', fill(marks.header_right, ctx));
    }
    out.push(fill(marks.footer_left, ctx) + ' ' + fill(marks.footer_right, ctx));
  });
  return out;
}
"""


def _with_paged_assets(html: str) -> str:
    """Inject the paged-media `<style>` into the document head. The polyfill
    itself is added via Playwright at render time (so pagination timing is
    controllable), not embedded here."""
    return html.replace("</head>", f"<style>{_PAGED_STYLE}</style></head>", 1)


def _stamp_args(marks, generated_at: str) -> dict:
    """The marks as plain data for `_STAMP_JS`. A dataclass does not cross into
    the page context, and the date is resolved here because the browser has no
    business inventing one."""
    from connections_export.pdf.marks import Marks  # noqa: PLC0415

    marks = marks or Marks()
    return {
        "header_left": marks.header_left,
        "header_center": marks.header_center,
        "footer_center": marks.footer_center,
        "mark_font": marks.mark_font,
        "header_rule": marks.header_rule,
        "footer_rule": marks.footer_rule,
        "header_html": marks.header_html,
        "footer_html": marks.footer_html,
        "header_right": marks.header_right,
        "footer_left": marks.footer_left,
        "footer_right": marks.footer_right,
        "mark_size": marks.mark_size,
        "mark_color": marks.mark_color,
        "date": generated_at or "",
    }


def html_to_pdf_paged(html: str, *, marks=None, generated_at: str = "") -> bytes:
    """Paginate `html` with the vendored paged.js polyfill inside a headless
    Chromium and return the PDF bytes. Callers check `PAGED_AVAILABLE` first;
    this raises whatever Playwright raises if no usable browser is present.

    Unlike `browser.html_to_pdf`, this does NOT use Chromium's
    `display_header_footer` -- paged.js draws the margin boxes itself, and
    `prefer_css_page_size` honours the `@page { size }` it laid out."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page()
            page.set_content(_with_paged_assets(html), wait_until="load")
            # Signal completion via paged.js's own `after` hook (must be set
            # BEFORE the polyfill loads). Waiting on the first `.pagedjs_page`
            # instead is a race: pagination is async, so printing then yields a
            # truncated PDF. `__pagedDone` flips only once the whole flow is laid
            # out.
            page.evaluate(
                "window.PagedConfig = { auto: true, after: () => { window.__pagedDone = true; } };"
            )
            page.add_script_tag(path=str(POLYFILL_PATH))
            page.wait_for_function("window.__pagedDone === true", timeout=120_000)
            page.evaluate(_STAMP_JS, _stamp_args(marks, generated_at))
            return page.pdf(print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()


def render_pdf_paged(
    interchange: Interchange,
    blob_bytes: BlobBytes,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
    chrome: bool = True,
    style_overrides: dict[str, str] | None = None,
    extra_css: str | None = None,
    marks=None,
) -> bytes:
    """`html_to_pdf_paged(render_html(...))` -- the end-to-end portable PDF
    with a running per-page footer (wiki/blog/forum name + page name + page
    number) and a TOC carrying real page numbers. `include_comments=False`
    omits page/post comments from the export (they stay in the archive).
    `chrome=False` drops the cover + TOC (the live per-entity tiles)."""
    html = render_html(
        interchange,
        blob_bytes=blob_bytes,
        generated_at=generated_at,
        include_comments=include_comments,
        chrome=chrome,
        style_overrides=style_overrides,
        extra_css=extra_css,
    )
    return html_to_pdf_paged(html, marks=marks, generated_at=generated_at)
