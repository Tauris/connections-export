"""An in-export link opens what it names, whatever kind of thing that is.

`crosslink` resolves links to wiki pages, blog posts, forum topics and
Highlights pages -- everything an export captured. The reader turns each
resolved link into a button carrying the target's id, and then looked that id
up in `REAL_MODEL.wikis` alone.

So a link to a post, a topic or a Highlights page produced a button that
looked live, said "in this archive", and did nothing when clicked. Silence is
the worst of the three possible behaviours: a dead link at least tells you it
is dead.
"""

from __future__ import annotations

import re

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def _navigate() -> str:
    """The navigation table and the function that walks it, together: the
    table is what says which components are reachable."""
    match = re.search(r"const NAVIGABLE = \[.*?function navigateToPageId\(.*?\n  \}\n", JS, re.S)
    assert match, "navigateToPageId and its table not found in console.js"
    return match.group(0)


def test_a_link_to_a_wiki_page_opens_it():
    assert "openReaderPageReal(" in _navigate()


def test_a_link_to_a_blog_post_opens_it():
    assert "openReaderPostReal(" in _navigate()


def test_a_link_to_a_forum_topic_opens_it():
    assert "openReaderTopicReal(" in _navigate()


def test_a_link_to_a_highlights_page_opens_it():
    assert "openReaderRichContentReal(" in _navigate()


def test_every_container_the_model_can_hold_is_searched():
    """Adding a component without adding it here is exactly how this broke."""
    body = _navigate()
    assert "REAL_MODEL[collection]" in body, "the table is what decides, so it must be walked"
    for collection in ("wikis", "blogs", "forums", "rich_content"):
        assert f'"{collection}"' in body, collection
