"""An archive read straight out of a zip, without unpacking it.

`ArchiveIndex` reads two metadata streams at open and pulls blobs lazily by
digest, which is exactly what a zip's central directory serves well. Opening a
large archive to read one page costs those two streams plus the blobs that
page actually uses -- no extraction, and no paying for the rest.
"""

from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from connections_export.archive.source import ArchiveSourceError, ZipSource

DIGEST = "aa" * 32


def _zip(tmp_path, prefix="", name="archive.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{prefix}manifest.jsonl", '{"url": "a"}\n{"url": "b"}\n')
        archive.writestr(f"{prefix}feeds.jsonl", '{"feed_id": "f"}\n')
        archive.writestr(f"{prefix}blobs/{DIGEST}", b"body bytes")
        archive.writestr(f"{prefix}run-a.json", '{"run_id": "a"}')
        archive.writestr(f"{prefix}archive-summary.json", '{"status": "ok"}')
    return path


def test_a_flat_zip_reads_like_a_directory(tmp_path):
    source = ZipSource(_zip(tmp_path))
    source.check()

    assert list(source.read_lines("manifest.jsonl")) == ['{"url": "a"}', '{"url": "b"}']
    assert source.read_blob(DIGEST) == b"body bytes"
    assert source.read_json("archive-summary.json") == {"status": "ok"}
    assert [entry.name for entry in source.run_metadata()] == ["run-a.json"]


def test_an_archive_nested_under_one_directory_is_found(tmp_path):
    """What "Send to > Compressed folder" produces on Windows."""
    source = ZipSource(_zip(tmp_path, prefix="export-2026-08-23/"))
    source.check()

    assert source.read_blob(DIGEST) == b"body bytes"


def test_two_candidate_roots_are_refused_rather_than_guessed(tmp_path):
    path = tmp_path / "two.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("one/manifest.jsonl", "{}\n")
        archive.writestr("two/manifest.jsonl", "{}\n")

    with pytest.raises(ArchiveSourceError) as excinfo:
        ZipSource(path).check()

    assert "one" in str(excinfo.value) and "two" in str(excinfo.value)


def test_a_zip_without_a_manifest_is_not_an_archive(tmp_path):
    path = tmp_path / "holiday.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("beach.jpg", b"not an archive")

    with pytest.raises(ArchiveSourceError) as excinfo:
        ZipSource(path).check()

    assert "manifest.jsonl" in str(excinfo.value)


def test_a_file_that_is_not_a_zip_says_so(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"this is not a zip")

    with pytest.raises(ArchiveSourceError):
        ZipSource(path).check()


def test_a_missing_blob_is_none(tmp_path):
    assert ZipSource(_zip(tmp_path)).read_blob("bb" * 32) is None


def test_concurrent_blob_reads_return_the_right_bytes(tmp_path):
    """The GUI serves blobs from several threads at once and `ZipFile` is not
    thread-safe: one handle shared across threads returns torn reads."""
    source = ZipSource(_zip(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _n: source.read_blob(DIGEST), range(64)))

    assert results == [b"body bytes"] * 64


def test_a_zip_has_no_directory(tmp_path):
    """`package_root` hands a real directory to the Manual's spec endpoint;
    a zip has none, and `None` is already a state that endpoint handles."""
    assert ZipSource(_zip(tmp_path)).directory is None


def test_lines_agree_with_a_directory_for_a_trailing_newline(tmp_path):
    """A file ending in a newline must yield the same list either way, or
    derive counts one blank record more from a zip than from a directory."""
    from connections_export.archive.source import DirectorySource

    root = tmp_path / "archive"
    (root / "blobs").mkdir(parents=True)
    (root / "manifest.jsonl").write_text('{"url": "a"}\n{"url": "b"}\n', encoding="utf-8")

    assert list(ZipSource(_zip(tmp_path)).read_lines("manifest.jsonl")) == list(
        DirectorySource(root).read_lines("manifest.jsonl")
    )
