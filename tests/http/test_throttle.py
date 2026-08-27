"""Tests for self-throttling: a minimum interval between request starts
(spec: "Self-throttling"), via injected clock/sleep only."""

import httpx

from connections_export.http.client import HttpClient, RetryPolicy

# --- 7.1 with min_interval set, request 2 starts >= interval after request 1 ---


def test_second_request_waits_at_least_min_interval_after_first(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        min_interval=3.0,
        sleep=recording_sleep,
        clock=fake_clock,
    )

    client.get("https://example.com/one")
    client.get("https://example.com/two")

    # Both requests happened with zero real elapsed time (fake_clock never
    # ticks on its own), so the throttle must have slept ~min_interval.
    assert recording_sleep.calls == [3.0]


def test_no_throttle_wait_when_min_interval_is_zero(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        sleep=recording_sleep,
        clock=fake_clock,
    )

    client.get("https://example.com/one")
    client.get("https://example.com/two")

    assert recording_sleep.calls == []


def test_throttle_wait_is_skipped_if_enough_time_already_elapsed(recording_sleep, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        min_interval=1.0,
        sleep=recording_sleep,
        clock=fake_clock,
    )

    client.get("https://example.com/one")
    fake_clock.advance(5.0)  # plenty of real time passed between requests
    client.get("https://example.com/two")

    assert recording_sleep.calls == []


def test_throttle_does_not_call_real_time_sleep(recording_sleep, fake_clock, monkeypatch):
    import time

    real_sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep_calls.append(s))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    client = HttpClient(
        transport=httpx.MockTransport(handler),
        min_interval=2.0,
        sleep=recording_sleep,
        clock=fake_clock,
        retry_policy=RetryPolicy(base_delay=0.0),
    )

    client.get("https://example.com/one")
    client.get("https://example.com/two")

    assert real_sleep_calls == []
    assert recording_sleep.calls == [2.0]
