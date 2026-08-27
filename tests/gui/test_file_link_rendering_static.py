"""A resolved file link reaches its file.

Marking a link `in_export` is bookkeeping; the promise is that clicking it
still works with no deployment in reach. That means the reader has to
route a link naming a captured DOCUMENT to that document's bytes, the way it
already routes a link naming a captured page to that page.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def test_a_link_to_a_captured_file_is_routed_to_it():
    assert "link.target_file_id" in JS
    assert '"in_export_file"' in JS


def test_a_link_to_a_captured_page_still_routes_to_the_page():
    """The existing case must be untouched: a page link is answered by the
    page, not swept into the file path."""
    assert "link.target_page_id" in JS
    assert '{ kind: "in_export", targetPageId: link.target_page_id' in JS


def test_an_unresolved_deployment_link_still_says_where_it_points():
    assert 'label: "original system"' in JS
