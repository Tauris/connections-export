"""Attachments are listed in the PDF by name and size, not carried in it.

A link whose address is the whole file, base64-encoded into the document,
would carry every attachment through the browser's memory and into the PDF
inside a link most viewers cannot open; a large archive's attachments alone
can exhaust the machine. Asked to, the renderer attaches them to the
PDF as real file attachments instead."""

from __future__ import annotations

import io

import pytest

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import attaching_files, render_html

DIGEST = "c" * 64
DATA = b"%PDF-1.4 a report" + b"x" * 3000


def _attachment(att_id: str, filename: str, present: bool = True) -> DerivedAttachment:
    return DerivedAttachment(
        id=att_id,
        filename=filename,
        asset=ResolvedAsset(
            original_href=f"{att_id}.pdf",
            resolved_url=f"https://hcl.example.invalid/{att_id}.pdf",
            blob_hash=f"sha256:{DIGEST}" if present else None,
            present=present,
            scope="same",
        ),
    )


def _model() -> Interchange:
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page",
        content_html="<p>x</p>",
        attachments=[
            _attachment("a1", "report.pdf"),
            _attachment("a2", "report.pdf"),
            _attachment("a3", "gone.pdf", present=False),
        ],
    )
    return Interchange(
        wikis=[DerivedWiki(id="w", label="w", title="W", root_page_ids=["p1"], pages={"p1": page})]
    )


def _blobs(digest: str) -> bytes | None:
    return DATA if digest == DIGEST else None


def test_an_attachment_is_named_with_its_size_and_its_bytes_stay_out():
    html = render_html(_model(), blob_bytes=_blobs)
    assert "report.pdf" in html and "2.9 KB" in html
    assert "data:" not in html and "base64" not in html
    assert "not captured" in html.lower()


def test_attached_files_are_recorded_once_per_name():
    with attaching_files() as attached:
        html = render_html(_model(), blob_bytes=_blobs)
    assert attached == [("report.pdf", DIGEST), ("report (2).pdf", DIGEST)]
    assert "attached to this PDF" in html and "attached as report (2).pdf" in html


@pytest.mark.skipif(not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium")
def test_the_pdf_carries_them_as_file_attachments_only_when_asked():
    from pypdf import PdfReader

    from connections_export.pdf.paged import render_pdf_paged

    plain = PdfReader(io.BytesIO(render_pdf_paged(_model(), _blobs)))
    assert not plain.attachments
    attached = PdfReader(io.BytesIO(render_pdf_paged(_model(), _blobs, attach_attachments=True)))
    assert sorted(attached.attachments) == ["report (2).pdf", "report.pdf"]
    assert attached.attachments["report.pdf"] == [DATA]
