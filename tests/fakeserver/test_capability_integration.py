"""Drive `make_app` through the real `HttpClient` end to end.

`httpx.ASGITransport` is async-only (`handle_async_request`), while
`HttpClient` is built on the synchronous `httpx.Client`. There is no
literal way to hand `httpx.ASGITransport` straight to a sync client --
`httpx.Client(transport=httpx.ASGITransport(app=app))` raises
`AttributeError: 'ASGITransport' object has no attribute
'handle_request'` (verified directly against installed httpx 0.28).

`_SyncASGIBridge` below is the minimal fix: an `httpx.BaseTransport`
whose sync `handle_request` runs `httpx.ASGITransport.handle_async_request`
to completion via `asyncio.run`, then rebuilds a fully-buffered,
sync-compatible `httpx.Response`. It is still entirely in-process --
no socket, no certificate -- exactly what the TLS section
promises; only the sync/async seam needed bridging, since neither
`connections_export.http` nor this fakeserver capability may be modified to
change that seam (out of scope for this change).
"""

import asyncio
import threading

import httpx

from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.http.client import HttpClient, RetryPolicy
from connections_export.http.results import FailureKind, Fetched, FetchFailure


class _SyncASGIBridge(httpx.BaseTransport):
    """Bridges a synchronous `httpx.Client` to an in-process ASGI app.

    `httpx.ASGITransport` only implements `handle_async_request`; a
    sync `httpx.Client` requires `handle_request`. This wraps the async
    transport and runs it to completion on one reused event loop per
    thread -- not a fresh `asyncio.run` per request, whose Windows
    self-pipe (`socket.socketpair()`) can deadlock at `accept()` on
    Python 3.14 across a crawl's many requests.
    """

    def __init__(self, app):
        self._async_transport = httpx.ASGITransport(app=app)
        self._local = threading.local()

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self._local, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._local.loop = loop
        return loop

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _do():
            response = await self._async_transport.handle_async_request(request)
            content = await response.aread()
            return content, response.status_code, response.headers

        content, status, headers = self._loop().run_until_complete(_do())
        return httpx.Response(status_code=status, headers=headers, content=content, request=request)


def _client(app, **kwargs) -> HttpClient:
    return HttpClient(
        transport=_SyncASGIBridge(app),
        retry_policy=RetryPolicy(max_attempts=1),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_page_entry_fetch_succeeds_end_to_end():
    wikiset = synthesize(SynthSeed(seed=61, wiki_count=1, depth=1, pages_per_level=1))
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, auth_root="basic")
    client = _client(app)

    url = f"https://fake/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/entry"
    result = client.get(url)

    assert isinstance(result, Fetched)
    assert result.status == 200
    assert b"<td:label>" in result.content
    assert page.label.encode() in result.content


def test_faulted_url_yields_fetch_failure_for_timeout():
    wikiset = synthesize(SynthSeed(seed=62, wiki_count=1, depth=1, pages_per_level=1))
    faulted_path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset, faults=Faults(timeout_paths={faulted_path}))
    client = _client(app)

    result = client.get(f"https://fake{faulted_path}")

    assert isinstance(result, FetchFailure)
    assert result.kind == FailureKind.timeout


def test_faulted_url_yields_fetched_with_forced_status():
    wikiset = synthesize(SynthSeed(seed=63, wiki_count=1, depth=1, pages_per_level=1))
    faulted_path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset, faults=Faults(status_for={faulted_path: 500}))
    client = _client(app)

    result = client.get(f"https://fake{faulted_path}")

    # A received response, however bad its status, is a `Fetched` --
    # never a `FetchFailure` (see connections_export.http.results docstring).
    assert isinstance(result, Fetched)
    assert result.status == 500


def test_unfaulted_wiki_feed_is_unaffected_by_a_faulted_sibling_path():
    wikiset = synthesize(SynthSeed(seed=64, wiki_count=1, depth=1, pages_per_level=1))
    wiki = wikiset.wikis[0]
    faulted_path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset, faults=Faults(status_for={faulted_path: 500}))
    client = _client(app)

    result = client.get(f"https://fake/wikis/basic/api/wiki/{wiki.label}/feed")

    assert isinstance(result, Fetched)
    assert result.status == 200
