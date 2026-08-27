"""One Highlights page, through the endpoints the preview and export use.

The scoper is unit-tested; this is the path a user actually triggers -- the
live preview asking for one tile, and the export dropdown asking for the open
item. Both go through `/api/pdf`, and a kind the server does not narrow
correctly comes back as a blank tile or a whole-archive PDF, neither of which
announces itself.
"""

from __future__ import annotations

import pytest

from connections_export.derive.model import (
    DerivedForum,
    DerivedRichContent,
    DerivedRichContentPage,
    Interchange,
)
from connections_export.derive.scope import scope_interchange, single_node_interchange
from connections_export.pdf.html import render_html


def _no_blobs(_digest: str) -> bytes | None:
    return None


@pytest.fixture
def model() -> Interchange:
    return Interchange(
        forums=[DerivedForum(id="F", title="Support")],
        rich_content=[
            DerivedRichContent(
                id="C",
                title="Highlights",
                page_ids=["p1", "p2"],
                pages={
                    "p1": DerivedRichContentPage(
                        id="p1",
                        resource_id="p1",
                        title="Welcome",
                        content_html="<p>Only this one</p>",
                    ),
                    "p2": DerivedRichContentPage(
                        id="p2",
                        resource_id="p2",
                        title="Who to ask",
                        content_html="<p>Not this one</p>",
                    ),
                },
                placed=3,
                initialized=2,
            )
        ],
    )


def test_a_preview_tile_renders_only_its_own_page(model):
    scoped = single_node_interchange(model, kind="rich_content_page", target_id="p1")

    html = render_html(scoped, blob_bytes=_no_blobs)

    assert "Only this one" in html
    assert "Not this one" not in html


def test_a_preview_tile_does_not_carry_the_communitys_counts(model):
    """The whole-area render adds "1 further rich content area was placed...".
    On a single-page tile that sentence is about the community, not the tile."""
    scoped = single_node_interchange(model, kind="rich_content_page", target_id="p1")

    html = render_html(scoped, blob_bytes=_no_blobs)

    assert "never written in" not in html


def test_exporting_the_open_item_gives_that_page_alone(model):
    scoped = scope_interchange(model, kind="rich_content_page", target_id="p2")

    html = render_html(scoped, blob_bytes=_no_blobs)

    assert "Not this one" in html
    assert "Only this one" not in html
    assert "Support" not in html, "the forum rode along with a scoped export"


def test_the_whole_area_still_reports_the_gap(model):
    """The counts belong on the container render and must not have been lost
    while making the single-page scope drop them."""
    html = render_html(model, blob_bytes=_no_blobs)

    assert "never written in" in html
