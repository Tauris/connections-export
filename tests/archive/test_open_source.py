"""Picking a source from a path, and never creating anything while reading."""

from __future__ import annotations

import zipfile

import pytest

from connections_export.archive.source import (
    ArchiveSourceError,
    DirectorySource,
    ReadableArchive,
    ZipSource,
    open_source,
)
from connections_export.archive.store import Archive


def _dir_archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    return root


def _zip_archive(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.jsonl", "{}\n")
    return path


def test_a_directory_path_gives_a_directory_source(tmp_path):
    assert isinstance(open_source(_dir_archive(tmp_path)), DirectorySource)


def test_a_zip_path_gives_a_zip_source(tmp_path):
    assert isinstance(open_source(_zip_archive(tmp_path)), ZipSource)


def test_opening_for_reading_never_creates_anything(tmp_path):
    """`Archive.open` creates as it opens, which is right for a crawl target
    and wrong for a read: a mistyped path must not leave an empty archive
    behind for someone to find later and wonder about."""
    missing = tmp_path / "not-here"

    with pytest.raises(ArchiveSourceError):
        open_source(missing)

    assert not missing.exists()


def test_a_file_that_is_neither_says_which_it_is(tmp_path):
    stray = tmp_path / "notes.txt"
    stray.write_text("hello", encoding="utf-8")

    with pytest.raises(ArchiveSourceError) as excinfo:
        open_source(stray)

    assert "neither" in str(excinfo.value)


def test_an_archive_exposes_its_own_source(tmp_path):
    archive = Archive.open(tmp_path / "archive")

    assert isinstance(archive.source, DirectorySource)
    assert archive.source.directory == archive.root


def test_a_readable_archive_wraps_either_kind(tmp_path):
    """What the read paths take: a source, and nothing else. `Archive`
    creates directories as it opens them because it exists to be written
    into; this is the read side."""
    directory = _dir_archive(tmp_path)

    assert ReadableArchive.open(_zip_archive(tmp_path)).root is None
    assert ReadableArchive.open(directory).root == directory
