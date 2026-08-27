"""Reading an archive through a source, rather than through the filesystem.

An archive is `manifest.jsonl`, `feeds.jsonl`, `run-*.json` and
`blobs/<digest>`. Writing one means writing files; READING one is four
operations, none of which requires a filesystem. This is that seam, so the
same archive can be read from a directory or straight out of a zip.
"""

from __future__ import annotations

import os
import time

import pytest

from connections_export.archive.source import ArchiveSourceError, DirectorySource


def _archive(tmp_path):
    root = tmp_path / "archive"
    (root / "blobs").mkdir(parents=True)
    (root / "manifest.jsonl").write_text('{"url": "a"}\n\n{"url": "b"}\n', encoding="utf-8")
    (root / "feeds.jsonl").write_text('{"feed_id": "f"}\n', encoding="utf-8")
    (root / "blobs" / ("aa" * 32)).write_bytes(b"body bytes")
    (root / "archive-summary.json").write_text('{"status": "ok"}', encoding="utf-8")
    return root


def test_a_directory_source_reads_lines_blobs_and_json(tmp_path):
    source = DirectorySource(_archive(tmp_path))

    assert list(source.read_lines("manifest.jsonl")) == ['{"url": "a"}', "", '{"url": "b"}']
    assert source.read_blob("aa" * 32) == b"body bytes"
    assert source.read_json("archive-summary.json") == {"status": "ok"}


def test_a_missing_blob_is_none_not_an_error(tmp_path):
    """A blob the archive does not hold is a visible gap the callers already
    render, never an exception thrown from underneath them."""
    assert DirectorySource(_archive(tmp_path)).read_blob("bb" * 32) is None


def test_a_missing_stream_reads_as_empty(tmp_path):
    """An archive written before `feeds.jsonl` existed has none, and that is
    a documented fallback path, not a failure."""
    source = DirectorySource(_archive(tmp_path))

    assert list(source.read_lines("nothing.jsonl")) == []
    assert source.read_json("nothing.json") is None


def test_run_metadata_comes_back_oldest_first(tmp_path):
    """Ordering is the source's job. A directory has mtimes and a zip entry
    has a two-second timestamp, and no caller should have to know which it is
    looking at."""
    root = _archive(tmp_path)
    (root / "run-b.json").write_text('{"run_id": "b"}', encoding="utf-8")
    (root / "run-a.json").write_text('{"run_id": "a"}', encoding="utf-8")
    old = time.time() - 600
    os.utime(root / "run-b.json", (old, old))

    entries = DirectorySource(root).run_metadata()

    assert [entry.name for entry in entries] == ["run-b.json", "run-a.json"]
    assert entries[-1].data == b'{"run_id": "a"}'


def test_a_directory_that_is_not_an_archive_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ArchiveSourceError) as excinfo:
        DirectorySource(empty).check()

    assert "manifest.jsonl" in str(excinfo.value)
