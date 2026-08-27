"""A single Highlights page must be scopable, like a page/post/topic.

The ingest preview renders one tile per entity and the reader's export offers
"just this one". Both go through the single-item scopers, and a kind that is
not in them yields an empty interchange -- which the preview shows as a blank
tile and the export as an empty PDF, neither of which says what went wrong.

Rich Content pages were the only leaf entity with no scoper: capturable,
readable, and impossible to preview or export on their own.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedForum,
    DerivedRichContent,
    DerivedRichContentPage,
    Interchange,
)
from connections_export.derive.scope import (
    scope_interchange,
    select_interchange,
    single_node_interchange,
)

KIND = "rich_content_page"


def _model() -> Interchange:
    return Interchange(
        forums=[DerivedForum(id="F", title="Support")],
        rich_content=[
            DerivedRichContent(
                id="C",
                title="Highlights",
                page_ids=["p1", "p2"],
                pages={
                    "p1": DerivedRichContentPage(
                        id="p1", resource_id="p1", title="Welcome", content_html="<p>a</p>"
                    ),
                    "p2": DerivedRichContentPage(
                        id="p2", resource_id="p2", title="Who to ask", content_html="<p>b</p>"
                    ),
                },
                placed=3,
                initialized=2,
            )
        ],
    )


def test_one_page_can_be_scoped_on_its_own():
    scoped = scope_interchange(_model(), kind=KIND, target_id="p1")

    assert len(scoped.rich_content) == 1
    assert scoped.rich_content[0].page_ids == ["p1"]
    assert "p2" not in scoped.rich_content[0].pages


def test_scoping_one_page_drops_the_other_apps():
    scoped = scope_interchange(_model(), kind=KIND, target_id="p1")

    assert scoped.forums == []


def test_the_preview_gets_exactly_one_tile_for_one_page():
    """`single_node_interchange` feeds the append-only preview, where each
    entity becomes exactly one tile. Two pages in one tile would duplicate
    content across the strip."""
    scoped = single_node_interchange(_model(), kind=KIND, target_id="p2")

    assert scoped.rich_content[0].page_ids == ["p2"]
    assert scoped.rich_content[0].pages["p2"].title == "Who to ask"


def test_a_scoped_page_keeps_its_body():
    scoped = single_node_interchange(_model(), kind=KIND, target_id="p1")

    assert scoped.rich_content[0].pages["p1"].content_html == "<p>a</p>"


def test_the_placed_counts_are_not_carried_into_a_single_page_scope():
    """A one-page tile that still announced "3 areas placed, 2 written in"
    would be describing the community, not the thing on screen."""
    scoped = single_node_interchange(_model(), kind=KIND, target_id="p1")

    highlights = scoped.rich_content[0]
    assert highlights.placed == 1
    assert highlights.initialized == 1


def test_an_unmatched_page_yields_nothing_rather_than_everything():
    scoped = scope_interchange(_model(), kind=KIND, target_id="does-not-exist")

    assert scoped.rich_content == []
    assert scoped.forums == []


def test_a_page_can_also_be_named_in_a_multi_selection():
    """`--include rich_content_page:<id>` alongside other selections, the way
    the export dropdown builds a fine-grained scope."""
    scoped = select_interchange(_model(), [(KIND, "p1"), ("forum", "F")])

    assert scoped.rich_content[0].page_ids == ["p1"]
    assert [f.id for f in scoped.forums] == ["F"]
