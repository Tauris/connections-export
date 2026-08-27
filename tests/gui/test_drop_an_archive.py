"""Dropping an archive reads it; dropping a deployment URL captures from it.

The console has always treated a drop as "a URL to capture from". An archive
handed to you is a drop too -- a folder, a zip, a file synced out of OneDrive
-- and it means the opposite: read this, do not crawl anything.

The two are told apart by what was dropped, not by where it landed, so there
is no second drop target to aim at and no mode to be in.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def test_a_dropped_file_is_read_rather_than_crawled():
    """A File in the drop means the browser has the bytes, which only happens
    for something on disk."""
    assert "dt.files" in JS
    assert "/api/upload-archive" in JS


def test_a_dropped_folder_or_zip_path_opens_where_it_lies():
    """`file:///...` is what a file manager puts in a drop. Nothing is copied
    for these -- the archive is read where it is."""
    assert "/api/open-external" in JS
    assert "file://" in JS


def test_a_zip_link_from_the_web_is_treated_as_an_archive_not_a_deployment():
    assert "looksLikeArchiveDrop" in JS


def test_an_ordinary_deployment_url_still_starts_the_capture_flow():
    """The existing behaviour, which must not become collateral damage."""
    assert "identifyUrl(url)" in JS


def test_the_reader_is_shown_once_an_archive_opens():
    assert "openDroppedArchive" in JS
    assert 'showSection("reader")' in JS


def test_a_refusal_is_reported_rather_than_swallowed():
    """A drop that does not open must say why: the alternative is a page that
    appears to ignore you."""
    start = JS.index("function openDroppedArchive")
    body = JS[start : start + 2000]
    assert "notify(" in body
