"""A set of communities reads as a set, not as one pile.

Every container already carries `community_uuid`/`community_title` (see
`derive/model.py`), so grouping needs no model change -- only the nav asking
the question.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_css, served_console_js

JS = served_console_js()
CSS = served_console_css()


def test_the_nav_groups_by_community_only_when_there_is_more_than_one():
    """One community stays flat: a heading above every section would be a
    level with one child on every screen."""
    assert "communitiesIn(REAL_MODEL)" in JS
    assert "renderAppSections(REAL_MODEL, '')" in JS
    # Counted by NAMED communities: one community plus containers recording
    # none is still one community's archive, and heading it would make an
    # attribution gap look like a second community.
    assert "namedCommunities.length > 1" in JS
    assert "communities.filter((community) => community.uuid)" in JS


def test_containers_belonging_to_no_community_are_still_shown():
    """A standalone wiki is a real thing; filing it nowhere would hide it."""
    assert "Not in a community" in JS


def test_two_communities_do_not_share_one_open_state():
    """Both have a section called WIKIS; without a per-community key they
    would open and close together."""
    assert 'const openKey = (keyPrefix || "") + label;' in JS
    assert "readerComponentOpen.has(openKey)" in JS


def test_the_community_heading_is_styled():
    assert ".r-community-head" in CSS


def test_the_verdict_says_what_each_community_contributed():
    """A single total for a set answers "how much" and not "from where"."""
    from tests.gui._served_assets import served_console_html

    html = served_console_html()

    assert 'id="v-communities"' in html
    assert 'renderCommunityBreakdown(model, "v-communities")' in JS
    assert "modelTypeCounts(scopeForCommunity(model, community.uuid))" in JS


def test_one_community_gets_no_per_community_breakdown():
    """It would repeat the totals directly above it."""
    assert "if (communities.length < 2)" in JS
