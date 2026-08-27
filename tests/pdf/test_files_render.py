"""A community's files in the PDF: an inventory, not a rendering.

A PDF cannot show a spreadsheet or a zip, and pretending otherwise would be
worse than saying plainly what the archive holds. So the library renders as a
listing -- and the one column that must never be guessed at is whether a
document's bytes actually came down.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedFile,
    DerivedFileFolder,
    DerivedFileLibrary,
    Interchange,
    ResolvedAsset,
)
from connections_export.pdf.html import _pluralize, render_html


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _library() -> DerivedFileLibrary:
    captured = DerivedFile(
        id="f1",
        name="Q1 Budget: draft?.xlsx",
        size=40118,
        version_label="2",
        author="M. Lindqvist",
        folder_ids=["F1"],
        asset=ResolvedAsset(
            original_href="https://fake/x",
            resolved_url="https://fake/x",
            present=True,
            scope="same",
            blob_hash="sha256:abc",
        ),
    )
    missing = DerivedFile(id="f2", name="Never Fetched.pdf", size=10)
    return DerivedFileLibrary(
        id="L",
        title="Community Files",
        file_ids=["f1", "f2"],
        files={"f1": captured, "f2": missing},
        folders=[DerivedFileFolder(id="F1", name="Finance", file_ids=["f1"])],
    )


def test_the_library_is_listed_with_its_names_verbatim():
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert "Community Files" in html
    # Escaped in the HTML, but the name itself is not altered.
    assert "Q1 Budget: draft?.xlsx" in html or "Q1 Budget: draft?.xlsx" in html.replace("&#", "")


def test_folder_size_version_and_author_are_shown():
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert "Finance" in html
    assert "39.2 KB" in html  # bytes are data; a size is information
    assert "M. Lindqvist" in html


def test_whether_the_bytes_were_captured_is_stated_per_file():
    """ "Listed but not captured" is exactly the thing a reader must not have to
    infer from silence."""
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert "in package" in html
    assert "not captured" in html


def test_the_reader_is_told_where_the_documents_are():
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert "files/" in html


def test_a_library_starts_its_own_page():
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert ".hcl-files" in html and "break-before: page" in html


def test_an_empty_library_renders_nothing():
    """Asserting on the CLASS NAME would always pass: the stylesheet ships in
    the same document, so `hcl-file-table` is present whether or not a table
    is. Check for the markup."""
    empty = DerivedFileLibrary(id="L", title="Files")

    html = render_html(Interchange(file_libraries=[empty]), blob_bytes=_no_blobs)

    assert '<section class="hcl-files"' not in html
    assert "<table" not in html


def test_the_cover_counts_files():
    html = render_html(Interchange(file_libraries=[_library()]), blob_bytes=_no_blobs)

    assert "2 files" in html


def test_library_pluralizes_properly():
    """A plain trailing `s` gave "3 file librarys" until a noun ending in `y`
    turned up on the cover."""
    assert _pluralize(1, "file library") == "1 file library"
    assert _pluralize(3, "file library") == "3 file libraries"
    assert _pluralize(2, "day") == "2 days"


def test_a_document_left_out_by_a_filter_does_not_read_as_a_failure():
    """ "Not captured" is what the listing says when bytes would not come down.
    A document nobody asked for, because the capture was filtered to one
    person, has to say something else -- otherwise a filtered export reads as
    a broken one."""
    excluded = DerivedFile(
        id="f9",
        name="Someone Else's Notes.docx",
        author="P. Novak",
        excluded_by_author_filter=True,
        asset=ResolvedAsset(
            original_href="https://fake/y",
            resolved_url="https://fake/y",
            present=False,
            scope="same",
        ),
    )
    library = _library()
    library.files[excluded.id] = excluded
    library.file_ids.append(excluded.id)

    html = render_html(Interchange(file_libraries=[library]), blob_bytes=_no_blobs)

    row = html[html.index("Someone Else&#x27;s Notes.docx") :][:400]
    assert "outside the filter" in row
    assert "not captured" not in row
