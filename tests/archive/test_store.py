"""Tests for connections_export.archive.store.Archive: the writer/reader surface."""

import hashlib
import json

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from tests.archive.conftest import FakeResponse


def manifest_lines(archive_dir):
    path = archive_dir / "manifest.jsonl"
    return path.read_text(encoding="utf-8").splitlines()


# --- 5.1 write_response appends one manifest line and stores one blob ---


def test_write_response_appends_one_manifest_line_and_stores_one_blob(tmp_path):
    archive = Archive.open(tmp_path)
    response = FakeResponse(
        url="https://host/wikis/basic/api/wikis/feed",
        method="GET",
        status=200,
        headers={"Content-Type": "application/atom+xml; charset=UTF-8"},
        content=b"<feed>hello</feed>",
    )

    record = archive.write_response(
        response,
        request_headers={"Accept": "application/atom+xml"},
        fetched_at="2026-07-20T18:00:00Z",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
    )

    lines = manifest_lines(tmp_path)
    assert len(lines) == 1
    assert record.outcome == Outcome.ok
    assert record.status == 200
    assert record.body_hash == "sha256:" + hashlib.sha256(response.content).hexdigest()

    blob_files = list((tmp_path / "blobs").glob("*"))
    assert len(blob_files) == 1
    assert blob_files[0].read_bytes() == response.content


# --- 5.2 byte-exact persistence, including Content-Encoding responses ---


def test_write_response_stores_body_bytes_verbatim(tmp_path):
    archive = Archive.open(tmp_path)
    body = bytes(range(256)) * 4  # binary-looking payload
    response = FakeResponse(url="https://host/x", method="GET", status=200, content=body)

    record = archive.write_response(response, fetched_at="2026-07-20T18:00:00Z")

    digest = record.body_hash.removeprefix("sha256:")
    stored = (tmp_path / "blobs" / digest).read_bytes()
    assert stored == body


def test_content_encoding_response_stored_without_decompression(tmp_path):
    import gzip

    archive = Archive.open(tmp_path)
    original = b"repeat repeat repeat repeat repeat"
    compressed = gzip.compress(original)
    response = FakeResponse(
        url="https://host/compressed",
        method="GET",
        status=200,
        headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"},
        content=compressed,
    )

    record = archive.write_response(response, fetched_at="2026-07-20T18:00:00Z")

    digest = record.body_hash.removeprefix("sha256:")
    stored = (tmp_path / "blobs" / digest).read_bytes()
    # Stored bytes are exactly the compressed bytes — never decompressed.
    assert stored == compressed
    assert stored != original
    assert record.response_headers["Content-Encoding"] == "gzip"


# --- 5.3 write_failure (transport) + HTTP error responses ---


def test_write_failure_transport_has_null_body_hash_and_error(tmp_path):
    archive = Archive.open(tmp_path)

    record = archive.write_failure(
        url="https://host/unreachable",
        method="GET",
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.transport_error,
        error="connection refused",
    )

    assert record.outcome == Outcome.transport_error
    assert record.body_hash is None
    assert record.error == "connection refused"
    lines = manifest_lines(tmp_path)
    assert len(lines) == 1


def test_http_error_response_still_stores_body_blob(tmp_path):
    archive = Archive.open(tmp_path)
    response = FakeResponse(
        url="https://host/missing",
        method="GET",
        status=404,
        content=b"<error>not found</error>",
    )

    record = archive.write_response(response, fetched_at="2026-07-20T18:00:00Z")

    assert record.outcome == Outcome.http_error
    assert record.status == 404
    assert record.body_hash is not None
    digest = record.body_hash.removeprefix("sha256:")
    assert (tmp_path / "blobs" / digest).read_bytes() == response.content


# --- 5.4 redaction applied on every write path, no bypass ---


def test_redaction_applied_on_write_response_request_headers(tmp_path):
    archive = Archive.open(tmp_path)
    response = FakeResponse(url="https://host/x", method="GET", status=200, content=b"ok")

    record = archive.write_response(
        response,
        request_headers={"Authorization": "Bearer secret-token"},
        fetched_at="2026-07-20T18:00:00Z",
    )

    assert record.request_headers["Authorization"] == "<redacted>"
    raw = manifest_lines(tmp_path)[0]
    assert "secret-token" not in raw


def test_redaction_applied_on_write_response_response_headers(tmp_path):
    archive = Archive.open(tmp_path)
    response = FakeResponse(
        url="https://host/x",
        method="GET",
        status=200,
        headers={"Set-Cookie": "session=leak-me"},
        content=b"ok",
    )

    record = archive.write_response(response, fetched_at="2026-07-20T18:00:00Z")

    assert record.response_headers["Set-Cookie"] == "<redacted>"
    raw = manifest_lines(tmp_path)[0]
    assert "leak-me" not in raw


def test_redaction_applied_on_write_failure_request_headers(tmp_path):
    archive = Archive.open(tmp_path)

    record = archive.write_failure(
        url="https://host/x",
        method="GET",
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.transport_error,
        error="timeout",
        request_headers={"Cookie": "session=leak-me-too"},
    )

    assert record.request_headers["Cookie"] == "<redacted>"
    raw = manifest_lines(tmp_path)[0]
    assert "leak-me-too" not in raw


# --- 5.5 reopening an existing archive appends rather than truncates ---


def test_reopening_archive_appends_rather_than_truncates(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_response(
        FakeResponse(url="https://host/a", method="GET", status=200, content=b"a"),
        fetched_at="2026-07-20T18:00:00Z",
    )
    archive.close()

    reopened = Archive.open(tmp_path)
    reopened.write_response(
        FakeResponse(url="https://host/b", method="GET", status=200, content=b"b"),
        fetched_at="2026-07-20T18:01:00Z",
    )

    lines = manifest_lines(tmp_path)
    assert len(lines) == 2
    urls = [json.loads(line)["url"] for line in lines]
    assert urls == ["https://host/a", "https://host/b"]


# --- fetched_at is caller-injected, never read from the wall clock ---


def test_fetched_at_is_exactly_the_caller_supplied_value(tmp_path):
    archive = Archive.open(tmp_path)

    record = archive.write_response(
        FakeResponse(url="https://host/x", method="GET", status=200, content=b"x"),
        fetched_at="1999-01-01T00:00:00Z",
    )

    assert record.fetched_at == "1999-01-01T00:00:00Z"
