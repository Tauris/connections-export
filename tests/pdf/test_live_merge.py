"""Merging per-page PDFs either works or says why -- never quietly returns one.

`pypdf` is a runtime dependency, not a test-only one. Declared for tests
alone it is absent at runtime: `PdfWriter` is `None`,
`PdfWriter` raises TypeError, a bare `except Exception` catches it, and the
function returns `pdf_parts[0]`: a one-page PDF for a two-hundred-page export,
with every page still reported as rendered.

That is the failure this project keeps naming elsewhere -- a gap that reports
success. The dependency is declared now, so the import cannot go missing; this
covers the other half, which is that a merge failing for ANY reason has to be
visible.
"""

from __future__ import annotations

import pytest

from connections_export.pdf.live import merge_pdf_parts


def _pdf(text: str) -> bytes:
    """A one-page PDF, built by pypdf so it is unarguably valid.

    Hand-writing one is a trap: without an xref table pypdf rejects it, and a
    fixture it rejects makes every merge here look like a failure -- a test
    that fails for a reason that has nothing to do with what it is testing.
    """
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_one_part_needs_no_merge():
    only = _pdf("a")
    merged, reason = merge_pdf_parts([only])
    assert merged == only
    assert reason is None


def test_several_parts_merge_into_one_document():
    merged, reason = merge_pdf_parts([_pdf("a"), _pdf("b"), _pdf("c")])
    assert reason is None

    import io

    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(merged)).pages) == 3


def test_a_failed_merge_reports_the_reason_rather_than_truncating_silently():
    """The old code returned part one and said nothing."""
    merged, reason = merge_pdf_parts([_pdf("a"), b"not a pdf at all"])
    assert reason is not None
    assert "merge" in reason.lower()


def test_a_failed_merge_still_returns_something_readable():
    """A partial PDF beats no PDF -- as long as the caller is told."""
    merged, reason = merge_pdf_parts([_pdf("a"), b"not a pdf at all"])
    assert merged
    assert reason


def test_the_reason_names_how_many_pages_were_lost():
    """ "Merging failed" is a fact; "3 of 4 pages are not in this file" is what
    tells someone whether to trust the document in front of them."""
    _merged, reason = merge_pdf_parts([_pdf("a"), b"bad", b"bad", b"bad"])
    assert "4" in reason


def test_nothing_to_merge_is_not_a_failure():
    merged, reason = merge_pdf_parts([])
    assert merged == b""
    assert reason is None


def test_pypdf_is_a_declared_runtime_dependency():
    """Not a dev-group one. It is imported on a shipped code path, and the
    executable bundles it while a pip install did not."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    runtime = " ".join(pyproject["project"]["dependencies"])
    assert "pypdf" in runtime


@pytest.mark.parametrize("count", [2, 5])
def test_every_page_survives_a_successful_merge(count):
    import io

    from pypdf import PdfReader

    merged, reason = merge_pdf_parts([_pdf(str(i)) for i in range(count)])
    assert reason is None
    assert len(PdfReader(io.BytesIO(merged)).pages) == count
