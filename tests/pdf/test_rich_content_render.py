"""Rich Content in the PDF.

Unlike a file, a Highlights page HAS a body worth rendering -- it is prose,
tables and images. So this is a real render, not an inventory, and it goes
through the same sanitiser as every other body.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedRichContent,
    DerivedRichContentPage,
    Interchange,
)
from connections_export.pdf.html import render_html


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _model(*, placed: int = 4, initialized: int = 2) -> Interchange:
    return Interchange(
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
                        content_html="<h2>Welcome</h2><p>Start here.</p>",
                        author="R. Delgado",
                        version_label="126",
                    ),
                    "p2": DerivedRichContentPage(
                        id="p2",
                        resource_id="p2",
                        title="Who to ask",
                        content_html="<table><tr><td>Builds</td></tr></table>",
                    ),
                },
                placed=placed,
                initialized=initialized,
            )
        ]
    )


def test_the_pages_bodies_are_rendered():
    html = render_html(_model(), blob_bytes=_no_blobs)

    assert "Start here." in html
    assert "<table" in html


def test_each_page_keeps_its_title_and_version():
    html = render_html(_model(), blob_bytes=_no_blobs)

    assert "Welcome" in html
    assert "version 126" in html


def test_widgets_nobody_wrote_in_are_accounted_for():
    """Two placed, never written in. A reader counting two pages otherwise has
    no way to know two more spaces existed."""
    html = render_html(_model(), blob_bytes=_no_blobs)

    assert "never written in" in html
    assert "2 further rich content areas were placed" in html


def test_the_note_reads_correctly_for_a_single_empty_area():
    html = render_html(_model(placed=3, initialized=2), blob_bytes=_no_blobs)

    assert "1 further rich content area was placed" in html


def test_no_note_when_every_area_was_written_in():
    html = render_html(_model(placed=2, initialized=2), blob_bytes=_no_blobs)

    assert "never written in" not in html


def test_an_empty_highlights_area_renders_nothing():
    empty = DerivedRichContent(id="C", title="Highlights")

    html = render_html(Interchange(rich_content=[empty]), blob_bytes=_no_blobs)

    assert '<section class="hcl-rc"' not in html


def test_a_script_in_a_page_body_does_not_survive():
    """A Highlights page is authored HTML from the deployment and goes through
    the same sanitiser as every other body."""
    model = _model()
    model.rich_content[0].pages["p1"].content_html = "<p>hi</p><script>alert(1)</script>"

    html = render_html(model, blob_bytes=_no_blobs)

    assert "<script>alert(1)</script>" not in html
