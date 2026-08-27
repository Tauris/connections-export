"""A Highlights page links into the community it fronts. Those links have to
land inside the archive.

`crosslink` is the pass that upgrades a link from "somewhere on the
deployment" to "in this export", and it runs over the finished model because
nothing can be resolved earlier. Every component has to appear in it in both
directions: as a source whose links get upgraded, and as a target other
components can link TO. A component in neither is invisible to the pass, and
its links stay pointing at the live system.

The rich content deriver resolves its body links with an empty `page_lookup`,
exactly as blogs and forums do, on the understanding that this pass finishes
the job -- so Highlights has to be in it.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl, crawl_blogs, crawl_rich_content
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_rich_content,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

COMMUNITY = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=2)


@pytest.fixture
def model(tmp_path: pathlib.Path):
    wikis, blogs = synthesize(SEED), synthesize_blogs(SEED)
    rich = synthesize_rich_content(SEED, community_uuid=COMMUNITY)
    root = tmp_path / "archive"

    def client():
        app = make_app(wikis, blogset=blogs, rteset=rich)
        return HttpClient(
            transport=httpx.MockTransport(SyncASGIBridge(app).handle_request),
            sleep=lambda _s: None,
        )

    def config():
        return Config(base_url="https://fake", output_dir=root)

    crawl(config=config(), client=client(), archive=Archive.open(root), emit=lambda _e: None)
    crawl_blogs(
        config=config(),
        client=client(),
        archive=Archive.open(root),
        blogs_homepage=blogs.homepage,
        emit=lambda _e: None,
    )
    crawl_rich_content(
        config=config(),
        client=client(),
        archive=Archive.open(root),
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    return derive(Archive.open(root))


def _highlights_links(model):
    return [
        link
        for section in model.rich_content
        for page in section.pages.values()
        for link in (page.links or [])
    ]


def test_a_highlights_link_to_a_wiki_page_lands_in_the_archive(model):
    pages = {page.id for wiki in model.wikis for page in wiki.pages.values()}
    resolved = [
        link
        for link in _highlights_links(model)
        if link.scope == "in_export" and link.target_page_id in pages
    ]
    assert resolved, [(link.original_href, link.scope) for link in _highlights_links(model)]


def test_a_highlights_link_to_a_blog_post_lands_in_the_archive(model):
    posts = {post.id for blog in model.blogs for post in blog.posts.values()}
    resolved = [
        link
        for link in _highlights_links(model)
        if link.scope == "in_export" and link.target_page_id in posts
    ]
    assert resolved, [(link.original_href, link.scope) for link in _highlights_links(model)]


def test_a_link_that_really_does_leave_the_export_is_left_alone(model):
    """The pass only ever upgrades. A wiki home URL naming no captured page
    stays what it was, rather than being pointed at something approximate."""
    stayed = [link for link in _highlights_links(model) if link.scope != "in_export"]
    assert stayed, "the fixture must include a link that cannot resolve"
