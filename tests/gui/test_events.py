"""Each crawler event type serializes to a JSON dict with
the fields the front end reads; round-trips
through `json.dumps`/`json.loads` unchanged.
"""

from __future__ import annotations

import json

from connections_export.crawler import events
from connections_export.crawler.report import CrawlReport
from connections_export.gui.events import to_json


def _roundtrip(event) -> dict:
    payload = to_json(event)
    # Every field must already be JSON-safe -- prove it by actually
    # going through json.dumps/json.loads, not just eyeballing types.
    return json.loads(json.dumps(payload))


def test_run_started_serializes():
    event = events.RunStarted(run_id="r1", base_url="https://fake", source_version="8.0")

    payload = _roundtrip(event)

    assert payload == {
        "type": "run_started",
        "run_id": "r1",
        "base_url": "https://fake",
        "source_version": "8.0",
    }


def test_discovered_serializes():
    event = events.Discovered(url="https://fake/x", kind="page", discovered_from="https://fake/y")

    payload = _roundtrip(event)

    assert payload == {
        "type": "discovered",
        "url": "https://fake/x",
        "kind": "page",
        "discovered_from": "https://fake/y",
    }


def test_fetching_serializes():
    event = events.Fetching(url="https://fake/x", kind="body")

    payload = _roundtrip(event)

    assert payload == {"type": "fetching", "url": "https://fake/x", "kind": "body"}


def test_fetched_serializes():
    event = events.Fetched(
        url="https://fake/x", kind="page", status=200, num_bytes=42, from_cache=False
    )

    payload = _roundtrip(event)

    assert payload == {
        "type": "fetched",
        "url": "https://fake/x",
        "kind": "page",
        "status": 200,
        "num_bytes": 42,
        "from_cache": False,
    }


def test_retried_serializes():
    event = events.Retried(url="https://fake/x", attempt=2, reason="retrying after a 0.50s wait")

    payload = _roundtrip(event)

    assert payload == {
        "type": "retried",
        "url": "https://fake/x",
        "attempt": 2,
        "reason": "retrying after a 0.50s wait",
    }


def test_failed_serializes():
    event = events.Failed(url="https://fake/x", kind="timeout", error="boom")

    payload = _roundtrip(event)

    assert payload == {
        "type": "failed",
        "url": "https://fake/x",
        "kind": "timeout",
        "error": "boom",
    }


def test_page_derived_serializes():
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

    payload = _roundtrip(event)

    assert payload == {
        "type": "page_derived",
        "page_id": "p1",
        "wiki": "wiki0",
        "wiki_title": "Wiki Zero",
        "title": "Home",
        "parent": None,
        "ordinal": 0,
        "comment_count": 1,
        "version_count": 2,
        "attachment_count": 3,
        "author": None,
        "modified": None,
    }


def test_blog_post_derived_serializes():
    event = events.BlogPostDerived(
        post_id="post1",
        blog="blog0",
        blog_title="Team Blog",
        title="Release notes",
        comment_count=4,
        comments_enabled=True,
    )

    payload = _roundtrip(event)

    assert payload == {
        "type": "blog_post_derived",
        "post_id": "post1",
        "blog": "blog0",
        "blog_title": "Team Blog",
        "title": "Release notes",
        "comment_count": 4,
        "comments_enabled": True,
        "author": None,
        "published": None,
    }


def test_forum_topic_derived_serializes():
    event = events.ForumTopicDerived(
        topic_id="topic1",
        forum="forum0",
        forum_title="Support Forum",
        title="How do I export?",
        reply_count=7,
        flags=("answered", "question"),
    )

    payload = _roundtrip(event)

    assert payload == {
        "type": "forum_topic_derived",
        "topic_id": "topic1",
        "forum": "forum0",
        "forum_title": "Support Forum",
        "title": "How do I export?",
        "author": None,
        "published": None,
        "reply_count": 7,
        "flags": ["answered", "question"],
    }


def test_warning_serializes():
    event = events.Warning(kind="orphan", detail="parent missing", ref="p1")

    payload = _roundtrip(event)

    assert payload == {
        "type": "warning",
        "kind": "orphan",
        "detail": "parent missing",
        "ref": "p1",
    }


def test_warning_ref_none_serializes():
    event = events.Warning(kind="truncation", detail="feed looks truncated")

    payload = _roundtrip(event)

    assert payload["ref"] is None


def test_run_complete_serializes_report():
    report = CrawlReport(
        counts={"page": 3},
        failures_by_category={},
        possibly_truncated=[],
        orphans=[],
        pages_crawled=3,
    )
    event = events.RunComplete(report=report)

    payload = _roundtrip(event)

    assert payload["type"] == "run_complete"
    assert payload["report"]["counts"] == {"page": 3}
    assert payload["report"]["pages_crawled"] == 3


def test_pruned_event_serializes():
    payload = _roundtrip(events.Pruned(kind="page", id="p-42"))
    assert payload["type"] == "pruned"
    assert payload["kind"] == "page"
    assert payload["id"] == "p-42"
    assert payload["reason"] == "author-filter"


def test_author_filter_summary_serializes():
    payload = _roundtrip(
        events.AuthorFilterSummary(
            kept=0, total=5, author="J. Weber", identities=("J. Weber (uid: 42)",)
        )
    )
    assert payload["type"] == "author_filter_summary"
    assert payload["kept"] == 0 and payload["total"] == 5
    assert payload["author"] == "J. Weber"
    assert payload["identities"] == ["J. Weber (uid: 42)"]


def test_unknown_event_type_raises():
    import pytest

    with pytest.raises(TypeError):
        to_json(object())
