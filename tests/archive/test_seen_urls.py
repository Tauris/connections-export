"""Tests for Archive.seen_urls: resumability."""

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from tests.archive.conftest import FakeResponse


def test_seen_urls_includes_ok_records(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_response(
        FakeResponse(url="https://host/ok", method="GET", status=200, content=b"x"),
        fetched_at="2026-07-20T18:00:00Z",
    )

    assert archive.seen_urls() == {"https://host/ok"}


def test_seen_urls_excludes_failure_only_urls(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_failure(
        url="https://host/broken",
        method="GET",
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.transport_error,
        error="timeout",
    )
    archive.write_response(
        FakeResponse(url="https://host/not-found", method="GET", status=404, content=b""),
        fetched_at="2026-07-20T18:00:00Z",
    )

    assert archive.seen_urls() == set()


def test_seen_urls_reflects_prior_records_after_reopening(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_response(
        FakeResponse(url="https://host/a", method="GET", status=200, content=b"a"),
        fetched_at="2026-07-20T18:00:00Z",
    )
    archive.close()

    reopened = Archive.open(tmp_path)
    assert reopened.seen_urls() == {"https://host/a"}
