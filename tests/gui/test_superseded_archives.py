"""Grouping repeated attempts at the same target.

Archive directory names encode only `export-<host>-<stamp>-<suffix>` -- the
host and when the run happened, never *what* was captured. So "another
attempt at the same community" cannot be recognised from the name; it is
recognised from the run's persisted `archive-summary.json`, whose group and
community titles are the content signature.
"""

from __future__ import annotations

import json

from connections_export.gui.archives import (
    SUMMARY_FILENAME,
    archive_target_key,
    list_archives,
    superseded_names,
)


def _seed(base, name, summary=None, mtime=None):
    d = base / name
    d.mkdir(parents=True)
    if summary is not None:
        (d / SUMMARY_FILENAME).write_text(json.dumps(summary), encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(d, (mtime, mtime))
    return d


def _summary(*titles, base_url="https://connections.example.com"):
    return {
        "status": "ok",
        "base_url": base_url,
        "groups": [{"kind": "forum", "title": t, "count": 3} for t in titles],
        "communities": [],
    }


def test_same_captured_content_yields_the_same_target_key():
    """Two attempts at one forum differ in item counts as the crawl
    progresses, so counts must not enter the signature."""
    first = dict(_summary("Help & Support"))
    second = dict(_summary("Help & Support"))
    second["groups"] = [{"kind": "forum", "title": "Help & Support", "count": 99}]

    assert archive_target_key(first) == archive_target_key(second)


def test_different_content_yields_different_target_keys():
    assert archive_target_key(_summary("Help & Support")) != archive_target_key(
        _summary("General Discussion")
    )


def test_an_archive_without_a_summary_has_no_target_key():
    """Nothing is known about what it captured, so it must never be grouped
    -- and therefore never auto-selected for deletion."""
    assert archive_target_key(None) is None
    assert archive_target_key({"status": "unavailable", "detail": "boom"}) is None


def test_superseded_selects_every_attempt_but_the_newest_per_target(tmp_path):
    _seed(tmp_path, "export-h-20260101-000000-a", _summary("Help & Support"), mtime=1000)
    _seed(tmp_path, "export-h-20260102-000000-b", _summary("Help & Support"), mtime=2000)
    newest = "export-h-20260103-000000-c"
    _seed(tmp_path, newest, _summary("Help & Support"), mtime=3000)
    other = "export-h-20260101-000000-z"
    _seed(tmp_path, other, _summary("General Discussion"), mtime=1500)

    superseded = superseded_names(list_archives(tmp_path))

    assert superseded == {"export-h-20260101-000000-a", "export-h-20260102-000000-b"}
    assert newest not in superseded, "the most recent attempt is always kept"
    assert other not in superseded, "a target with a single attempt is never superseded"


def test_superseded_never_includes_an_archive_of_unknown_content(tmp_path):
    _seed(tmp_path, "export-h-20260101-000000-a", None, mtime=1000)
    _seed(tmp_path, "export-h-20260102-000000-b", None, mtime=2000)

    assert superseded_names(list_archives(tmp_path)) == set()


def test_superseded_never_includes_a_demo_archive(tmp_path):
    """Demo runs have their own bulk delete; mixing them in here would make
    'delete superseded' quietly remove the demo data too."""
    _seed(tmp_path, "DEMO-FAKE-DATA-20260101-000000-a", _summary("Help & Support"), mtime=1000)
    _seed(tmp_path, "DEMO-FAKE-DATA-20260102-000000-b", _summary("Help & Support"), mtime=2000)

    assert superseded_names(list_archives(tmp_path)) == set()
