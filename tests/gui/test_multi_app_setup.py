""": the setup screen identifies wiki | blog | forum
URLs (not just wikis) and starts the matching import. `parse_url` is the
pure identifier; `/api/identify` exposes it; `/api/start` routes to the
right crawl. UI URL forms for blogs/forums are not documented (first
contact), so this covers the API/known shapes.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui.app import make_app
from connections_export.gui.wiki_url import parse_url

BASE = "https://example.corp"


def _run(coro):
    return asyncio.run(coro)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


# --- parse_url: which app + target ----------------------------------------


def test_wiki_url_is_still_identified_as_wiki():
    t = parse_url(f"{BASE}/wikis/basic/api/wiki/eng-handbook/nav/feed")
    assert t.ok and t.app == "wiki"
    assert t.base_url == BASE and t.wiki_label == "eng-handbook" and t.auth_root == "basic"


def test_blog_api_url_is_identified():
    t = parse_url(f"{BASE}/blogs/team-blog/feed/entries/atom")
    assert t.ok and t.app == "blog"
    assert t.base_url == BASE and t.blog_handle == "team-blog"
    assert t.wiki_label is None and t.forum_uuid is None


def test_blog_ui_url_is_identified_by_handle():
    t = parse_url(f"{BASE}/blogs/team-blog/entry/some-post")
    assert t.ok and t.app == "blog" and t.blog_handle == "team-blog"


def test_forum_url_with_a_forum_uuid_is_identified():
    t = parse_url(f"{BASE}/forums/atom/topics?forumUuid=F-123")
    assert t.ok and t.app == "forum"
    assert t.base_url == BASE and t.forum_uuid == "F-123"


def test_forum_url_without_a_uuid_targets_the_whole_app():
    t = parse_url(f"{BASE}/forums/html/forums")
    assert t.ok and t.app == "forum" and t.forum_uuid is None


def test_context_root_is_kept_in_base_url():
    t = parse_url(f"{BASE}/connections/blogs/team-blog/feed/entries/atom")
    assert t.ok and t.app == "blog"
    assert t.base_url == f"{BASE}/connections" and t.blog_handle == "team-blog"


def test_unrecognised_url_is_reported_not_guessed():
    t = parse_url("https://example.com/something/else")
    assert t.ok is False and t.app is None and t.base_url is None


# --- /api/identify + /api/start -------------------------------------------


def test_identify_returns_the_app_for_a_forum_url():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as c:
            return await c.post(
                "/api/identify", json={"url": f"{BASE}/forums/atom/topics?forumUuid=F9"}
            )

    body = _run(_do()).json()
    assert body["ok"] is True and body["app"] == "forum" and body["forum_uuid"] == "F9"


def test_real_blog_start_reaches_the_crawl_and_surfaces_the_auth_gap():
    """A real (non-demo) blog start routes to the blog crawl; with the
    stub auth it surfaces the gap explicitly (never hangs) — same contract
    as the wiki real-start, proving the app is accepted and dispatched."""
    app = make_app(demo=False)

    async def _do():
        collected = []
        async with _client(app) as c:
            start = await c.post(
                "/api/start",
                json={
                    "base_url": BASE,
                    "auth_mode": "sspi",
                    "app": "blog",
                    "blog_handle": "team-blog",
                },
            )
            assert start.status_code == 200
            async with c.stream("GET", "/events") as resp:
                import json as _json

                buf = ""
                async for chunk in resp.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        if raw.startswith("data: "):
                            collected.append(_json.loads(raw[6:]))
        return collected

    events = _run(_do())
    assert events[-1]["type"] == "run_complete"
    assert any(e["type"] == "failed" for e in events)
