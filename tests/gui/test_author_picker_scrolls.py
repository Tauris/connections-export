"""A long author list stays a panel, not a wall.

The list merges every component and carries up to 200 identities. A real
community can fill that, and 200 chips laid out unbounded push everything
below them off the screen.

So: a bounded height that scrolls, a count so the size is legible before
scrolling, and a filter once there are enough that scanning stops working.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_css, served_console_js

CSS = served_console_css()
JS = served_console_js()


def test_the_chip_area_is_bounded_and_scrolls():
    rules = CSS[CSS.index(".author-picker") : CSS.index(".author-picker") + 1200]
    assert "max-height" in rules
    assert "overflow-y" in rules


def test_the_count_is_shown_so_the_size_is_legible():
    assert "authorCountLabel" in JS


def test_a_long_list_gets_a_filter():
    assert "author-filter-chips" in JS
    assert "AUTHOR_FILTER_THRESHOLD" in JS


def test_a_short_list_does_not_get_one():
    """A search box over eight names is furniture."""
    assert "identities.length > AUTHOR_FILTER_THRESHOLD" in JS


def test_filtering_hides_chips_rather_than_rebuilding_them():
    """Each chip carries a click handler that re-imports that person; a
    rebuild on every keystroke would drop them."""
    assert "chip.hidden = " in JS


def test_the_whole_list_can_be_collapsed():
    """Author filtering is not what most people came for, and a panel of
    thirty names above the verdict is in the way if you do not want it."""
    assert "<details" in JS
    assert "authorPickerOpen" in JS


def test_the_choice_is_remembered():
    assert "hcl-export-authors-open" in JS


def test_it_opens_itself_when_a_filter_matched_nothing():
    """That is the one case where the list is not a curiosity but the fix:
    the filter did not match, and the exact stored names are right there."""
    assert "kept === 0" in JS
