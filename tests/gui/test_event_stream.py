"""One run's events, read by however many people are looking.

`EventStream` exists because the terminator kept being delivered by a
different road from the events. These tests are fast and direct -- no demo
run, no SSE -- so the invariant is pinned at the object rather than only
observed through a capture that takes half a minute to produce it.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from connections_export.gui.event_stream import EventStream

_DONE = object()


def test_a_subscriber_that_arrives_late_gets_everything_that_happened():
    stream = EventStream()
    stream.reset()
    stream.publish({"type": "a"})
    stream.publish({"type": "b"})

    history = stream.subscribe().history

    assert history == [{"type": "a"}, {"type": "b"}]


def test_the_ending_is_in_the_history_like_any_other_event():
    """The whole point. A reader that arrives after the run finished replays
    its way to the ending and closes; delivered live-only, that reader waits
    forever on a run that is already over."""
    stream = EventStream()
    stream.reset()
    stream.publish({"type": "a"})
    stream.publish(_DONE)

    history = stream.subscribe().history

    assert history[-1] is _DONE


def test_two_subscribers_both_get_every_event_rather_than_one_each():
    stream = EventStream()
    stream.reset()
    first = stream.subscribe().queue
    second = stream.subscribe().queue

    stream.publish({"type": "a"})
    stream.publish({"type": "b"})

    assert [first.get_nowait(), first.get_nowait()] == [{"type": "a"}, {"type": "b"}]
    assert [second.get_nowait(), second.get_nowait()] == [{"type": "a"}, {"type": "b"}]


def test_history_and_live_delivery_do_not_overlap_or_gap():
    """Subscribing takes the same lock publishing does, so an event landing
    at that moment is in exactly one of the two -- never both (the console
    would show it twice) and never neither (it would vanish)."""
    stream = EventStream()
    stream.reset()
    stop = threading.Event()

    def _publish() -> None:
        index = 0
        while not stop.is_set():
            stream.publish({"n": index})
            index += 1
            # Paced, so the writer does not simply hold the lock: an
            # unthrottled loop starves `subscribe` and measures scheduling
            # rather than the invariant.
            time.sleep(0.0005)

    writer = threading.Thread(target=_publish, daemon=True)
    writer.start()
    try:
        for _ in range(50):
            subscription = stream.subscribe()
            subscriber = subscription.queue
            seen = list(subscription.history)
            for _ in range(3):
                try:
                    seen.append(subscriber.get(timeout=1))
                except queue.Empty:  # pragma: no cover - the writer is running
                    break
            stream.unsubscribe(subscriber)
            numbers = [item["n"] for item in seen]
            # Contiguous and in order: nothing seen twice, nothing skipped
            # across the handover from replayed history to live delivery.
            assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    finally:
        stop.set()
        writer.join(timeout=5)


def test_a_new_run_supersedes_the_last():
    stream = EventStream()
    stream.reset()
    stream.publish({"type": "old"})
    stream.reset()
    stream.publish({"type": "new"})

    history = stream.subscribe().history

    assert history == [{"type": "new"}]


def test_nothing_has_started_until_a_run_starts():
    """`/events` waits on this. Serving an empty, immediately-closed stream
    to a setup screen would read as a run that finished instantly."""
    stream = EventStream()
    assert stream.started is False
    stream.reset()
    assert stream.started is True


# --- resuming a dropped connection ------------------------------------
# A browser re-establishes a dropped EventSource on its own and sends back
# the last `id:` it saw. Honouring that is what separates "carry on" from
# "here is the run again" -- and the console counts what it is sent, so the
# second means every counter, log row and tree node arriving twice.


def test_a_reconnecting_reader_gets_only_what_it_missed():
    stream = EventStream()
    stream.reset()
    for n in range(5):
        stream.publish({"n": n})
    seen = stream.subscribe()

    resumed = stream.subscribe(seen.event_id(2))

    assert resumed.resumed is True
    assert resumed.history == [{"n": 3}, {"n": 4}]
    assert resumed.start == 3


def test_a_reader_with_no_last_id_gets_the_whole_run():
    """A second window, or a reloaded page: nothing to resume from, so it
    gets everything. This is the case the replay buffer was added for, and
    resuming must not cost it."""
    stream = EventStream()
    stream.reset()
    stream.publish({"n": 0})
    stream.publish({"n": 1})

    fresh = stream.subscribe()

    assert fresh.resumed is False
    assert fresh.history == [{"n": 0}, {"n": 1}]


def test_an_id_from_a_previous_run_is_not_a_position_in_this_one():
    """The failure this token exists to prevent: a browser reconnecting
    across a restart would otherwise be told it had already seen the first
    several hundred events of a run that had not started when it last
    looked, and the console would show a capture missing its beginning."""
    stream = EventStream()
    stream.reset()
    for n in range(400):
        stream.publish({"n": n})
    stale = stream.subscribe().event_id(399)

    stream.reset()
    stream.publish({"n": "new"})
    after_restart = stream.subscribe(stale)

    assert after_restart.resumed is False
    assert after_restart.history == [{"n": "new"}]


def test_a_malformed_last_id_starts_from_the_beginning():
    stream = EventStream()
    stream.reset()
    stream.publish({"n": 0})

    for bad in ("", "nonsense", "abc:xyz", ":", "12"):
        assert stream.subscribe(bad).history == [{"n": 0}]


def test_an_id_past_the_end_replays_rather_than_waits_forever():
    stream = EventStream()
    stream.reset()
    stream.publish({"n": 0})
    subscription = stream.subscribe()

    beyond = stream.subscribe(subscription.event_id(99))

    assert beyond.start == 1
    assert beyond.history == []


def test_a_resumed_reader_still_receives_what_happens_next():
    stream = EventStream()
    stream.reset()
    stream.publish({"n": 0})
    stream.publish({"n": 1})
    resumed = stream.subscribe(stream.subscribe().event_id(0))

    stream.publish({"n": 2})

    assert resumed.history == [{"n": 1}]
    assert resumed.queue.get_nowait() == {"n": 2}


def test_publishing_to_a_gone_subscriber_does_not_reach_it():
    stream = EventStream()
    stream.reset()
    subscriber = stream.subscribe().queue
    stream.unsubscribe(subscriber)

    stream.publish({"type": "a"})

    with pytest.raises(queue.Empty):
        subscriber.get_nowait()
