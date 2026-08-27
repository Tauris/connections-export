"""The manual has one source, and it is in the page before the tab opens.

`docs/manual.md` is both the document you read in the repository and the
console's Manual tab. These tests hold that arrangement in place: that
the text is not duplicated back into `console.html`, and that it is
substituted in server-side rather than fetched by the page -- a tab that
fetched its own text would open empty and fill in a moment later.
"""

from __future__ import annotations

from connections_export.gui.manual import (
    MANUAL_DOC_PATH,
    MANUAL_PLACEHOLDER,
    manual_html,
)
from connections_export.gui.support import CONSOLE_HTML

from ._served_assets import served_console_html


def test_the_manual_source_is_a_markdown_document():
    assert MANUAL_DOC_PATH.is_file()
    assert MANUAL_DOC_PATH.name == "manual.md"
    assert MANUAL_DOC_PATH.read_text(encoding="utf-8").startswith("# Manual\n")


def test_console_html_holds_a_placeholder_rather_than_a_second_copy():
    """The manual lives in one file. If it were also written out in the
    markup, the two would drift apart -- which is the whole reason for
    the single source."""
    markup = CONSOLE_HTML.read_text(encoding="utf-8")
    assert MANUAL_PLACEHOLDER in markup
    assert 'id="man-intro"' not in markup
    assert "The archive format" not in markup


def test_the_served_page_already_contains_the_manual():
    """Rendered into the document before it is sent: no request, and so
    no empty panel while one is in flight."""
    served = served_console_html()
    assert MANUAL_PLACEHOLDER not in served
    assert 'id="man-intro"' in served
    assert "<h2" in served and "The archive format" in served


def test_every_contents_entry_resolves_to_a_section():
    """The contents list is written by hand; the ids it points at come
    from the Markdown. A renamed section would otherwise leave a link
    that scrolls nowhere."""
    import re

    html = manual_html()
    targets = set(re.findall(r'id="([^"]+)"', html))
    nav = html.split("</nav>")[0]
    links = re.findall(r'href="#([^"]+)"', nav)
    assert links, "the manual should offer a contents list"
    assert not [name for name in links if name not in targets]
