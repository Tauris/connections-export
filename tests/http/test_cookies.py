"""Tests for session cookie continuity (spec: "Session cookie continuity")."""

import httpx

from connections_export.http.client import HttpClient

# --- 5.1 a cookie set on response 1 is present on request 2 ---


def test_cookie_set_on_first_response_is_sent_on_second_request():
    seen_cookies_on_second_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/one":
            return httpx.Response(200, headers={"Set-Cookie": "session=abc123; Path=/"})
        seen_cookies_on_second_request["cookie"] = request.headers.get("cookie")
        return httpx.Response(200)

    client = HttpClient(transport=httpx.MockTransport(handler))

    client.get("https://example.com/one")
    client.get("https://example.com/two")

    assert seen_cookies_on_second_request["cookie"] == "session=abc123"
