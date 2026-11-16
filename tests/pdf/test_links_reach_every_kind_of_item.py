"""A link resolved to any item in the export jumps to that item's anchor.

Derive records only the target's id, and each kind of item is rendered under
its own prefix (`p-` wiki page, `b-` blog post, `t-` forum topic, `rc-`
Highlights page, `f-` file). Writing `#p-` for all of them sent every link
between apps nowhere."""

from __future__ import annotations

import lxml.html

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
)
from connections_export.derive.scope import scope_interchange
from connections_export.pdf.browser_fidelity import render_html_browser
from connections_export.pdf.html import render_html


def _link(href: str, target: str) -> LinkRef:
    return LinkRef(
        original_href=href,
        resolved_url=f"https://hcl.example.invalid/{href}",
        scope="in_export",
        target_page_id=target,
    )


def _model() -> Interchange:
    page = DerivedPage(
        id="pg1",
        label="pg1",
        title="Wiki page",
        content_html='<a href="x-post">post</a> <a href="x-topic">topic</a>',
        links=[_link("x-post", "post1"), _link("x-topic", "topic1")],
    )
    post = DerivedBlogPost(
        id="post1",
        title="Post",
        content_html='<a href="x-page">page</a>',
        links=[_link("x-page", "pg1")],
    )
    topic = DerivedForumTopic(
        id="topic1",
        title="Topic",
        content_html='<a href="x-post">post</a>',
        links=[_link("x-post", "post1")],
    )
    return Interchange(
        wikis=[
            DerivedWiki(id="w", label="w", title="W", root_page_ids=["pg1"], pages={"pg1": page})
        ],
        blogs=[DerivedBlog(id="b", title="B", post_ids=["post1"], posts={"post1": post})],
        forums=[DerivedForum(id="f", title="F", topic_ids=["topic1"], topics={"topic1": topic})],
    )


def _jumps(html: str) -> dict[str, str]:
    tree = lxml.html.fromstring(html)
    return {
        a.text_content(): a.get("href")
        for a in tree.xpath("//a[@href]")
        if a.text_content() in ("post", "topic", "page")
    }


def _ids(html: str) -> set[str]:
    return set(lxml.html.fromstring(html).xpath("//@id"))


def test_links_between_apps_land_on_the_right_anchor():
    html = render_html(_model(), blob_bytes=lambda _d: None)
    jumps = _jumps(html)
    assert jumps == {"post": "#b-post1", "topic": "#t-topic1", "page": "#p-pg1"}
    assert {"b-post1", "t-topic1", "p-pg1"} <= _ids(html)


def test_the_browser_fidelity_renderer_agrees():
    html = render_html_browser(_model(), blob_bytes=lambda _d: None)
    assert _jumps(html) == {"post": "#b-post1", "topic": "#t-topic1", "page": "#p-pg1"}


def test_a_target_outside_the_exported_scope_keeps_its_address():
    only_wiki = scope_interchange(_model(), kind="wiki", target_id="w")
    html = render_html(only_wiki, blob_bytes=lambda _d: None)
    jumps = _jumps(html)
    assert not jumps["post"].startswith("#")
    assert "hcl.example.invalid" in jumps["post"]
