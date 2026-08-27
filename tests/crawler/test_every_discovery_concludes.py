"""Everything discovered reaches a conclusion, including when a run is stopped.

From a live ingest: 2,958 discovered against 2,953 successful and 4 failed --
one item that never resolved into anything. Reproduced: a run interrupted by
Stop leaves exactly one request that emitted `Discovered` and `Fetching` and
then returned silently, because the stopped branch was the only exit from the
fetch path with no terminal event.

That matters beyond arithmetic. A counter that cannot balance cannot be used to
notice when something really is lost -- which is the job those numbers exist to
do.
"""

from __future__ import annotations

import collections
import threading

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

TERMINAL = ("Fetched", "Failed", "Stopped")


def _run(tmp_path, *, stop_after=None):
    stop = threading.Event()
    events: list = []
    seen = 0

    def note(event):
        nonlocal seen
        events.append(event)
        if stop_after is not None and type(event).__name__ == "Discovered":
            seen += 1
            if seen == stop_after:
                stop.set()

    root = tmp_path / "archive"
    transport = httpx.MockTransport(
        SyncASGIBridge(make_app(synthesize(SynthSeed(seed=0)))).handle_request
    )
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None, stop_event=stop),
        archive=Archive.open(root),
        emit=note,
        stop_event=stop,
    )
    return events


def _unconcluded(events):
    concluded = {e.url for e in events if type(e).__name__ in TERMINAL}
    return [e.url for e in events if type(e).__name__ == "Discovered" and e.url not in concluded]


def test_a_complete_run_concludes_everything_it_discovered(tmp_path):
    events = _run(tmp_path)
    counts = collections.Counter(type(e).__name__ for e in events)
    assert counts["Discovered"] > 20
    assert _unconcluded(events) == []


def test_a_stopped_run_concludes_everything_too(tmp_path):
    events = _run(tmp_path, stop_after=12)
    assert _unconcluded(events) == [], "a stopped request vanished from the accounting"


def test_a_stopped_request_is_not_reported_as_a_failure(tmp_path):
    """ "Failed" is the data-loss signal. An interruption the user asked for is
    not one, and counting it there would cry wolf."""
    events = _run(tmp_path, stop_after=12)
    assert not [e for e in events if type(e).__name__ == "Failed"]
    assert [e for e in events if type(e).__name__ == "Stopped"]


def test_the_stopped_event_says_what_it_was(tmp_path):
    events = _run(tmp_path, stop_after=12)
    stopped = next(e for e in events if type(e).__name__ == "Stopped")
    assert stopped.url
    assert stopped.kind


def test_it_serialises_for_the_console(tmp_path):
    from connections_export.crawler import events as crawler_events
    from connections_export.gui.events import to_json

    payload = to_json(crawler_events.Stopped(url="https://fake/x", kind="page"))
    assert payload == {"type": "stopped", "url": "https://fake/x", "kind": "page"}


@pytest.mark.parametrize("stop_after", [3, 12, 25])
def test_the_invariant_holds_wherever_the_stop_lands(tmp_path, stop_after):
    events = _run(tmp_path, stop_after=stop_after)
    counts = collections.Counter(type(e).__name__ for e in events)
    terminal = sum(counts[name] for name in TERMINAL)
    assert counts["Discovered"] == terminal, dict(counts)
