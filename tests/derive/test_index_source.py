"""The index reads an archive through a source, not through a path."""

from __future__ import annotations

import zipfile

import pytest

from connections_export.archive.source import ArchiveSourceError, ZipSource, open_source
from connections_export.derive.index import ArchiveIndex


def test_the_index_takes_a_source(tmp_path):
    root = tmp_path / "archive"
    (root / "blobs").mkdir(parents=True)
    (root / "manifest.jsonl").write_text("", encoding="utf-8")

    index = ArchiveIndex(open_source(root))

    assert index.get("https://fake/nothing") is None


def test_a_recorded_blob_that_is_missing_is_loud(tmp_path):
    """A manifest entry whose body is absent is a corrupt archive. Saying so
    beats deriving an empty body, which downstream cannot tell from a page
    that genuinely had no content."""
    path = tmp_path / "archive.zip"
    record = (
        '{"url": "https://fake/p", "method": "GET", "outcome": "ok",'
        ' "fetched_at": "2026-08-23T00:00:00Z", "body_hash": "sha256:'
        + "cc" * 32
        + '", "content_type": "text/html"}'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.jsonl", record + "\n")

    with pytest.raises(ArchiveSourceError) as excinfo:
        ArchiveIndex(ZipSource(path)).get("https://fake/p")

    assert "cc" * 32 in str(excinfo.value)


def _record(url: str, body_hash: str) -> str:
    return (
        '{"url": "' + url + '", "method": "GET", "outcome": "ok",'
        ' "fetched_at": "2026-08-23T00:00:00Z", "body_hash": "' + body_hash + '",'
        ' "content_type": "text/html"}'
    )


def test_a_manifest_hash_that_walks_out_of_blobs_reads_nothing(tmp_path):
    """The manifest is the author's text, and archives are handed on. A
    `body_hash` of `sha256:../../secret` used to pull that file off the
    reader's disk into the page body -- and from there into the reader, the
    PDF and every export. It is a corrupt record: skipped, named, and the
    rest of the archive still reads."""
    (tmp_path / "secret").write_bytes(b"private key")
    root = tmp_path / "archive"
    (root / "blobs").mkdir(parents=True)
    (root / "blobs" / ("dd" * 32)).write_bytes(b"<p>good</p>")
    (root / "manifest.jsonl").write_text(
        _record("https://fake/evil", "sha256:../../secret")
        + "\n"
        + _record("https://fake/good", "sha256:" + "dd" * 32)
        + "\n",
        encoding="utf-8",
    )

    index = ArchiveIndex(open_source(root))

    assert index.get("https://fake/evil") is None
    assert index.corrupt_urls == ["https://fake/evil"]
    assert index.get("https://fake/good").content == b"<p>good</p>"
