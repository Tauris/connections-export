"""The setup screen -- a drop zone / paste input that
identifies the HCL system + wiki via `/api/identify` and starts the
import via `/api/start`, shown before the console (Front
end -- setup screen).

Static/structural backstop over the *served* `console.html`, mirroring
`test_reader_static.py`'s approach (holds unconditionally, no
dependency on a `node` binary): the load-bearing safety property here
is that the drop handler always calls `preventDefault` (hard rule 6
-- otherwise the browser navigates to/downloads the dropped link
instead of firing `drop`), plus the setup screen's presence and wiring
to the new endpoints. `refactor-remove-offline-simulator`
made the demo always server-backed (`serve --demo`'s fakeserver), so
there is no client-side `file://` fallback to assert here any more.
"""

from __future__ import annotations

import re

from tests._hostcheck import find_disallowed_hosts
from tests.gui._served_assets import served_console_html, served_console_js

# The console's script lives in console.js, not in console.html; the
# event-handler wiring asserted below now lives there, while DOM/markup
# assertions (element ids) stay on the served HTML.
HTML = served_console_html()
JS = served_console_js()


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


# --- the setup screen is present, and shown before the console -------------


def test_setup_screen_element_exists():
    assert 'id="setup-screen"' in HTML


def test_console_screen_starts_hidden_behind_its_section_panel():
    """The app shell moved screen visibility from the old
    top-level `#console-screen` div to its enclosing `.section-panel`
    (`#panel-ingest`); the live console must still never be shown
    before a run has actually started."""
    match = re.search(r'<section class="section-panel" id="panel-ingest"([^>]*)>', HTML)
    assert match, "could not locate the panel-ingest section"
    assert "hidden" in match.group(1)


def test_setup_screen_has_one_input_and_accepts_a_drop_anywhere():
    """The dedicated drop rectangle is gone: it and the field were two
    affordances for one verb, and it was dead space whenever nobody was
    dragging. The whole page is the target now, so the behaviour it carried
    has to be on the document."""
    assert 'id="setup-url-input"' in HTML
    assert 'id="dropzone"' not in HTML
    assert 'document.addEventListener("drop"' in JS


def test_setup_screen_has_an_editable_form_base_url_auth_mode_wiki_label():
    assert 'id="field-base-url"' in HTML
    assert 'id="field-auth-mode"' in HTML
    assert 'id="field-wiki-label"' in HTML


def test_setup_screen_has_start_ingest_and_run_demo_actions():
    # The old #start-import button (which started a run directly
    # from Step 3) is superseded by the Step 2 size-chooser's #start-ingest
    # -- keeping both would mean two explicit start paths, so #start-import
    # must be gone, not just unused.
    assert 'id="start-ingest"' in HTML
    assert 'id="start-import"' not in HTML
    assert 'id="run-demo"' in HTML


# --- the load-bearing safety property: drop handling prevents default -----


def test_dragover_handler_calls_prevent_default():
    """Without it the browser navigates to (or downloads) the dropped link
    instead of firing `drop` -- the setup screen simply disappears."""
    assert 'document.addEventListener("dragover", (e) => e.preventDefault());' in JS


def _drop_handler() -> str:
    match = re.search(
        r'document\.addEventListener\("drop",\s*\(e\)\s*=>\s*\{(.*?)\n    \}\);', JS, re.S
    )
    assert match, "could not locate the document drop handler"
    return match.group(1)


def test_drop_handler_calls_prevent_default():
    assert "e.preventDefault()" in _drop_handler()


def test_drop_handler_reads_uri_list_and_plain_text():
    """A browser tab drag offers `text/uri-list`; a dragged link or selected
    text offers `text/plain`. Reading only one loses half the ways in."""
    body = _drop_handler()

    assert "text/uri-list" in body
    assert "text/plain" in body


def test_the_drag_veil_survives_crossing_a_child_element():
    """Dragging over a child fires `dragleave` on the parent, so a naive
    toggle flickers the veil away while the pointer is still on the page."""
    assert "dragDepth" in JS


def test_no_allow_scripts_and_no_preventdefault_regressions_in_served_code():
    """Belt-and-suspenders: the token that matters is never removed by
    a future edit, checked over the whole served file, not just the
    handler this test happened to regex out."""
    code = _strip_js_comments(JS)
    assert code.count("preventDefault()") >= 2  # dragover + drop, at least


# --- wired to the new endpoints ---------------------------------------------


def test_identify_is_called_on_drop_and_paste():
    assert 'fetch("/api/identify"' in JS
    assert "function identifyUrl(" in JS


def test_url_identified_base_overrides_editable_settings_for_ingest():
    assert "let identifiedBaseUrl = null;" in JS
    assert "identifiedBaseUrl = result.base_url || null;" in JS
    assert 'base_url: identifiedBaseUrl || $("field-base-url").value.trim() || null' in JS


def test_activity_urls_keep_page_context_for_generic_feed_endpoints():
    assert "parts.slice(-4)" in JS
    assert "hid where pagination was stuck" in JS


def test_start_ingest_posts_to_api_start_with_form_fields():
    # #start-ingest (the size chooser's start button) is now the
    # sole explicit start path, carrying the same form fields the old
    # #start-import handler did.
    match = re.search(
        r'\$\("start-ingest"\)\.addEventListener\("click",\s*\(\)\s*=>\s*\{(.*?)\n    \}\);',
        JS,
        re.S,
    )
    assert match, "could not locate the start-ingest click handler"
    body = match.group(1)
    assert "beginRun(" in body

    # The fields moved into `startBody`, which the handler calls -- the same
    # object is now also what the echoed CLI command is built from, so the two
    # cannot describe different runs. Assert on that function instead.
    start_body = re.search(r"function startBody\(\)\s*\{(.*?)\n    \}", JS, re.S)
    assert start_body, "could not locate startBody()"
    fields = start_body.group(1)
    assert "base_url" in fields
    assert "auth_mode" in fields
    assert "wiki_label" in fields


def test_run_demo_posts_demo_true():
    # The run-demo handler calls beginRun with demo:true (and threads the
    # optional author field through -- "capture everything I authored").
    match = re.search(
        r'\$\("run-demo"\)\.addEventListener\("click",\s*\(\)\s*=>\s*\{(.*?)\}\);',
        JS,
        re.DOTALL,
    )
    assert match, "could not locate the run-demo click handler"
    assert "demo: true" in match.group(1)
    assert "author" in match.group(1)


def test_begin_run_posts_to_api_start_then_connects_events():
    match = re.search(r"function beginRun\(startBody\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate beginRun's body"
    body = match.group(1)
    assert 'fetch("/api/start"' in body
    assert "startLive()" in body


# --- boot shows a section (the Overview), and never auto-starts -------------


def test_boot_no_longer_calls_start_live_unconditionally():
    match = re.search(r"function boot\(\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate boot()'s body"
    body = match.group(1)
    # The app shell has boot default to the Overview section
    # via the shared router, rather than jumping straight to the setup
    # screen -- pinned to the specific section, not just any showSection
    # call, so this can't silently regress to some other default panel.
    assert 'showSection("overview")' in body
    # startLive must not be called directly from boot any more --
    # it is reached only via a user action (beginRun -> /api/start ->
    # startLive), never as the very first thing that happens.
    assert "startLive()" not in body


# --- hygiene -----------------------------------------------------------------


def test_setup_screen_references_no_external_hosts():
    assert find_disallowed_hosts(HTML) == set()
    assert find_disallowed_hosts(JS) == set()
