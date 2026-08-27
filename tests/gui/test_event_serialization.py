"""Every crawler event must survive the trip to the browser.

`to_json` raises `TypeError` on an event it does not know, and the live
console's SSE stream is the only view a user has of a running ingest. An event
type added to the crawler but not here does not degrade gracefully -- it breaks
the stream for the run that emits it.

`CommunityFileDerived` was added with the Files work and never reached this
function, so a Files ingest emitted an event the console could not encode.
This test walks the `Event` union rather than a hand-kept list, so the next
one cannot be forgotten the same way.
"""

from __future__ import annotations

import dataclasses
import typing

import pytest

from connections_export.crawler import events
from connections_export.crawler.report import CrawlReport
from connections_export.gui.events import to_json


def _event_types() -> list[type]:
    return [t for t in typing.get_args(events.Event) if dataclasses.is_dataclass(t)]


def _instantiate(cls: type):
    """Build a minimal instance: required fields get a plausible stand-in."""
    kwargs = {}
    for field in dataclasses.fields(cls):
        if field.default is not dataclasses.MISSING or (
            field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        ):
            continue
        annotation = str(field.type)
        if "CrawlReport" in annotation:
            kwargs[field.name] = CrawlReport(counts={}, failures_by_category={})
        elif "int" in annotation and "str" not in annotation:
            kwargs[field.name] = 1
        elif "bool" in annotation:
            kwargs[field.name] = True
        elif "list" in annotation:
            kwargs[field.name] = []
        else:
            kwargs[field.name] = "x"
    return cls(**kwargs)


@pytest.mark.parametrize("event_type", _event_types(), ids=lambda t: t.__name__)
def test_every_event_in_the_union_can_be_encoded(event_type):
    payload = to_json(_instantiate(event_type))

    assert isinstance(payload, dict)
    assert payload.get("type"), f"{event_type.__name__} produced no discriminator"


def test_the_union_is_what_the_encoder_is_checked_against():
    """If the union were empty or unreadable the test above would vacuously
    pass, which is exactly the failure it exists to catch."""
    assert len(_event_types()) >= 10
