"""Tests for retry with backoff on transient failures, and deterministic
timing (spec: "Retry with backoff on transient failures", "Deterministic
timing"). Sleep and clock are always injected fakes here — no real delay,
per project rule."""

import threading

import httpx
import pytest

from connections_export.http.client import HttpClient, RetryPolicy
from connections_export.http.results import FailureKind, Fetched, FetchFailure

# --- 6.1 503, 503, 200 -> success; two backoff waits, exponential ---


def test_retries_503_twice_then_succeeds_with_exponential_backoff(recording_sleep, fake_clock):
    statuses = iter([503, 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=4, base_delay=0.5, backoff_factor=2.0),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    result = client.get("https://example.com/flaky")

    assert isinstance(result, Fetched)
    assert result.status == 200
    assert recording_sleep.calls == [0.5, 1.0]


# --- 6.2 429 with Retry-After: 2 -> waits >= 2s (recorded, not real) ---


def test_retry_after_header_overrides_backoff_delay(recording_sleep, fake_clock):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, content=b""),
            httpx.Response(200, content=b"ok"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(base_delay=0.1),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    result = client.get("https://example.com/limited")

    assert isinstance(result, Fetched)
    assert result.status == 200
    assert len(recording_sleep.calls) == 1
    assert recording_sleep.calls[0] >= 2


def test_stop_event_interrupts_retry_backoff_before_next_request():
    stop_event = threading.Event()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        stop_event.set()
        return httpx.Response(503, content=b"busy")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=4, base_delay=30.0),
        stop_event=stop_event,
    )

    result = client.get("https://example.com/stop")

    assert isinstance(result, FetchFailure)
    assert result.error == "stopped"
    assert len(calls) == 1


# --- 6.3 transport error on every attempt -> FetchFailure(transport) ---


def test_transport_error_every_attempt_returns_fetch_failure(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=4, base_delay=0.01),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    result = client.get("https://example.com/down")

    assert isinstance(result, FetchFailure)
    assert result.kind == FailureKind.transport
    assert result.url == "https://example.com/down"
    assert result.method == "GET"
    assert "connection refused" in result.error
    # 4 attempts -> 3 waits between them, no real time passed
    assert len(recording_sleep.calls) == 3


def test_transport_error_does_not_raise_past_the_client(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    # Must not raise; must not be a success-shaped empty result either.
    result = client.get("https://example.com/down")
    assert isinstance(result, FetchFailure)


# --- 6.4 timeout maps to FailureKind.timeout ---


def test_timeout_every_attempt_returns_fetch_failure_with_timeout_kind(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.01),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    result = client.get("https://example.com/slow")

    assert isinstance(result, FetchFailure)
    assert result.kind == FailureKind.timeout
    assert len(recording_sleep.calls) == 2


# --- exhausting retries on a retry-status still returns Fetched (per
# "A received response is returned regardless of status") ---


def test_exhausting_retries_on_503_still_returns_fetched_with_last_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"still down")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.0),
        sleep=lambda seconds: None,
    )

    result = client.get("https://example.com/persistent")

    assert isinstance(result, Fetched)
    assert result.status == 503
    assert result.content == b"still down"


def test_no_real_sleep_happens_during_retries(recording_sleep, fake_clock, monkeypatch):
    import time

    real_sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep_calls.append(s))

    statuses = iter([503, 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(base_delay=0.5),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    client.get("https://example.com/flaky")

    assert real_sleep_calls == []
    assert recording_sleep.calls  # the fake recorded the waits instead


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_default_retry_statuses_include_common_5xx(status, recording_sleep, fake_clock):
    statuses = iter([status, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(base_delay=0.0),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    result = client.get("https://example.com/x")

    assert isinstance(result, Fetched)
    assert result.status == 200
