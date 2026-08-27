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


def test_list_archives_newest_first_and_flags_demo(tmp_path):
    _mk(tmp_path, "export-host-20260101-000000-aaaa")
    _mk(tmp_path, "DEMO-FAKE-DATA-20260102-000000-bbbb")
    # make the demo one newer
    (tmp_path / "DEMO-FAKE-DATA-20260102-000000-bbbb").touch()
    infos = list_archives(tmp_path)
    assert [i.name for i in infos][0].startswith("DEMO-FAKE-DATA")
    assert all(isinstance(i, ArchiveInfo) for i in infos)
    assert any(i.is_demo for i in infos) and any(not i.is_demo for i in infos)


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
