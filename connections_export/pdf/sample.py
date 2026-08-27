"""A small, fixed document for previewing how a PDF will look.

Rendering the real export to preview a font size is not a reasonable trade: a
demo export is seventy pages and takes tens of seconds. This is two pages that
exercise everything the style tokens touch -- a contents list, heading levels,
body prose, a link, a tag chip, and a comment thread -- so a change to any token
is visible in the preview.

Fixed content, not synthesized: a preview whose text moves around between
renders makes it harder to see what the setting actually changed.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedComment,
    DerivedPage,
    DerivedWiki,
    Interchange,
)

_BODY = (
    "<p>Body text at the base size, which everything else is measured against. "
    'A <a href="https://connections.example.corp/wikis">link</a> renders with its '
    "URL shown after it, at the secondary size.</p>"
    "<h2>A second-level heading</h2>"
    "<p>Enough prose to judge the line height and the measure. Whether a page "
    "feels crowded or generous is mostly this paragraph and the space around "
    "it, not the heading above.</p>"
    "<h3>A third-level heading</h3>"
    "<p>Shorter still, to show the smallest heading against the body.</p>"
)


def sample_interchange() -> Interchange:
    """Two pages, one with comments, exercising every styled element."""
    overview = DerivedPage(
        id="preview-overview",
        label="overview",
        title="Style preview",
        content_html=_BODY,
        author="J. Doe",
        modified="2026-01-01T09:00:00Z",
        tags=["preview", "typography"],
        child_ids=["preview-detail"],
        comments=[
            DerivedComment(
                id="c1",
                author="R. Delgado",
                created="2026-01-01T10:00:00Z",
                content_html="<p>A comment, to show the thread rule and the meta line.</p>",
            ),
            DerivedComment(
                id="c2",
                author="M. Lindqvist",
                created="2026-01-01T11:00:00Z",
                content_html="<p>A reply beneath it.</p>",
            ),
        ],
    )
    detail = DerivedPage(
        id="preview-detail",
        label="detail",
        title="A nested page",
        content_html="<p>A child page, so the contents list shows its indent.</p>",
        parent_id="preview-overview",
    )
    wiki = DerivedWiki(
        id="preview",
        label="preview",
        title="Appearance preview",
        root_page_ids=["preview-overview"],
        pages={"preview-overview": overview, "preview-detail": detail},
    )
    return Interchange(wikis=[wiki])
