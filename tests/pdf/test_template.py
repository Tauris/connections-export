"""Footer template: the old CSS-only
footer (`@page { @bottom-right { content: string(sectitle) " · "
counter(page); } } }`, keyed off a `.pdf-sectitle { string-set:... }` rule)
never worked -- Chromium's print-to-PDF engine doesn't implement CSS named
strings (`string`/`string-set`) in `@page` margin boxes, so it rendered
completely empty. Both `_STYLE` blocks must no longer contain that dead CSS;
the real footer (page number + document title) is wired through Chromium's
own `page.pdf(display_header_footer=..., footer_template=...)` mechanism in
`pdf/browser.py` (see `test_browser_launch.py`).

`.pdf-sectitle` itself stays on the rendered heading markup (a semantic
marker only, no longer backed by a CSS rule), and print font-size is now set
explicitly rather than left at the browser default.
"""

from __future__ import annotations

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
from connections_export.pdf import browser_fidelity as pdf_bf
from connections_export.pdf import html as pdf_html


def test_neither_style_contains_the_broken_css_footer():
    for style in (pdf_html._STYLE, pdf_bf._STYLE):
        assert "@bottom-right" not in style
        assert "string(sectitle)" not in style
        assert "string-set" not in style


def test_both_styles_set_a_print_appropriate_base_font_size():
    for style in (pdf_html._STYLE, pdf_bf._STYLE):
        # Expressed as a token now, so a reader can override the base size
        # without touching our rules -- the size itself is unchanged.
        assert "--pdf-body-size: 10.5pt" in style
        assert "font-size: var(--pdf-body-size)" in style


def test_svg_bytes_are_labelled_svg_not_guessed_from_href():
    # An SVG served at a lying `.png` href (the demo diagrams do exactly this)
    # must be sniffed as image/svg+xml, or its data-URI renders blank in the
    # exported PDF ("diagrams not visible") while the reader shows it fine.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    assert pdf_html._guess_content_type("diagram.png", svg) == "image/svg+xml"
    xml_svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert pdf_html._guess_content_type("x.png", xml_svg) == "image/svg+xml"
    # A real PNG is still a PNG (magic wins over the href and over SVG sniffing).
    assert pdf_html._guess_content_type("x.svg", b"\x89PNG\r\n\x1a\n") == "image/png"


def _tiny_interchange() -> Interchange:
    page = DerivedPage(id="p1", label="p1", title="Page One")
    wiki = DerivedWiki(
        id="w1", label="w1", title="Wiki One", root_page_ids=["p1"], pages={"p1": page}
    )
    return Interchange(wikis=[wiki])


def test_include_comments_false_omits_comments_but_keeps_the_body():
    from connections_export.derive.model import DerivedComment

    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page One",
        content_html="<p>The page body.</p>",
        comments=[DerivedComment(id="c1", author="A", content_html="<p>A comment.</p>")],
    )
    wiki = DerivedWiki(
        id="w1", label="w1", title="Wiki One", root_page_ids=["p1"], pages={"p1": page}
    )
    ix = Interchange(wikis=[wiki])

    full = pdf_html.render_html(ix, blob_bytes=_no_blobs, include_comments=True)
    assert ">Comments<" in full and "A comment." in full

    # Comments are always ingested; the export may omit them -- body stays.
    without = pdf_html.render_html(ix, blob_bytes=_no_blobs, include_comments=False)
    assert ">Comments<" not in without and "A comment." not in without
    assert "The page body." in without


def _no_blobs(_digest: str) -> bytes | None:
    return None


def test_page_titles_carry_the_sectitle_class():
    # a rendered page/post/topic title element still carries the marker
    # class, even though no CSS rule uses it for a footer anymore.
    ix = _tiny_interchange()
    out = pdf_html.render_html(ix, blob_bytes=_no_blobs)
    assert 'class="pdf-sectitle"' in out


def test_browser_fidelity_page_titles_carry_the_sectitle_class():
    ix = _tiny_interchange()
    out = pdf_bf.render_html_browser(ix, _no_blobs)
    assert 'class="pdf-sectitle"' in out


def test_document_title_is_a_meaningful_name():
    ix = _tiny_interchange()
    out = pdf_html.render_html(ix, blob_bytes=_no_blobs)
    assert "<title>HCL Connections Export</title>" in out


def test_document_title_includes_base_url_when_present():
    ix = Interchange(
        base_url="https://connections.example.com",
        wikis=[
            DerivedWiki(
                id="w1",
                label="w1",
                title="Wiki One",
                root_page_ids=["p1"],
                pages={"p1": DerivedPage(id="p1", label="p1", title="Page One")},
            )
        ],
    )
    out = pdf_html.render_html(ix, blob_bytes=_no_blobs)
    assert "<title>HCL Connections Export" in out
    assert "connections.example.com" in out


# --- cover page -------------------------------------------------------------
#
# Ported from `feat/pdf-template-and-preview` (8e44217), which predated the
# app-aware heading (`_export_heading`) and the Chromium footer fix and so
# could not be rebased: only the cover's layout and its counts line are
# carried over, onto master's heading and name lines.


def _cover_interchange() -> Interchange:
    wiki = DerivedWiki(
        id="w1",
        label="handbook",
        title="Engineering Handbook",
        root_page_ids=["p1", "p2"],
        pages={
            "p1": DerivedPage(id="p1", label="a", title="A"),
            "p2": DerivedPage(id="p2", label="b", title="B"),
        },
    )
    return Interchange(wikis=[wiki], base_url="https://connections.example.com")


def test_cover_counts_items_and_pluralizes_them():
    html = pdf_html.render_html(_cover_interchange(), blob_bytes=_no_blobs)

    # One wiki, two pages: singular and plural both come from the same helper.
    assert "1 wiki" in html
    assert "2 pages" in html
    assert "1 wikis" not in html


def test_cover_starts_its_own_page():
    """Without this the cover ran straight into the first section -- the
    branch added it inline because neither `_STYLE` defines `#title-page`."""
    html = pdf_html.render_html(_cover_interchange(), blob_bytes=_no_blobs)

    cover = html.split('id="title-page"', 1)[1].split("</section>", 1)[0]
    assert "page-break-after: always" in cover


def test_cover_keeps_the_app_aware_heading_and_content_names():
    """The port must not regress `_export_heading` (6a13282) or drop the
    titles the cover already listed -- the counts line is additive."""
    html = pdf_html.render_html(_cover_interchange(), blob_bytes=_no_blobs)

    assert "HCL Wiki Export" in html
    assert "Engineering Handbook" in html
    assert "Source: https://connections.example.com" in html


def test_browser_fidelity_cover_is_styled_too():
    """Both render paths share `_render_title_page`, which is why the cover
    is styled inline rather than through either `_STYLE` block."""
    cover = pdf_bf._render_title_page(_cover_interchange(), "2026-01-01")

    assert "page-break-after: always" in cover
    assert "1 wiki" in cover


def test_cover_counts_posts_and_topics_not_just_wiki_pages():
    """The branch this was ported from counted only wiki pages, which left
    the counts line on a blog- or forum-only export saying nothing the name
    line above it hadn't already said (`1 forum` under `Forums: Support
    Forum`). Blogs and forums carry countable children too."""
    from connections_export.derive.model import (
        DerivedBlog,
        DerivedBlogPost,
        DerivedForum,
        DerivedForumTopic,
    )

    blog = DerivedBlog(
        id="b",
        handle="team",
        title="Team Blog",
        posts={f"x{i}": DerivedBlogPost(id=f"x{i}", title=str(i)) for i in range(3)},
    )
    forum = DerivedForum(
        id="f",
        title="Support Forum",
        topics={f"t{i}": DerivedForumTopic(id=f"t{i}", title=str(i)) for i in range(2)},
    )

    blog_html = pdf_html.render_html(Interchange(blogs=[blog]), blob_bytes=_no_blobs)
    assert "3 posts" in blog_html

    forum_html = pdf_html.render_html(Interchange(forums=[forum]), blob_bytes=_no_blobs)
    assert "2 topics" in forum_html


def test_toc_page_numbers_do_not_float_out_of_the_flow():
    """The page number was floated right, and a float escapes the flow
    paged.js measures: a table of contents longer than one page had its
    overflow DROPPED rather than continued, so the last container's entries
    went missing from the TOC while its body rendered normally."""
    from connections_export.pdf import paged

    style = paged._PAGED_STYLE
    assert "float: right" not in style
    assert "#toc a {" in style and "display: flex" in style
    assert "#toc, #toc ul, #toc li { break-inside: auto" in style


def test_every_container_starts_its_own_page():
    """A container's <h1> was stranded at the foot of the previous page: the
    first item inside it carries `.page-break` and jumped to the next one."""
    from connections_export.pdf import paged

    style = paged._PAGED_STYLE
    # Asserting the exact rule TEXT meant editing this test for every new
    # container -- which is precisely the moment a container gets added to one
    # sheet and forgotten in the other. Assert the property instead: every
    # container class appears in a `break-before: page` rule in BOTH sheets.
    # (paged.js paginates in screen context, so it re-declares these outside
    # the print media query; the two sheets drift silently otherwise.)
    containers = (".hcl-wiki", ".hcl-blog", ".hcl-forum", ".hcl-files", ".hcl-rc")
    for sheet in (pdf_html._STYLE, style):
        rules = "\n".join(line for line in sheet.splitlines() if "break-before: page" in line)
        for container in containers:
            assert container in rules, f"{container} never starts its own page"
        assert "break-after: avoid" in sheet


def test_the_toc_lists_every_item_in_every_container():
    """The TOC and the body iterate the same ids, so a mismatch here means
    one of them silently lost a container's contents."""
    import re

    from connections_export.derive.model import (
        DerivedBlog,
        DerivedBlogPost,
        DerivedForum,
        DerivedForumTopic,
    )
    from connections_export.pdf.html import _render_toc

    blog = DerivedBlog(
        id="b",
        handle="team",
        title="Team",
        post_ids=[f"p{i}" for i in range(9)],
        posts={f"p{i}": DerivedBlogPost(id=f"p{i}", title=f"Post {i}") for i in range(9)},
    )
    forum = DerivedForum(
        id="f",
        title="Forum",
        topic_ids=[f"t{i}" for i in range(9)],
        topics={f"t{i}": DerivedForumTopic(id=f"t{i}", title=f"Topic {i}") for i in range(9)},
    )
    toc = _render_toc(Interchange(blogs=[blog], forums=[forum]))

    assert len(re.findall(r"#b-p\d", toc)) == 9
    assert len(re.findall(r"#t-t\d", toc)) == 9


def test_container_names_are_list_items_not_standalone_headings():
    """A heading between lists is a block of its own, and paged.js drops such
    a block when it lands exactly on a page boundary -- so whichever container
    fell at the break lost its name while its entries stayed. `break-after:
    avoid` only changed which one it was."""
    from connections_export.derive.model import DerivedBlog, DerivedBlogPost
    from connections_export.pdf.html import _render_toc

    blog = DerivedBlog(
        id="b",
        handle="team",
        title="Team Blog",
        post_ids=["p"],
        posts={"p": DerivedBlogPost(id="p", title="Post")},
    )
    toc = _render_toc(Interchange(blogs=[blog]))

    assert '<li class="toc-group">Team Blog</li>' in toc
    assert "<h3>" not in toc, "a standalone heading can be dropped at a page break"


def test_the_toc_has_no_list_markers():
    """`list-style-position: inside` put the marker in the li's content flow,
    and the entry is a flex row -- a block-level box -- so every bullet
    rendered on its own line ABOVE its entry."""
    from connections_export.pdf import paged

    assert "#toc ul { list-style: none; }" in paged._PAGED_STYLE
    # The comment above the rule names the old declaration, so check for the
    # declaration itself rather than the phrase.
    assert "#toc li { list-style-position: inside; }" not in paged._PAGED_STYLE


def test_the_contents_are_split_into_page_sized_blocks():
    """paged.js loses a fragment when it has to split one long list across
    pages -- an entire page of entries disappeared between two that rendered.
    The split is done here so it is never asked to."""
    from connections_export.derive.model import DerivedForum, DerivedForumTopic
    from connections_export.pdf.html import _TOC_ROWS_PER_PAGE, _render_toc

    forum = DerivedForum(
        id="f",
        title="Forum",
        topic_ids=[f"t{i}" for i in range(60)],
        topics={f"t{i}": DerivedForumTopic(id=f"t{i}", title=f"Topic {i}") for i in range(60)},
    )
    toc = _render_toc(Interchange(forums=[forum]))

    blocks = toc.count('<ul class="toc-list')
    rows = 60 + 1 + 1  # topics, the container name, the "Table of contents" row
    assert blocks == -(-rows // _TOC_ROWS_PER_PAGE), f"{rows} rows should span {blocks} blocks"
    assert toc.count("toc-continued") == blocks - 1, "every block after the first breaks"
    # Every entry survives the split.
    import re

    assert len(re.findall(r"#t-t\d+", toc)) == 60


def test_wiki_hierarchy_is_flattened_with_indent_not_nesting():
    """Nested lists made one row render as many lines, and the split counts
    rows."""
    from connections_export.derive.model import DerivedPage, DerivedWiki
    from connections_export.pdf.html import _render_toc

    wiki = DerivedWiki(
        id="w",
        label="w",
        title="W",
        root_page_ids=["a"],
        pages={
            "a": DerivedPage(id="a", label="a", title="A", child_ids=["b"]),
            "b": DerivedPage(id="b", label="b", title="B", parent_id="a"),
        },
    )
    toc = _render_toc(Interchange(wikis=[wiki]))

    assert 'class="toc-d0"' in toc and 'class="toc-d1"' in toc
    assert "<ul><li>" not in toc, "hierarchy is an indent class, not a nested list"
