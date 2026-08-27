"""The reader's Highlights view: static backstop over the served console.

A Highlights page is authored HTML from the deployment, so the property that
matters is the same one the wiki reader has: the body goes through the
sandboxed render path (`readerBody`), never into a live-markup sink in the
trusted top-level document. Everything else here is about not hiding the gap
between what the community placed and what was captured.
"""

from __future__ import annotations

import re

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def _view() -> str:
    match = re.search(r"function openReaderRichContentReal\(.*?\n  \}\n", JS, re.S)
    assert match, "openReaderRichContentReal not found in console.js"
    return match.group(0)


def test_the_reader_has_a_highlights_view():
    assert "function openReaderRichContentReal(" in JS


def test_the_nav_offers_a_highlights_section():
    assert '"HIGHLIGHTS"' in JS
    assert "openReaderRichContentReal(container.id, pageId)" in JS


def test_the_body_goes_through_the_sandboxed_render():
    """The one load-bearing safety property, shared with every other reader
    view: an authored body is never assigned into the trusted document."""
    view = _view()

    assert "readerBody(page.content_html" in view
    assert ".innerHTML = page.content_html" not in view


def test_titles_and_metadata_are_escaped():
    view = _view()

    assert "escapeHtml(page.author)" in view
    assert "escapeHtml(page.version_label)" in view


def test_areas_nobody_wrote_in_are_shown_in_the_nav():
    """The count of placed-but-empty widgets is the only signal that a
    community set aside a space and never used it."""
    assert "placed but never written in" in JS
    assert "(container.placed || 0) - (container.initialized || 0)" in JS


def test_reopening_after_a_refresh_returns_to_the_page():
    assert "if (realRichContentId && realRichPageId)" in JS


def test_rich_content_events_enable_the_reader():
    assert "function liveRichContentDerived(evt)" in JS
    assert 'case "rich_content_derived": return liveRichContentDerived(evt);' in JS
    assert "setReaderEnabled(true)" in JS[JS.index("function liveRichContentDerived") :]


def test_rich_content_events_populate_the_live_hierarchy():
    view = JS[JS.index("function liveRichContentDerived") :]
    assert '_liveGroupNode("rich_content:" + evt.community_uuid' in view
    assert 'kind: "rich_content"' in view
    assert "addTreeNode(it)" in view


def test_rich_content_hierarchy_items_open_their_reader_page():
    assert 'it.kind === "rich_content"' in JS
    assert 'node.kind === "rich_content"' in JS
    assert 'deep.app === "rich_content"' in JS


def test_highlights_pages_are_counted_in_the_type_strip():
    assert 'rich_content: "highlights pages"' in JS


def test_the_picker_describes_the_component():
    assert re.search(r'rich_content:\s*"[^"]+"', JS)


def test_the_picker_says_when_areas_were_never_written_in():
    """The count on a checkbox must not read as the whole story: an owner can
    place a rich content area and never write in it, and the picker is where a
    user decides what they are getting."""
    assert "never written in" in JS
    assert "component.placed" in JS


def test_the_gap_is_only_claimed_when_both_numbers_are_known():
    """A missing `placed` (an older server answer, or another component kind)
    must not render "NaN areas never written in"."""
    assert "Number.isFinite(placed)" in JS


def test_the_picker_explains_a_missing_highlights_component():
    """Rich Content is addressed by the community uuid, not discovered by
    name, so its absence leaves no half-built row to hint at a cause. On a
    community whose front page the user is looking at, showing nothing is the
    worst possible answer."""
    assert "data.rich_content_note" in JS
    assert "Highlights (rich content): " in JS


# --- the ingest preview and export scopes -----------------------------------
#
# Everything a page/post/topic can do on its own, a Highlights page must be
# able to do too: appear as its own tile in the live preview, be the "current
# item" the export dropdown offers, and be tickable in the fine-grained picker.
# A kind missing from any of these is content the user can see and cannot
# single out.


def test_a_highlights_page_gets_its_own_preview_tile():
    """`lpEntityOrder` drives the append-only preview strip -- one tile per
    entity. A kind absent from it never renders during an ingest."""
    view = JS[JS.index("function lpEntityOrder") :]
    view = view[: view.index("\n  }")]

    assert 'kind: "rich_content_page"' in view
    assert "Highlights \\u00b7 " in view or "Highlights · " in view


def test_the_export_dropdown_can_scope_to_the_open_highlights_page():
    """`currentReaderScope` is what relabels and enables "Current item". With
    a Highlights page open it returned null, so the option stayed disabled and
    silently fell back to the whole archive."""
    view = JS[JS.index("function currentReaderScope") :]
    view = view[: view.index("\n  }")]

    assert "realRichContentId && realRichPageId" in view
    assert 'kind: "rich_content_page"' in view


def test_the_fine_grained_picker_offers_the_area_and_its_pages():
    view = JS[JS.index("function renderPdfPicker") :]
    view = view[: view.index("\n  }\n")]

    assert '"rich_content:" + container.id' in view
    assert '"rich_content_page:" + pid' in view


def test_individual_highlights_pages_start_unticked():
    """They overlap the whole-area row. Ticking both asks for the same content
    twice, which is why wiki subtrees behave the same way."""
    assert 'input[value^="rich_content_page:"]' in JS
