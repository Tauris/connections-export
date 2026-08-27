"""A community's own picture, in the header or footer.

Now that a band can carry an image, the obvious image to carry is the
community's own — and the archive is where it should come from, not the live
system: a PDF produced from an archive two years later must still show it.

So `{logo}` resolves to the captured bytes, inlined. Inlined rather than
linked because of the one renderer difference that bites: the Live PDF fetches
its template outside the document, where an image by URL does not load. A data
URI works in both, so there is one behaviour to explain instead of two.
"""

from __future__ import annotations

import base64

import pytest

from connections_export.pdf.marks import PLACEHOLDERS, chromium_raw, resolve

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


def test_the_logo_is_a_known_placeholder():
    assert "logo" in PLACEHOLDERS


def test_it_becomes_an_inline_image():
    marks = resolve({"footer_html": "{logo} <b>Handbook</b>"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "<img" in template
    assert "data:image/png;base64," in template


def test_the_bytes_are_the_captured_ones(tmp_path):
    """Not a link to the live system: an archive read in two years' time has
    to render the same page."""
    marks = resolve({"footer_html": "{logo}"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert base64.b64encode(PNG).decode() in template


def test_it_is_sized_so_it_cannot_swallow_the_band():
    """A community logo is whatever size someone uploaded. Dropped into a
    12mm margin unconstrained, a 2000px image takes the page with it."""
    marks = resolve({"footer_html": "{logo}"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "height" in template


def test_a_community_with_no_picture_leaves_no_gap():
    """Not every community has one. An empty box, a broken-image icon or the
    literal text `{logo}` would each be worse than nothing there."""
    marks = resolve({"footer_html": "{logo}<b>Handbook</b>"})

    template = chromium_raw(marks.footer_html, marks, logo=None)

    assert "<img" not in template
    assert "{logo}" not in template
    assert "<b>Handbook</b>" in template


def test_it_works_in_a_plain_slot_too():
    """The slots and the HTML bands take the same placeholders; a keyword that
    only worked in one would be a trap."""
    from connections_export.pdf.marks import chromium_template

    marks = resolve({"footer_left": "{logo} Handbook"})

    template = chromium_template(
        marks.footer_left, marks.footer_right, marks, logo=(PNG, "image/png")
    )

    assert "<img" in template


def test_an_unknown_placeholder_is_still_refused():
    with pytest.raises(ValueError):
        resolve({"footer_left": "{sigil}"})


# --- how big, and what shape -----------------------------------------------
#
# Community logos are 155x155 and the system shows them circle-cropped, which
# is how people recognise them. A square one in a PDF header would read as a
# different picture of the same thing.


def test_it_is_round_by_default():
    """Matching how the source system shows it. A square crop of a picture
    everyone has only ever seen in a circle looks like the wrong image."""
    marks = resolve({"footer_html": "{logo}"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "border-radius:50%" in template.replace(" ", "")


def test_the_shape_can_be_squared_off():
    marks = resolve({"footer_html": "{logo}", "logo_shape": "square"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "border-radius:50%" not in template.replace(" ", "")


def test_a_round_logo_is_cropped_rather_than_squashed():
    """155x155 is square, but nothing guarantees every deployment agrees. A
    non-square image scaled into a circle without cover would be distorted."""
    marks = resolve({"footer_html": "{logo}"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "object-fit:cover" in template.replace(" ", "")


def test_the_size_has_a_sensible_default():
    """Big enough to recognise at 155px native, small enough to sit in a
    margin band without pushing the text off it."""
    assert resolve(None).logo_size


def test_the_size_can_be_set_to_anything():
    marks = resolve({"footer_html": "{logo}", "logo_size": "12mm"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png"))

    assert "12mm" in template


def test_both_dimensions_are_set_so_the_circle_is_a_circle():
    """Height alone with `width:auto` gives an ellipse the moment the source
    image is not square."""
    marks = resolve({"footer_html": "{logo}", "logo_size": "7mm"})

    template = chromium_raw(marks.footer_html, marks, logo=(PNG, "image/png")).replace(" ", "")

    assert "width:7mm" in template and "height:7mm" in template
