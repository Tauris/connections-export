"""Completeness + orphan detection (`report.py`), unit-level."""

from connections_export.adapters.model import NavNode
from connections_export.archive.store import ArchiveReport
from connections_export.crawler import report


def test_detect_orphans_finds_page_whose_parent_is_missing():
    nodes = [
        NavNode(id="a", parent=None, ordinal=0, is_root=True),
        NavNode(id="b", parent="ghost", ordinal=0, is_root=False),
    ]

    assert report.detect_orphans(nodes) == ["b"]


def test_detect_orphans_top_level_pages_are_not_orphans():
    nodes = [NavNode(id="a", parent=None, ordinal=0, is_root=True)]

    assert report.detect_orphans(nodes) == []


def test_detect_orphans_parent_present_is_not_orphan():
    nodes = [
        NavNode(id="a", parent=None, ordinal=0, is_root=True),
        NavNode(id="b", parent="a", ordinal=0, is_root=False),
    ]

    assert report.detect_orphans(nodes) == []


def test_detect_orphans_empty_string_parent_is_not_orphan():
    # The nav feed represents "no parent" as "" (see NavNode/parse_nav_feed);
    # an empty-string parent must be treated the same as None, never
    # looked up as a literal id.
    nodes = [NavNode(id="a", parent="", ordinal=0, is_root=True)]

    assert report.detect_orphans(nodes) == []


def test_build_report_assembles_archive_counts_plus_orphans():
    archive_report = ArchiveReport(
        counts={"ok": 3, "http_error": 1},
        failures_by_category={"http_error": 1},
        possibly_truncated=["https://fake/x"],
    )

    crawl_report = report.build_report(archive_report, orphans=["p1"], pages_crawled=3)

    assert crawl_report.counts == {"ok": 3, "http_error": 1}
    assert crawl_report.failures_by_category == {"http_error": 1}
    assert crawl_report.possibly_truncated == ["https://fake/x"]
    assert crawl_report.orphans == ["p1"]
    assert crawl_report.pages_crawled == 3
