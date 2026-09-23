"""`SyncASGIBridge`: bridges the synchronous `HttpClient` to an
in-process ASGI app (the fakeserver), so `demo.py` can run the real
crawler against it with no real socket.

This is a small, deliberate duplicate of the same-named helper in
`tests/crawler/conftest.py` (itself documented as a duplicate of
`tests/fakeserver/test_capability_integration.py`'s private
`_SyncASGIBridge`). It is production code here -- `hcl-serve --demo`
runs it on every request -- so it lives in `connections_export`, not imported
from `tests`, which may not even be installed alongside the package.

`httpx.ASGITransport` only implements `handle_async_request`; a sync
`httpx.Client` (what `HttpClient` is built on) requires
`handle_request`. This wraps the async transport and runs it to
completion per request, then rebuilds a fully buffered,
sync-compatible `httpx.Response` -- still entirely in-process, no
socket, no certificate.

It runs each request on ONE reused event loop per thread, not a fresh
`asyncio.run` per request. Creating a loop is not free, and on Windows
it builds a self-pipe with `socket.socketpair()`, whose no-AF_UNIX
fallback (listen/connect/accept on loopback) can deadlock at `accept()`
on Python 3.14 -- rarely, but a demo crawl makes thousands of requests,
so a per-request loop turned "rarely" into "eventually". One loop per
thread creates that self-pipe once, is thread-safe (each thread has its
own), and is the shape a sync-over-async bridge should have anyway.
"""

from __future__ import annotations

import asyncio
import threading

import httpx


class SyncASGIBridge(httpx.BaseTransport):
    def __init__(self, app) -> None:
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
