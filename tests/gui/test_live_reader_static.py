"""Static/structural backstop for the
front-end pieces that make content browsable while a run is still
importing -- asserted over the *served* `console.html` (no `node`
dependency; `test_reader_pure_js.py` covers behaviour when `node` is
present). The load-bearing shape: a prominent top reader button, the
button enabled on the first `page_derived`, and the reader reading the
`X-HCL-Model-State` header to keep refreshing while partial.
"""

from __future__ import annotations

from tests._hostcheck import find_disallowed_hosts
from tests.gui._served_assets import served_console_html, served_console_js

# The console's script lives in console.js, not in console.html; the
# event-wiring/behavior asserted below now lives there, while the two
# static button attributes stay on the served HTML.
HTML = served_console_html()
JS = served_console_js()


def test_a_prominent_top_reader_button_exists():
    # In the topbar (top of the console), not only the footer control.
    assert 'id="open-reader-top"' in HTML
    assert 'class="btn primary reader-cta"' in HTML


def test_both_reader_buttons_are_wired_to_enter_the_reader():
    # Arrow-wrapped so the click Event is not handed in as `opts`; both
    # in-flow buttons arrive from a run, so they reveal the nav path.
    wired = '.addEventListener("click", () => enterReader({ reveal: true }))'
    assert '$("open-reader")' + wired in JS
    assert '$("open-reader-top")' + wired in JS


def test_reader_is_enabled_on_the_first_imported_page():
    # livePageDerived enables the reader (it starts disabled at run start).
    assert "function setReaderEnabled(" in JS
    assert "setReaderEnabled(false)" in JS  # disabled while there's nothing to read
    assert "setReaderEnabled(true)" in JS  # enabled once a page arrives


def test_reader_reads_the_model_state_header_and_refreshes_while_partial():
    assert 'res.headers.get("X-HCL-Model-State")' in JS
    assert "function scheduleLiveRefresh(" in JS
    assert '"partial"' in JS


def test_a_live_page_node_can_be_opened_in_the_reader():
    assert "function findRealPageByLiveNode(" in JS
    assert "enterReaderReal(" in JS


def test_the_live_drawer_renders_a_pages_real_content_inline():
    # Clicking a page node in the live tree shows its rendered content
    # (body/comments/attachments) in the drawer, filled from the live model.
    assert "function livePageContentHtml(" in JS
    assert "function loadLiveDrawerContent(" in JS
    assert 'id="d-live-content"' in JS
    # The inline body still goes through the sandbox + image resolver (the
    # drawer resolves images into `rendered`, then sandboxes it).
    assert "resolveBodyImages(page.content_html" in JS
    assert "sandboxedBodyMarkup(rendered)" in JS
    assert "renderCommentsReal(page)" in JS
    assert "renderAttachmentsReal(page)" in JS


def test_served_console_references_no_external_hosts():
    assert find_disallowed_hosts(HTML) == set()
    assert find_disallowed_hosts(JS) == set()
