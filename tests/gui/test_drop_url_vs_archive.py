"""A dropped link is a link, even when the desktop attaches a file to it.

Reported: dropping a community URL was refused as "not a zip file".

Dragging a link hands the page BOTH a `text/uri-list` and, on Windows and some
Linux desktops, a shortcut FILE (`connections.url`). The drop handler looked at
`dataTransfer.files` first, so a link arrived as a file, went to the upload
endpoint, and was correctly told it was not a zip -- an answer to a question
nobody had asked.

Text first, therefore: what was dropped is decided by what it SAYS, and only a
drop with no usable link is treated as a file.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def test_the_link_is_read_before_the_files():
    handler = JS[JS.index('document.addEventListener("drop"') :][:2600]
    text_at = handler.index("looksLikeArchiveDrop")
    files_at = handler.index("dt.files")
    assert text_at < files_at, "the file branch still runs before the link is considered"


def test_a_deployment_url_still_starts_a_capture():
    handler = JS[JS.index('document.addEventListener("drop"') :][:2600]
    assert "identifyUrl(url)" in handler
    # ...and that branch is reachable before any file handling.
    assert handler.index("identifyUrl(url)") < handler.index("openDroppedArchive({ file:")


def test_a_dropped_file_that_is_not_an_archive_says_what_it_was():
    """ "Not a zip" is the right answer to the wrong question when the user
    dropped a link; when they really did drop a file, it should name it."""
    assert "isZipFile" in JS
    assert "is not an archive" in JS


def test_a_zip_file_is_still_uploaded():
    assert "openDroppedArchive({ file:" in JS
