"""Tests for Archive.report: completeness reporting."""

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from tests.archive.conftest import FakeResponse


def test_report_gives_counts_by_outcome_and_failures_by_category(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_response(
        FakeResponse(url="https://host/ok1", method="GET", status=200, content=b"1"),
        fetched_at="2026-07-20T18:00:00Z",
    )
    archive.write_response(
        FakeResponse(url="https://host/ok2", method="GET", status=200, content=b"2"),
        fetched_at="2026-07-20T18:00:00Z",
    )
    archive.write_response(
        FakeResponse(url="https://host/missing", method="GET", status=404, content=b""),
        fetched_at="2026-07-20T18:00:00Z",
    )
    archive.write_failure(
        url="https://host/broken",
        method="GET",
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.transport_error,
        error="connection refused",
    )

    report = archive.report()

    assert report.counts == {
        "ok": 2,
        "http_error": 1,
        "transport_error": 1,
    }
    assert report.failures_by_category == {
        "http_error": 1,
        "transport_error": 1,
    }


def test_report_flags_truncation_signature(tmp_path):
    archive = Archive.open(tmp_path)
    # Signature: returned item count == requested page size, no next link.
    archive.write_response(
        FakeResponse(url="https://host/feed-truncated", method="GET", status=200, content=b"x"),
        fetched_at="2026-07-20T18:00:00Z",
        item_count=50,
        page_size=50,
        has_next=False,
    )
    # Not flagged: has a next-page link.
    archive.write_response(
        FakeResponse(url="https://host/feed-has-next", method="GET", status=200, content=b"y"),
        fetched_at="2026-07-20T18:00:00Z",
        item_count=50,
        page_size=50,
        has_next=True,
    )
    # Not flagged: fewer items than the page size.
    archive.write_response(
        FakeResponse(url="https://host/feed-complete", method="GET", status=200, content=b"z"),
        fetched_at="2026-07-20T18:00:00Z",
        item_count=10,
        page_size=50,
        has_next=False,
    )

    report = archive.report()

    assert report.possibly_truncated == ["https://host/feed-truncated"]


def test_report_on_empty_archive(tmp_path):
    archive = Archive.open(tmp_path)

    report = archive.report()

    assert report.counts == {}
    assert report.failures_by_category == {}
    assert report.possibly_truncated == []
