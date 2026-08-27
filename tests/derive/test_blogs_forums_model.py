""": the interchange model gains blogs & forums,
mirroring the wiki shapes (HCL-isms in Provenance, id-keyed containers,
resolved assets/links). Additive — a wiki-only model is unchanged — and
the whole thing round-trips through JSON, since the package serializes
it (Stage-2 phase 5).
"""

from __future__ import annotations

from connections_export.derive import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    Interchange,
)


def test_interchange_defaults_to_no_blogs_or_forums():
    model = Interchange(base_url="https://fake")
    assert model.blogs == []
    assert model.forums == []


def test_blog_with_threaded_comments_round_trips():
    blog = DerivedBlog(
        id="b1",
        handle="team-blog",
        title="Team Blog",
        post_ids=["p1"],
        posts={
            "p1": DerivedBlogPost(
                id="p1",
                title="Hello",
                author="A. Okafor",
                content_html="<p>hi</p>",
                ordinal=0,
                tags=["news"],
                comments=[
                    DerivedComment(id="c1", author="M. Lindqvist", content_html="<p>+1</p>"),
                    DerivedComment(id="c2", content_html="<p>re</p>", parent_comment_id="c1"),
                ],
            )
        },
    )
    model = Interchange(base_url="https://fake", blogs=[blog])

    parsed = Interchange.model_validate_json(model.model_dump_json())
    assert [b.title for b in parsed.blogs] == ["Team Blog"]
    post = parsed.blogs[0].posts["p1"]
    assert post.title == "Hello"
    assert [c.parent_comment_id for c in post.comments] == [None, "c1"]


def test_forum_reply_tree_round_trips_flat_by_id():
    # topic -> r1 -> r1a; r2 (top level)
    topic = DerivedForumTopic(
        id="t1",
        title="How do I…?",
        content_html="<p>question</p>",
        flags=["question", "answered"],
        reply_ids=["r1", "r2"],
        replies={
            "r1": DerivedForumReply(id="r1", content_html="<p>try this</p>", child_ids=["r1a"]),
            "r1a": DerivedForumReply(id="r1a", content_html="<p>worked</p>", flags=["answer"]),
            "r2": DerivedForumReply(id="r2", content_html="<p>also</p>"),
        },
    )
    forum = DerivedForum(id="f1", title="Support", topic_ids=["t1"], topics={"t1": topic})
    model = Interchange(base_url="https://fake", forums=[forum])

    parsed = Interchange.model_validate_json(model.model_dump_json())
    ptopic = parsed.forums[0].topics["t1"]
    assert ptopic.reply_ids == ["r1", "r2"]
    assert ptopic.replies["r1"].child_ids == ["r1a"]
    assert ptopic.replies["r1a"].flags == ["answer"]
    assert "answered" in ptopic.flags


def test_extra_fields_are_still_forbidden():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Interchange.model_validate({"base_url": "https://fake", "not_a_field": 1})
