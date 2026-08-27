"""The picker holds a set of communities, not one.

Static/structural over the served console, in the style of
`test_setup_screen.py`: holds unconditionally, with no browser needed.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
CSS = served_console_css()


def test_the_run_posts_the_whole_set():
    """Alongside the single-community fields, so a server that has not learned
    the new shape still runs the first community."""
    assert "communities: identifiedCommunity ? communitiesForRun() : []" in JS
    assert "community_components: identifiedCommunity ? selectedCommunityComponents() : []" in JS


def test_children_are_offered_rather_than_added():
    """Discovery supplies candidates; the set is the user's to choose."""
    assert "/api/subcommunities" in JS
    assert 'id="add-subcommunities"' in HTML
    assert 'id="subcommunity-candidates"' in HTML


def test_every_discovered_child_is_offered_by_name():
    """A count alone ("add its 2 sub-communities") asks the user to accept
    something they cannot see. Each candidate is listed with its own title and
    its own Add, so the set stays theirs to choose one at a time."""
    assert "subcommunity-candidates" in JS
    assert "child.title" in JS
    assert ".subcommunity-candidate" in CSS


def test_a_child_already_in_the_run_is_shown_as_added_rather_than_offered_again():
    assert "Added" in JS


def test_a_community_can_be_added_by_url_because_related_ones_are_discoverable_from_nowhere():
    assert 'id="add-community-url"' in HTML
    assert "communityUuidFromInput" in JS


def test_a_url_that_carries_no_uuid_says_so_rather_than_failing_silently():
    assert "Paste a community URL containing communityUuid=" in JS


def test_a_community_with_nothing_ticked_is_not_captured():
    assert "group.components.length" in JS


def test_each_added_community_gets_its_own_heading():
    assert "community-group-head" in JS
    assert ".community-group-head" in CSS


def test_identifying_a_new_community_clears_the_previous_set():
    """A new identification is a new run; the last one's set is not this
    one's."""
    assert "extraCommunities = []" in JS


def test_every_community_gets_the_same_component_choices():
    """An added community's components were rendered by a second, poorer
    routine: a bare checkbox and "title · count", where the first community
    got a card with the kind's description, a formatted count, the Highlights
    "placed but never written in" caveat, and Select all / Clear all.

    Same decision, same information. One renderer, used by both."""
    assert JS.count("function componentOption(") == 1
    assert JS.count("function componentControls(") == 1
    # The kind descriptions exist once, in that renderer -- not once per
    # community-rendering routine.
    assert JS.count('wiki: "Wiki pages and hierarchy"') == 1


def test_the_added_community_uses_the_shared_renderer():
    assert "group.appendChild(componentControls(" in JS
    assert "options.appendChild(componentOption(" in JS


def test_the_shared_renderer_keeps_the_highlights_caveat():
    """Rich Content is the one component whose count can be smaller than what
    the community holds. That caveat belongs to every community, not the
    first."""
    assert JS.count('" with content, "') == 1


def test_an_added_communitys_select_all_only_reaches_its_own_boxes():
    """Two communities, two independent sets. A Select all that reached across
    them would tick content the user did not choose, into their archive."""
    assert "host === undefined" not in JS
    assert "scope.querySelectorAll" in JS
