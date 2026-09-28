"""The portable PDF carries bookmarks.

A long export -- several wikis, blogs and forums, hundreds of pages -- is
navigated from the viewer's sidebar. Only the browser-fidelity PDF asked
Chromium for an outline; the portable PDF, the default, had none. Its
headings (each wiki, blog and forum, and every page, post and topic under
it) become the bookmarks, nested as they are in the document.
"""

from __future__ import annotations

import io

import pytest

from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE

pytestmark = pytest.mark.skipif(
    not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium and paged.js"
)


def _outline_titles(outline, depth=0, out=None):
    out = [] if out is None else out
    for entry in outline:
        if isinstance(entry, list):
            _outline_titles(entry, depth + 1, out)
        else:
            out.append((depth, entry.title))
    return out


@pytest.fixture(scope="module")
def demo_pdf(tmp_path_factory):
    from connections_export.cli import pdf_main

    out = tmp_path_factory.mktemp("pdf") / "demo.pdf"
    assert pdf_main(["--demo", "--output", str(out)]) == 0
    return out.read_bytes()


def test_the_portable_pdf_has_bookmarks_for_containers_and_pages(demo_pdf):
    from pypdf import PdfReader

    titles = _outline_titles(PdfReader(io.BytesIO(demo_pdf)).outline)
    names = [title for _depth, title in titles]

    assert titles, "the portable PDF has no bookmarks"
    depth = {title: d for d, title in titles}
    # Containers at the top, their pages beneath, a wiki's sub-pages deeper.
    assert depth["Engineering Handbook"] == 0
    assert depth["Onboarding"] == 1
    assert depth["Dev Environment"] == 2
    assert depth["Engineering Blog"] == 0 and depth["How we cut build times in half"] == 1
    assert depth["Help & Support"] == 0 and depth["How do I reset my password?"] == 1
    # Only the document's own structure: no section labels, and none of the
    # headings authors wrote inside their pages.
    for noise in ("Comments", "Attachments", "Replies", "Diagram & links demo"):
        assert noise not in names, noise


def test_the_bookmarks_panel_stays_closed_unless_asked_for(demo_pdf):
    """Some viewers lay an opened bookmarks panel over the page (VS Code's
    preview does); a PDF should not impose that. The bookmarks are there
    either way."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(demo_pdf))
    assert reader.outline
    assert reader.page_mode != "/UseOutlines"


def test_the_panel_can_be_asked_to_open(tmp_path):
    from pypdf import PdfReader

    from connections_export.cli import pdf_main

    out = tmp_path / "open.pdf"
    assert pdf_main(["--demo", "--open-bookmarks", "--output", str(out)]) == 0
    assert PdfReader(out).page_mode == "/UseOutlines"


def test_with_outline_sets_the_panel_only_when_asked():
    from pypdf import PdfReader, PdfWriter

    from connections_export.pdf.paged import with_outline

    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    blank = io.BytesIO()
    writer.write(blank)
    entries = [{"title": "Wiki", "level": 1, "page": 0}]

    closed = PdfReader(io.BytesIO(with_outline(blank.getvalue(), entries)))
    opened = PdfReader(io.BytesIO(with_outline(blank.getvalue(), entries, open_panel=True)))

    assert closed.outline and closed.page_mode != "/UseOutlines"
    assert opened.page_mode == "/UseOutlines"


def test_a_bookmark_leads_to_the_page_its_heading_is_on(demo_pdf):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(demo_pdf))
    entry = next(
        e for e in reader.outline if not isinstance(e, list) and e.title == "Engineering Blog"
    )
    page = reader.get_destination_page_number(entry)
    assert "Engineering Blog" in reader.pages[page].extract_text()
