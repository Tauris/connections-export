"""A run captures a set of communities; one is the special case, not the shape.

`derive/crosslink.py` upgrades a link from one community to another only when
both are in the SAME export, so "which communities" is a property of a run,
and a run that captures one is a list of length one.
"""

from __future__ import annotations

from connections_export.gui.requests import CommunitySelection, _StartRequest
from connections_export.gui.routes.run import selected_communities


def test_the_legacy_single_community_shape_still_works():
    """The console posts this today, and a saved CLI invocation may too. It
    reads as a list of one rather than as a second code path."""
    body = _StartRequest(
        app="community",
        community_uuid="aaa",
        community_components=["wiki:eng-handbook", "files:aaa"],
    )

    selected = selected_communities(body)

    assert len(selected) == 1
    assert selected[0].uuid == "aaa"
    assert selected[0].components == ["wiki:eng-handbook", "files:aaa"]


def test_a_list_of_communities_comes_back_in_order():
    body = _StartRequest(
        app="community",
        communities=[
            CommunitySelection(uuid="aaa", title="Parent", components=["wiki:w1"]),
            CommunitySelection(uuid="bbb", title="Child", components=["forum:f1", "files:bbb"]),
        ],
    )

    selected = selected_communities(body)

    assert [c.uuid for c in selected] == ["aaa", "bbb"]
    assert selected[1].components == ["forum:f1", "files:bbb"]


def test_a_community_with_nothing_ticked_is_not_captured():
    """Unticking everything is how a community is removed -- no second
    control that can disagree with the checkboxes."""
    body = _StartRequest(
        app="community",
        communities=[
            CommunitySelection(uuid="aaa", components=["wiki:w1"]),
            CommunitySelection(uuid="bbb", components=[]),
        ],
    )

    assert [c.uuid for c in selected_communities(body)] == ["aaa"]


def test_the_list_wins_when_both_shapes_are_sent():
    """A client that learned the new shape is not also guessing with the old
    one; honouring both would capture a community twice into one archive."""
    body = _StartRequest(
        app="community",
        community_uuid="legacy",
        community_components=["wiki:old"],
        communities=[CommunitySelection(uuid="aaa", components=["wiki:w1"])],
    )

    assert [c.uuid for c in selected_communities(body)] == ["aaa"]


def test_no_selection_at_all_is_an_empty_list():
    assert selected_communities(_StartRequest(app="community")) == []
