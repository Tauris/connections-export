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

import contextlib
import functools
import secrets
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from connections_export.derive.model import Interchange
from connections_export.pdf.browser import CHROMIUM_AVAILABLE, _launch, block_network
from connections_export.pdf.html import (
    DEFAULT_GENERATED_AT,
    BlobBytes,
    ServedImages,
    attaching_files,
    document_head,
    document_pieces,
    remote_link_register,
    serving_images,
)
from connections_export.pdf.progress import (
    PdfOutOfMemoryError,
    PdfProgress,
    ProgressCallback,
    ignore,
)

#: Where each heading the renderer marked for the outline (`data-outline`: its
#: level) landed once paged.js laid the document out -- the same page the
#: table of contents' number points at. paged.js can repeat an element that
#: straddles a page break; its `data-ref` says which original it is.
_OUTLINE_JS = """
() => {
  const pages = Array.from(document.querySelectorAll('.pagedjs_page'));
  const seen = new Set();
  const out = [];
  document.querySelectorAll('[data-outline]').forEach((el, i) => {
    const key = el.getAttribute('data-ref') || ('i' + i);
    if (seen.has(key)) return;
    seen.add(key);
    const page = pages.indexOf(el.closest('.pagedjs_page'));
    const title = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (page >= 0 && title) {
      out.push({ title, level: parseInt(el.getAttribute('data-outline'), 10) || 1, page });
    }
  });
  return out;
}
"""


def with_outline(pdf: bytes, entries: list[dict], *, open_panel: bool = False) -> bytes:
    """`pdf` with bookmarks for `entries` (`{title, level, page}`, in document
    order), nested by level. `open_panel` asks viewers to open with the
    bookmarks panel showing -- a choice, not a default: some viewers lay the
    panel over the page rather than beside it.

    Built here rather than asked of Chromium: its outline turns every heading
    into a bookmark -- each "Comments" and "Replies" label and every heading an
    author wrote inside a page -- and nests a wiki's pages beside the wiki.
    These are exactly the document's containers and pages."""
    if not entries:
        return pdf
    import io  # noqa: PLC0415

    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    add_outline(writer, entries, open_panel=open_panel)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def add_outline(writer, entries: list[dict], *, open_panel: bool = False) -> None:
    """Add bookmarks for `entries` to a pypdf `writer` (see `with_outline`)."""
    if not entries:
        return
    last = len(writer.pages) - 1
    parents: dict[int, object] = {}
    for entry in entries:
        level = max(1, int(entry["level"]))
        parent = next((parents[n] for n in range(level - 1, 0, -1) if n in parents), None)
        item = writer.add_outline_item(
            entry["title"], min(max(int(entry["page"]), 0), last), parent=parent
        )
        parents[level] = item
        for deeper in [n for n in parents if n > level]:
            del parents[deeper]
    if open_panel:
        writer.page_mode = "/UseOutlines"


@dataclass
class SetMember:
    """One file of a set of PDFs (`pdf.sets`): links to items in the other
    files (item id -> link target), the cover's note, the item anchors whose
    positions the other files need, and where to put those positions."""

    remote: dict[str, str]
    set_note: str
    extra_targets: set[str]
    positions_out: dict = field(default_factory=dict)


def attach_files(pdf: bytes, attached, blob_bytes) -> bytes:
    """`pdf` with each `(name, digest)` of `attached` added as an embedded
    file, read from the archive one at a time."""
    if not attached:
        return pdf
    import io  # noqa: PLC0415

    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    for name, digest in attached:
        data = blob_bytes(digest)
        if data is not None:
            writer.add_attachment(name, data)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class PdfRenderError(RuntimeError):
    """A PDF failure with browser resources that could not load.

    `failures` are loads that went wrong; `blocked` are requests the render
    refused on purpose (`browser.block_network`) -- listed apart, since they
    are the guard working rather than the cause of anything."""

    def __init__(
        self, message: str, *, failures: list[str] | None = None, blocked: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.failures = failures or []
        self.blocked = blocked or []


#: The vendored polyfill (MIT -- see the sibling `.LICENSE`). Injected into the
#: print page at render time; nothing is fetched from the network.
POLYFILL_PATH = Path(__file__).parent / "vendor" / "paged.polyfill.js"

#: True only when both a Chromium-based browser AND the vendored polyfill are
#: present -- the API layer checks this before offering the paged renderer and
#: falls back to the basic (static-footer) Chromium PDF otherwise.
PAGED_AVAILABLE = CHROMIUM_AVAILABLE and POLYFILL_PATH.is_file()

#: Where paged.js 0.4.3 loses content, corrected as the polyfill is loaded --
#: the vendored file stays exactly as published. Each pair is (published
#: text, replacement); both must be found once, or loading fails loudly rather
#: than printing with the fault back in.
#:
#: After a break inside text, paged.js finds where the next page resumes by
#: looking for the text it rendered in the source: the FIRST text node of the
#: parent that contains it, and in that node the FIRST place it occurs. In a
#: paragraph or table cell whose text runs between screenshots, links or bold
#: words, an earlier run often contains the same words -- the next page then
#: resumed at the earlier place, repeated what followed it, and what came
#: after was never printed -- in a two-column table with screenshots, the
#: whole second column.
#:
#: The text node is now found next to the nearest element beside it, which
#: paged.js does track; the offset counts from the end when the rendered text
#: is the rest of the node, as it is on a continued page.
_POLYFILL_PATCHES: tuple[tuple[str, str], ...] = (
    (
        """	function indexOfTextNode(node, parent) {
		if (!isText(node)) {
			return -1;
		}
		let nodeTextContent = node.textContent;
		let child;
		let index = -1;
		for (var i = 0; i < parent.childNodes.length; i++) {
			child = parent.childNodes[i];
			if (child.nodeType === 3) {
				let text = parent.childNodes[i].textContent;
				if (text.includes(nodeTextContent)) {
					index = i;
					break;
				}
			}
		}

		return index;
	}
""",
        """	function indexOfTextNode(node, parent) {
		if (!isText(node)) {
			return -1;
		}
		let nodeTextContent = node.textContent;
		let kids = parent.childNodes;
		let holds = (k) => k.nodeType === 3 && k.textContent.includes(nodeTextContent);
		let refIndex = (ref) => {
			for (let i = 0; i < kids.length; i++) {
				if (kids[i].nodeType === 1 && kids[i].dataset && kids[i].dataset.ref === ref) {
					return i;
				}
			}
			return -1;
		};
		/* hcl: anchored on the nearest element before it, then after it. */
		for (let s = node.previousSibling; s; s = s.previousSibling) {
			if (s.nodeType === 1 && s.dataset && s.dataset.ref) {
				let at = refIndex(s.dataset.ref);
				for (let j = at + 1; at !== -1 && j < kids.length && kids[j].nodeType !== 1; j++) {
					if (holds(kids[j])) return j;
				}
				break;
			}
		}
		for (let s = node.nextSibling; s; s = s.nextSibling) {
			if (s.nodeType === 1 && s.dataset && s.dataset.ref) {
				let at = refIndex(s.dataset.ref);
				for (let j = at - 1; at !== -1 && j >= 0 && kids[j].nodeType !== 1; j--) {
					if (holds(kids[j])) return j;
				}
				break;
			}
		}
		for (let i = 0; i < kids.length; i++) {
			if (holds(kids[i])) return i;
		}
		return -1;
	}
""",
    ),
    (
        "\t\t\t\toffset += node.textContent.indexOf(container.textContent);",
        "\t\t\t\toffset += node.textContent.endsWith(container.textContent)"
        " ? node.textContent.length - container.textContent.length"
        " : node.textContent.indexOf(container.textContent); /* hcl */",
    ),
)


@functools.cache
def polyfill_source() -> str:
    """The polyfill as injected: the vendored file with `_POLYFILL_PATCHES`."""
    source = POLYFILL_PATH.read_text(encoding="utf-8")
    for published, replacement in _POLYFILL_PATCHES:
        if source.count(published) != 1:
            raise RuntimeError(
                "the vendored paged.js is not the version these corrections were "
                f"written for; this text was not found once: {published[:60]!r}"
            )
        source = source.replace(published, replacement)
    return source


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
  /* The side margins decide the content width: CONTENT_WIDTH is 210mm less
     these two. Change them together. */
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
  /* A fixed width, so an entry wraps the same whatever its number is. A
     document rendered in parts lays its contents out before the numbers are
     known, and must still come out the same length (pdf/parts.py). */
  min-width: 3.2em; text-align: right;
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
/* Where each external image first appears, as the TOC gives it. */
.hcl-external-table a.hcl-external-mark::after {
  content: " · p. " target-counter(attr(href), page);
  /* Fixed width for the same reason as the contents' numbers: the column's
     width must not depend on them. */
  display: inline-block; min-width: 4.6em; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
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
  // `section` and `title` come from the captured content -- page titles
  // anyone could write. Where the result becomes markup (a whole band, set
  // with innerHTML) each substituted value is escaped; the band's own markup
  // is the user's and stays markup. Slots are written as text, unescaped.
  const esc = (value) => String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fill = (tpl, ctx, encode) =>
    (tpl || '').replace(/\{([a-z]+)\}/g, (_, name) => (
      Object.prototype.hasOwnProperty.call(ctx, name)
        ? (encode ? encode(ctx[name]) : ctx[name]) : ''));
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
  // A document rendered in parts: this part's first page number in the whole,
  // the whole's page count, and the running names in effect where the
  // previous part ended (`pdf/parts.py`). All absent for a one-piece render.
  const offset = marks.page_offset || 0;
  const total = marks.pages_total || (offset + pages.length);
  let container = marks.start_container || '', item = marks.start_item || '', out = [];
  pages.forEach((pageEl, i) => {
    const content = pageEl.querySelector('.pagedjs_page_content');
    // The cover stands alone: no running header or footer on it. It still
    // counts as page 1.
    if (content && content.querySelector('#title-page')) { out.push(''); return; }
    if (content) {
      // In document order, update whichever running name each heading sets.
      content.querySelectorAll(
        '.hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1, #external-content > h1, .pdf-sectitle')
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
      page: String(offset + i + 1),
      pages: String(total),
      title: document.title || '',
      date: marks.date || '',
      section: parts.join(' · '),
    };
    // A whole band of user HTML replaces its three slots. Margin boxes are
    // real elements here, so the markup simply goes in. Images in it must be
    // inline (`data:` or `<svg>`), as in the other renderer: the print page
    // loads nothing by URL (`browser.block_network`).
    const band = (pageEl, side, htmlText) => {
      const el = box(pageEl, side + '-center');
      if (!el) return false;
      el.innerHTML = fill(htmlText, ctx, esc);
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
  return { lines: out, container: container, item: item };
}
"""


def _content_security_policy(nonce: str) -> str:
    """Script from the polyfill only.

    paged.js needs JavaScript on, and the document is untrusted: a script or
    `on*=` handler that slipped past sanitization would run beside it with the
    whole export in reach. The policy admits only a script carrying this
    render's nonce -- the polyfill, inserted by us -- and no inline handler.
    Loads are left to `browser.block_network`, which answers them without
    touching the network; refusing them here instead would fail paged.js's own
    stylesheet requests and stall pagination."""
    return f"script-src 'nonce-{nonce}'; object-src 'none'; base-uri 'none'; form-action 'none'"


#: The TOC and external-images numbers, as `target-counter` writes them.
_TARGET_COUNTERS = (
    ("content: target-counter(attr(href), page);", "content: attr(data-hcl-page);"),
    (
        'content: " · p. " target-counter(attr(href), page);',
        'content: " · p. " attr(data-hcl-page);',
    ),
)


def _numbers_from_attribute(style: str) -> str:
    """`style` with page numbers read from `data-hcl-page` instead of computed
    by `target-counter`. A document rendered in parts points its links at
    other documents, where `target-counter` finds nothing (paged.js then
    writes 0, and its generated rules win over any override); the numbers
    are filled in from where the other parts put each target."""
    for counted, given in _TARGET_COUNTERS:
        assert counted in style, counted
        style = style.replace(counted, given)
    return style


#: Where in a document's head the paged-media sheet goes, when not last.
AFTER_PAGED_STYLE = "<!--hcl-after-paged-style-->"


def _with_paged_assets(
    html: str, nonce: str | None = None, *, numbers_from_attribute: bool = False
) -> str:
    """Inject the paged-media `<style>` into the document head, and -- given
    a nonce -- the content security policy as the head's FIRST element, so it
    is in force before any of the document's own markup is parsed. The
    polyfill itself is added at render time (so pagination timing is
    controllable), not embedded here."""
    style = _numbers_from_attribute(_PAGED_STYLE) if numbers_from_attribute else _PAGED_STYLE
    # A part of a larger document carries its author sheets in the head; they
    # must follow the paged-media sheet, as they do in the one document.
    marker = AFTER_PAGED_STYLE if AFTER_PAGED_STYLE in html else "</head>"
    html = html.replace(marker, f"<style>{style}</style>{marker}", 1)
    if nonce is not None:
        meta = (
            '<meta http-equiv="Content-Security-Policy" '
            f'content="{_content_security_policy(nonce)}">'
        )
        html = html.replace("<head>", f"<head>{meta}", 1)
    return html


#: The width a page's content has: A4 (210 mm) less the 15 mm side margins
#: `_PAGED_STYLE` gives `@page`. Change the two together.
CONTENT_WIDTH = "180mm"

#: An A4 sheet in CSS pixels: the window the layout is done in.
PRINT_VIEWPORT = {"width": 794, "height": 1123}

#: Make every captured body fit the page's width, before paged.js lays it out.
#: A page written for a scrolling window may be wider than paper -- a table
#: whose cells were given more width than the table, a positioned box, text an
#: author kept from wrapping -- and a page box clips what sticks out. Content
#: must never be lost that way, so, measured at the content width the page
#: will have (body set to CONTENT_WIDTH while measuring), in this order:
#:
#: 1. text kept on one line (nowrap, pre) may wrap;
#:    a picture, video or embed is capped at the page width itself;
#: 2. a box an author placed (absolute, fixed, relative or translated) out past
#:    the edge goes back into the normal flow, where it appears after what
#:    precedes it -- scaling cannot bring back a box that starts beyond the
#:    edge without shrinking everything around it;
#: 3. a table that sticks out is loosened -- automatic layout, and the widths
#:    an author gave the table, its columns and its cells dropped -- so it
#:    reflows to the page instead of honouring them;
#: 4. what still sticks out is scaled down as a whole (CSS `zoom`, which the
#:    layout then measures as it is). There is no lower limit: smaller and
#:    complete beats cut off.
#:
#: Whatever the width: a table whose pictures its columns squeeze below 60% of
#: their own width prints its cells one after another, and every table's
#: column widths are fixed for paged.js to keep across page breaks.
#:
#: "Sticks out" is measured over the whole subtree, not the element's own box:
#: a cell's content can run past a table whose box fits. Returns how many
#: blocks were scaled.
_FIT_JS = r"""
([width]) => {
  const body = document.body;
  const saved = [body.style.width, body.style.margin];
  body.style.width = width;
  body.style.margin = '0';
  const reach = (el) => {
    const own = el.getBoundingClientRect();
    let right = Math.max(own.right, own.left + el.scrollWidth);
    for (const d of el.querySelectorAll('*')) {
      const r = d.getBoundingClientRect();
      if (r.width || r.height) right = Math.max(right, r.right);
    }
    return right;
  };
  const important = (el, prop, value) => el.style.setProperty(prop, value, 'important');
  const loosen = (table) => {
    important(table, 'table-layout', 'auto');
    important(table, 'width', 'auto');
    table.removeAttribute('width');
    table.querySelectorAll('td, th, col, colgroup').forEach((cell) => {
      if (cell.closest('table') !== table) return;
      cell.removeAttribute('width');
      important(cell, 'width', 'auto');
    });
  };
  // The width a picture asks for: its width attribute, else its own CSS
  // width in pixels, else its natural width once loaded.
  const asked = (img) => {
    const attr = parseFloat(img.getAttribute('width'));
    if (attr > 0) return attr;
    const inline = img.style.width;
    if (inline && inline.endsWith('px')) return parseFloat(inline);
    return img.naturalWidth || 0;
  };
  // A table whose pictures are squeezed by its columns -- screenshots side by
  // side, a German column beside an English one -- prints its cells one after
  // another at the full width instead. Side by side, each would be a fraction
  // of the page, too small to read.
  const squeezed = (table, width) => Array.from(table.querySelectorAll('img')).some((img) => {
    if (img.closest('table') !== table) return false;
    const want = Math.min(asked(img), width);
    return want >= 200 && img.getBoundingClientRect().width < 0.6 * want;
  });
  const stack = (table) => {
    const parts = table.querySelectorAll('thead, tbody, tfoot, tr, td, th, caption');
    for (const el of [table, ...parts]) {
      if (el !== table && el.closest('table') !== table) continue;
      important(el, 'display', 'block');
      important(el, 'width', 'auto');
      if (el.tagName === 'TD' || el.tagName === 'TH') important(el, 'margin-bottom', '0.4em');
    }
    table.removeAttribute('width');
    table.setAttribute('data-hcl-stacked', '');
  };
  // Each table keeps the column widths it has now. paged.js continues a row
  // that does not fit on the next page by copying the table there, and the
  // copy works its columns out again from what it holds: where one column's
  // text has ended, that column collapsed and another took the whole width.
  const freeze = (table) => {
    if (getComputedStyle(table).display !== 'table') return;
    const cells = Array.from(table.querySelectorAll('td, th')).filter(
      (cell) => cell.closest('table') === table);
    if (!cells.length) return;
    const widths = cells.map((cell) => parseFloat(getComputedStyle(cell).width));
    important(table, 'width', getComputedStyle(table).width);
    important(table, 'table-layout', 'fixed');
    cells.forEach((cell, i) => { if (widths[i] > 0) important(cell, 'width', widths[i] + 'px'); });
  };
  let scaled = 0;
  const fit = (el, right) => {
    for (const child of Array.from(el.children)) {
      if (reach(child) <= right + 1) continue;
      const box = child.getBoundingClientRect();
      // Its own box fits: what sticks out is inside it, so fix that instead
      // of shrinking everything around it.
      if (box.right <= right + 1 && child.children.length) {
        fit(child, right);
        if (reach(child) <= right + 1) continue;
      }
      const room = right - box.left;
      const need = reach(child) - box.left;
      if (room <= 0 || need <= 0) continue;
      const factor = room / need;
      child.style.zoom = String(factor * (parseFloat(child.style.zoom) || 1));
      child.setAttribute('data-hcl-fit', factor.toFixed(2));
      scaled += 1;
    }
  };
  // What sticks out past the page: text unwrapped, boxes placed, tables
  // loosened, and what still does not fit scaled down.
  const widen = (root, right) => {
    root.querySelectorAll('*').forEach((el) => {
      // Its text as well as its box: a box capped to the page can still have
      // a line inside that runs past it.
      const own = el.getBoundingClientRect();
      if (Math.max(own.right, own.left + el.scrollWidth) <= right + 1) return;
      const style = getComputedStyle(el);
      // A picture, video or embed is scaled to the page itself -- even
      // against an author's inline `max-width: none !important` -- rather
      // than the table or box around it.
      if (/^(IMG|SVG|VIDEO|CANVAS|IFRAME|OBJECT|EMBED)$/i.test(el.tagName)) {
        important(el, 'max-width', '100%');
        important(el, 'height', 'auto');
      }
      if (style.whiteSpace === 'nowrap') important(el, 'white-space', 'normal');
      else if (style.whiteSpace === 'pre') important(el, 'white-space', 'pre-wrap');
      if (style.position !== 'static' || style.transform !== 'none') {
        important(el, 'position', 'static');
        important(el, 'transform', 'none');
        for (const side of ['left', 'right', 'top', 'bottom']) important(el, side, 'auto');
      }
      if (parseFloat(style.marginLeft) > 0 && el.getBoundingClientRect().left >= right) {
        important(el, 'margin-left', '0');
      }
    });
    // Innermost first: a nested table loosened first lets its parent shrink.
    Array.from(root.querySelectorAll('table')).reverse().forEach((table) => {
      if (reach(table) > right + 1) loosen(table);
    });
    fit(root, right);
  };
  document.querySelectorAll(
    '.page-body, .post-body, .topic-body, .reply-body, .comment-body, .rc-body'
  ).forEach((root) => {
    const right = root.getBoundingClientRect().right;
    const tables = () => Array.from(root.querySelectorAll('table')).reverse();
    // Innermost first throughout: a nested table settled first lets its
    // parent settle around it.
    tables().forEach((table) => { if (squeezed(table, root.clientWidth)) stack(table); });
    if (reach(root) > right + 1) widen(root, right);
    tables().forEach(freeze);
  });
  body.style.width = saved[0];
  body.style.margin = saved[1];
  return scaled;
}
"""

#: The last guarantee, after paged.js has laid the pages out: anything that
#: still reaches past a page's content area -- however it got there -- is
#: scaled down inside its page with a transform (top-left anchored, so it
#: keeps its place and only leaves white space below). The layout is fixed by
#: then, so this cannot reflow anything; it only makes sure nothing is cut off.
#: Runs before the footers are stamped and link targets measured, so both see
#: the final page. Returns how many elements it scaled.
_FIT_PAGES_JS = r"""
() => {
  let scaled = 0;
  document.querySelectorAll('.pagedjs_page .pagedjs_page_content').forEach((area) => {
    const a = area.getBoundingClientRect();
    // How far an element and what is inside it reach, box by box. paged.js
    // lays a page out in CSS columns: what runs past the page's foot goes on
    // in the next column, out of sight to the right, and is printed on the
    // next page. Those boxes of an element continued there are not on this
    // page at all. Counted, one row ending a few pixels below
    // the foot made its cell look two thousand pixels wide, and the page
    // holding it was shrunk to a quarter.
    const span = (el, deep) => {
      let left = Infinity, right = -Infinity;
      const take = (e) => {
        const rects = Array.from(e.getClientRects());
        // Continued past the foot: the element is on this page AND beyond
        // its right edge. A box that is only beyond the edge -- one an author
        // placed there -- is on this page, cut off, and counts.
        const continued = rects.some((r) => r.left < a.right - 1);
        for (const r of rects) {
          if ((!r.width && !r.height) || (continued && r.left >= a.right - 1)) continue;
          left = Math.min(left, r.left);
          right = Math.max(right, r.right);
        }
      };
      take(el);
      if (deep) el.querySelectorAll('*').forEach(take);
      return left === Infinity ? [a.left, a.left] : [left, right];
    };
    const inside = ([left, right]) => left >= a.left - 1 && right <= a.right + 1;
    const walk = (el) => {
      for (const child of Array.from(el.children)) {
        let reach = span(child, true);
        if (inside(reach)) continue;
        if (inside(span(child, false)) && child.children.length) {
          walk(child);
          reach = span(child, true);
          if (inside(reach)) continue;
        }
        const [left, right] = reach;
        const factor = Math.min(1, (a.right - a.left) / (right - left));
        const shift = Math.max(0, a.left - left) / factor;
        child.style.transformOrigin = 'top left';
        child.style.transform = `scale(${factor}) translateX(${shift}px)`;
        child.setAttribute('data-hcl-fit-page', factor.toFixed(2));
        scaled += 1;
      }
    };
    // paged.js flows content through a wrapper of CSS columns that is wider
    // than the page by design; the page's content is inside it.
    const flow = area.querySelector(':scope > div');
    walk(flow || area);
  });
  return scaled;
}
"""

#: Inserts the polyfill as a script carrying the render's nonce -- which is
#: what the content security policy admits. (`add_script_tag` cannot set one.)
_INSERT_POLYFILL_JS = """
([source, nonce]) => {
  const script = document.createElement('script');
  script.setAttribute('nonce', nonce);
  script.textContent = source;
  document.head.appendChild(script);
}
"""


def _stamp_args(
    marks,
    generated_at: str,
    *,
    page_offset: int = 0,
    pages_total: int | None = None,
    start_container: str = "",
    start_item: str = "",
) -> dict:
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
        "page_offset": page_offset,
        "pages_total": pages_total or 0,
        "start_container": start_container,
        "start_item": start_item,
    }


#: The printing step has no progress to watch, so its limit grows with the
#: document: half a second a page on top of the render timeout -- about ten
#: times what an 879-page export with 60 photos needed.
PRINT_SECONDS_PER_PAGE = 0.5

_DONE = "html[data-paged-done]"


def _wait_for_layout(page, *, stall_ms: int, on_page: Callable[[int], None] | None = None) -> int:
    """Wait for paged.js to finish, failing only when it stops making pages.

    Layout time grows with the export, so one deadline for all of it fails a
    large archive that is laying out steadily just as it fails a hang. Each
    new page restarts the budget instead; the wait itself runs in Playwright,
    so a page stuck in script still times out. Returns the page count.
    `on_page` hears the count each time it grows."""
    pages = 0
    while True:
        try:
            page.wait_for_selector(
                f"{_DONE}, .pagedjs_pages > .pagedjs_page:nth-child({pages + 1})",
                state="attached",
                timeout=stall_ms,
            )
        except Exception as exc:
            raise TimeoutError(
                f"The layout made no progress for {stall_ms // 1000} s after {pages} pages ({exc})"
            ) from exc
        if page.query_selector(_DONE) is not None:
            return page.locator(".pagedjs_page").count()
        pages = page.locator(".pagedjs_page").count()
        if on_page is not None:
            on_page(pages)


#: What the person is told when the browser runs out of memory and nothing
#: smaller can be tried.
OUT_OF_MEMORY_ADVICE = (
    "Close other programs and try again, export a smaller selection (Export menu,"
    " Choose components), or leave out external images. A smaller"
    ' "PDF part size" (Settings) makes each part need less memory.'
)


def is_out_of_memory(exc: BaseException, crashed: bool = False) -> bool:
    """Whether a render failure is the browser running out of memory.

    Chromium's renderer process is killed when it runs out of memory; the page
    reports a crash and every pending Playwright call fails with "Target
    crashed" (or the page/browser "has been closed", when the whole browser
    went). A page that merely hung is a timeout, not this."""
    if crashed:
        return True
    text = f"{exc} {exc.__cause__ or ''}".lower()
    return any(
        marker in text for marker in ("target crashed", "page crashed", "out of memory", "oom")
    )


def _print_timeout_ms(*, pdf_timeout: float, pages: int) -> int:
    return int((pdf_timeout + PRINT_SECONDS_PER_PAGE * pages) * 1000)


class PrintPage:
    """One guarded page in a launched browser, and what went wrong in it.

    Everything a print page needs before untrusted content goes in: no service
    workers, every load answered by `block_network`, failed loads recorded,
    and the renderer's crash -- the only reliable sign that it ran out of
    memory -- noticed."""

    def __init__(self, browser, *, served: ServedImages | None, pdf_timeout: float) -> None:
        self.failures: list[str] = []
        self.blocked: list[str] = []
        self.crashed = False
        # A service worker would see requests before the route does.
        self.page = browser.new_page(service_workers="block")
        self.page.on("crash", self._on_crash)
        # Laid out as it will be printed: in print media, at the width of the
        # sheet. Author CSS for print, or for a narrow window, otherwise takes
        # effect only when Chromium prints -- after the layout was measured
        # and fitted to the page -- and what it widens is cut off.
        self.page.emulate_media(media="print")
        self.page.set_viewport_size(PRINT_VIEWPORT)
        # The setting governs every step -- loading a large document with
        # its images embedded can take longer than Playwright's own 30 s.
        self.page.set_default_timeout(pdf_timeout * 1000)
        # Before `set_content`: the document is untrusted, and nothing in
        # it may load from -- or report to -- anywhere. The polyfill is
        # unaffected: it is inserted as text, never requested.
        block_network(self.page, self.blocked, served)
        self.page.on("requestfailed", self._request_failed)
        self.page.on("response", self._response_failed)

    def _on_crash(self, _page) -> None:
        self.crashed = True

    def _request_failed(self, request) -> None:
        if request.url in self.blocked:
            return
        self.failures.append(f"{request.url} ({request.failure or 'request failed'})")

    def _response_failed(self, response) -> None:
        if response.status >= 400:
            self.failures.append(f"{response.url} (HTTP {response.status})")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.page.close()

    def error(self, exc: BaseException, *, what: str = "the PDF") -> Exception:
        """The exception to raise for `exc`: out of memory, or a render error
        carrying the failed loads."""
        if is_out_of_memory(exc, self.crashed):
            return PdfOutOfMemoryError(
                f"The browser ran out of memory while laying out {what}. " + OUT_OF_MEMORY_ADVICE
            )
        return PdfRenderError(str(exc), failures=self.failures, blocked=self.blocked)


def lay_out(
    page,
    html: str,
    *,
    pdf_timeout: float,
    on_page: Callable[[int], None] | None = None,
    before_layout: tuple[str, object] | None = None,
    numbers_from_attribute: bool = False,
) -> int:
    """Load `html` into `page`, let paged.js paginate it, return the page count.

    `before_layout` is a `(script, argument)` evaluated after the document is
    in but before the polyfill runs -- the parts renderer fills in page numbers
    there."""
    nonce = secrets.token_urlsafe(18)
    # The document can contain user-provided image/footer resources
    # that never complete a browser load event. DOM readiness is enough
    # here; paged.js is the actual completion gate below.
    page.set_content(
        _with_paged_assets(html, nonce, numbers_from_attribute=numbers_from_attribute),
        wait_until="domcontentloaded",
    )
    if before_layout is not None:
        page.evaluate(*before_layout)
    page.evaluate(_FIT_JS, [CONTENT_WIDTH])
    # Signal completion via paged.js's own `after` hook (must be set
    # BEFORE the polyfill loads). Waiting on the first `.pagedjs_page`
    # instead is a race: pagination is async, so printing then yields a
    # truncated PDF. The marker attribute appears only once the whole
    # flow is laid out. (An attribute and a selector wait, not a flag and
    # `wait_for_function`: that evaluates a string in the page, which
    # the content security policy -- rightly -- refuses.)
    page.evaluate(
        "window.PagedConfig = { auto: true, after: () => {"
        " document.documentElement.setAttribute('data-paged-done', ''); } };"
    )
    page.evaluate(_INSERT_POLYFILL_JS, [polyfill_source(), nonce])
    pages = _wait_for_layout(page, stall_ms=int(pdf_timeout * 1000), on_page=on_page)
    page.evaluate(_FIT_PAGES_JS)
    return pages


def print_pdf(page, *, pdf_timeout: float, pages: int, path: str | None = None) -> bytes | None:
    """Print the laid-out page.

    To a `path`, Chromium hands the PDF over in 1 MB pieces that go straight
    to the file (`Page.printToPDF` with `ReturnAsStream`): Playwright's own
    `page.pdf` passes the whole PDF through this process, base64-encoded --
    about four times the part's size at once (measured: 420 MB for a 69 MB
    part). The options are the ones `page.pdf` sends, and the output is the
    same file byte for byte apart from its timestamps. Anything that stops the
    stream falls back to `page.pdf`.

    Without a path, the bytes are returned. `pdf()` takes no timeout of its
    own; it obeys the page default, which grows with the page count. (The
    streamed print has no time limit of its own; a stuck layout -- the way an
    export has ever hung -- is still caught by `_wait_for_layout`.)"""
    page.set_default_timeout(_print_timeout_ms(pdf_timeout=pdf_timeout, pages=pages))
    if path is not None:
        try:
            _print_streamed(page, Path(path))
            return None
        except Exception:  # noqa: BLE001 - the ordinary print is the fallback
            Path(path).unlink(missing_ok=True)
    return page.pdf(print_background=True, prefer_css_page_size=True, path=path)


#: `page.pdf(print_background=True, prefer_css_page_size=True)`, as the
#: DevTools protocol call Playwright makes for it.
_PRINT_OPTIONS = {
    "printBackground": True,
    "preferCSSPageSize": True,
    "marginTop": 0,
    "marginBottom": 0,
    "marginLeft": 0,
    "marginRight": 0,
    "paperWidth": 8.5,
    "paperHeight": 11,
    "generateTaggedPDF": False,
    "generateDocumentOutline": False,
    "transferMode": "ReturnAsStream",
}


def _print_streamed(page, path: Path) -> None:
    import base64  # noqa: PLC0415

    session = page.context.new_cdp_session(page)
    try:
        handle = session.send("Page.printToPDF", _PRINT_OPTIONS)["stream"]
        try:
            with path.open("wb") as out:
                while True:
                    chunk = session.send("IO.read", {"handle": handle, "size": 1 << 20})
                    data = chunk.get("data", "")
                    out.write(
                        base64.b64decode(data)
                        if chunk.get("base64Encoded")
                        else data.encode("latin-1")
                    )
                    if chunk.get("eof"):
                        break
        finally:
            session.send("IO.close", {"handle": handle})
    finally:
        session.detach()


def html_to_pdf_paged(
    html: str,
    *,
    marks=None,
    generated_at: str = "",
    pdf_timeout: float = 120.0,
    open_bookmarks: bool = False,
    served: ServedImages | None = None,
    progress: ProgressCallback | None = None,
    output: Path | None = None,
    attachments=(),
) -> bytes | None:
    """Paginate `html` with the vendored paged.js polyfill inside a headless
    Chromium and return the PDF bytes. Callers check `PAGED_AVAILABLE` first;
    this raises whatever Playwright raises if no usable browser is present.

    Unlike `browser.html_to_pdf`, this does NOT use Chromium's
    `display_header_footer` -- paged.js draws the margin boxes itself, and
    `prefer_css_page_size` honours the `@page { size }` it laid out."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    report = progress or ignore
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        guard: PrintPage | None = None
        try:
            guard = PrintPage(browser, served=served, pdf_timeout=pdf_timeout)
            page = guard.page
            report(PdfProgress(stage="laying_out", message="Laying out pages…"))
            pages = lay_out(
                page,
                html,
                pdf_timeout=pdf_timeout,
                on_page=lambda n: report(
                    PdfProgress(
                        stage="laying_out",
                        pages_done=n,
                        message=f"Laying out pages… {n:,} so far",
                    )
                ),
            )
            page.evaluate(_STAMP_JS, _stamp_args(marks, generated_at))
            outline = page.evaluate(_OUTLINE_JS)
            report(
                PdfProgress(
                    stage="printing",
                    pages_done=pages,
                    pages_total=pages,
                    message=f"Writing {pages:,} pages to PDF…",
                )
            )
            if output is not None:
                # Printed to disk and finished there by the streaming joiner:
                # bookmarks, attachments and Chromium's named links resolved
                # without the PDF ever being held in memory (`pdf.join`).
                raw = output.with_name(output.name + ".raw.pdf")
                print_pdf(page, pdf_timeout=pdf_timeout, pages=pages, path=str(raw))
                _finish_one_piece(raw, output, outline, open_bookmarks, attachments)
                result = None
            else:
                pdf = print_pdf(page, pdf_timeout=pdf_timeout, pages=pages)
                result = with_outline(pdf, outline, open_panel=open_bookmarks)
            report(
                PdfProgress(
                    stage="done",
                    pages_done=pages,
                    pages_total=pages,
                    message=f"Done: {pages:,} pages.",
                )
            )
            return result
        except Exception as exc:
            if guard is None:
                raise PdfRenderError(str(exc)) from exc
            raise guard.error(exc) from exc
        finally:
            browser.close()


def _finish_one_piece(raw: Path, output: Path, outline, open_panel: bool, attachments) -> None:
    from connections_export.pdf import join  # noqa: PLC0415

    try:
        join.join(
            [raw],
            output,
            outline=[join.Bookmark(e["title"], e["level"], e["page"]) for e in outline],
            open_outline=open_panel,
            attachments=attachments,
        )
    except join.NotSimple:
        pdf = with_outline(raw.read_bytes(), outline, open_panel=open_panel)
        output.write_bytes(attach_files_loaded(pdf, attachments))
    finally:
        raw.unlink(missing_ok=True)


def attach_files_loaded(pdf: bytes, attachments) -> bytes:
    """`pdf` with `(name, load)` attachments added -- the pypdf fallback."""
    attachments = list(attachments)
    if not attachments:
        return pdf
    import io  # noqa: PLC0415

    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    for name, load in attachments:
        data = load()
        if data is not None:
            writer.add_attachment(name, data)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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
    pdf_timeout: float = 120.0,
    open_bookmarks: bool = False,
    external_images: Mapping[str, bytes | None] | None = None,
    small_image_px: int | None = None,
    progress: ProgressCallback | None = None,
    part_size_mb: float | None = None,
    attach_attachments: bool = False,
    attach_inline_images: bool = True,
    member: SetMember | None = None,
    output: Path | None = None,
) -> bytes | None:
    """`html_to_pdf_paged(render_html(...))` -- the end-to-end portable PDF
    with a running per-page footer (wiki/blog/forum name + page name + page
    number) and a TOC carrying real page numbers. `include_comments=False`
    omits page/post comments from the export (they stay in the archive).
    `chrome=False` drops the cover + TOC (the live per-entity tiles).
    `progress` hears each step (`pdf.progress`). `part_size_mb` bounds how
    much of the document is laid out at once (`pdf.parts`); None means the
    default, 0 means never split. `attach_attachments` embeds captured
    attachments. By default this also
    includes original files for images shown inline; set
    `attach_inline_images=False` to omit those originals while keeping other
    attachments. Inline images are not repeated in the document's attachment
    list.

    With `output`, the PDF is written there and None returned -- the way to
    make a large one: it is never held in memory. Without, the bytes are
    returned."""
    from connections_export.pdf import parts  # noqa: PLC0415

    (progress or ignore)(PdfProgress(stage="preparing", message="Preparing the document…"))
    with tempfile.TemporaryDirectory(prefix="connections-export-pdf-") as work:
        workdir = Path(work)
        (workdir / "images").mkdir()
        with (
            serving_images(spool=workdir / "images") as served,
            attaching_files(include_inline_images=attach_inline_images)
            if attach_attachments
            else contextlib.nullcontext() as attached,
            remote_link_register(member.remote) if member else contextlib.nullcontext(),
        ):
            # One piece at a time, onto disk: the whole document is never in
            # memory (`pdf.parts.plan_document`).
            plan = parts.plan_document(
                document_head(interchange),
                document_pieces(
                    interchange,
                    blob_bytes=blob_bytes,
                    generated_at=generated_at,
                    include_comments=include_comments,
                    chrome=chrome,
                    style_overrides=style_overrides,
                    extra_css=extra_css,
                    external_images=external_images,
                    small_image_px=small_image_px,
                    set_note=member.set_note if member else None,
                ),
                served=served,
                spool=workdir / "pieces.html",
            )
        # Still inside the work folder: the spooled images and pieces must
        # outlive the browser.
        target = output if output is not None else workdir / "export.pdf"
        # Attached files are read from the archive one at a time, as the
        # finished PDF is written.
        attachments = [
            (name, (lambda d=digest: blob_bytes(d))) for name, digest in (attached or [])
        ]
        if member is not None:
            # A file of a set always goes through the parts renderer: it is
            # the one that reports where every target landed.
            plan.extra_targets = member.extra_targets
            budget_mb = _part_size(part_size_mb, progress)
            plan.pack(int(budget_mb * 2**20) if budget_mb > 0 else plan.weight + 1)
            parts.render_in_parts(
                plan,
                served=served,
                marks=marks,
                generated_at=generated_at,
                pdf_timeout=pdf_timeout,
                open_bookmarks=open_bookmarks,
                progress=progress,
                positions_out=member.positions_out,
                output=target,
                attachments=attachments,
            )
        else:
            _render_plan(
                plan,
                served=served,
                part_size_mb=part_size_mb,
                marks=marks,
                generated_at=generated_at,
                pdf_timeout=pdf_timeout,
                open_bookmarks=open_bookmarks,
                progress=progress,
                output=target,
                attachments=attachments,
            )
        if attachments:
            from connections_export.pdf import join  # noqa: PLC0415

            # Never a reason to lose the PDF: every file is embedded and in the
            # viewer's attachments panel whether or not each name became a
            # paperclip, and a name wrapped across a page break is two links.
            join.rewrite_embedded_attachment_links(target)
        return None if output is not None else target.read_bytes()


def _part_size(part_size_mb: float | None, progress: ProgressCallback | None) -> float:
    """The part size to use: as set, or -- unset -- what the memory free now
    affords (`pdf.memory`)."""
    if part_size_mb is not None:
        return part_size_mb
    from connections_export.pdf import memory  # noqa: PLC0415

    free = memory.available_memory()
    size = memory.automatic_part_mb(free)
    if free is not None:
        (progress or ignore)(
            PdfProgress(
                stage="preparing",
                message=(
                    f"Preparing the document… ({free / 2**30:.1f} GB of memory free:"
                    f" laying out up to {size:.0f} MB at a time)"
                ),
            )
        )
    return size


def _render_plan(
    plan,
    *,
    served: ServedImages,
    part_size_mb: float | None,
    marks,
    generated_at: str,
    pdf_timeout: float,
    open_bookmarks: bool,
    progress: ProgressCallback | None,
    output: Path,
    attachments=(),
) -> None:
    """In one piece, or -- above the part budget, or when one piece runs out
    of memory -- in parts (`pdf.parts`); written to `output`."""
    from connections_export.pdf import parts  # noqa: PLC0415

    budget_mb = _part_size(part_size_mb, progress)
    budget = int(budget_mb * 2**20)
    options = {
        "served": served,
        "marks": marks,
        "generated_at": generated_at,
        "pdf_timeout": pdf_timeout,
        "open_bookmarks": open_bookmarks,
        "progress": progress,
        "output": output,
        "attachments": attachments,
    }
    if budget > 0 and plan.weight > budget:
        plan.pack(budget)
        if len(plan.order) > 1:
            parts.render_in_parts(plan, **options)
            return
    try:
        html_to_pdf_paged(plan.whole_document(), **options)
    except PdfOutOfMemoryError:
        if budget <= 0:
            raise
        # Under the budget and still too much for this machine: try again in
        # parts, each half the size of the whole.
        (progress or ignore)(
            PdfProgress(
                stage="retrying",
                message="The browser ran out of memory; trying again in smaller parts.",
            )
        )
        plan.pack(max(1, plan.weight // 2))
        parts.render_in_parts(plan, **options)
