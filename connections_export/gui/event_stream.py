"""The ingest event stream, with every reader getting the whole run.

A capture emits events for as long as it runs, and the console reads them
over SSE. One shared queue made that a race between readers rather than a
broadcast: two readers stole events from each other, so a reconnecting
console, a second tab, or a browser re-establishing a dropped EventSource
each got a fraction of the run -- and nothing said so, because a missing
event looks exactly like an event that has not happened yet.

So the stream keeps its history and fans out to every subscriber. The part
worth stating plainly is the terminator: it travels the same road as the
events. `publish` appends to the history AND hands to the subscribers under
one lock, and the end of the run is published like anything else. A reader
that joins after the run finished therefore replays the events and then the
ending, and its connection closes.

Delivering the ending only to whoever was listening at the time is the
failure this class exists to make impossible: the run reads as complete,
because the events say so, while the connection behind it stays open
forever. Three places publish (each event, the end of a run, and server
shutdown from `app.py`), which is why the fan-out lives here rather than at
each of them -- one of the three had already been missed.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Subscription:
    """One reader's place in the stream.

    `resumed` says whether the reader picked up where it left off or is
    starting over -- the difference between a browser silently
    re-establishing a dropped connection and a genuinely new reader, which
    look identical from the socket.
    """

    token: str
    #: Absolute index of the first item in `history`; ids continue from here.
    start: int
    history: list[Any] = field(default_factory=list)
    queue: queue.Queue = field(default_factory=queue.Queue)
    resumed: bool = False

    def event_id(self, index: int) -> str:
        """The `id:` an item at absolute `index` is sent under."""
        return f"{self.token}:{index}"


class EventStream:
    """One run's events, replayable, broadcast to every subscriber."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: list[Any] = []
        self._subscribers: set[queue.Queue] = set()
        self._started = False
        #: Identifies THIS run. A reconnecting browser sends back the last id
        #: it saw, and without a per-run token an id left over from the
        #: previous run would be read as a position in this one -- skipping
        #: its first several hundred events and calling that a resume.
        self._token = ""

    @property
    def started(self) -> bool:
        """Whether a run has begun.

        `/events` waits on this rather than serving an empty stream: a setup
        screen that has not started an import has nothing to say, and saying
        it as an immediately-closed stream reads as a finished run.
        """
        with self._lock:
            return self._started

    def reset(self) -> None:
        """Begin a new run, superseding whatever the last one left.

        Subscribers are dropped rather than carried over: they are reading
        the previous run and their `finally` removes them anyway; keeping
        them would splice two runs into one stream.
        """
        with self._lock:
            self._history = []
            self._subscribers = set()
            self._started = True
            self._token = uuid.uuid4().hex

    def publish(self, item: Any) -> None:
        """Record `item` and hand it to every current subscriber.

        Both, under one lock, so a subscriber added between the two cannot
        miss it or receive it twice -- `subscribe` takes the same lock.
        """
        with self._lock:
            self._history.append(item)
            for subscriber in self._subscribers:
                subscriber.put(item)

    def subscribe(self, last_event_id: str | None = None) -> Subscription:
        """What this reader has still to see, and a queue for what comes next.

        `last_event_id` is SSE's own resume mechanism: a browser
        re-establishing a dropped EventSource sends back the last `id:` it
        received, without being asked and without the page knowing it
        happened. Honouring it is what separates "carry on" from "here is
        the run again" -- and the console counts what it is sent, so sending
        the run again means every counter, every log row and every tree node
        arriving twice.

        An id from a different run is not a position in this one, so it
        starts from the beginning. So does an unparsable one.
        """
        subscriber: queue.Queue = queue.Queue()
        with self._lock:
            start = 0
            resumed = False
            if last_event_id:
                token, _, index = last_event_id.rpartition(":")
                if token == self._token and index.isdigit():
                    # `min`: an id past the end can only be from a run whose
                    # history was replaced, and replaying nothing forever is
                    # worse than replaying too much.
                    start = min(int(index) + 1, len(self._history))
                    resumed = True
            self._subscribers.add(subscriber)
            return Subscription(
                token=self._token,
                start=start,
                history=list(self._history[start:]),
                queue=subscriber,
                resumed=resumed,
            )

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)
