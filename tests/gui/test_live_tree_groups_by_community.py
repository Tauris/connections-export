"""The live ingest tree groups by community, as the reader does.

The reader groups a finished model by community. The ingest could not: its
events name a wiki, a blog, a forum -- never the community those belong to,
and the marker that would say lives on a feed a scoped crawl never fetches.

But the console does not need the crawl to tell it. It is the thing that
asked: every component was ticked under a community in the picker, so the
mapping from "wiki:eng-handbook" to the community it belongs to is already in
the request it sent. That mapping is the grouping.

Insertion, not appending, because a demo run crawls app-by-app across the whole
set -- community A's wiki, then B's wiki -- so nodes for one community arrive
after nodes for another and must still land under their own heading.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def test_the_grouping_comes_from_the_request_the_console_sent():
    assert "liveCommunityByComponent" in JS
    assert "startBody.communities" in JS


def test_a_group_header_is_placed_under_its_community():
    assert "liveCommunityOf" in JS
    assert "communitygroup" in JS


def test_one_community_is_not_dressed_up_as_a_grouping():
    """A single-community run reads as it always did -- wiki then pages -- with
    no heading above it repeating what the title bar already says."""
    assert "if (liveCommunityCount <= 1) return null;" in JS


def test_nodes_are_inserted_under_their_community_not_appended():
    """Appending would put community B's wiki under community A's heading the
    moment a run crawls app-by-app rather than community-by-community."""
    assert "liveCommunityTail" in JS
    assert "insertBefore" in JS


def test_every_live_container_kind_can_be_grouped():
    """Wiki, blog, forum, files and Highlights -- the same five the picker
    offers. A kind the picker can tick and the tree cannot place would group
    four apps and silently flatten the fifth."""
    for key in ('"wiki:"', '"blog:"', '"forum:"', '"files:"', '"rich_content:"'):
        assert key in JS
