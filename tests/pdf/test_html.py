"""`render_html` (render_html (the core)):
pure interchange-model -> print-ready HTML, no browser involved. This
is the real coverage for the pdf capability -- every scenario in
specs/pdf/the design is proven here, offline, without Chromium.

Fixtures build `Interchange`/`DerivedWiki`/`DerivedPage` models by hand
(the derive-level round trip in tests/derive/test_roundtrip.py already
proves a *real* archive assembles into this shape; here we only need
the shape itself, on purpose small and explicit so each assertion maps
to one scenario).
"""

from __future__ import annotations

import base64
import re

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.pdf.html import DEFAULT_GENERATED_AT, render_html

# A minimal, real PNG magic-number prefix -- enough for the renderer's
# content-type sniffer to recognize "image/png", not a full valid image.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _wiki(pages: dict[str, DerivedPage], root_page_ids: list[str], **kwargs) -> DerivedWiki:
    return DerivedWiki(
        id=kwargs.pop("id", "w1"),
        label=kwargs.pop("label", "w1"),
        title=kwargs.pop("title", "Wiki One"),
        root_page_ids=root_page_ids,
        pages=pages,
    )


def _page(page_id: str, /, **kwargs) -> DerivedPage:
    kwargs.setdefault("label", page_id)
    kwargs.setdefault("title", f"Title {page_id}")
    return DerivedPage(id=page_id, **kwargs)


# --- 2.1: hierarchy + ordinal order, depth-first, stable anchors -----------


def test_title_page_names_the_export_by_app():
    from connections_export.derive.model import DerivedBlog, DerivedForum, DerivedWiki

    forum = DerivedForum(id="f", title="Support Forum")
    html = render_html(Interchange(forums=[forum]), blob_bytes=_no_blobs)
    assert "HCL Forum Export" in html and "HCL Wiki Export" not in html
    assert "Support Forum" in html  # not the uuid

    blog = DerivedBlog(id="b", handle="team", title="Team Blog")
    assert "HCL Blog Export" in render_html(Interchange(blogs=[blog]), blob_bytes=_no_blobs)

    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=[], pages={})
    assert "HCL Wiki Export" in render_html(Interchange(wikis=[wiki]), blob_bytes=_no_blobs)

    # a mixed export is app-neutral
    mixed = render_html(Interchange(wikis=[wiki], forums=[forum]), blob_bytes=_no_blobs)
    assert "HCL Connections Export" in mixed


def test_print_safety_css_keeps_wide_content_within_the_page():
    # A PDF can't scroll, so wide content (images, <pre>, tables, long tokens)
    # must be forced to fit -- else it's clipped off the page's right edge.
    wide = (
        "<p>https://example.com/a/really/long/unbreakable/path/that/would/overflow</p>"
        "<pre>lots-of-very-wide-preformatted-text-with-no-spaces-to-wrap-on-xxxxxxxxxx</pre>"
        '<img src="https://example.com/huge.png">'
        "<table><tr><td>a</td><td>b</td></tr></table>"
    )
    page = DerivedPage(id="p", title="P", content_html=wide)
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    html = render_html(Interchange(wikis=[wiki]), blob_bytes=_no_blobs)

    # The constraining rules are present and scoped to the captured body.
    assert "max-width: 100% !important" in html  # media
    assert ".page-body pre" in html and "pre-wrap" in html  # preformatted wraps
    assert "table-layout: fixed" in html  # wide tables fit
    assert "word-break: break-all" in html or "word-break: break-word" in html  # long tokens


def test_print_safety_css_controls_pre_and_code_spacing():
    page = DerivedPage(id="p", title="P", content_html="<pre><code>line</code></pre>")
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    html = render_html(Interchange(wikis=[wiki]), blob_bytes=_no_blobs)

    assert ".page-body pre" in html and "margin: 0.75em 0 !important" in html
    assert "page-break-inside: auto !important" in html
    assert ".page-body code" in html and "height: auto !important" in html


def test_chrome_false_drops_cover_and_toc_but_keeps_body():
    from connections_export.derive.model import DerivedPage, DerivedWiki

    page = DerivedPage(id="p", title="Onboarding", content_html="<p>welcome</p>")
    wiki = DerivedWiki(id="w", label="w", title="Handbook", root_page_ids=["p"], pages={"p": page})
    ix = Interchange(wikis=[wiki])

    full = render_html(ix, blob_bytes=_no_blobs)
    assert "Table of contents" in full and "HCL Wiki Export" in full

    # The live-preview tiles: body only, no cover, no TOC -- but the page's
    # own content and anchor still render.
    bare = render_html(ix, blob_bytes=_no_blobs, chrome=False)
    assert "Table of contents" not in bare
    assert "HCL Wiki Export" not in bare
    assert 'id="p-p"' in bare and "welcome" in bare


def test_wiki_page_tags_render_as_chips():
    page = _page("root-a", ordinal=0, tags=["engineering", "onboarding"])
    wiki = _wiki({"root-a": page}, root_page_ids=["root-a"])
    html = render_html(Interchange(wikis=[wiki]), blob_bytes=_no_blobs)

    assert '<div class="hcl-tags">' in html
    assert '<span class="hcl-tag">engineering</span>' in html
    assert '<span class="hcl-tag">onboarding</span>' in html


def test_page_with_no_tags_renders_no_tag_block():
    page = _page("root-a", ordinal=0)
    wiki = _wiki({"root-a": page}, root_page_ids=["root-a"])
    html = render_html(Interchange(wikis=[wiki]), blob_bytes=_no_blobs)
    assert '<div class="hcl-tags">' not in html  # the CSS rule may exist; the block must not


def test_pages_render_in_hierarchy_and_ordinal_order_never_resorted():
    # Ordinals are deliberately misleading (reverse of root_page_ids /
    # child_ids) to prove the renderer walks the pre-ordered lists
    # as-is and never re-sorts by ordinal or by id.
    root_z = _page("root-z", ordinal=0, child_ids=["child-a"])
    child_a = _page("child-a", parent_id="root-z", ordinal=9)
    root_a = _page("root-a", ordinal=1)

    wiki = _wiki(
        {"root-z": root_z, "child-a": child_a, "root-a": root_a},
        root_page_ids=["root-z", "root-a"],
    )
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert 'id="p-root-z"' in html
    assert 'id="p-child-a"' in html
    assert 'id="p-root-a"' in html

    pos_root_z = html.index('id="p-root-z"')
    pos_child_a = html.index('id="p-child-a"')
    pos_root_a = html.index('id="p-root-a"')

    # Depth-first: root-z's own child comes before its sibling root-a,
    # even though root-a's ordinal (1) is lower than child-a's (9).
    assert pos_root_z < pos_child_a < pos_root_a


def test_each_page_section_has_a_stable_id_anchor():
    page = _page("only-page")
    wiki = _wiki({"only-page": page}, root_page_ids=["only-page"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert re.search(r'<section[^>]*id="p-only-page"', html)


# --- 2.2: script removed, style kept ----------------------------------------


def test_script_removed_style_and_classes_kept():
    body = (
        "<style>.hcl-x { color: red; }</style>"
        '<p class="hcl-x" onclick="evil()">Hello</p>'
        "<script>alert(1)</script>"
    )
    page = _page("p1", content_html=body)
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert "color: red" in html
    assert 'class="hcl-x"' in html
    assert "<script" not in html.lower()
    assert "alert(1)" not in html
    assert "onclick" not in html.lower()
    assert "evil()" not in html


# --- 2.3: image embedding / not-captured marker -----------------------------


def test_present_image_becomes_data_uri_from_injected_blob_bytes():
    page = _page(
        "p1",
        content_html='<img src="cid:present.png" alt="d">',
        assets=[
            ResolvedAsset(
                original_href="cid:present.png",
                resolved_url="https://fake/present.png",
                blob_hash="sha256:aaaa",
                present=True,
                scope="same",
            )
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes={"aaaa": PNG_BYTES}.get)

    expected_uri = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
    assert expected_uri in html
    assert 'src="cid:present.png"' not in html


def test_not_present_image_is_a_visible_marker_not_silent():
    page = _page(
        "p1",
        content_html='<img src="cid:gone.png" alt="d">',
        assets=[
            ResolvedAsset(
                original_href="cid:gone.png",
                resolved_url="https://fake/gone.png",
                blob_hash=None,
                present=False,
                scope="same",
            )
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert "[image not captured]" in html
    assert 'src="cid:gone.png"' not in html


def test_unresolved_image_with_no_matching_asset_is_also_a_visible_marker():
    # An <img> whose src has no entry at all in page.assets -- distinct
    # from present=False, still must never be silently dropped.
    page = _page("p1", content_html='<img src="cid:unknown.png" alt="d">', assets=[])
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert "[image not captured]" in html
    assert 'src="cid:unknown.png"' not in html


# --- 2.4: comments inlined continuously, replies indented ------------------


def test_comments_inlined_continuously_with_reply_indentation():
    comments = [
        DerivedComment(id="c1", author="Alice", content_html="<p>Top</p>", parent_comment_id=None),
        DerivedComment(
            id="c2", author="Bob", content_html="<p>Reply to top</p>", parent_comment_id="c1"
        ),
        DerivedComment(
            id="c3", author="Carol", content_html="<p>Second top</p>", parent_comment_id=None
        ),
    ]
    page = _page("p1", comments=comments)
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    # Never a paging widget.
    assert "page 1 of" not in html.lower()
    assert re.search(r'class="[^"]*pager', html) is None

    pos_c1 = html.index('id="comment-c1"')
    pos_c2 = html.index('id="comment-c2"')
    pos_c3 = html.index('id="comment-c3"')
    # The reply (c2) is inlined immediately after its parent (c1),
    # ahead of the next top-level comment (c3) -- a continuous thread.
    assert pos_c1 < pos_c2 < pos_c3

    assert 'data-depth="0"' in html
    assert 'data-depth="1"' in html
    # c2 (the reply) is the one indented.
    c2_section = html[pos_c2 : html.index('id="comment-c3"')]
    assert 'data-depth="1"' in c2_section

    assert "Alice" in html and "Bob" in html and "Carol" in html


def test_comment_script_is_neutralized_too():
    comments = [
        DerivedComment(
            id="c1",
            author="Eve",
            content_html='<p onclick="evil()">hi</p><script>alert(2)</script>',
        )
    ]
    page = _page("p1", comments=comments)
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert "<script" not in html.lower()
    assert "alert(2)" not in html
    assert "onclick" not in html.lower()


# --- 2.5: link classification ------------------------------------------------


def test_in_export_link_becomes_internal_anchor_to_target_section():
    target = _page("target-page", title="Target")
    source = _page(
        "source-page",
        content_html='<a href="/wiki/w1/page/target-page/entry">See target</a>',
        links=[
            LinkRef(
                original_href="/wiki/w1/page/target-page/entry",
                resolved_url="https://fake/wiki/w1/page/target-page/entry",
                scope="in_export",
                target_page_id="target-page",
            )
        ],
    )
    wiki = _wiki(
        {"target-page": target, "source-page": source},
        root_page_ids=["source-page", "target-page"],
    )
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert 'href="#p-target-page"' in html
    assert re.search(r'<section[^>]*id="p-target-page"', html)
    # The original (deployment-relative) href never leaks through.
    assert 'href="/wiki/w1/page/target-page/entry"' not in html


def test_hcl_deployment_link_keeps_original_url_and_shows_it_visibly():
    page = _page(
        "p1",
        content_html='<a href="/wiki/w1/page/other/entry">Other wiki page</a>',
        links=[
            LinkRef(
                original_href="/wiki/w1/page/other/entry",
                resolved_url="https://fake/wiki/w1/page/other/entry",
                scope="hcl_deployment",
            )
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert 'href="https://fake/wiki/w1/page/other/entry"' in html
    # Shown as visible text too, not just the href attribute.
    assert html.count("https://fake/wiki/w1/page/other/entry") >= 2


def test_external_link_keeps_original_url_and_shows_it_visibly():
    page = _page(
        "p1",
        content_html='<a href="https://docs.example.com/spec">External doc</a>',
        links=[
            LinkRef(
                original_href="https://docs.example.com/spec",
                resolved_url="https://docs.example.com/spec",
                scope="external",
            )
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    assert 'href="https://docs.example.com/spec"' in html
    assert html.count("https://docs.example.com/spec") >= 2


# --- 2.6: table of contents + self-contained document -----------------------


def test_table_of_contents_lists_every_page():
    pages = {pid: _page(pid, title=f"T-{pid}") for pid in ("a", "b", "c")}
    pages["a"].child_ids = ["b"]
    wiki = _wiki(pages, root_page_ids=["a", "c"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes=_no_blobs)

    toc_start = html.index('id="toc"')
    toc_end = html.index("</nav>", toc_start)
    toc = html[toc_start:toc_end]

    for pid in ("a", "b", "c"):
        assert f'href="#p-{pid}"' in toc


def test_document_is_self_contained_except_deployment_and_external_links():
    present_asset = ResolvedAsset(
        original_href="cid:present.png",
        resolved_url="https://fake/present.png",
        blob_hash="sha256:bbbb",
        present=True,
        scope="same",
    )
    page = _page(
        "p1",
        content_html=(
            '<img src="cid:present.png">'
            '<a href="/wiki/w1/page/p1/entry">self link</a>'
            '<a href="/wiki/w1/page/other/entry">deployment link</a>'
            '<a href="https://docs.example.com/spec">external link</a>'
        ),
        assets=[present_asset],
        links=[
            LinkRef(
                original_href="/wiki/w1/page/p1/entry",
                resolved_url="https://fake/wiki/w1/page/p1/entry",
                scope="in_export",
                target_page_id="p1",
            ),
            LinkRef(
                original_href="/wiki/w1/page/other/entry",
                resolved_url="https://fake/wiki/w1/page/other/entry",
                scope="hcl_deployment",
            ),
            LinkRef(
                original_href="https://docs.example.com/spec",
                resolved_url="https://docs.example.com/spec",
                scope="external",
            ),
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes={"bbbb": PNG_BYTES}.get)

    found = set(re.findall(r'(?:src|href)="(https?://[^"]*)"', html))
    allowed = {
        "https://fake/wiki/w1/page/other/entry",
        "https://docs.example.com/spec",
    }
    assert found == allowed, f"unexpected external src/href left in document: {found - allowed}"


# --- determinism -------------------------------------------------------------


def test_render_html_has_no_wall_clock_and_is_deterministic():
    page = _page("p1", content_html="<p>hello</p>")
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html_1 = render_html(interchange, blob_bytes=_no_blobs)
    html_2 = render_html(interchange, blob_bytes=_no_blobs)

    assert html_1 == html_2
    assert DEFAULT_GENERATED_AT in html_1

    html_injected = render_html(
        interchange, blob_bytes=_no_blobs, generated_at="2020-01-01T00:00:00Z"
    )
    assert "2020-01-01T00:00:00Z" in html_injected
    assert DEFAULT_GENERATED_AT not in html_injected


# --- attachments -------------------


def test_attachments_present_and_not_present_are_both_shown():
    page = _page(
        "p1",
        attachments=[
            DerivedAttachment(
                id="att1",
                filename="present.pdf",
                content_type="application/pdf",
                asset=ResolvedAsset(
                    original_href="att1.pdf",
                    resolved_url="https://fake/att1.pdf",
                    blob_hash="sha256:cccc",
                    present=True,
                    scope="same",
                ),
            ),
            DerivedAttachment(
                id="att2",
                filename="missing.pdf",
                content_type="application/pdf",
                asset=ResolvedAsset(
                    original_href="att2.pdf",
                    resolved_url="https://fake/att2.pdf",
                    blob_hash=None,
                    present=False,
                    scope="same",
                ),
            ),
        ],
    )
    wiki = _wiki({"p1": page}, root_page_ids=["p1"])
    interchange = Interchange(wikis=[wiki])

    html = render_html(interchange, blob_bytes={"cccc": b"%PDF-1.4 fake"}.get)

    assert "present.pdf" in html
    assert "missing.pdf" in html
    assert "not captured" in html.lower()
