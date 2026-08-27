"""Tests for basic fetch and "received response returned regardless of
status" semantics (spec requirements: TLS-only proceeds on https; A
received response is returned regardless of status)."""

import httpx

from connections_export.http.client import HttpClient
from connections_export.http.results import Fetched

# --- 4.1 a 200 returns a Fetched with exact status/headers/body bytes ---


def test_get_200_returns_fetched_with_exact_status_headers_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/atom+xml"},
            content=b"<feed>hello</feed>",
        )

    client = HttpClient(transport=httpx.MockTransport(handler))

    result = client.get("https://example.com/feed")

    assert isinstance(result, Fetched)
    assert result.status == 200
    assert result.headers["content-type"] == "application/atom+xml"
    assert result.content == b"<feed>hello</feed>"
    assert result.method == "GET"
    assert result.url == "https://example.com/feed"


# --- 4.2 a 403-with-body returns a Fetched (not a failure) ---


def test_get_403_with_body_returns_fetched_not_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<error>forbidden</error>")

    client = HttpClient(transport=httpx.MockTransport(handler))

    result = client.get("https://example.com/secret")

    assert isinstance(result, Fetched)
    assert result.status == 403
    assert result.content == b"<error>forbidden</error>"
