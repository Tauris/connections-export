"""`demo_sample_urls` (`connections_export.gui.demo`)
and `GET /api/demo-urls` (`connections_export.gui.app`).

The coherence guarantee this module exists to prove: every sample chip
URL `demo_sample_urls` builds actually round-trips through
`connections_export.gui.wiki_url.parse_url` -- the same parser
`/api/identify` uses -- to the expected app/scope/id, and every id in
those URLs is drawn from the fakeserver's own synthesized entities
(`build_prototype_wikiset`/`synthesize_blogs`/`synthesize_forums`), never
hardcoded. If a shape didn't actually round-trip, this is where it would
be caught -- these assertions are not to be weakened to make them pass.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.fakeserver import SynthSeed, synthesize_blogs, synthesize_forums
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.gui.app import make_app
from connections_export.gui.demo import demo_sample_urls
from connections_export.gui.wiki_url import parse_url


def _urls_by_kind() -> dict[str, dict]:
    return {item["kind"]: item for item in demo_sample_urls()}


def test_returns_one_item_per_documented_kind():
    kinds = {item["kind"] for item in demo_sample_urls()}
    assert kinds == {
        "wiki-all",
        "wiki-page",
        "blog-all",
        "blog-post",
        "forum-all",
        "forum-thread",
        "community-all",
        "unrecognized",
    }


def test_wiki_all_round_trips():
    item = _urls_by_kind()["wiki-all"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "wiki"
    assert result.scope == "all"
    assert result.wiki_label


def test_wiki_page_round_trips():
    item = _urls_by_kind()["wiki-page"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "wiki"
    # A `.../page/{label}` URL is a single-page target: `page_label` is
    # populated and `scope == "single"`, so capture samples that page (not the
    # wiki's first). A wiki-level URL stays `scope == "all"`, page_label None.
    assert result.scope == "single"
    assert result.page_label
    assert result.wiki_label


def test_blog_all_round_trips():
    item = _urls_by_kind()["blog-all"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "blog"
    assert result.scope == "all"
    assert result.blog_handle
    assert result.entry_slug is None


def test_blog_post_round_trips():
    item = _urls_by_kind()["blog-post"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "blog"
    assert result.scope == "single"
    assert result.blog_handle
    assert result.entry_slug


def test_forum_all_round_trips():
    item = _urls_by_kind()["forum-all"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "forum"
    assert result.scope == "all"
    assert result.forum_uuid
    assert result.topic_id is None


def test_forum_thread_round_trips():
    item = _urls_by_kind()["forum-thread"]
    result = parse_url(item["url"])
    assert result.ok is True
    assert result.app == "forum"
    assert result.scope == "single"
    assert result.forum_uuid
    assert result.topic_id


def test_unrecognized_is_not_identified():
    item = _urls_by_kind()["unrecognized"]
    result = parse_url(item["url"])
    assert result.ok is False


def test_ids_come_from_the_fakeserver_synth_not_hardcoded():
    # wiki: the chip's label carries a real wiki label from
    # build_prototype_wikiset -- read from the synth, never a literal
    # string duplicated in the chip builder.
    wikiset = build_prototype_wikiset()
    expected_wiki_label = wikiset.wikis[0].label
    wiki_item = _urls_by_kind()["wiki-all"]
    assert expected_wiki_label in wiki_item["url"]

    expected_page_label = wikiset.wikis[0].pages[0].label
    page_item = _urls_by_kind()["wiki-page"]
    assert expected_page_label in page_item["url"]

    # blog: the first blog's handle and its first post's slug.
    bf_seed = SynthSeed(seed=0, human_names=True)
    blogset = synthesize_blogs(bf_seed)
    expected_handle = blogset.blogs[0].handle
    expected_slug = blogset.blogs[0].posts[0].slug
    assert expected_handle in _urls_by_kind()["blog-all"]["url"]
    assert expected_slug in _urls_by_kind()["blog-post"]["url"]

    # forum: the first forum's uuid and its first topic's uuid.
    forumset = synthesize_forums(bf_seed)
    expected_forum_uuid = forumset.forums[0].uuid
    expected_topic_uuid = forumset.forums[0].topics[0].uuid
    assert expected_forum_uuid in _urls_by_kind()["forum-all"]["url"]
    assert expected_topic_uuid in _urls_by_kind()["forum-thread"]["url"]


def test_demo_urls_pure_and_deterministic():
    assert demo_sample_urls() == demo_sample_urls()


def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)

    async def _do():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)

    return asyncio.run(_do())


def test_endpoint_returns_urls_when_demo_true():
    app = make_app(demo=True)
    response = _get(app, "/api/demo-urls")
    assert response.status_code == 200
    body = response.json()
    assert body["urls"]
    assert {item["kind"] for item in body["urls"]} >= {"wiki-all", "unrecognized"}


def test_the_urls_are_offered_however_the_server_was_started():
    """They cost nothing to list and are the only thing to try on a machine
    with no deployment configured. Gating them on a mode is what made the
    mode necessary."""

    async def ask(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return (await c.get("/api/demo-urls")).json()["urls"]

    for started_as in (True, False):
        urls = asyncio.run(ask(make_app(demo=started_as)))

        assert urls, f"no demo URLs offered when started with demo={started_as}"


def test_community_all_round_trips():
    """Without a community chip the whole community flow -- identify, the
    component picker, a scoped ingest -- could not be exercised at all without
    a live deployment."""
    item = _urls_by_kind()["community-all"]
    result = parse_url(item["url"])

    assert result.ok is True
    assert result.app == "community"
    assert result.community_uuid
