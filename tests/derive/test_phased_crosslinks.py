"""Links must resolve across components captured in different runs.

A community can be captured in phases: the wiki today, the blog next week when
someone decides it matters too. A link from a wiki page to a blog post has to
work in the finished archive regardless of which run brought each side in --
otherwise "extend" produces an archive whose halves cannot see each other, and
the user is punished for not having decided everything up front.

This works because derive is not incremental: `derive(archive)` recomputes the
whole model from the manifest every time, and `crosslink` then runs across all
of it. These tests pin that property, because an "optimization" that made
derive incremental would break it silently -- the links would simply stay
external, which looks like a captured link to an uncaptured target.
"""

from __future__ import annotations

import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl, crawl_blogs
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_blogs
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=2, pages_per_level=2, human_names=True)
BASE = "https://fake"


@pytest.fixture
def deployment(tmp_path):
    wikiset = synthesize(SEED)
    blogset = synthesize_blogs(SEED)
    app = make_app(wikiset, blogset=blogset)
    client = HttpClient(transport=SyncASGIBridge(app), sleep=lambda _s: None)
    return client, Archive.open(tmp_path / "archive"), wikiset, blogset


def _wiki_links(model) -> list:
    """Only the wiki's links. The claim under test is about what a wiki page
    points at; a blog post's own body links are a separate population and
    sweeping them in would make the assertion mean something else."""
    return [link for wiki in model.wikis for page in wiki.pages.values() for link in page.links]


def _links(model) -> list:
    out = []
    for wiki in model.wikis:
        for page in wiki.pages.values():
            out.extend(page.links)
    for blog in model.blogs:
        for post in blog.posts.values():
            out.extend(post.links)
    return out


def _resolved(model) -> list:
    return [link for link in _links(model) if getattr(link, "target_id", None)]


def test_a_link_left_dangling_by_the_first_run_resolves_after_the_second(deployment, tmp_path):
    """The point of the whole feature, in one assertion: capture the wiki
    alone, then add the blog, and the wiki's links into the blog now land
    inside the archive."""
    client, archive, _wikiset, blogset = deployment
    config = Config(base_url=BASE, output_dir=tmp_path)

    crawl(config=config, client=client, archive=archive, emit=lambda _e: None)
    wiki_only = derive(archive)

    # After phase one the wiki's links into the blog are known to be on this
    # deployment, and known not to be in the archive.
    dangling = [link for link in _wiki_links(wiki_only) if link.scope == "hcl_deployment"]
    assert dangling, "the fixture has no cross-app link, so this proves nothing"
    assert not any(link.target_page_id for link in dangling)

    crawl_blogs(
        config=Config(base_url=BASE, output_dir=tmp_path, fetch="update"),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=lambda _e: None,
    )
    both = derive(archive)

    assert both.blogs, "the second run captured no blog"
    # Every one of them now points inside the archive.
    assert not [link for link in _wiki_links(both) if link.scope == "hcl_deployment"]
    resolved_wiki = [link for link in _wiki_links(both) if link.target_page_id]
    resolved_before = [link for link in _wiki_links(wiki_only) if link.target_page_id]
    assert len(resolved_wiki) == len(resolved_before) + len(dangling)


def test_deriving_twice_over_the_same_archive_gives_the_same_links(deployment, tmp_path):
    """Derive is a pure function of the archive. If it ever became
    incremental, a phased capture would silently keep its first-run answer --
    and an unresolved cross-link is indistinguishable from a link to something
    that was never captured."""
    client, archive, _w, blogset = deployment
    config = Config(base_url=BASE, output_dir=tmp_path)
    crawl(config=config, client=client, archive=archive, emit=lambda _e: None)
    crawl_blogs(
        config=config,
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=lambda _e: None,
    )

    first = derive(archive)
    second = derive(archive)

    def shape(model):
        return sorted(
            (getattr(link, "href", ""), getattr(link, "target_id", None)) for link in _links(model)
        )

    assert shape(first) == shape(second)


def test_the_order_the_components_arrived_in_does_not_change_the_result(deployment, tmp_path):
    """Wiki-then-blog and blog-then-wiki must produce the same archive. A
    result that depended on capture order would make "extend" a different
    product from "capture it all at once"."""
    client, archive, _w, blogset = deployment
    config = Config(base_url=BASE, output_dir=tmp_path)
    crawl(config=config, client=client, archive=archive, emit=lambda _e: None)
    crawl_blogs(
        config=config,
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=lambda _e: None,
    )
    wiki_first = derive(archive)

    other = Archive.open(tmp_path / "other")
    other_config = Config(base_url=BASE, output_dir=tmp_path / "other")
    crawl_blogs(
        config=other_config,
        client=client,
        archive=other,
        blogs_homepage=blogset.homepage,
        emit=lambda _e: None,
    )
    crawl(config=other_config, client=client, archive=other, emit=lambda _e: None)
    blog_first = derive(other)

    assert len(_resolved(wiki_first)) == len(_resolved(blog_first))
    assert {w.id for w in wiki_first.wikis} == {w.id for w in blog_first.wikis}
    assert {b.id for b in wiki_first.blogs} == {b.id for b in blog_first.blogs}
