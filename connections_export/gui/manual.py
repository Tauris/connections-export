"""The console's Manual, rendered from its single Markdown source.

The manual is written once, as `docs/manual.md`. That one file is both
the document you read in the source tree and the Manual tab in this
console -- so the two cannot drift apart, which is what happens as soon
as a manual exists twice.

The HTML is built here, on the server, and substituted into
`console.html` before the page is sent. It is deliberately not fetched
by the page after it loads: a tab that fetches its own text shows an
empty panel first and fills it in a moment later, and there is nothing
to fill in if the text is already in the document.

Resolution mirrors the interchange spec's: the copy shipped inside the
installed package first, the repository's own `docs/manual.md` second,
so this works both from a plain `pip install` and from a checkout.
"""

from __future__ import annotations

from pathlib import Path

import markdown

from connections_export.interchange.package import REPO_ROOT

#: Where the rendered manual is substituted into `console.html`.
MANUAL_PLACEHOLDER = "<!--MANUAL-->"

_PACKAGED_MANUAL = Path(__file__).parent.parent / "manual.md"
_REPO_MANUAL = REPO_ROOT / "docs" / "manual.md"
MANUAL_DOC_PATH = _PACKAGED_MANUAL if _PACKAGED_MANUAL.is_file() else _REPO_MANUAL

#: `tables` and `fenced_code` cover the reference tables and the
#: configuration samples; `attr_list` carries the one class the manual
#: sets on a paragraph.
#:
#: Section ids are NOT attr_list: they are `<a id>` anchors written into
#: the headings, because `{: #man-intro }` is Python-Markdown's syntax and
#: a forge rendering the same file shows it as punctuation in the heading.
#: The document is read in both places.
_EXTENSIONS = ["fenced_code", "tables", "attr_list"]

# Rendering is pure and the source does not change under a running
# server, so it is done once. Keyed on the file's modification time
# anyway, so editing the manual and reloading shows the edit -- the
# same reason the console's assets are served `no-store`.
_cache: tuple[float, str] | None = None


def manual_html() -> str:
    """The manual as HTML, ready to place inside `<article class="manual">`.

    Returns an empty string if the source is missing rather than
    failing the whole console: a manual that cannot be found should
    cost you the Manual tab, not the application.
    """
    global _cache
    path = MANUAL_DOC_PATH
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return ""
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]
    html = markdown.markdown(path.read_text(encoding="utf-8"), extensions=_EXTENSIONS)
    _cache = (stamp, html)
    return html


def console_document(console_html: Path) -> str:
    """`console.html` with the manual rendered into it."""
    return console_html.read_text(encoding="utf-8").replace(MANUAL_PLACEHOLDER, manual_html())
