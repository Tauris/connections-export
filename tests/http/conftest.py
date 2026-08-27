"""Shared fixtures for http capability tests: recording sleep + fake
clock, so backoff and throttle are asserted deterministically with zero
real delay (spec: "Deterministic timing")."""

import pytest


class RecordingSleep:
    """Records every requested delay instead of actually sleeping."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeClock:
    """A manually-advanced clock. Advances automatically by whatever
    duration `RecordingSleep` records, if wired via `advance_with`."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def recording_sleep():
    return RecordingSleep()


@pytest.fixture
def fake_clock():
    return FakeClock()
