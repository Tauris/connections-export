"""`LocalGuardMiddleware` is wired into
`make_app`, so `hcl-serve` defends itself against the two browser-borne
attacks a plain 127.0.0.1 bind does not (see
`connections_export/gui/security.py`):

- **DNS rebinding** -- a request whose `Host` is not a permitted
  localhost name is refused (403), even though it reached our socket.
- **Cross-origin state change (CSRF)** -- a state-changing request
  (POST/...) carrying a *non-local* `Origin` is refused; a local
  `Origin`, or none at all (a non-browser client like curl), is allowed.

Driven in-process via `httpx.ASGITransport`, per this project's
offline-testing convention. `httpx` derives the `Host` header from the
client `base_url`, so `base_url="http://127.0.0.1"` is a permitted
Host; a per-request `headers={"host":...}` overrides it to simulate a
rebound request. The `/events` streaming assertion proves the guard
(pure-ASGI) does not buffer the SSE stream.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from connections_export.gui.app import make_app


def _run(coro):
    return asyncio.run(coro)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


# --- DNS-rebinding defense: the Host header must be a localhost name --------


def test_a_rebound_host_is_rejected():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            # A page that rebound its own hostname to 127.0.0.1 still
            # sends *its* Host, not ours -- refuse it.
            return await client.get("/api/health", headers={"host": "attacker.example"})

    response = _run(_do())
    assert response.status_code == 403


def test_a_permitted_localhost_host_is_allowed():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/health", headers={"host": "localhost:8000"})

    response = _run(_do())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_configured_bound_host_is_added_to_the_allowlist():
    """`make_app(bound_host=...)` (serve_main passes the host it binds)
    widens the allowlist to that host, so serving on a non-default host
    still works while every *other* Host stays refused."""
    app = make_app(demo=True, bound_host="myhost.local")

    async def _do():
        async with _client(app) as client:
            allowed = await client.get("/api/health", headers={"host": "myhost.local"})
            other = await client.get("/api/health", headers={"host": "elsewhere.example"})
            return allowed, other

    allowed, other = _run(_do())
    assert allowed.status_code == 200
    assert other.status_code == 403


# --- CSRF defense: a non-local Origin on a state-changing method is refused --


def test_a_cross_origin_post_is_rejected():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.post(
                "/api/identify",
                json={"url": "https://example.corp/wikis/basic/api/wiki/x/nav/feed"},
                headers={"origin": "https://evil.example"},
            )

    response = _run(_do())
    assert response.status_code == 403


def test_a_same_origin_post_is_allowed():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.post(
                "/api/identify",
                json={"url": "https://example.corp/wikis/basic/api/wiki/x/nav/feed"},
                headers={"origin": "http://127.0.0.1"},
            )

    response = _run(_do())
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_a_post_with_no_origin_is_allowed():
    """A non-browser client (curl, our own tests) sends no `Origin`;
    only a browser attaches one cross-origin, so no-Origin is allowed."""
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.post("/api/identify", json={"url": "not-a-wiki-url"})

    response = _run(_do())
    assert response.status_code == 200
    assert response.json()["ok"] is False


# --- the guard preserves the SSE stream (pure-ASGI, never buffered) ---------


def test_events_still_streams_under_the_guard():
    """A safe GET is Host-checked only, and the pure-ASGI guard passes
    the stream straight through -- so `/events` still delivers events
    after a start (safe GET requests are unaffected, including
    the events stream)."""
    app = make_app(demo=True, demo_seed=7, demo_delay=0)

    async def _do():
        async with _client(app) as client:
            start = await client.post("/api/start", json={"demo": True})
            assert start.status_code == 200
            collected: list[dict] = []
            async with client.stream("GET", "/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        raw, buffer = buffer.split("\n\n", 1)
                        if raw.startswith("data: "):
                            collected.append(json.loads(raw[len("data: ") :]))
            return collected

    events = _run(_do())
    assert events
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_complete"
