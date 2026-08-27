"""Running headers and footers, on two renderers that share no mechanism.

The portable renderer uses Chromium's `footer_template` -- HTML rendered
outside the document, where the page's CSS cannot reach and which cannot know
where in the document a page fell. The paged renderer uses paged.js margin
boxes filled in by script after pagination, which knows both.

Raw HTML would therefore work on one and silently do nothing on the other:
a setting that works where you test it and not where you export. So the setting
is placeholder strings, and each renderer builds its own marks from them.
"""

from __future__ import annotations

import pytest

from connections_export.pdf.marks import (
    DEFAULT_MARKS,
    PLACEHOLDERS,
    Marks,
    chromium_template,
    resolve,
)


def test_defaults_name_the_document_and_number_the_page():
    marks = resolve(None)

    assert marks.footer_right == "{page} / {pages}"
    assert marks.footer_left == "{section}"
    assert not marks.has_header  # an export of someone's wiki needs no letterhead


def test_an_unknown_setting_is_reported():
    with pytest.raises(ValueError, match="unknown header/footer setting"):
        resolve({"footer": "x"})


def test_an_unknown_placeholder_is_reported_not_silently_dropped():
    """A mark that renders nothing looks identical to one that is switched
    off, so a typo has to be an error rather than a shrug."""
    with pytest.raises(ValueError, match=r"unknown placeholder \{pagenum\}"):
        resolve({"footer_right": "{pagenum}"})


def test_literal_text_survives():
    marks = resolve({"footer_left": "Confidential"})
    template = chromium_template(marks.footer_left, marks.footer_right, marks)

    assert "Confidential" in template


def test_placeholders_become_chromiums_own_fields():
    marks = resolve({"footer_right": "{page}/{pages}", "header_right": "{title}"})

    footer = chromium_template("", marks.footer_right, marks)
    header = chromium_template("", marks.header_right, marks)

    assert 'class="pageNumber"' in footer and 'class="totalPages"' in footer
    assert 'class="title"' in header


def test_section_degrades_to_the_title_rather_than_vanishing():
    """`{section}` means "where in the document this page is". Chromium's
    template cannot know, so it answers with the most specific location it
    can -- the document title. That also preserves the footer this renderer
    has always had."""
    template = chromium_template("{section}", "", Marks())

    assert 'class="title"' in template


def test_empty_sides_produce_a_template_that_renders_nothing():
    """Chromium needs a non-empty template to render anything at all, and an
    empty span to render nothing -- there is no other off switch."""
    assert chromium_template("", "", Marks()) == "<span></span>"


def test_every_default_is_a_known_setting():
    assert set(DEFAULT_MARKS) == {f.name for f in Marks.__dataclass_fields__.values()}


def test_every_placeholder_is_documented_as_supported_or_not():
    """The module docstring is where someone learns what they can write. A
    placeholder that works and is not described there is one nobody uses."""
    import connections_export.pdf.marks as marks_module

    assert set(PLACEHOLDERS) == {"page", "pages", "title", "date", "section", "logo"}
    for name in PLACEHOLDERS:
        assert f"{{{name}}}" in marks_module.__doc__, name
