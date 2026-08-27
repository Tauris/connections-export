"""Blogs & forums in the PDF renderers (Stage-2 phase 7): both Path A
(`render_html`) and Path B (`render_html_browser`) render blogs and forums
alongside wikis. Fixtures build the interchange model by hand -- a blog with
threaded comments and a forum with an arbitrary-depth reply tree -- and the
same content assertions run against both paths.
"""

from __future__ import annotations

import re

import pytest

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    Interchange,
)
from connections_export.pdf.browser_fidelity import render_html_browser
from connections_export.pdf.html import render_html


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _blog() -> DerivedBlog:
    return DerivedBlog(
        id="blog1",
        handle="team-blog",
        title="Team Blog",
        post_ids=["post-a", "post-b"],
        posts={
            "post-a": DerivedBlogPost(
                id="post-a",
                title="First Post",
                author="Alice",
                created="2020-01-01",
                content_html="<p>Post A body</p><script>alert('post')</script>",
                comments=[
                    DerivedComment(
                        id="bc1",
                        author="Bob",
                        content_html="<p>Top comment</p>",
                        created="2020-01-02",
                    ),
                    DerivedComment(
                        id="bc2",
                        author="Carol",
                        content_html="<p>Reply comment</p><script>alert('c')</script>",
                        created="2020-01-03",
                        parent_comment_id="bc1",
                    ),
                ],
            ),
            "post-b": DerivedBlogPost(
                id="post-b",
                title="Second Post",
                author="Dave",
                created="2020-02-01",
                content_html="<p>Post B body</p>",
            ),
        },
    )


def _forum() -> DerivedForum:
    # topic -> r1 -> r2 (nested); r1 -> r3; topic -> r4
    return DerivedForum(
        id="forum1",
        title="Support Forum",
        topic_ids=["topic-x"],
        topics={
            "topic-x": DerivedForumTopic(
                id="topic-x",
                title="How do I?",
                author="Eve",
                created="2020-03-01",
                flags=["pinned", "question"],
                content_html="<p>Topic body</p><script>alert('topic')</script>",
                reply_ids=["r1", "r4"],
                replies={
                    "r1": DerivedForumReply(
                        id="r1",
                        author="Frank",
                        content_html="<p>Reply one</p>",
                        created="2020-03-02",
                        child_ids=["r2", "r3"],
                    ),
                    "r2": DerivedForumReply(
                        id="r2",
                        author="Grace",
                        content_html="<p>Reply two</p><script>alert('r2')</script>",
                        created="2020-03-03",
                        flags=["answer"],
                    ),
                    "r3": DerivedForumReply(
                        id="r3",
                        author="Heidi",
                        content_html="<p>Reply three</p>",
                        created="2020-03-04",
                    ),
                    "r4": DerivedForumReply(
                        id="r4",
                        author="Ivan",
                        content_html="<p>Reply four</p>",
                        created="2020-03-05",
                    ),
                },
            )
        },
    )


def _interchange() -> Interchange:
    return Interchange(base_url="https://fake", blogs=[_blog()], forums=[_forum()])


# render both paths through the same content assertions.
_RENDERERS = [
    pytest.param(lambda ic: render_html(ic, blob_bytes=_no_blobs), id="path-a"),
    pytest.param(lambda ic: render_html_browser(ic, _no_blobs), id="path-b"),
]


@pytest.mark.parametrize("render", _RENDERERS)
def test_every_post_comment_topic_and_reply_text_is_present(render):
    html = render(_interchange())
    for text in (
        "Post A body",
        "Post B body",
        "Top comment",
        "Reply comment",
        "Topic body",
        "Reply one",
        "Reply two",
        "Reply three",
        "Reply four",
    ):
        assert text in html, f"missing: {text}"
    # authors surface too
    for author in ("Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi", "Ivan"):
        assert author in html


@pytest.mark.parametrize("render", _RENDERERS)
def test_scripts_in_post_topic_and_reply_bodies_do_not_survive(render):
    html = render(_interchange())
    assert "<script" not in html.lower()
    for payload in ("alert('post')", "alert('c')", "alert('topic')", "alert('r2')"):
        assert payload not in html


@pytest.mark.parametrize("render", _RENDERERS)
def test_toc_lists_blogs_and_forums(render):
    html = render(_interchange())
    toc = html[html.index('id="toc"') : html.index("</nav>")]
    assert "Team Blog" in toc
    assert "Support Forum" in toc
    assert 'href="#b-post-a"' in toc
    assert 'href="#b-post-b"' in toc
    assert 'href="#t-topic-x"' in toc


@pytest.mark.parametrize("render", _RENDERERS)
def test_post_topic_and_reply_anchors_are_present(render):
    html = render(_interchange())
    assert re.search(r'<section[^>]*id="b-post-a"', html)
    assert re.search(r'<section[^>]*id="b-post-b"', html)
    assert re.search(r'<section[^>]*id="t-topic-x"', html)
    for reply_id in ("r1", "r2", "r3", "r4"):
        assert re.search(rf'id="r-{reply_id}"', html)


@pytest.mark.parametrize("render", _RENDERERS)
def test_reply_tree_is_depth_first_and_indented(render):
    html = render(_interchange())
    # r1, then its nested children r2/r3, then the next top-level reply r4.
    pos = {rid: html.index(f'id="r-{rid}"') for rid in ("r1", "r2", "r3", "r4")}
    assert pos["r1"] < pos["r2"] < pos["r3"] < pos["r4"]
    # nested replies are indented by depth, top-level ones are not.
    r1_block = html[pos["r1"] : pos["r2"]]
    r2_block = html[pos["r2"] : pos["r3"]]
    assert 'data-depth="0"' in r1_block
    assert 'data-depth="1"' in r2_block
    assert "margin-left: 1.5em" in r2_block
    # the "answer" flag on r2 surfaces.
    assert "answer" in r2_block


def test_path_b_scopes_conflicting_post_author_css_per_section():
    """Path B fidelity: two posts with conflicting author `<style>` are
    scoped to their own sections, so post A's `.x` can't restyle post B's
    -- mirroring the page isolation test in test_browser_fidelity.py."""
    blog = DerivedBlog(
        id="blog1",
        title="Styled Blog",
        post_ids=["pa", "pb"],
        posts={
            "pa": DerivedBlogPost(
                id="pa",
                title="A",
                content_html="<style>.x{color:red}</style><p class='x'>A</p>",
            ),
            "pb": DerivedBlogPost(
                id="pb",
                title="B",
                content_html="<style>.x{color:blue}</style><p class='x'>B</p>",
            ),
        },
    )
    html = render_html_browser(Interchange(blogs=[blog]), _no_blobs)

    assert "#b-pa .x" in html and "#b-pb .x" in html
    # the raw, unscoped global rules must NOT survive (no bleed)
    assert ".x{color:red}" not in html and ".x{color:blue}" not in html


def test_path_b_scopes_conflicting_topic_author_css_per_section():
    forum = DerivedForum(
        id="forum1",
        title="Styled Forum",
        topic_ids=["ta", "tb"],
        topics={
            "ta": DerivedForumTopic(
                id="ta",
                title="A",
                content_html="<style>.y{color:red}</style><p class='y'>A</p>",
            ),
            "tb": DerivedForumTopic(
                id="tb",
                title="B",
                content_html="<style>.y{color:blue}</style><p class='y'>B</p>",
            ),
        },
    )
    html = render_html_browser(Interchange(forums=[forum]), _no_blobs)

    assert "#t-ta .y" in html and "#t-tb .y" in html
    assert ".y{color:red}" not in html and ".y{color:blue}" not in html
