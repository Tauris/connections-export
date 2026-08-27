"""The reader's Files view: static backstop over the *served* console assets.

A file library is the one thing in an archive with no readable document behind
it -- the payload is bytes, not prose. So the reader gives it a listing, and
the properties worth pinning are about honesty rather than layout: names shown
as the deployment had them, and a per-file statement of whether the content is
actually here.

Escaping matters more here than elsewhere. Every other reader view renders
untrusted body HTML inside the sandboxed iframe; this one puts server-supplied
strings (file names, folder names, authors) straight into the trusted
top-level document, so each one must go through `escapeHtml`.
"""

from __future__ import annotations

import re

from tests.gui._served_assets import served_console_css, served_console_js

JS = served_console_js()
CSS = served_console_css()


def _files_view() -> str:
    """The body of `openReaderFilesReal`, up to the next top-level function."""
    match = re.search(r"function openReaderFilesReal\(.*?\n  \}\n", JS, re.S)
    assert match, "openReaderFilesReal not found in console.js"
    return match.group(0)


def test_the_reader_has_a_files_view():
    assert "function openReaderFilesReal(" in JS


def test_the_nav_offers_a_files_section():
    assert '"FILES"' in JS
    assert "openReaderFilesReal(library.id)" in JS


def test_every_server_supplied_string_is_escaped():
    """A file NAME comes from the deployment and lands in the trusted
    document. `Q1 Budget <img onerror=...>.xlsx` is a legal file name."""
    view = _files_view()
    for field in ("file.name", "file.author", "file.version_label"):
        assert f"escapeHtml({field}" in view, field
    assert "escapeHtml(folderNames[id]" in view


def test_a_captured_file_links_to_its_bytes():
    view = _files_view()

    assert "blobUrl(file.asset.blob_hash)" in view
    assert "download=" in view


def test_a_missing_file_says_so_rather_than_looking_captured():
    """The failure this guards is a listing where an uncaptured document is
    indistinguishable from a captured one -- a silently incomplete archive."""
    view = _files_view()

    assert "fl-missing" in view
    assert "absenceLabel(file)" in view
    assert "not captured" in JS
    # Only the presence of the asset decides whether there is a link -- never
    # the file's metadata.
    assert "file.asset && file.asset.present" in view


def test_a_filtered_out_document_does_not_read_as_a_failed_one():
    """An author-filtered capture never asked for other people's documents, so
    saying "not captured" about them reports a loss that did not happen."""
    assert "function absenceLabel(" in JS
    assert "excluded_by_author_filter" in JS
    assert "outside the filter" in JS


def test_the_content_column_is_styled_distinctly():
    assert ".fl-missing" in CSS
    assert ".fl-have" in CSS
    assert ".fl-table" in CSS


def test_reopening_after_a_refresh_returns_to_the_library():
    """The live refresh re-opens whatever the reader had open. Without a Files
    branch it silently jumped back to a wiki page mid-run."""
    assert "if (realLibraryId) return openReaderFilesReal(realLibraryId);" in JS


def test_the_picker_describes_the_files_component():
    assert re.search(r'files:\s*"[^"]+"', JS), "no description for the files component"


def test_files_are_counted_in_the_type_strip():
    assert 'file: "files"' in JS
