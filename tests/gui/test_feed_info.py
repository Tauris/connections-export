"""Finding #26: `GET /api/feed-info` returns the feed's own `title`
(the blog/forum/wiki's display name, e.g. "Team Updates") alongside the
existing `total`/`feed_url`, so the identify result can show the user a
real name instead of the bare handle/uuid `parse_url` extracts from the
URL. `feed_info` fetches with a bare `httpx.get(...)` (not through the
app's ASGI transport, since it targets an arbitrary external base_url),
so the network call is monkeypatched to serve real feed bytes built by
the fakeserver's own `blog_entries_feed` -- a "served feed fixture" in
the sense the task calls for, without spinning up a real socket.

task-1826demo: in demo mode, a demo chip's URL points at the
placeholder host `DEMO_SAMPLE_BASE_URL`, which the demo's in-process
fakeserver never actually serves over HTTP -- so the fetch above always
fails for one. `feed_info` special-cases exactly that host (when
`app.state.demo`) and answers from the same synth data
`demo_sample_urls` builds its chips from, instead of fetching. The
tests above (a real-looking `https://example.corp` URL under
`demo=True`) still exercise the ordinary HTTP path unchanged --
`demo=True` alone isn't enough to trigger the synth path, only that
placeholder host is.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

from connections_export.fakeserver import synthesize_blogs, synthesize_forums
from connections_export.fakeserver.atom import blog_entries_feed
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.gui.app import make_app
from connections_export.gui.demo import demo_sample_urls, demo_synth_seed

BASE = "https://example.corp"


def _run(coro):
    return asyncio.run(coro)


def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)

    async def _do():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)

    return _run(_do())


def test_feed_info_returns_the_feed_title_for_a_blog(monkeypatch):
    blogset = synthesize_blogs(demo_synth_seed())
    blog = blogset.blogs[0]
    feed_bytes = blog_entries_feed(blog, base_url=BASE)
    assert blog.title  # the fixture actually carries a name to assert on

    def _fake_get(url, **kwargs):
        return httpx.Response(200, content=feed_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _fake_get)

    app = make_app(demo=True)
    url = f"{BASE}/blogs/{blog.handle}/feed/entries/atom"
    response = _get(app, "/api/feed-info?url=" + url)

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == blog.title
    assert body["total"] == len(blog.posts)
    assert body["feed_url"]


def test_feed_info_title_is_none_on_fetch_failure(monkeypatch):
    def _fake_get(url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _fake_get)

    app = make_app(demo=True)
    response = _get(app, "/api/feed-info?url=" + f"{BASE}/blogs/team-blog/feed/entries/atom")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] is None
    # Degrades gracefully -- no "title" key promised on the error path,
    # matching the existing `{"total": null}` shape.
    assert body.get("title") is None


def _chip(kind: str) -> str:
    return next(chip["url"] for chip in demo_sample_urls() if chip["kind"] == kind)


def _feed_info_path(raw_url: str) -> str:
    # `quote` (not the bare concatenation the tests above use for
    # simple URLs) because a demo chip's URL can itself carry a `?`/`#`
    # (the wiki hash-route chips do) that would otherwise be misread as
    # this *request's* own query/fragment delimiter.
    return "/api/feed-info?url=" + quote(raw_url, safe="")


def _fail_any_fetch(url, **kwargs):
    """A demo-chip URL must never actually be fetched -- the demo
    fakeserver isn't reachable at its placeholder host, and #18/#26's
    whole point is answering from synth data instead."""
    raise AssertionError(f"feed_info fetched a demo chip URL over HTTP: {url}")


def test_feed_info_demo_blog_chip_returns_real_title_and_count(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fail_any_fetch)
    blogset = synthesize_blogs(demo_synth_seed())
    blog = blogset.blogs[0]

    app = make_app(demo=True)
    response = _get(app, _feed_info_path(_chip("blog-all")))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == blog.title
    assert body["total"] == len(blog.posts)


def test_feed_info_demo_blog_post_chip_returns_the_owning_blog(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fail_any_fetch)
    blogset = synthesize_blogs(demo_synth_seed())
    blog = blogset.blogs[0]

    app = make_app(demo=True)
    response = _get(app, _feed_info_path(_chip("blog-post")))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == blog.title
    assert body["total"] == len(blog.posts)


def test_feed_info_demo_forum_chip_returns_real_title_and_count(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fail_any_fetch)
    forumset = synthesize_forums(demo_synth_seed())
    forum = forumset.forums[0]

    app = make_app(demo=True)
    response = _get(app, _feed_info_path(_chip("forum-all")))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == forum.title
    assert body["total"] == len(forum.topics)


def test_feed_info_demo_wiki_chip_returns_real_title_and_count(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fail_any_fetch)
    wikiset = build_prototype_wikiset()
    wiki = wikiset.wikis[0]

    app = make_app(demo=True)
    response = _get(app, _feed_info_path(_chip("wiki-all")))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == wiki.title
    assert body["total"] == len(wiki.pages)


def test_feed_info_demo_wiki_page_chip_returns_the_owning_wiki(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fail_any_fetch)
    wikiset = build_prototype_wikiset()
    wiki = wikiset.wikis[0]

    app = make_app(demo=True)
    response = _get(app, _feed_info_path(_chip("wiki-page")))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == wiki.title
    assert body["total"] == len(wiki.pages)
