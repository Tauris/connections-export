""": the portable package carries blogs and
forums too. The interchange.json serializes the whole model (pydantic),
so this checks the manifest derivation (counts + capabilities per app)
and a write→load round-trip of a blog/forum model.
"""

from __future__ import annotations

from connections_export.archive.store import Archive
from connections_export.derive import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedCommunity,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    Interchange,
)
from connections_export.interchange.manifest import build_manifest
from connections_export.interchange.package import load_package, write_package


def _model_with_blog_and_forum() -> Interchange:
    blog = DerivedBlog(
        id="b1",
        handle="team-blog",
        title="Team Blog",
        post_ids=["p1"],
        posts={
            "p1": DerivedBlogPost(
                id="p1",
                title="Hello",
                content_html="<p>hi</p>",
                comments=[
                    DerivedComment(id="c1", content_html="<p>a</p>"),
                    DerivedComment(id="c2", content_html="<p>b</p>", parent_comment_id="c1"),
                ],
            )
        },
    )
    forum = DerivedForum(
        id="f1",
        title="Support",
        topic_ids=["t1"],
        topics={
            "t1": DerivedForumTopic(
                id="t1",
                title="How?",
                content_html="<p>q</p>",
                flags=["question", "answered"],
                reply_ids=["r1"],
                replies={
                    "r1": DerivedForumReply(id="r1", content_html="<p>x</p>", child_ids=["r1a"]),
                    "r1a": DerivedForumReply(id="r1a", content_html="<p>y</p>", flags=["answer"]),
                },
            )
        },
    )
    return Interchange(
        base_url="https://fake",
        blogs=[blog],
        forums=[forum],
        communities=[DerivedCommunity(id="community-1", forum_ids=["f1"], blog_id="b1")],
    )


def test_manifest_counts_and_capabilities_cover_blogs_and_forums():
    manifest = build_manifest(_model_with_blog_and_forum())

    assert manifest.counts.blogs == 1
    assert manifest.counts.blog_posts == 1
    assert manifest.counts.blog_comments == 2
    assert manifest.counts.forums == 1
    assert manifest.counts.forum_topics == 1
    assert manifest.counts.forum_replies == 2
    assert manifest.counts.communities == 1

    assert manifest.capabilities.blogs == "present"
    assert manifest.capabilities.forums == "present"
    assert manifest.capabilities.communities == "present"
    # one blog comment has a parent, one reply has children
    assert manifest.capabilities.blog_comment_threading == "present"
    assert manifest.capabilities.forum_reply_threading == "present"


def test_wiki_only_manifest_reports_blogs_and_forums_absent():
    manifest = build_manifest(Interchange(base_url="https://fake"))
    assert manifest.capabilities.blogs == "absent"
    assert manifest.capabilities.forums == "absent"
    assert manifest.capabilities.blog_comment_threading == "none"
    assert manifest.capabilities.forum_reply_threading == "none"
    assert manifest.counts.blogs == 0 and manifest.counts.forums == 0


def test_package_round_trips_blogs_and_forums(tmp_path):
    model = _model_with_blog_and_forum()
    archive = Archive.open(tmp_path / "archive")  # no blobs referenced (assets absent)
    dest = tmp_path / "package"

    write_package(model, archive, dest, generated_at="2026-07-21T00:00:00Z")
    loaded = load_package(dest)

    assert loaded == model  # full pydantic equality, blogs + forums included
    assert loaded.forums[0].topics["t1"].replies["r1"].child_ids == ["r1a"]
