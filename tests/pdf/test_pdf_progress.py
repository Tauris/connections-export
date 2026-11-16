"""A PDF render says what it is doing, and an out-of-memory crash says so.

A large export takes minutes; a spinner for all of them looks like a hang, and
a browser killed for running out of memory used to surface as an opaque
Playwright error."""

from __future__ import annotations

import pytest

from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.paged import is_out_of_memory
from connections_export.pdf.progress import STAGES, PdfProgress


def _model(pages: int = 3) -> Interchange:
    page_map = {
        f"p{i}": DerivedPage(id=f"p{i}", label=f"p{i}", title=f"Page {i}", content_html="<p>x</p>")
        for i in range(pages)
    }
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=list(page_map), pages=page_map)
    return Interchange(wikis=[wiki])


def test_a_target_crash_is_out_of_memory_and_a_timeout_is_not():
    assert is_out_of_memory(RuntimeError("Page.pdf: Target crashed"))
    assert is_out_of_memory(RuntimeError("anything"), crashed=True)
    assert not is_out_of_memory(TimeoutError("Timeout 30000ms exceeded"))


def test_a_report_is_complete_on_its_own():
    report = PdfProgress(stage="laying_out", part=2, parts=5, pages_done=40, message="x")
    assert report.as_dict() == {
        "stage": "laying_out",
        "part": 2,
        "parts": 5,
        "pages_done": 40,
        "pages_total": None,
        "message": "x",
        "file": 1,
        "files": 1,
    }


@pytest.mark.skipif(not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium")
def test_the_paged_render_reports_its_stages_in_order():
    from connections_export.pdf.paged import render_pdf_paged

    seen: list[PdfProgress] = []
    render_pdf_paged(_model(), lambda _d: None, progress=seen.append)

    stages = [r.stage for r in seen]
    assert stages[0] == "preparing" and stages[-1] == "done"
    assert "laying_out" in stages and "printing" in stages
    order = [STAGES.index(s) for s in stages]
    assert order == sorted(order)
    assert seen[-1].pages_total == seen[-1].pages_done > 0
    assert all(r.message for r in seen)
