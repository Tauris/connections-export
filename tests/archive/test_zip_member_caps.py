"""A zip member is decompressed only up to a size that could be real.

An archive zip may come from anyone. A few kilobytes of deflated zeros
expand to gigabytes, and `ZipFile.read` would hold every one of them in
memory before a single byte was looked at. A member over its cap is a
corrupt member: absent for a blob or a sidecar, an explained refusal for a
manifest stream -- never a process that runs out of memory.
"""

from __future__ import annotations

import zipfile

import pytest

from connections_export.archive import source as source_module
from connections_export.archive.source import ArchiveSourceError, ZipSource

DIGEST = "aa" * 32


def _zip(tmp_path, *, manifest=b'{"url": "a"}\n', blob=b"body", sidecar=b'{"status": "ok"}'):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.jsonl", manifest)
        archive.writestr(f"blobs/{DIGEST}", blob)
        archive.writestr("archive-summary.json", sidecar)
        archive.writestr("run-a.json", sidecar)
    return path


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(source_module, "MAX_RECORDS_MEMBER_BYTES", 64)
    monkeypatch.setattr(source_module, "MAX_BLOB_MEMBER_BYTES", 64)


def test_members_within_the_caps_read_as_before(tmp_path, small_caps):
    source = ZipSource(_zip(tmp_path))

    assert list(source.read_lines("manifest.jsonl")) == ['{"url": "a"}']
    assert source.read_blob(DIGEST) == b"body"
    assert source.read_json("archive-summary.json") == {"status": "ok"}
    assert [entry.data for entry in source.run_metadata()] == [b'{"status": "ok"}']


def test_an_oversized_blob_reads_as_absent(tmp_path, small_caps):
    source = ZipSource(_zip(tmp_path, blob=b"\0" * 10_000))

    assert source.read_blob(DIGEST) is None


def test_an_oversized_sidecar_reads_as_absent(tmp_path, small_caps):
    padded = b'{"status": "ok", "pad": "' + b" " * 10_000 + b'"}'
    source = ZipSource(_zip(tmp_path, sidecar=padded))

    assert source.read_json("archive-summary.json") is None
    assert source.run_metadata() == []


def test_an_oversized_manifest_is_refused_in_words(tmp_path, small_caps):
    """A manifest cannot be half-read -- the index would derive from a
    truncated list and call it the archive -- so it is refused, as the
    archive-level error every caller already reports."""
    source = ZipSource(_zip(tmp_path, manifest=b'{"url": "a"}\n' * 1_000))

    with pytest.raises(ArchiveSourceError, match="manifest.jsonl"):
        list(source.read_lines("manifest.jsonl"))


def test_a_member_whose_declared_size_lies_is_still_stopped(tmp_path, small_caps):
    """`file_size` comes from the zip's own directory, which whoever built the
    zip controls. The cap has to hold while decompressing too."""
    path = _zip(tmp_path, blob=b"\0" * 10_000)
    source = ZipSource(path)
    info = source._handle().getinfo(f"blobs/{DIGEST}")
    info.file_size = 4  # what a forged central directory would claim

    assert source.read_blob(DIGEST) is None
