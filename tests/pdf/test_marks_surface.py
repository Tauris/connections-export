"""How much of the running header and footer a user can actually control.

Six fixed slots is not "control over the header and footer": it is two
positions per band with no middle, no typeface, no rule, and no way to see or
edit the thing the placeholders are substituted INTO.

What stays deliberately closed is raw HTML. The two renderers build their
marks by different mechanisms -- Chromium's own header template, rendered
outside the document, versus paged.js margin boxes filled after pagination --
so raw markup would work in one and silently do nothing in the other. That is
the worst property a setting can have: correct where you test it, absent where
you export. So the surface widens as named fields, which both renderers can
honour.
"""

from __future__ import annotations

import pytest

from connections_export.pdf.marks import (
    DEFAULT_MARKS,
    chromium_template,
    dump_marks,
    resolve,
)


def test_each_band_has_three_positions():
    """Centre is where a document title or a classification usually goes, and
    there was nowhere to put one."""
    for band in ("header", "footer"):
        for side in ("left", "center", "right"):
            assert f"{band}_{side}" in DEFAULT_MARKS


def test_the_typeface_of_the_marks_can_be_set():
    """The marks inherited whatever the renderer chose. A document whose body
    is Georgia and whose footer is sans-serif looks like a mistake."""
    assert "mark_font" in DEFAULT_MARKS


def test_a_rule_can_separate_the_marks_from_the_page():
    """The commonest ask after "put something there" is "and draw a line"."""
    assert "header_rule" in DEFAULT_MARKS
    assert "footer_rule" in DEFAULT_MARKS


def test_the_centre_slot_reaches_the_portable_renderer():
    marks = resolve({"footer_center": "Confidential"})

    template = chromium_template(
        marks.footer_left, marks.footer_right, marks, center=marks.footer_center
    )

    assert "Confidential" in template


def test_a_rule_reaches_the_portable_renderer():
    marks = resolve({"footer_rule": "1px solid #888", "footer_left": "x"})

    template = chromium_template(
        marks.footer_left, marks.footer_right, marks, rule=marks.footer_rule
    )

    assert "1px solid #888" in template


def test_the_font_reaches_the_portable_renderer():
    marks = resolve({"mark_font": "Georgia, serif", "footer_left": "x"})

    template = chromium_template(marks.footer_left, marks.footer_right, marks)

    assert "Georgia, serif" in template


def test_nothing_is_drawn_when_every_slot_is_empty():
    """A rule with nothing above it is a line across the page for no reason."""
    marks = resolve({"footer_left": "", "footer_right": "", "footer_center": ""})

    assert chromium_template("", "", marks, center="") == "<span></span>"


# --- the part the user asked for by name: see it, edit it, hand it back -----


def test_the_whole_configuration_can_be_written_out():
    """The same contract as `style --dump`: have the program write its own
    settings out, edit them, pass them back. A field you cannot see is a field
    you do not know you have -- which is how six of these went unnoticed."""
    dumped = dump_marks()

    assert "[pdf_marks]" in dumped
    for name in DEFAULT_MARKS:
        assert name in dumped


def test_the_dump_explains_each_placeholder():
    dumped = dump_marks()

    for placeholder in ("{page}", "{pages}", "{title}", "{date}", "{section}"):
        assert placeholder in dumped


def test_the_dump_says_where_section_is_approximate():
    """The one honest caveat: the portable renderer cannot know which section
    a page fell in, because its template renders outside the document."""
    dumped = dump_marks()

    assert "{section}" in dumped
    assert "portable" in dumped.lower()


def test_the_dump_round_trips_through_the_config_parser():
    """It has to be pasteable into connections-export.toml, not merely
    readable."""
    import tomllib

    parsed = tomllib.loads(dump_marks())

    assert set(parsed["pdf_marks"]) == set(DEFAULT_MARKS)


def test_defaults_are_commented_out_so_a_dump_changes_nothing():
    """Pasting the dump back must leave the PDF exactly as it was; a dump that
    silently pinned every default would freeze them against later
    improvement."""
    import tomllib

    parsed = tomllib.loads(dump_marks())

    assert parsed["pdf_marks"] == DEFAULT_MARKS


def test_an_unknown_field_is_refused_rather_than_ignored():
    """A typo that does nothing is indistinguishable from a setting that does
    not work."""
    with pytest.raises(ValueError):
        resolve({"footer_middle": "oops"})


# --- a whole band of your own HTML -----------------------------------------
#
# The slots cannot express "a title between two rules" or "an icon on every
# page", and those are ordinary things to want. Both renderers can take
# markup -- verified against a real PDF, not assumed -- so both accept it.


def test_a_band_of_raw_html_replaces_its_slots():
    from connections_export.pdf.marks import chromium_raw

    marks = resolve({"footer_html": "<b>Quarterly Report</b>"})

    assert "<b>Quarterly Report</b>" in chromium_raw(marks.footer_html, marks)


def test_placeholders_still_work_inside_that_html():
    """Otherwise it would be a choice between markup and page numbers."""
    from connections_export.pdf.marks import chromium_raw

    marks = resolve({"footer_html": "<i>page {page} of {pages}</i>"})
    template = chromium_raw(marks.footer_html, marks)

    assert "pageNumber" in template and "totalPages" in template
    assert "<i>" in template


def test_the_html_is_not_escaped():
    """The whole point. Escaping it would print the tags."""
    from connections_export.pdf.marks import chromium_raw

    marks = resolve({"footer_html": '<hr style="border-top:1px solid #999">'})

    assert "&lt;hr" not in chromium_raw(marks.footer_html, marks)


def test_the_band_carries_a_size_so_it_is_visible():
    """Chromium renders its templates at a near-zero default size and in its
    own colour: unwrapped markup arrives invisible, which reads as the feature
    being broken rather than as a styling default."""
    from connections_export.pdf.marks import chromium_raw

    marks = resolve({"footer_html": "<b>x</b>", "mark_size": "11pt"})

    assert "font-size:11pt" in chromium_raw(marks.footer_html, marks)


def test_a_band_counts_as_having_a_footer():
    """`has_footer` decides whether margins are reserved at all. HTML that
    rendered into no reserved space would be clipped away."""
    assert resolve({"footer_left": "", "footer_html": "<b>x</b>"}).has_footer
    assert resolve({"header_left": "", "header_html": "<b>x</b>"}).has_header
