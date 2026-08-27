"""#24: the archive list must not grow without bound -- every run
(especially every demo run) leaves behind a new timestamped archive that
persists forever, so an unbounded list eventually swamps the page.

The cap was 12 while this list lived on the Overview, where it was one
short strip among many. It now has its own Archives panel, whose whole job
is showing archives, and rendering a row no longer costs an HTTP request
(the listing carries each summary), so a page-sized cap is affordable and a
12-row window made selecting a range across hundreds of runs impractical.
The bulk-selection helpers deliberately operate over every archive the
server listed, not just the rendered ones, so the cap bounds rendering
only. Static/structural backstop over the served `console.js`, in the
style of `test_live_ingest_parity_static.py` (no `node` dependency: this
function is DOM-manipulating, not part of the DOM-free `READER-PURE`
block those tests drive under real Node).
"""

from __future__ import annotations

import re

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def _function_body(name: str, js: str = JS) -> str:
    match = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{(.*?)\n  \}", js, re.S)
    assert match, f"could not locate {name}'s body"
    return match.group(1)


def test_recent_archives_list_is_capped_at_a_page():
    match = re.search(r"RECENT_ARCHIVES_LIMIT\s*=\s*(\d+)", JS)
    assert match, "RECENT_ARCHIVES_LIMIT constant not found"
    assert match.group(1) == "200"


def test_load_recent_archives_caps_the_rendered_list():
    body = _function_body("loadRecentArchives")
    assert "items.slice(0, RECENT_ARCHIVES_LIMIT)" in body


def test_load_recent_archives_shows_loading_and_failure_states():
    body = _function_body("loadRecentArchives")
    assert "Reading recent archives" in body
    assert "Could not read recent archives" in body
    assert "retry-recent-archives" in body


def test_load_recent_archives_shows_exact_directory_name():
    body = _function_body("loadRecentArchives")
    assert 'archiveName.className = "archive-name mono"' in body
    assert "archiveName.textContent = a.name" in body
    # Only the capped subset is rendered (`shown`), not the full `items`.
    assert "shown.forEach" in body
    assert "items.forEach" not in body


def test_load_recent_archives_shows_a_muted_overflow_line_when_capped():
    body = _function_body("loadRecentArchives")
    assert "items.length - shown.length" in body
    assert "extra > 0" in body
    assert "…and " in body and "more" in body
    # Muted styling, matching the other footnote-style text in this list.
    assert 'more.className = "foot-note"' in body
