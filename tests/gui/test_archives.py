import os
import sys
import tempfile
from pathlib import Path

import pytest

from connections_export.gui.archives import ArchiveInfo, list_archives, resolve_archive


# Symlink creation on Windows requires either Developer Mode or the
# SeCreateSymbolicLinkPrivilege. Probe once at collection time so the
# two symlink-security tests skip gracefully instead of erroring with
# WinError 1314.
def _symlinks_available() -> bool:
    if sys.platform != "win32":
        return True
    try:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "target").mkdir()
            Path(d, "link").symlink_to(Path(d, "target"))
        return True
    except OSError:
        return False


_symlink_skip = pytest.mark.skipif(
    not _symlinks_available(),
    reason="symlink creation requires Developer Mode or SeCreateSymbolicLinkPrivilege on Windows",
)


def _mk(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    return d


def _age(path: Path, seconds: int) -> None:
    """Give `path` an mtime `seconds` after a fixed epoch.

    Set explicitly rather than by creating one directory after another and
    calling `touch`: two things made in the same second share an mtime, and
    then the order under test is `iterdir` order, which is the filesystem's
    business and differs between machines. This test passed here and failed
    on CI for exactly that reason -- it was reading a coin toss.
    """
    stamp = 1_700_000_000 + seconds
    os.utime(path, (stamp, stamp))


def test_list_archives_newest_first_and_flags_demo(tmp_path):
    _mk(tmp_path, "export-host-20260101-000000-aaaa")
    _mk(tmp_path, "DEMO-FAKE-DATA-20260102-000000-bbbb")
    _age(tmp_path / "export-host-20260101-000000-aaaa", 0)
    _age(tmp_path / "DEMO-FAKE-DATA-20260102-000000-bbbb", 60)
    infos = list_archives(tmp_path)
    assert [i.name for i in infos][0].startswith("DEMO-FAKE-DATA")
    assert all(isinstance(i, ArchiveInfo) for i in infos)
    assert any(i.is_demo for i in infos) and any(not i.is_demo for i in infos)


def test_archives_written_in_the_same_second_still_have_an_order(tmp_path):
    """mtime does not distinguish them, and `iterdir` order is not an order
    -- it varies by filesystem and can change between two listings of a
    directory nobody touched. The console's archive list would reshuffle for
    no reason."""
    for name in ("export-a-20260101-000000-aaaa", "export-b-20260101-000000-bbbb"):
        _age(_mk(tmp_path, name), 0)

    once = [i.name for i in list_archives(tmp_path)]
    again = [i.name for i in list_archives(tmp_path)]

    assert once == again
    assert once == sorted(once, reverse=True)


def test_list_archives_missing_base_is_empty(tmp_path):
    assert list_archives(tmp_path / "nope") == []


def test_resolve_archive_accepts_direct_child(tmp_path):
    d = _mk(tmp_path, "export-host-20260101-000000-aaaa")
    assert resolve_archive(tmp_path, "export-host-20260101-000000-aaaa") == d


def test_resolve_archive_rejects_traversal_and_absolute(tmp_path):
    _mk(tmp_path, "real")
    assert resolve_archive(tmp_path, "../etc") is None
    assert resolve_archive(tmp_path, "a/b") is None
    assert resolve_archive(tmp_path, "/etc") is None
    assert resolve_archive(tmp_path, "missing") is None


@_symlink_skip
def test_resolve_archive_rejects_symlink_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "evil").symlink_to(outside)
    assert resolve_archive(base, "evil") is None


@_symlink_skip
def test_resolve_archive_rejects_symlink_loop(tmp_path):
    base = tmp_path
    (base / "loop1").symlink_to(base / "loop2")
    (base / "loop2").symlink_to(base / "loop1")
    assert resolve_archive(base, "loop1") is None


def test_resolve_archive_rejects_null_byte(tmp_path):
    assert resolve_archive(tmp_path, "foo\x00bar") is None


def test_display_name_demo(tmp_path):
    d = _mk(tmp_path, "DEMO-FAKE-DATA-20260726-211153-abcd")
    infos = list_archives(tmp_path)
    assert infos[0].path == d
    assert infos[0].display_name == "Demo (fake data) · 2026-07-26 21:11"


def test_display_name_real_extracts_host(tmp_path):
    # A dotted host, not a bare single-label one -- test_hygiene.py's
    # disallowed-host scanner covers tests/**, and an `example`-labelled
    # host is the one curated placeholder allowed to look real while
    # still being an obvious non-deployment stand-in.
    d = _mk(tmp_path, "export-wiki.example.corp-20260726-140000-efgh")
    infos = list_archives(tmp_path)
    assert infos[0].path == d
    assert infos[0].display_name == "wiki.example.corp · 2026-07-26 14:00"


def test_display_name_falls_back_to_raw_name_when_unmatched(tmp_path):
    _mk(tmp_path, "some-hand-made-dir")
    infos = list_archives(tmp_path)
    assert infos[0].display_name == "some-hand-made-dir"


def test_item_count_from_manifest_lines(tmp_path):
    d = _mk(tmp_path, "export-host-20260101-000000-aaaa")
    (d / "manifest.jsonl").write_text('{"a":1}\n{"a":2}\n\n{"a":3}\n', encoding="utf-8")
    infos = list_archives(tmp_path)
    assert infos[0].item_count == 3


def test_item_count_zero_when_manifest_absent(tmp_path):
    _mk(tmp_path, "export-host-20260101-000000-aaaa")
    infos = list_archives(tmp_path)
    assert infos[0].item_count == 0


def test_an_export_written_beside_the_archives_is_not_listed_as_one(tmp_path):
    """Exports are written beside the archive they came from -- inside the
    archives folder when the archive is there -- and a Hugo content folder,
    Jekyll site or Obsidian vault is not an archive: listed, it looked like
    one. A folder that does hold an archive is listed whatever its name."""
    _mk(tmp_path, "export-host-20260101-000000-aaaa")
    for suffix in ("-hugo-content", "-jekyll-site", "-obsidian-vault"):
        (tmp_path / f"export-host-20260101-000000-aaaa{suffix}" / "content").mkdir(parents=True)
    (tmp_path / "combined-2-archives-1a2b3c4d-hugo-content").mkdir()
    oddly_named = tmp_path / "team-hugo-content"
    oddly_named.mkdir()
    (oddly_named / "manifest.jsonl").write_text("", encoding="utf-8")

    names = {i.name for i in list_archives(tmp_path)}

    assert names == {"export-host-20260101-000000-aaaa", "team-hugo-content"}


def test_every_export_format_suffix_is_recognised_as_an_export():
    """The listing's rule and the ingest route's folder names cannot drift."""
    from connections_export.gui.archives import EXPORT_FOLDER_SUFFIXES
    from connections_export.gui.routes.ingest import _FORMATS

    assert {spec["suffix"] for spec in _FORMATS.values()} <= set(EXPORT_FOLDER_SUFFIXES)
