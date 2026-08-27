"""Links between things captured in the same export resolve to the export.

Each container used to resolve links against whatever it knew while it was
being assembled: for a wiki, its own pages; for a blog or a forum, nothing at
all -- they were assembled with an empty lookup, so a post linking to another
post in the same blog you had just ingested was classified "somewhere on the
deployment" and left pointing at the live system.

That cannot be fixed at assembly time. A link from the first wiki to the last
forum is unresolvable until the last forum exists, so the pass runs afterwards,
over the finished model.
"""

from __future__ import annotations

from connections_export.derive.crosslink import crosslink
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
)


def _post(post_id: str, url: str, links: list[LinkRef] | None = None) -> DerivedBlogPost:
    return DerivedBlogPost(id=post_id, title=post_id, alternate_url=url, links=links or [])


def _deployment_link(url: str) -> LinkRef:
    return LinkRef(original_href=url, resolved_url=url, scope="hcl_deployment")


def test_a_post_linking_to_another_post_in_the_same_blog_resolves():
    target = _post("p2", "https://fake/blogs/b/entry/two")
    source = _post("p1", "https://fake/blogs/b/entry/one", [_deployment_link(target.alternate_url)])
    interchange = Interchange(blogs=[DerivedBlog(id="b", posts={"p1": source, "p2": target})])

    assert crosslink(interchange) == 1
    assert source.links[0].scope == "in_export"
    assert source.links[0].target_page_id == "p2"


def test_it_crosses_apps_because_they_were_captured_together():
    """A community export captures its wiki, blogs and forums in one run, so a
    blog post pointing at a wiki page is pointing at something right here."""
    page = DerivedPage(
        id="pg", label="pg", title="Page", alternate_url="https://fake/wikis/w/page/pg"
    )
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["pg"], pages={"pg": page})
    post = _post("p1", "https://fake/blogs/b/entry/one", [_deployment_link(page.alternate_url)])
    interchange = Interchange(wikis=[wiki], blogs=[DerivedBlog(id="b", posts={"p1": post})])

    assert crosslink(interchange) == 1
    assert post.links[0].target_page_id == "pg"


def test_a_link_to_something_not_captured_is_left_alone():
    """Pointing at the live deployment is the honest answer for content this
    export does not contain -- it is not a broken link, it is an outside one."""
    post = _post(
        "p1",
        "https://fake/blogs/b/entry/one",
        [_deployment_link("https://fake/blogs/other/entry/z")],
    )
    interchange = Interchange(blogs=[DerivedBlog(id="b", posts={"p1": post})])

    assert crosslink(interchange) == 0
    assert post.links[0].scope == "hcl_deployment"
    assert post.links[0].target_page_id is None


def test_external_links_are_never_reconsidered():
    external = LinkRef(
        original_href="https://example.com/x",
        resolved_url="https://example.com/x",
        scope="external",
    )
    post = _post("p1", "https://fake/blogs/b/entry/one", [external])

    crosslink(Interchange(blogs=[DerivedBlog(id="b", posts={"p1": post})]))

    assert post.links[0].scope == "external"


def test_it_only_ever_upgrades():
    """A link its own container resolved was resolved against better
    information than this pass has."""
    already = LinkRef(
        original_href="#x", resolved_url="https://fake/a", scope="in_export", target_page_id="own"
    )
    post = _post("p1", "https://fake/blogs/b/entry/one", [already])

    crosslink(Interchange(blogs=[DerivedBlog(id="b", posts={"p1": post})]))

    assert post.links[0].target_page_id == "own"


def test_the_pass_is_idempotent():
    target = _post("p2", "https://fake/blogs/b/entry/two")
    source = _post("p1", "https://fake/blogs/b/entry/one", [_deployment_link(target.alternate_url)])
    interchange = Interchange(blogs=[DerivedBlog(id="b", posts={"p1": source, "p2": target})])

    assert crosslink(interchange) == 1
    assert crosslink(interchange) == 0


def test_the_demo_community_actually_cross_links():
    """The end-to-end check, and the one that caught the real gap: blogs and
    forums carried no `alternate_url`, so nothing in them could be the target
    of a link. The sample feeds now emit `rel="alternate"` the way a real
    deployment does, without which this pass has nothing to match against."""
    from connections_export.gui.demo import run_demo

    interchange = run_demo(
        lambda _e: None, archive_dir=None, delay=0.0, sleep=lambda _s: None
    ).interchange

    blog_links = [
        link for blog in interchange.blogs for post in blog.posts.values() for link in post.links
    ]

    assert blog_links, "the demo blogs have no links to resolve"
    assert all(link.scope == "in_export" for link in blog_links)
    assert all(link.target_page_id for link in blog_links)
