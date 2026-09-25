"""Several archives become one model: `derive.combine`.

Someone who captured a community in March and again in June, or two
communities separately, wants one site out of them, not two. The rules the
combining step keeps, each pinned here:

- containers from different archives sit side by side;
- the same container or item in several archives appears once, and the copy
  from the most recent capture wins -- per item, so a wiki is the union of
  its pages, each from the newest archive that has it;
- an item's comments and replies are its winning copy's, never a mixture;
- a link that left one archive for content another archive holds becomes an
  in-export link;
- the same id from two different deployments is two things, kept apart;
- nothing disappears silently: every duplicate is counted and every
  duplicated container says which archive won.
"""

from __future__ import annotations

import pytest

from connections_export.derive.combine import CombineInput, combine_interchanges
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedFile,
    DerivedFileLibrary,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)

BASE = "https://connections.example.com"
OTHER = "https://other.example.org"
_BLOB = "sha256:" + "d" * 64


def _page(pid, *, title=None, parent=None, children=(), body="", comments=(), links=(), **kw):
    return DerivedPage(
        id=pid,
        label=pid,
        title=title or pid.title(),
        parent_id=parent,
        child_ids=list(children),
        content_html=body,
        comments=[DerivedComment(id=c, content_html=c) for c in comments],
        links=list(links),
        alternate_url=f"{BASE}/wikis/home/wiki/handbook/page/{pid}",
        provenance=Provenance(source_url=f"{BASE}/wikis/basic/api/wiki/handbook/page/{pid}/entry"),
        **kw,
    )


def _wiki(pages, roots, *, wid="w1", title="Handbook"):
    return DerivedWiki(
        id=wid,
        label="handbook",
        title=title,
        root_page_ids=roots,
        pages={p.id: p for p in pages},
    )


def _model(*, base=BASE, wikis=(), blogs=(), forums=(), libraries=()):
    return Interchange(
        base_url=base,
        hcl_hosts=[],
        wikis=list(wikis),
        blogs=list(blogs),
        forums=list(forums),
        file_libraries=list(libraries),
    )


def _input(label, model, when, blobs=None):
    return CombineInput(
        label=label,
        model=model,
        blob_reader=lambda digest: (blobs or {}).get(digest),
        captured_at=when,
    )


def _blog(bid, posts, *, title="News"):
    return DerivedBlog(
        id=bid, title=title, post_ids=[p.id for p in posts], posts={p.id: p for p in posts}
    )


def _post(pid, *, body="", links=(), comments=()):
    return DerivedBlogPost(
        id=pid,
        title=pid.title(),
        content_html=body,
        links=list(links),
        comments=[DerivedComment(id=c, content_html=c) for c in comments],
        alternate_url=f"{BASE}/blogs/news/entry/{pid}",
    )


# --- side by side -----------------------------------------------------------------


def test_disjoint_archives_sit_side_by_side():
    """Two communities captured separately: every container of both, nothing
    reported as a duplicate, and each item says which archive it came from."""
    a = _model(wikis=[_wiki([_page("home")], ["home"])])
    b = _model(blogs=[_blog("b1", [_post("hello")])])

    combined, _read, report = combine_interchanges(
        [_input("march", a, "2025-03-01T10:00:00Z"), _input("june", b, "2025-06-01T10:00:00Z")]
    )

    assert [w.id for w in combined.wikis] == ["w1"]
    assert [b.id for b in combined.blogs] == ["b1"]
    assert report.archives == ["march", "june"]
    assert report.duplicates_total == 0
    assert report.containers == []
    assert report.origins["home"] == "march"
    assert report.origins["hello"] == "june"
    assert report.origins["w1"] == "march" and report.origins["b1"] == "june"


def test_combining_does_not_change_the_models_it_was_given():
    """The inputs are the callers' -- a console may still be showing one."""
    link = LinkRef(
        original_href=f"{BASE}/blogs/news/entry/hello",
        resolved_url=f"{BASE}/blogs/news/entry/hello",
        scope="hcl_deployment",
    )
    a = _model(wikis=[_wiki([_page("home", links=[link])], ["home"])])
    b = _model(blogs=[_blog("b1", [_post("hello")])])

    combine_interchanges([_input("a", a, "2025-01-01T00:00:00Z"), _input("b", b, None)])

    assert a.wikis[0].pages["home"].links[0].scope == "hcl_deployment"


def test_no_archives_is_an_error_not_an_empty_site():
    with pytest.raises(ValueError):
        combine_interchanges([])


# --- overlap: most recent capture wins ------------------------------------------------


def test_the_same_wiki_is_the_union_of_its_pages_each_from_the_newest_copy():
    """March has home+old, June has home (edited)+new: the wiki appears once,
    with all three pages, `home` from June."""
    march = _model(
        wikis=[
            _wiki(
                [_page("home", body="march", children=["old"]), _page("old", parent="home")],
                ["home"],
            )
        ]
    )
    june = _model(
        wikis=[
            _wiki(
                [_page("home", body="june", children=["new"]), _page("new", parent="home")],
                ["home"],
                title="Handbook (renamed)",
            )
        ]
    )

    combined, _read, report = combine_interchanges(
        [
            _input("june", june, "2025-06-01T10:00:00Z"),
            _input("march", march, "2025-03-01T10:00:00Z"),
        ]
    )

    assert len(combined.wikis) == 1
    wiki = combined.wikis[0]
    assert set(wiki.pages) == {"home", "old", "new"}
    assert wiki.pages["home"].content_html == "june"
    assert wiki.title == "Handbook (renamed)", "the container's own metadata is the newest copy's"
    assert report.duplicates["page"] == 1
    assert report.duplicates["wiki"] == 1
    [container] = report.containers
    assert (container.kind, container.id, container.winner) == ("wiki", "w1", "june")
    assert container.archives == ["june", "march"]
    assert report.origins["home"] == "june" and report.origins["old"] == "march"


def test_the_order_archives_are_given_in_does_not_decide_the_winner():
    older = _model(wikis=[_wiki([_page("home", body="old")], ["home"])])
    newer = _model(wikis=[_wiki([_page("home", body="new")], ["home"])])

    for order in ([("o", older, "2024-01-01T00:00:00Z"), ("n", newer, "2025-01-01T00:00:00Z")],):
        for inputs in (order, list(reversed(order))):
            combined, _r, _rep = combine_interchanges([_input(*args) for args in inputs])
            assert combined.wikis[0].pages["home"].content_html == "new"


def test_an_items_own_fetch_time_beats_its_archives_capture_time():
    """An archive updated in June that did not re-read a page still holds the
    March copy of it. The page's own fetch time is what says how new it is."""
    stale = _model(wikis=[_wiki([_page("home", body="fetched in march")], ["home"])])
    fresh = _model(wikis=[_wiki([_page("home", body="fetched in may")], ["home"])])
    entry = f"{BASE}/wikis/basic/api/wiki/handbook/page/home/entry"
    updated_in_june = CombineInput(
        label="updated",
        model=stale,
        blob_reader=lambda _d: None,
        captured_at="2025-06-01T00:00:00Z",
        fetched_at={entry: "2025-03-01T00:00:00Z"},
    )
    may = CombineInput(
        label="may",
        model=fresh,
        blob_reader=lambda _d: None,
        captured_at="2025-05-01T00:00:00Z",
        fetched_at={entry: "2025-05-01T00:00:00Z"},
    )

    combined, _r, _rep = combine_interchanges([updated_in_june, may])

    assert combined.wikis[0].pages["home"].content_html == "fetched in may"


def test_comments_travel_with_the_winning_copy_and_are_never_mixed():
    older = _model(blogs=[_blog("b1", [_post("hello", comments=["c-old-1", "c-old-2"])])])
    newer = _model(blogs=[_blog("b1", [_post("hello", comments=["c-new"])])])

    combined, _r, report = combine_interchanges(
        [_input("old", older, "2025-01-01T00:00:00Z"), _input("new", newer, "2025-02-01T00:00:00Z")]
    )

    assert [c.id for c in combined.blogs[0].posts["hello"].comments] == ["c-new"]
    assert report.duplicates["post"] == 1


def test_replies_travel_with_the_winning_topic():
    def forum(reply_ids):
        topic = DerivedForumTopic(
            id="t1",
            title="Question",
            reply_ids=list(reply_ids),
            replies={r: DerivedForumReply(id=r, content_html=r) for r in reply_ids},
        )
        return DerivedForum(id="f1", title="Help", topic_ids=["t1"], topics={"t1": topic})

    older = _model(forums=[forum(["r1", "r2"])])
    newer = _model(forums=[forum(["r1", "r2", "r3"])])

    combined, _r, _rep = combine_interchanges(
        [_input("new", newer, "2025-02-01T00:00:00Z"), _input("old", older, "2025-01-01T00:00:00Z")]
    )

    assert list(combined.forums[0].topics["t1"].replies) == ["r1", "r2", "r3"]


def test_a_blog_keeps_every_post_in_feed_order_newest_copy_first():
    older = _model(blogs=[_blog("b1", [_post("a"), _post("b")])])
    newer = _model(blogs=[_blog("b1", [_post("c"), _post("a")])])

    combined, _r, _rep = combine_interchanges(
        [_input("old", older, "2025-01-01T00:00:00Z"), _input("new", newer, "2025-02-01T00:00:00Z")]
    )

    assert combined.blogs[0].post_ids == ["c", "a", "b"]


def test_a_library_is_the_union_of_its_files_and_folders_follow_the_winning_copies():
    """March's `plan` sat in Drafts; June's copy of it is in Final. The folder
    lists come out of the files' newest copies, so it is in Final only."""
    from connections_export.derive.model import DerivedFileFolder

    def library(files, folders):
        return DerivedFileLibrary(
            id="lib",
            title="Docs",
            file_ids=[f.id for f in files],
            files={f.id: f for f in files},
            folders=folders,
        )

    march = _model(
        libraries=[
            library(
                [
                    DerivedFile(id="plan", folder_ids=["drafts"]),
                    DerivedFile(id="notes", folder_ids=["drafts"]),
                ],
                [DerivedFileFolder(id="drafts", file_ids=["plan", "notes"])],
            )
        ]
    )
    june = _model(
        libraries=[
            library(
                [DerivedFile(id="plan", folder_ids=["final"])],
                [
                    DerivedFileFolder(id="drafts", file_ids=[]),
                    DerivedFileFolder(id="final", file_ids=["plan"]),
                ],
            )
        ]
    )

    combined, _r, report = combine_interchanges(
        [
            _input("march", march, "2025-03-01T00:00:00Z"),
            _input("june", june, "2025-06-01T00:00:00Z"),
        ]
    )

    [merged] = combined.file_libraries
    assert set(merged.files) == {"plan", "notes"}
    folders = {folder.id: folder.file_ids for folder in merged.folders}
    assert folders == {"drafts": ["notes"], "final": ["plan"]}
    assert report.duplicates == {"file library": 1, "file": 1}


# --- hierarchy -------------------------------------------------------------------------------


def test_a_page_moved_between_captures_sits_under_its_newest_parent_only():
    """In March `child` was under `a`; by June it moved under `b`. It must not
    appear under both -- `a`'s March child list is not the truth any more."""
    march = _model(
        wikis=[
            _wiki(
                [
                    _page("a", children=["child"]),
                    _page("b"),
                    _page("child", parent="a"),
                ],
                ["a", "b"],
            )
        ]
    )
    june = _model(
        wikis=[_wiki([_page("b", children=["child"]), _page("child", parent="b")], ["b"])]
    )

    combined, _r, _rep = combine_interchanges(
        [
            _input("march", march, "2025-03-01T00:00:00Z"),
            _input("june", june, "2025-06-01T00:00:00Z"),
        ]
    )

    pages = combined.wikis[0].pages
    assert pages["child"].parent_id == "b"
    assert pages["b"].child_ids == ["child"]
    assert pages["a"].child_ids == [], "a stale child list would put the page in two places"
    assert combined.wikis[0].root_page_ids == ["b", "a"]


def test_a_page_whose_winning_parent_is_missing_stands_at_the_top():
    """June's copy of `orphan` names a parent no archive holds. Like any
    orphan it stands at the top level rather than being lost."""
    march = _model(wikis=[_wiki([_page("home"), _page("orphan")], ["home", "orphan"])])
    june = _model(wikis=[_wiki([_page("orphan", parent="gone")], [])])

    combined, _r, _rep = combine_interchanges(
        [
            _input("march", march, "2025-03-01T00:00:00Z"),
            _input("june", june, "2025-06-01T00:00:00Z"),
        ]
    )

    wiki = combined.wikis[0]
    assert "orphan" in wiki.root_page_ids
    assert wiki.pages["orphan"].parent_id == "gone", "what was captured is kept, not rewritten"


# --- cross-archive links -----------------------------------------------------------------------


def test_a_link_to_content_another_archive_holds_becomes_in_export():
    """The main reason to combine: the wiki archive's link to a blog post was
    a link to the live deployment; the blog archive holds the post."""
    link = LinkRef(
        original_href="/blogs/news/entry/hello",
        resolved_url=f"{BASE}/blogs/news/entry/hello",
        scope="hcl_deployment",
    )
    back = LinkRef(
        original_href=f"{BASE}/wikis/home/wiki/handbook/page/home",
        resolved_url=f"{BASE}/wikis/home/wiki/handbook/page/home",
        scope="hcl_deployment",
    )
    elsewhere = LinkRef(
        original_href="/blogs/news/entry/never-captured",
        resolved_url=f"{BASE}/blogs/news/entry/never-captured",
        scope="hcl_deployment",
    )
    wiki = _model(wikis=[_wiki([_page("home", links=[link, elsewhere])], ["home"])])
    blog = _model(blogs=[_blog("b1", [_post("hello", links=[back])])])

    combined, _r, report = combine_interchanges(
        [_input("wiki", wiki, "2025-01-01T00:00:00Z"), _input("blog", blog, "2025-01-02T00:00:00Z")]
    )

    home = combined.wikis[0].pages["home"]
    assert home.links[0].scope == "in_export"
    assert home.links[0].target_page_id == "hello"
    assert home.links[1].scope == "hcl_deployment", "an uncaptured target stays a live link"
    post = combined.blogs[0].posts["hello"]
    assert (post.links[0].scope, post.links[0].target_page_id) == ("in_export", "home")
    assert report.links_resolved == 2


def test_a_wiki_page_link_by_its_api_path_resolves_across_archives():
    """A wiki page is reachable by its entry URL's `/wiki/<w>/page/<p>` path as
    well as by its browser URL -- the same keys derive itself matches on."""
    link = LinkRef(
        original_href="/wikis/basic/api/wiki/handbook/page/home/media",
        resolved_url=f"{BASE}/wikis/basic/api/wiki/handbook/page/home/media",
        scope="hcl_deployment",
    )
    wiki = _model(wikis=[_wiki([_page("home")], ["home"])])
    blog = _model(blogs=[_blog("b1", [_post("hello", links=[link])])])

    combined, _r, report = combine_interchanges(
        [_input("wiki", wiki, None), _input("blog", blog, None)]
    )

    assert combined.blogs[0].posts["hello"].links[0].target_page_id == "home"
    assert report.links_resolved == 1


def test_a_link_to_a_file_another_archive_holds_becomes_in_export():
    link = LinkRef(
        original_href=f"{BASE}/files/app/file/doc-1",
        resolved_url=f"{BASE}/files/app/file/doc-1",
        scope="hcl_deployment",
    )
    library = DerivedFileLibrary(
        id="lib", title="Docs", file_ids=["doc-1"], files={"doc-1": DerivedFile(id="doc-1")}
    )
    pages = _model(wikis=[_wiki([_page("home", links=[link])], ["home"])])
    files = _model(libraries=[library])

    combined, _r, report = combine_interchanges(
        [_input("pages", pages, None), _input("files", files, None)]
    )

    resolved = combined.wikis[0].pages["home"].links[0]
    assert (resolved.scope, resolved.target_file_id) == ("in_export", "doc-1")
    assert report.links_resolved == 1


def test_a_link_never_resolves_into_a_different_deployment():
    """Same path on another deployment is another page."""
    link = LinkRef(
        original_href="/wikis/home/wiki/handbook/page/home",
        resolved_url=f"{OTHER}/wikis/home/wiki/handbook/page/home",
        scope="hcl_deployment",
    )
    here = _model(wikis=[_wiki([_page("home")], ["home"])])
    there = _model(base=OTHER, blogs=[_blog("b1", [_post("hello", links=[link])])])

    combined, _r, report = combine_interchanges(
        [_input("here", here, None), _input("there", there, None)]
    )

    assert combined.blogs[0].posts["hello"].links[0].scope == "hcl_deployment"
    assert report.links_resolved == 0


# --- blobs -------------------------------------------------------------------------------------


def test_a_blob_is_found_in_whichever_archive_holds_it():
    asset = ResolvedAsset(
        original_href="/img.png",
        resolved_url=f"{BASE}/img.png",
        blob_hash=_BLOB,
        present=True,
        scope="same",
    )
    a = _model(wikis=[_wiki([_page("home", assets=[asset])], ["home"])])
    b = _model(blogs=[_blog("b1", [_post("hello")])])

    _combined, read, _rep = combine_interchanges(
        [_input("a", a, None), _input("b", b, None, blobs={_BLOB: b"bytes"})]
    )

    assert read(_BLOB) == b"bytes"
    assert read("sha256:" + "e" * 64) is None
    assert read("../../etc/passwd") is None, "a digest is validated before any archive sees it"


# --- different deployments ---------------------------------------------------------------------


def test_the_same_id_from_two_deployments_is_kept_apart_and_reported():
    here = _model(wikis=[_wiki([_page("home", body="here")], ["home"])])
    there = _model(base=OTHER, wikis=[_wiki([_page("home", body="there")], ["home"])])

    combined, _r, report = combine_interchanges(
        [
            _input("here", here, "2025-01-01T00:00:00Z"),
            _input("there", there, "2025-02-01T00:00:00Z"),
        ]
    )

    assert len(combined.wikis) == 2
    bodies = sorted(page.content_html for wiki in combined.wikis for page in wiki.pages.values())
    assert bodies == ["here", "there"]
    assert report.duplicates_total == 0, "two deployments' things are not duplicates"
    renamed = {(c.kind, c.id, c.archive) for c in report.collisions}
    assert ("wiki", "w1", "there") in renamed and ("page", "home", "there") in renamed
    second = next(w for w in combined.wikis if w.id != "w1")
    [page_id] = second.pages
    assert second.root_page_ids == [page_id] and page_id != "home"
    assert report.origins[page_id] == "there"


def test_the_report_summarises_what_happened():
    older = _model(wikis=[_wiki([_page("home")], ["home"])])
    newer = _model(wikis=[_wiki([_page("home")], ["home"])])

    _c, _r, report = combine_interchanges(
        [_input("old", older, "2025-01-01T00:00:00Z"), _input("new", newer, "2025-02-01T00:00:00Z")]
    )

    text = report.summary()
    assert "2 archives combined" in text
    assert "2 duplicate(s) merged" in text
    assert "0 cross-archive link(s) resolved" in text
