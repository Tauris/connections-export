"""Path B — **browser-fidelity** PDF.

Like Path A, this produces **one combined document** — a cover page, a
clickable table of contents, and every wiki page in hierarchy order, all
in a single Chromium print. That single print is what keeps navigation
alive: Chromium turns each `<a href="#p-{id}">` (the TOC entries and the
in-export body links) into an **active internal PDF link**, and
`outline=True` adds a **bookmark outline** from the heading structure.

What Path B adds over Path A is **per-page author-CSS scoping**: each
page's `<style>` selectors are rewritten to apply only within that page's
section (`#p-{id}`), so one page's styling can't bleed into another (or
into the cover/TOC) — higher fidelity for pages that carry distinct inline
styling, without giving up the single-document navigation.

The document generation (`render_html_browser`, `_scope_css`) is pure and
tested; only `render_pdf_browser` (the Chromium print) needs a browser.

**Scope note.** This renders *our reconstructed* pages. Printing the
**live** HCL deployment page (for exact *platform*-CSS parity) additionally
needs real-system access + chrome-stripping / lazy-load automation and remains a first-contact
extension.
"""

from __future__ import annotations

import lxml.html

from connections_export.derive.model import DerivedPage, Interchange
from connections_export.pdf.html import (
    DEFAULT_GENERATED_AT,
    BlobBytes,
    _document_title,
    _escape,
    _heading_level,
    _parse_fragment,
    _render_attachments,
    _render_blog,
    _render_comments,
    _render_forum,
    _render_title_page,
    _render_toc,
    _sanitize_body,
    _walk_pages,
)

#: At-rules whose block contains nested style rules (so we recurse to scope
#: them) vs. at-rules whose block is passed through untouched (`@keyframes`
#: frame stops, `@font-face` descriptors, `@page`, etc. must not be scoped).
_NESTED_AT_RULES = frozenset({"@media", "@supports", "@container", "@layer", "@document"})


def _scope_selector(selector: str, scope: str) -> str:
    """Prefix one selector so it only matches inside `scope`. `html`/`body`/
    `:root` (and their leading use, e.g. `body.x`) map *to* the scope,
    since the page's section is that page's root."""
    stripped = selector.strip()
    if not stripped:
        return stripped
    low = stripped.lower()
    if stripped == ":root" or low in ("html", "body"):
        return scope
    for root in ("html", "body"):
        if low.startswith(root):
            rest = stripped[len(root) :]
            if rest[:1] in ("", " ", ">", "+", "~", ".", "#", ":", "[", ","):
                return f"{scope}{rest}"
    if stripped.startswith("*"):
        return f"{scope} {stripped}"
    return f"{scope} {stripped}"


def _scope_css(css: str, scope: str) -> str:
    """Rewrite `css` so every style rule applies only within `scope`
    (`#p-{id}`). Nested at-rules (`@media` …) are recursed into; other
    at-rules (`@keyframes`, `@font-face`, `@page`, `@import`) pass through
    unchanged. A small hand-rolled walk — enough for the inline author CSS
    wiki pages carry, without pulling in a CSS-parser dependency."""
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            out.append(css[i:])
            break
        prelude = css[i:brace]
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        block = css[brace + 1 : j - 1]
        stripped = prelude.strip()
        if stripped.startswith("@"):
            keyword = stripped.split(None, 1)[0].lower()
            if keyword in _NESTED_AT_RULES:
                out.append(f"{prelude}{{{_scope_css(block, scope)}}}")
            else:
                out.append(css[i:j])  # @keyframes/@font-face/@page/... untouched
        else:
            selectors = [s for s in prelude.split(",") if s.strip()]
            scoped = ", ".join(_scope_selector(s, scope) for s in selectors)
            out.append(f"{scoped} {{{block}}}")
        i = j
    return "".join(out)


def _scope_body_styles(body_html: str, scope: str) -> str:
    """Scope every `<style>` block inside a page body to `scope`, leaving
    the rest of the markup (and all `href`s) untouched."""
    fragment = _parse_fragment(body_html)
    if fragment is None:
        return body_html
    for style in fragment.iter("style"):
        if style.text:
            style.text = _scope_css(style.text, scope)
    return lxml.html.tostring(fragment, encoding="unicode")


def _render_scoped_section(
    page: DerivedPage, depth: int, blob_bytes: BlobBytes, *, include_comments: bool = True
) -> str:
    """A page section like Path A's, but with the body's author CSS scoped
    to `#p-{id}` so it can't bleed into any other page."""
    level = _heading_level(depth)
    title = _escape(page.title or page.label or page.id)
    section_id = _escape(page.id)
    body = _scope_body_styles(_sanitize_body(page, blob_bytes), f"#p-{section_id}")
    comments = _render_comments(page.comments) if include_comments else ""
    attachments = _render_attachments(page.attachments, blob_bytes)
    # Every wiki page starts on a fresh PDF page (not just depth-0 roots).
    page_break = " page-break"
    return (
        f'<section id="p-{section_id}" class="hcl-page{page_break}" data-depth="{depth}">'
        f'<h{level} class="pdf-sectitle">{title}</h{level}>'
        f'<div class="page-body">{body}</div>'
        f"{comments}{attachments}"
        "</section>"
    )


# See pdf/html.py's `_STYLE` comment: Chromium's print engine doesn't
# implement CSS named strings (`string`/`string-set`), so the old
# `@page { @bottom-right { content: string(sectitle)... } }` footer here
# rendered empty. The real footer (page number + document title) now comes
# from `page.pdf(display_header_footer=...,...)` in `pdf/browser.py`.
# `.pdf-sectitle` stays on the heading markup as a semantic marker only.
#: Same design tokens as the portable stylesheet (`pdf/html.py`), so a reader's
#: overrides apply whichever renderer produced the PDF. This sheet is
#: deliberately thinner: the captured page's own CSS carries the look here, and
#: only our chrome around it is ours to style.
_STYLE = """
:root {
  --pdf-font: sans-serif;
  --pdf-body-size: 10.5pt;
  --pdf-line-height: 1.4;
  --pdf-meta-size: 8.5pt;
  --pdf-toc-title-size: 15pt;
  --pdf-toc-group-size: 11.5pt;
  --pdf-toc-entry-size: 10pt;
  --pdf-muted: #444;
}
body {
  font-family: var(--pdf-font);
  font-size: var(--pdf-body-size);
  line-height: var(--pdf-line-height);
}
#toc .toc-title { font-size: var(--pdf-toc-title-size); font-weight: 700; }
#toc .toc-group { font-size: var(--pdf-toc-group-size); font-weight: 700; }
#toc li { font-size: var(--pdf-toc-entry-size); }
#title-page { text-align: center; page-break-after: always; }
#toc { page-break-after: always; }
#toc a { text-decoration: none; color: #123; }
.hcl-wiki > h1 { border-bottom: 2px solid #333; }
.hcl-missing-image, .hcl-missing-attachment { color: #a00; font-style: italic; }
.hcl-link-url { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
/* Threaded/indented discussion view -- comments AND forum replies share it. */
.comment, .forum-reply { border-left: 2px solid #cbd2dc; padding-left: 0.6em; margin-top: 0.5em; }
.comment-meta, .forum-reply .entry-meta {
  font-weight: bold; font-size: var(--pdf-meta-size); color: #333;
}
.comments > h3, .forum-replies > h3 {
  font-size: 0.95em; margin: 1em 0 0.3em; padding-bottom: 2px; border-bottom: 1px solid #e5e8ee;
}
@media print {
  .page-break { break-before: page; }
  * { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  @page {
    size: A4;
  }
}
"""


def render_html_browser(
    interchange: Interchange,
    blob_bytes: BlobBytes,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
) -> str:
    """One combined, print-ready HTML document (cover + clickable TOC +
    every wiki page in hierarchy order), with each page's author CSS
    scoped to its own section. Pure — no browser. Printed as a single
    document so the TOC and in-export body links become active internal
    PDF links and the heading structure yields a bookmark outline."""
    cover = _render_title_page(interchange, generated_at)
    toc = _render_toc(interchange)
    body: list[str] = []
    for wiki in interchange.wikis:
        heading = _escape(wiki.title or wiki.label)
        sections = "".join(
            _render_scoped_section(page, depth, blob_bytes, include_comments=include_comments)
            for page, depth in _walk_pages(wiki)
        )
        body.append(f'<section class="hcl-wiki"><h1>{heading}</h1>{sections}</section>')
    # Blogs/forums reuse Path A's renderers with `_scope_body_styles` as the
    # scoping seam, so each post/topic/reply body's author CSS is confined to
    # its own section (`#b-`/`#t-`/`#r-`) exactly as pages are — no bleed.
    for blog in interchange.blogs:
        body.append(
            _render_blog(blog, blob_bytes, _scope_body_styles, include_comments=include_comments)
        )
    for forum in interchange.forums:
        body.append(_render_forum(forum, blob_bytes, _scope_body_styles))
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_escape(_document_title(interchange))}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body>"
        f"{cover}{toc}{''.join(body)}"
        "</body></html>"
    )


def render_pdf_browser(
    interchange: Interchange,
    blob_bytes: BlobBytes,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
) -> bytes:
    """Render `interchange` to a browser-fidelity PDF: one combined
    document (cover + clickable TOC + scoped pages), printed once with a
    generated outline. Browser-gated — callers check `CHROMIUM_AVAILABLE`;
    raises whatever Playwright raises if no usable browser is present."""
    from connections_export.pdf.browser import html_to_pdf  # noqa: PLC0415

    html = render_html_browser(
        interchange, blob_bytes, generated_at=generated_at, include_comments=include_comments
    )
    return html_to_pdf(html, outline=True)
