"""`scope_interchange`: narrow a derived model to one addressable unit --
whole container (wiki/blog/forum) or single item (page/post/topic), with a
wiki page keeping its ancestors as context. Unknown kind / unmatched id ->
empty interchange, never a raise."""

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)
from connections_export.derive.scope import scope_interchange, single_node_interchange


def _wiki(wid: str) -> DerivedWiki:
    # root -> mid -> leaf; plus a sibling under root
    root = DerivedPage(id="root", title="Root", child_ids=["mid", "sib"])
    mid = DerivedPage(id="mid", title="Mid", parent_id="root", child_ids=["leaf"])
    leaf = DerivedPage(id="leaf", title="Leaf", parent_id="mid", child_ids=[])
    sib = DerivedPage(id="sib", title="Sib", parent_id="root")
    return DerivedWiki(
        id=wid,
        label=wid,
        title=wid.upper(),
        root_page_ids=["root"],
        pages={"root": root, "mid": mid, "leaf": leaf, "sib": sib},
    )


def _blog(bid: str) -> DerivedBlog:
    return DerivedBlog(
        id=bid,
        title=bid.upper(),
        post_ids=["p1", "p2"],
        posts={
            "p1": DerivedBlogPost(id="p1", title="P1"),
            "p2": DerivedBlogPost(id="p2", title="P2"),
        },
    )


def _forum(fid: str) -> DerivedForum:
    t1 = DerivedForumTopic(
        id="t1",
        title="T1",
        reply_ids=["r1"],
        replies={"r1": DerivedForumReply(id="r1", author="A", content_html="<p>r1</p>")},
    )
    t2 = DerivedForumTopic(id="t2", title="T2")
    return DerivedForum(
        id=fid, title=fid.upper(), topic_ids=["t1", "t2"], topics={"t1": t1, "t2": t2}
    )


def _full() -> Interchange:
    return Interchange(
        wikis=[_wiki("wa"), _wiki("wb")],
        blogs=[_blog("ba")],
        forums=[_forum("fa")],
    )


# --- whole-container scopes -------------------------------------------------


def test_scope_wiki_keeps_only_that_wiki_whole():
    result = scope_interchange(_full(), kind="wiki", target_id="wa")
    assert [w.id for w in result.wikis] == ["wa"]
    assert result.blogs == [] and result.forums == []
    # the whole wiki -- every page, nothing pruned
    assert set(result.wikis[0].pages) == {"root", "mid", "leaf", "sib"}


def test_scope_blog_keeps_only_that_blog_whole():
    result = scope_interchange(_full(), kind="blog", target_id="ba")
    assert [b.id for b in result.blogs] == ["ba"]
    assert result.wikis == [] and result.forums == []
    assert result.blogs[0].post_ids == ["p1", "p2"]


def test_scope_forum_keeps_only_that_forum_whole():
    result = scope_interchange(_full(), kind="forum", target_id="fa")
    assert [f.id for f in result.forums] == ["fa"]
    assert result.forums[0].topic_ids == ["t1", "t2"]


# --- single-item scopes -----------------------------------------------------


def test_scope_page_keeps_page_and_ancestors_as_context_prunes_children():
    result = scope_interchange(_full(), kind="page", target_id="mid")
    assert result.blogs == [] and result.forums == []
    (wiki,) = result.wikis
    # mid + its ancestor root; the leaf child and the sibling are gone
    assert set(wiki.pages) == {"root", "mid"}
    assert wiki.pages["mid"].is_context is False
    assert wiki.pages["root"].is_context is True
    assert wiki.pages["mid"].child_ids == []  # "this page", not its subtree
    assert wiki.pages["root"].child_ids == ["mid"]  # sib pruned
    assert wiki.root_page_ids == ["root"]


def test_scope_post_keeps_only_that_post():
    result = scope_interchange(_full(), kind="post", target_id="p2")
    (blog,) = result.blogs
    assert blog.post_ids == ["p2"]
    assert set(blog.posts) == {"p2"}


def test_scope_topic_keeps_whole_thread():
    result = scope_interchange(_full(), kind="topic", target_id="t1")
    (forum,) = result.forums
    assert forum.topic_ids == ["t1"]
    assert set(forum.topics) == {"t1"}
    # the whole thread comes along -- replies live inside the topic
    assert set(forum.topics["t1"].replies) == {"r1"}


# --- misses -----------------------------------------------------------------


def test_unmatched_id_yields_empty_interchange():
    result = scope_interchange(_full(), kind="topic", target_id="nope")
    assert result.wikis == [] and result.blogs == [] and result.forums == []


def test_unknown_kind_yields_empty_interchange():
    result = scope_interchange(_full(), kind="galaxy", target_id="wa")
    assert result.wikis == [] and result.blogs == [] and result.forums == []


def test_single_node_page_drops_ancestors_unlike_item_scope():
    # For the live-preview tiles, a page tile is JUST that page -- no ancestor
    # chain (which `scope_interchange(kind=page)` keeps), so tiles never
    # duplicate ancestors across the strip.
    result = single_node_interchange(_full(), kind="page", target_id="mid")
    (wiki,) = result.wikis
    assert set(wiki.pages) == {"mid"}  # no "root"
    assert wiki.root_page_ids == ["mid"]
    assert wiki.pages["mid"].parent_id is None
    assert wiki.pages["mid"].child_ids == []


def test_single_node_post_and_topic_match_item_scope():
    # Posts/topics are flat (no ancestors), so single-node == item scope there.
    post = single_node_interchange(_full(), kind="post", target_id="p2")
    assert post.blogs[0].post_ids == ["p2"]
    topic = single_node_interchange(_full(), kind="topic", target_id="t1")
    assert topic.forums[0].topic_ids == ["t1"]
    assert set(topic.forums[0].topics["t1"].replies) == {"r1"}


def test_single_node_unmatched_is_empty():
    assert single_node_interchange(_full(), kind="page", target_id="nope").wikis == []
    assert single_node_interchange(_full(), kind="wiki", target_id="wa").wikis == []  # leaves only


def test_scope_preserves_run_level_provenance():
    full = _full().model_copy(update={"base_url": "https://fake", "run_id": "run-1"})
    result = scope_interchange(full, kind="wiki", target_id="wa")
    assert result.base_url == "https://fake"
    assert result.run_id == "run-1"
