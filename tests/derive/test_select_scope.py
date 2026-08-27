"""Selecting several things to export, and a wiki subtree.

`scope_interchange` narrows to exactly one unit. Export offered "Whole
archive" or "the item open in the reader" and nothing between, so exporting
two of five forums meant exporting all of them and deleting pages afterwards.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)
from connections_export.derive.scope import select_interchange


def _page(pid, parent=None, children=()):
    return DerivedPage(
        id=pid, label=pid, title=pid.title(), parent_id=parent, child_ids=list(children)
    )


def _model():
    wiki = DerivedWiki(
        id="w1",
        label="handbook",
        title="Handbook",
        root_page_ids=["root", "other"],
        pages={
            "root": _page("root", children=["kid"]),
            "kid": _page("kid", parent="root", children=["grandkid"]),
            "grandkid": _page("grandkid", parent="kid"),
            "other": _page("other"),
        },
    )
    wiki2 = DerivedWiki(
        id="w2", label="ops", title="Ops", root_page_ids=["p"], pages={"p": _page("p")}
    )
    blog = DerivedBlog(
        id="b1",
        handle="team",
        title="Team",
        post_ids=["p1"],
        posts={"p1": DerivedBlogPost(id="p1", title="Post")},
    )
    forum1 = DerivedForum(
        id="f1",
        title="Help",
        topic_ids=["t1"],
        topics={"t1": DerivedForumTopic(id="t1", title="Topic")},
    )
    forum2 = DerivedForum(
        id="f2",
        title="General",
        topic_ids=["t2"],
        topics={"t2": DerivedForumTopic(id="t2", title="Other")},
    )
    return Interchange(wikis=[wiki, wiki2], blogs=[blog], forums=[forum1, forum2])


def test_selecting_several_containers_keeps_exactly_those():
    out = select_interchange(_model(), [("forum", "f2"), ("blog", "b1")])

    assert [f.id for f in out.forums] == ["f2"]
    assert [b.id for b in out.blogs] == ["b1"]
    assert out.wikis == []


def test_selecting_a_wiki_subtree_keeps_the_page_and_everything_under_it():
    out = select_interchange(_model(), [("subtree", "kid")])

    kept = set(out.wikis[0].pages)
    assert "kid" in kept and "grandkid" in kept, "the subtree itself must be kept"
    assert "root" in kept, "ancestors stay as context"
    assert "other" not in kept, "unrelated branches are dropped"
    assert out.wikis[0].pages["root"].is_context is True
    assert out.wikis[0].pages["kid"].is_context is False


def test_a_subtree_and_a_forum_can_be_exported_together():
    out = select_interchange(_model(), [("subtree", "kid"), ("forum", "f1")])

    assert [f.id for f in out.forums] == ["f1"]
    assert set(out.wikis[0].pages) == {"root", "kid", "grandkid"}


def test_two_subtrees_of_one_wiki_merge_rather_than_replace():
    out = select_interchange(_model(), [("subtree", "kid"), ("subtree", "other")])

    assert set(out.wikis[0].pages) == {"root", "kid", "grandkid", "other"}
    assert sorted(out.wikis[0].root_page_ids) == ["other", "root"]


def test_an_empty_or_unmatched_selection_yields_nothing_not_everything():
    """Failing open would export the whole archive when the user asked for
    one thing that no longer exists."""
    assert select_interchange(_model(), []).wikis == []
    unmatched = select_interchange(_model(), [("forum", "nope")])
    assert unmatched.forums == [] and unmatched.wikis == [] and unmatched.blogs == []


def test_provenance_fields_survive_selection():
    model = _model().model_copy(update={"base_url": "https://connections.example.corp"})

    out = select_interchange(model, [("forum", "f1")])

    assert out.base_url == "https://connections.example.corp"
