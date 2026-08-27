"""Integration test: an authenticated client (paste-token)
drives a 503->200 retry with throttling, and the final `Fetched` would
pass unchanged into `Archive.write_response` (attribute compatibility)."""

import tempfile
from pathlib import Path

import httpx

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from connections_export.http.auth import PasteTokenAuth
from connections_export.http.client import HttpClient, RetryPolicy
from connections_export.http.results import Fetched


def test_authenticated_client_retries_and_throttles_then_writes_into_archive(
    recording_sleep, fake_clock
):
    call_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append((request.url.path, request.headers.get("cookie")))
        if len(call_log) < 3:
            return httpx.Response(503, content=b"try again")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/atom+xml"},
            content=b"<feed>authenticated content</feed>",
        )

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=4, base_delay=0.2),
        min_interval=1.0,
        sleep=recording_sleep,
        clock=fake_clock,
    )
    PasteTokenAuth(ltpa_token="session-token-xyz").prepare(client)

    # A first, unrelated call establishes the throttle baseline.
    client.get("https://example.com/warmup")
    result = client.get("https://example.com/wikis/basic/api/wikis/feed")

    assert isinstance(result, Fetched)
    assert result.status == 200
    assert result.content == b"<feed>authenticated content</feed>"

    # Every call carried the paste-token cookie, including retried attempts.
    assert all(cookie == "LtpaToken2=session-token-xyz" for _path, cookie in call_log)

    # Throttling delayed the second top-level get relative to the first.
    assert 1.0 in recording_sleep.calls
    # Backoff delayed the retried attempts within the second get.
    assert 0.2 in recording_sleep.calls

    with tempfile.TemporaryDirectory() as tmp:
        archive = Archive.open(Path(tmp))
        record = archive.write_response(result, fetched_at="2026-07-20T18:00:00Z")

    assert record.outcome == Outcome.ok
    assert record.status == 200
    assert record.content_type == "application/atom+xml"
