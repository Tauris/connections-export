"""Capability round-trip.

Write a mixed batch (duplicates, a binary blob, a transport failure, an
HTTP error, a redacted header, a truncation-signature feed) -> reopen ->
assert manifest count, dedup, redaction, seen_urls, and report all hold
together.
"""

import hashlib

from connections_export.archive.records import Outcome, RunMetadata
from connections_export.archive.store import Archive
from tests.archive.conftest import FakeResponse

DUPLICATE_BODY = b"<entry>same content, fetched twice (e.g. two revisions)</entry>"
BINARY_BODY = bytes(range(256)) * 8  # stand-in for a fake binary attachment


def test_mixed_batch_round_trips_through_a_reopened_archive(tmp_path):
    archive = Archive.open(tmp_path)
    archive.write_run_metadata(
        RunMetadata(
            run_id="2026-07-20T180000Z",
            base_url="https://host/wikis/basic",
            principal="demo-user",
            adapter_version="wikis-adapter-0.1.0",
            source_system_version="HCL Connections 8.0",
        )
    )

    # 1 & 2: two responses with byte-identical bodies (dedup).
    archive.write_response(
        FakeResponse(
            url="https://host/page/1/rev/1",
            method="GET",
            status=200,
            content=DUPLICATE_BODY,
        ),
        request_headers={"Authorization": "Bearer secret-one"},
        fetched_at="2026-07-20T18:00:00Z",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
    )
    archive.write_response(
        FakeResponse(
            url="https://host/page/1/rev/2",
            method="GET",
            status=200,
            content=DUPLICATE_BODY,
        ),
        request_headers={"Authorization": "Bearer secret-two"},
        fetched_at="2026-07-20T18:01:00Z",
        discovered_from="https://host/page/1/rev/1",
    )

    # 3: a binary attachment.
    archive.write_response(
        FakeResponse(
            url="https://host/attachment/logo.png",
            method="GET",
            status=200,
            headers={"Content-Type": "image/png"},
            content=BINARY_BODY,
        ),
        fetched_at="2026-07-20T18:02:00Z",
        discovered_from="https://host/page/1/rev/1",
    )

    # 4: a transport failure — never got a response.
    archive.write_failure(
        url="https://host/page/2",
        method="GET",
        fetched_at="2026-07-20T18:03:00Z",
        outcome=Outcome.transport_error,
        error="connection reset by peer",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
    )

    # 5: an HTTP error — body still stored.
    archive.write_response(
        FakeResponse(
            url="https://host/page/3",
            method="GET",
            status=403,
            content=b"<error>forbidden</error>",
        ),
        fetched_at="2026-07-20T18:04:00Z",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
    )

    # 6: a feed response carrying a truncation signature.
    archive.write_response(
        FakeResponse(
            url="https://host/wikis/basic/api/wikis/feed?page=3",
            method="GET",
            status=200,
            content=b"<feed>...50 entries...</feed>",
        ),
        fetched_at="2026-07-20T18:05:00Z",
        discovered_from="https://host/wikis/basic/api/wikis/feed?page=2",
        item_count=25,
        page_size=25,
        has_next=False,
    )

    archive.close()

    # --- Reopen, as a resumed crawl would. ---
    reopened = Archive.open(tmp_path)

    manifest_lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 6
    for line in manifest_lines:
        import json

        json.loads(line)  # each line independently parseable

    # Dedup: two records, one blob, for the duplicate body.
    expected_dup_digest = hashlib.sha256(DUPLICATE_BODY).hexdigest()
    assert (tmp_path / "blobs" / expected_dup_digest).is_file()
    blob_files = list((tmp_path / "blobs").glob("*"))
    # duplicate body (1 blob) + binary attachment + 403 error body + feed body
    assert len(blob_files) == 4

    # Binary body survives round trip unchanged.
    binary_digest = hashlib.sha256(BINARY_BODY).hexdigest()
    assert (tmp_path / "blobs" / binary_digest).read_bytes() == BINARY_BODY

    # Redaction: secrets never hit disk, presence preserved.
    combined_manifest_text = "\n".join(manifest_lines)
    assert "secret-one" not in combined_manifest_text
    assert "secret-two" not in combined_manifest_text
    assert '"Authorization":"<redacted>"' in combined_manifest_text

    # seen_urls: only `ok` records, failures excluded (both transport and http_error).
    assert reopened.seen_urls() == {
        "https://host/page/1/rev/1",
        "https://host/page/1/rev/2",
        "https://host/attachment/logo.png",
        "https://host/wikis/basic/api/wikis/feed?page=3",
    }
    assert "https://host/page/2" not in reopened.seen_urls()
    assert "https://host/page/3" not in reopened.seen_urls()

    # report: counts, failures by category, truncation flag.
    report = reopened.report()
    assert report.counts == {"ok": 4, "transport_error": 1, "http_error": 1}
    assert report.failures_by_category == {"transport_error": 1, "http_error": 1}
    assert report.possibly_truncated == ["https://host/wikis/basic/api/wikis/feed?page=3"]

    # run metadata retrievable alongside the manifest after reopening.
    run = reopened.read_run_metadata("2026-07-20T180000Z")
    assert run.source_system_version == "HCL Connections 8.0"

    # Writing further after reopen appends, doesn't disturb prior records.
    reopened.write_response(
        FakeResponse(url="https://host/page/4", method="GET", status=200, content=b"new"),
        fetched_at="2026-07-20T18:06:00Z",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
    )
    manifest_lines_after = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines_after) == 7
    assert manifest_lines_after[:6] == manifest_lines
