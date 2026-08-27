"""`filter_by_author`: keep only a user's involvement, each with its full
chain; match by author name, snx:userid, or contributor; keep wiki ancestors
as context; drop empty containers."""

from connections_export.derive.author_filter import filter_by_author
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)

ME = "J. Weber"
MY_UID = "jweber"
OTHER = "A. Okafor"


def _wiki() -> DerivedWiki:
    # root(other) -> mid(other) -> leaf(me); sibling(other, no involvement)
    root = DerivedPage(id="root", title="Root", author=OTHER, child_ids=["mid", "sib"])
    mid = DerivedPage(id="mid", title="Mid", author=OTHER, parent_id="root", child_ids=["leaf"])
    leaf = DerivedPage(id="leaf", title="Leaf", author=ME, parent_id="mid")
    sib = DerivedPage(id="sib", title="Sib", author=OTHER, parent_id="root")
    return DerivedWiki(
        id="w",
        label="w",
        title="W",
        root_page_ids=["root"],
        pages={"root": root, "mid": mid, "leaf": leaf, "sib": sib},
    )


def _interchange(**kw) -> Interchange:
    return Interchange(
        wikis=kw.get("wikis", []), blogs=kw.get("blogs", []), forums=kw.get("forums", [])
    )


def test_wiki_keeps_authored_page_and_its_ancestors_as_context():
    result = filter_by_author(_interchange(wikis=[_wiki()]), author=ME)
    assert len(result.wikis) == 1
    pages = result.wikis[0].pages
    # leaf (authored) + its ancestors mid, root; the uninvolved sibling is gone
    assert set(pages) == {"leaf", "mid", "root"}
    assert pages["leaf"].is_context is False
    assert pages["mid"].is_context is True and pages["root"].is_context is True
    # tree stays intact: root's kept children only, no dangling ref to 'sib'
    assert pages["root"].child_ids == ["mid"]
    assert result.wikis[0].root_page_ids == ["root"]


def test_wiki_page_kept_when_the_user_only_commented():
    page = DerivedPage(
        id="p",
        title="P",
        author=OTHER,
        comments=[DerivedComment(id="c", author=ME, content_html="<p>hi</p>")],
    )
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    result = filter_by_author(_interchange(wikis=[wiki]), author=ME)
    assert set(result.wikis[0].pages) == {"p"}
    # the WHOLE page is kept -- every comment, not just the user's
    assert len(result.wikis[0].pages["p"].comments) == 1


def test_wiki_with_no_involvement_is_dropped():
    page = DerivedPage(id="p", title="P", author=OTHER)
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    assert filter_by_author(_interchange(wikis=[wiki]), author=ME).wikis == []


def test_match_by_userid_and_case_insensitively():
    page = DerivedPage(id="p", title="P", author="Someone Else", author_userid=MY_UID)
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    assert filter_by_author(_interchange(wikis=[wiki]), author="JWEBER").wikis  # userid, upper
    assert filter_by_author(_interchange(wikis=[wiki]), author="  jweber ").wikis  # trimmed


def test_match_by_contributor():
    page = DerivedPage(id="p", title="P", author=OTHER, contributors=[ME])
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    assert filter_by_author(_interchange(wikis=[wiki]), author=ME).wikis


def test_blog_keeps_post_on_author_or_comment_and_drops_others():
    mine = DerivedBlogPost(id="mine", title="Mine", author=ME)
    via_comment = DerivedBlogPost(
        id="viac",
        title="ViaComment",
        author=OTHER,
        comments=[DerivedComment(id="bc", author=ME)],
    )
    theirs = DerivedBlogPost(id="theirs", title="Theirs", author=OTHER)
    blog = DerivedBlog(
        id="b",
        handle="b",
        title="B",
        post_ids=["mine", "viac", "theirs"],
        posts={"mine": mine, "viac": via_comment, "theirs": theirs},
    )
    result = filter_by_author(_interchange(blogs=[blog]), author=ME)
    assert result.blogs[0].post_ids == ["mine", "viac"]
    assert set(result.blogs[0].posts) == {"mine", "viac"}


def test_forum_keeps_thread_on_topic_or_reply_at_any_depth():
    r_me = DerivedForumReply(id="r1", author=ME)
    topic_via_reply = DerivedForumTopic(
        id="t1", title="T1", author=OTHER, reply_ids=["r1"], replies={"r1": r_me}
    )
    topic_theirs = DerivedForumTopic(id="t2", title="T2", author=OTHER)
    forum = DerivedForum(
        id="f",
        title="F",
        topic_ids=["t1", "t2"],
        topics={"t1": topic_via_reply, "t2": topic_theirs},
    )
    result = filter_by_author(_interchange(forums=[forum]), author=ME)
    assert result.forums[0].topic_ids == ["t1"]
    # the whole thread (topic + all replies) is kept
    assert set(result.forums[0].topics["t1"].replies) == {"r1"}


def test_empty_author_is_a_noop():
    ic = _interchange(wikis=[_wiki()])
    assert filter_by_author(ic, author="  ").wikis[0].pages.keys() == ic.wikis[0].pages.keys()
