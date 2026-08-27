"""A zipped archive is an archive: it lists, it opens, and it never takes a
write.

A zip's whole point is that it moves cleanly -- someone hands you one, you
drop it in the archives folder. If the listing ignored it, the feature would
work only for people who already know the CLI flag.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from connections_export.gui.archives import list_archives, resolve_archive


def _archive_files(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    (root / "manifest.jsonl").write_text(
        '{"url": "https://fake/a", "method": "GET", "outcome": "ok",'
        ' "fetched_at": "2026-08-23T00:00:00Z"}\n'
        '{"url": "https://fake/b", "method": "GET", "outcome": "ok",'
        ' "fetched_at": "2026-08-23T00:00:01Z"}\n',
        encoding="utf-8",
    )
    (root / "archive-summary.json").write_text(
        json.dumps({"status": "ok", "counts": {"pages": 2}}), encoding="utf-8"
    )
    return root


def _zip_of(root, destination, prefix=""):
    with zipfile.ZipFile(destination, "w") as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                bundle.write(path, prefix + str(path.relative_to(root)).replace("\\", "/"))
    return destination


@pytest.fixture
def base(tmp_path):
    built = _archive_files(tmp_path / "build" / "export-2026-08-23")
    folder = tmp_path / "archives"
    folder.mkdir()
    _zip_of(built, folder / "export-2026-08-23.zip")
    return folder


def test_a_zip_in_the_archives_folder_is_listed(base):
    names = [info.name for info in list_archives(base)]
    assert "export-2026-08-23.zip" in names


def test_a_listed_zip_carries_a_real_item_count_not_a_placeholder(base):
    info = next(i for i in list_archives(base) if i.name.endswith(".zip"))
    assert info.item_count == 2


def test_a_listed_zip_reads_its_own_summary(base):
    from connections_export.gui.archives import read_archive_summary

    summary = read_archive_summary(base / "export-2026-08-23.zip")
    assert summary is not None
    assert summary["counts"]["pages"] == 2


def test_a_zip_resolves_by_name_under_the_same_guards(base):
    assert resolve_archive(base, "export-2026-08-23.zip") == base / "export-2026-08-23.zip"


def test_traversal_is_refused_for_zips_exactly_as_for_directories(base, tmp_path):
    outside = _archive_files(tmp_path / "outside")
    _zip_of(outside, tmp_path / "outside.zip")
    assert resolve_archive(base, "../outside.zip") is None
    assert resolve_archive(base, str(tmp_path / "outside.zip")) is None


def test_a_file_that_is_not_a_zip_is_not_listed(base):
    (base / "notes.txt").write_text("hello", encoding="utf-8")
    assert "notes.txt" not in [info.name for info in list_archives(base)]
