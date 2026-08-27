"""The normalized model entities construct and validate."""

import pytest
from pydantic import ValidationError

from connections_export.adapters.model import (
    Attachment,
    BlogComment,
    BlogPost,
    BlogRef,
    Comment,
    ForumReply,
    ForumTopic,
    NavNode,
    NavTree,
    Page,
    ReplyNode,
    Tag,
    Version,
    WikiRef,
)


def test_wiki_ref_constructs_with_required_fields():
    ref = WikiRef(uuid="u1", label="wiki0321", title="Wiki 0321")
    assert ref.uuid == "u1"
    assert ref.created is None
    assert ref.self_url is None


def test_wiki_ref_preserves_community_uuid():
    ref = WikiRef(uuid="u1", label="wiki0321", title="Wiki 0321", community_uuid="c1")
    assert ref.community_uuid == "c1"


def test_wiki_ref_requires_uuid_label_title():
    with pytest.raises(ValidationError):
        WikiRef(label="wiki0321", title="Wiki 0321")


def test_page_constructs_with_defaults():
    page = Page(uuid="u1", label="page1", title="Page 1")
    assert page.parent_uuid is None
    assert page.version_label is None
    assert page.content_src is None
    assert page.replies_url is None
    assert page.replies_count is None
    assert page.ranks == {}
    assert page.category_term is None


def test_page_holds_ranks_and_hierarchy():
    page = Page(
        uuid="u1",
        label="page1",
        title="Page 1",
        parent_uuid="parent-uuid",
        version_label=3,
        content_src="https://example.com/media",
        replies_url="https://example.com/feed",
        replies_count=4,
        ranks={"comment": 4, "versions": 3},
        category_term="page",
    )
    assert page.parent_uuid == "parent-uuid"
    assert page.ranks["comment"] == 4


def test_nav_node_defaults():
    node = NavNode(id="p1")
    assert node.parent is None
    assert node.ordinal == 0
    assert node.child_ids == []
    assert node.is_root is False


def test_nav_tree_holds_nodes_and_root_id():
    tree = NavTree(nodes=[NavNode(id="p1", ordinal=0), NavNode(id="p2", ordinal=1)], root_id="tree")
    assert len(tree.nodes) == 2
    assert tree.root_id == "tree"


def test_nav_tree_defaults_to_empty():
    tree = NavTree()
    assert tree.nodes == []
    assert tree.root_id is None


def test_comment_requires_only_an_id():
    comment = Comment(id="c1")
    assert comment.title is None
    assert comment.in_reply_to is None


def test_version_constructs():
    version = Version(
        uuid="v1", version_label=2, author="Fake Author", created="2020-01-01T00:00:00Z"
    )
    assert version.version_label == 2
    assert version.content_src is None


def test_attachment_constructs():
    attachment = Attachment(
        uuid="a1",
        filename="diagram.png",
        content_type="image/png",
        enclosure_href="urn:fake:attachment:a1",
    )
    assert attachment.filename == "diagram.png"


def test_tag_requires_term():
    tag = Tag(term="roadmap")
    assert tag.term == "roadmap"
    with pytest.raises(ValidationError):
        Tag()


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Page(uuid="u1", label="p", title="t", bogus_field="nope")


# --- Blogs/Forums models ---


def test_blog_ref_constructs_with_required_fields():
    ref = BlogRef(uuid="b1", title="Team Blog")
    assert ref.uuid == "b1"
    assert ref.self_url is None


def test_blog_post_defaults():
    post = BlogPost(id="p1")
    assert post.title is None
    assert post.tags == []
    assert post.comments_enabled is None
    assert post.ranks == {}
    assert post.provenance is None


def test_blog_post_full_construction():
    post = BlogPost(
        id="p1",
        title="Hello",
        author="Jane Doe",
        author_userid="jdoe",
        content_html="<p>hi</p>",
        published="2009-01-04T20:32:31.171Z",
        updated="2009-01-05T20:32:31.171Z",
        tags=["roadmap", "release"],
        comments_enabled=True,
        ranks={"comment": 2},
        provenance="urn:lsid:ibm.com:blogs:entry-p1",
    )
    assert post.tags == ["roadmap", "release"]
    assert post.comments_enabled is True
    assert post.ranks == {"comment": 2}


def test_blog_comment_requires_only_id():
    comment = BlogComment(id="c1")
    assert comment.in_reply_to is None


def test_blog_comment_records_in_reply_to():
    comment = BlogComment(id="c2", author="A", content_html="<p>reply</p>", in_reply_to="c1")
    assert comment.in_reply_to == "c1"


def test_forum_topic_defaults():
    topic = ForumTopic(id="t1")
    assert topic.forum_uuid is None
    assert topic.flags == set()
    assert topic.provenance is None


def test_forum_topic_full_construction():
    topic = ForumTopic(
        id="t1",
        forum_uuid="f1",
        title="Question",
        author="A",
        content_html="<p>q</p>",
        published="2009-01-04T20:32:31.171Z",
        flags={"pinned", "question"},
        provenance="urn:lsid:ibm.com:forum:t1",
    )
    assert topic.flags == {"pinned", "question"}


def test_forum_reply_defaults():
    reply = ForumReply(id="r1")
    assert reply.ref is None
    assert reply.source_topic is None
    assert reply.flags == set()


def test_forum_reply_full_construction():
    reply = ForumReply(
        id="r2",
        author="A",
        content_html="<p>a</p>",
        published="2009-01-04T20:32:31.171Z",
        ref="r1",
        source_topic="t1",
        flags={"answer"},
    )
    assert reply.ref == "r1"
    assert reply.source_topic == "t1"
    assert reply.flags == {"answer"}


def test_reply_node_nests_children():
    leaf = ReplyNode(reply=ForumReply(id="r2", ref="r1", source_topic="t1"), children=[])
    root = ReplyNode(reply=ForumReply(id="r1", ref="t1", source_topic="t1"), children=[leaf])
    assert root.children[0].reply.id == "r2"


def test_blogs_forums_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        BlogPost(id="p1", bogus_field="nope")
    with pytest.raises(ValidationError):
        ForumTopic(id="t1", bogus_field="nope")
