"""Shared fixtures for crawler tests.

`SyncASGIBridge` is the same sync<->async bridge introduced by the
fakeserver change (see `tests/fakeserver/test_capability_integration.py`'s
`_SyncASGIBridge` docstring for why it's needed: `httpx.ASGITransport`
is async-only, `HttpClient` is built on the synchronous `httpx.Client`).
Reused here, not imported from there, since that class is that test
module's own private helper -- duplicating ~15 lines of test-only glue
is cheaper than making it a cross-package dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from connections_export.http.client import HttpClient, RetryPolicy


class SyncASGIBridge(httpx.BaseTransport):
    def __init__(self, app):
        self._async_transport = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _do():
            response = await self._async_transport.handle_async_request(request)
            content = await response.aread()
            return content, response.status_code, response.headers

        content, status, headers = asyncio.run(_do())
        return httpx.Response(status_code=status, headers=headers, content=content, request=request)


def make_client(
    app, *, max_attempts: int = 1, sleep: Callable[[float], None] | None = None, **kwargs
) -> HttpClient:
    """An `HttpClient` wired to an in-process ASGI `app` -- no socket,
    no real delay (unless a test wants to observe retries, in which
    case it injects its own `sleep`)."""
    return HttpClient(
        transport=SyncASGIBridge(app),
        retry_policy=RetryPolicy(max_attempts=max_attempts),
        sleep=sleep if sleep is not None else (lambda _seconds: None),
        **kwargs,
    )


class OverrideTransport(httpx.BaseTransport):
    """A transport that intercepts specific requests (by predicate) and
    hands everything else to `inner`. Lets a test keep the real fake
    app's behavior for almost everything, while substituting a
    hand-crafted response for the one resource under test -- e.g. an
    attachment enclosure the fake only ever gives a symbolic `urn:`
    href for, or a version body the fake never links out to.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport,
        overrides: list[
            tuple[Callable[[httpx.Request], bool], Callable[[httpx.Request], httpx.Response]]
        ],
    ):
        self._inner = inner
        self._overrides = overrides

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for predicate, handler in self._overrides:
            if predicate(request):
                return handler(request)
        return self._inner.handle_request(request)


def static_response(
    status: int, content: bytes, *, content_type: str = "application/octet-stream"
) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=content, headers={"content-type": content_type}, request=request
        )

    return _handler


def path_is(path: str, *, query_contains: str | None = None) -> Callable[[httpx.Request], bool]:
    def _predicate(request: httpx.Request) -> bool:
        if request.url.path != path:
            return False
        if query_contains is None:
            return True
        return query_contains in str(request.url)

    return _predicate


def fail_n_times_then(
    n: int, handler: Callable[[httpx.Request], httpx.Response]
) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap `handler` so the first `n` calls return `503 Service
    Unavailable` instead -- a stateful fault `fakeserver.faults.Faults`
    cannot express (it only forces a *fixed* status), used to prove
    retries are observed for a URL that eventually succeeds."""
    calls = {"count": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= n:
            return httpx.Response(503, content=b"service unavailable", request=request)
        return handler(request)

    return _handler
