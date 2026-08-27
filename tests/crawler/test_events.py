"""Event types construct with their documented fields; the
default `Emit` is a safe no-op that swallows a raising consumer.
"""

from connections_export.crawler import events


def test_run_started_fields():
    event = events.RunStarted(run_id="r1", base_url="https://fake", source_version="8.0")

    assert event.run_id == "r1"
    assert event.base_url == "https://fake"
    assert event.source_version == "8.0"


def test_discovered_fields():
    event = events.Discovered(url="https://fake/x", kind="page", discovered_from="https://fake/y")

    assert event.url == "https://fake/x"
    assert event.kind == "page"
    assert event.discovered_from == "https://fake/y"


def test_fetching_fields():
    event = events.Fetching(url="https://fake/x", kind="body")

    assert event.url == "https://fake/x"
    assert event.kind == "body"


def test_fetched_fields():
    event = events.Fetched(
        url="https://fake/x", kind="page", status=200, num_bytes=42, from_cache=False
    )

    assert event.status == 200
    assert event.num_bytes == 42
    assert event.from_cache is False


def test_fetched_from_cache_defaults_false():
    event = events.Fetched(url="https://fake/x", kind="page", status=200, num_bytes=42)

    assert event.from_cache is False


def test_retried_fields():
    event = events.Retried(url="https://fake/x", attempt=2, reason="retry wait 0.50s")

    assert event.attempt == 2
    assert event.reason == "retry wait 0.50s"


def test_failed_fields():
    event = events.Failed(url="https://fake/x", kind="timeout", error="boom")

    assert event.kind == "timeout"
    assert event.error == "boom"


def test_page_derived_fields():
    event = events.PageDerived(
        page_id="p1",
        wiki="wiki0",
        wiki_title="Wiki Zero",
        title="Home",
        parent=None,
        ordinal=0,
        comment_count=1,
        version_count=2,
        attachment_count=3,
    )

    assert event.page_id == "p1"
    assert event.wiki == "wiki0"
    assert event.parent is None
    assert event.ordinal == 0
    assert event.comment_count == 1
    assert event.version_count == 2
    assert event.attachment_count == 3


def test_warning_fields():
    event = events.Warning(kind="orphan", detail="parent missing", ref="p1")

    assert event.kind == "orphan"
    assert event.detail == "parent missing"
    assert event.ref == "p1"


def test_warning_ref_defaults_none():
    event = events.Warning(kind="truncation", detail="feed looks truncated")

    assert event.ref is None


def test_run_complete_fields():
    event = events.RunComplete(report={"counts": {}})

    assert event.report == {"counts": {}}


def test_default_emit_is_a_safe_noop():
    # Calling the default emit with any event must not raise.
    events.default_emit(
        events.RunStarted(run_id="r", base_url="https://fake", source_version="8.0")
    )


def test_safe_emit_swallows_a_raising_consumer():
    def raising_emit(event):
        raise RuntimeError("consumer exploded")

    # Must not raise -- this is the fire-and-forget contract.
    events.safe_emit(
        raising_emit, events.RunStarted(run_id="r", base_url="https://fake", source_version="8.0")
    )


def test_safe_emit_calls_a_well_behaved_consumer():
    seen = []
    event = events.RunStarted(run_id="r", base_url="https://fake", source_version="8.0")

    events.safe_emit(seen.append, event)

    assert seen == [event]
