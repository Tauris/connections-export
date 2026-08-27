"""Path B (browser-fidelity): `render_html_browser` builds ONE combined
document — cover + clickable TOC + every page — with each page's author
CSS scoped to its section so styles don't bleed. Printed as a single
document, the TOC/body links become active internal PDF links and the
headings yield a bookmark outline (the print itself is covered by the
browser-gated test_browser.py). This file tests the pure core: the CSS
scoper and the assembled document.
"""

from __future__ import annotations

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange, Provenance
from connections_export.pdf.browser_fidelity import _scope_css, render_html_browser


def _page(page_id: str, content_html: str, **kwargs) -> DerivedPage:
    return DerivedPage(id=page_id, content_html=content_html, provenance=Provenance(), **kwargs)


def _interchange(pages: dict[str, DerivedPage], root_page_ids: list[str]) -> Interchange:
    wiki = DerivedWiki(
        id="w1", label="w1", title="Wiki One", pages=pages, root_page_ids=root_page_ids
    )
    return Interchange(base_url="https://fake", wikis=[wiki])


# --- the CSS scoper (the fidelity property) --------------------------------


def test_scope_css_prefixes_plain_selectors():
    assert _scope_css(".a { color: red }", "#p-x") == "#p-x .a { color: red }"


def test_scope_css_maps_body_and_root_to_the_scope():
    assert _scope_css("body { margin: 0 }", "#p-x") == "#p-x { margin: 0 }"
    assert _scope_css("body p { x: 1 }", "#p-x") == "#p-x p { x: 1 }"
    assert _scope_css(":root { --c: 1 }", "#p-x") == "#p-x { --c: 1 }"


def test_scope_css_recurses_into_media_but_leaves_keyframes():
    scoped = _scope_css("@media print { .a { c: red } }", "#p-x")
    assert "#p-x .a" in scoped
    # keyframe stops (0%/100%) must not be scoped, or the animation breaks.
    kf = _scope_css("@keyframes spin { from { x: 0 } to { x: 1 } }", "#p-x")
    assert kf == "@keyframes spin { from { x: 0 } to { x: 1 } }"


def test_scope_css_handles_multiple_selectors():
    assert _scope_css(".a, .b { c: red }", "#p-x") == "#p-x .a, #p-x .b { c: red }"


# --- the combined document -------------------------------------------------


def test_one_combined_document_with_cover_and_toc():
    pages = {
        "a": _page("a", "<p>Alpha</p>", title="Alpha", child_ids=["b"]),
        "b": _page("b", "<p>Beta</p>", title="Beta", parent_id="a"),
    }
    html = render_html_browser(_interchange(pages, ["a"]), lambda _d: None)

    assert html.startswith("<!DOCTYPE html>")
    assert html.count("<html") == 1 and html.count("</html>") == 1  # ONE document
    assert 'id="title-page"' in html  # cover
    assert 'id="toc"' in html  # table of contents
    # TOC links are in-page anchors -> active internal links in one print.
    assert 'href="#p-a"' in html and 'href="#p-b"' in html
    # each page is a section anchor target
    assert 'id="p-a"' in html and 'id="p-b"' in html


def test_author_css_is_scoped_per_page_in_the_combined_document():
    """The fidelity property: in ONE document, each page's author style is
    confined to its own section, so page A's `.x` rule can't restyle page
    B's `.x`."""
    pages = {
        "a": _page("a", "<style>.x{color:red}</style><p class='x'>A</p>", title="Alpha"),
        "b": _page("b", "<style>.x{color:blue}</style><p class='x'>B</p>", title="Beta"),
    }
    html = render_html_browser(_interchange(pages, ["a", "b"]), lambda _d: None)

    assert "#p-a .x" in html and "#p-b .x" in html
    # the raw, unscoped global rules must NOT survive
    assert ".x{color:red}" not in html and ".x{color:blue}" not in html


def test_in_export_links_stay_as_internal_anchors():
    from connections_export.derive.model import LinkRef

    link = LinkRef(original_href="/x", scope="in_export", target_page_id="b")
    pages = {
        "a": _page("a", '<a href="/x">to B</a>', title="Alpha", links=[link]),
        "b": _page("b", "<p>Beta</p>", title="Beta"),
    }
    html = render_html_browser(_interchange(pages, ["a", "b"]), lambda _d: None)
    # the in-export link is rewritten to an in-page anchor (active in one print)
    assert 'href="#p-b"' in html
