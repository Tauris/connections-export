"""Tests for TLS-only enforcement (spec: "TLS-only requests").

`get` must raise for any non-https:// URL before any transport call —
a pure input check, no MockTransport needed for the rejection case.
"""

import httpx
import pytest

from connections_export.http.client import HttpClient

# --- 3.1 http:// is rejected before any network activity ---


def test_get_rejects_plaintext_url_without_invoking_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    client = HttpClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError):
        client.get("http://example.com/insecure")

    assert calls == []


# --- 3.2 https:// proceeds to the transport ---


def test_get_allows_https_url_and_reaches_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"ok")

    client = HttpClient(transport=httpx.MockTransport(handler))

    result = client.get("https://example.com/secure")

    assert len(calls) == 1
    assert result.status == 200
