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
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import attaching_files, render_html

DIGEST = "c" * 64
DATA = b"%PDF-1.4 a report" + b"x" * 3000
IMAGE_DIGEST = "d" * 64
IMAGE_DATA = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
    b'<circle cx="16" cy="16" r="12" fill="blue"/></svg>'
)
IMAGE_URL = "https://hcl.example.invalid/forums/ajax/download?nodeId=topic-file"
ATTACHMENT_URL = "https://hcl.example.invalid/forums/atom/download?nodeId=topic-file"


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


def _forum_model() -> Interchange:
    reply = DerivedForumReply(
        id="r1",
        author="Reply author",
        attachments=[_attachment("reply-file", "reply.docx")],
    )
    topic = DerivedForumTopic(
        id="t1",
        title="Topic",
        attachments=[_attachment("topic-file", "topic.png")],
        reply_ids=["r1"],
        replies={"r1": reply},
    )
    return Interchange(
        forums=[DerivedForum(id="f1", title="Forum", topic_ids=["t1"], topics={"t1": topic})]
    )


def _forum_model_with_inline_attachment() -> Interchange:
    model = _forum_model()
    topic = model.forums[0].topics["t1"]
    image = ResolvedAsset(
        original_href=IMAGE_URL,
        resolved_url=IMAGE_URL,
        filename="topic.png",
        blob_hash=f"sha256:{IMAGE_DIGEST}",
        present=True,
        scope="same",
    )
    topic.content_html = f'<p>Screenshot:</p><img src="{IMAGE_URL}">'
    topic.assets = [image]
    topic.attachments = [
        DerivedAttachment(
            id="topic-file",
            filename="topic.png",
            content_type="image/svg+xml",
            asset=ResolvedAsset(
                original_href=ATTACHMENT_URL,
                resolved_url=ATTACHMENT_URL,
                filename="topic.png",
                blob_hash=f"sha256:{IMAGE_DIGEST}",
                present=True,
                scope="same",
            ),
        )
    ]
    return model


def _blobs(digest: str) -> bytes | None:
    return DATA if digest == DIGEST else None


def _inline_blobs(digest: str) -> bytes | None:
    return IMAGE_DATA if digest == IMAGE_DIGEST else _blobs(digest)


def test_forum_topic_and_reply_attachments_are_rendered_and_recorded_for_embedding():
    with attaching_files() as attached:
        html = render_html(_forum_model(), blob_bytes=_blobs)

    assert "topic.png" in html and "reply.docx" in html
    assert "Download" not in html
    assert (
        '<a class="hcl-attachment-download" '
        'href="connections-export-attachment:topic.png">topic.png</a>'
    ) in html
    assert (
        '<a class="hcl-attachment-download" '
        'href="connections-export-attachment:reply.docx">reply.docx</a>'
    ) in html
    assert "attached to this PDF" in html
    assert attached == [("topic.png", DIGEST), ("reply.docx", DIGEST)]


def test_inline_images_are_not_listed_but_original_can_be_attached():
    model = _forum_model_with_inline_attachment()
    assert "topic.png" not in render_html(model, blob_bytes=_inline_blobs)

    with attaching_files() as attached:
        html = render_html(model, blob_bytes=_inline_blobs)

    assert "topic.png" not in html
    assert "reply.docx" in html
    assert attached == [("topic.png", IMAGE_DIGEST), ("reply.docx", DIGEST)]


def test_inline_image_original_can_be_omitted_while_other_attachments_are_attached():
    with attaching_files(include_inline_images=False) as attached:
        html = render_html(_forum_model_with_inline_attachment(), blob_bytes=_inline_blobs)

    assert "topic.png" not in html
    assert "reply.docx" in html
    assert attached == [("reply.docx", DIGEST)]


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
def test_the_pdf_carries_clickable_forum_attachments_only_when_asked():
    from pypdf import PdfReader

    from connections_export.pdf.paged import render_pdf_paged

    plain = PdfReader(io.BytesIO(render_pdf_paged(_forum_model(), _blobs)))
    assert not plain.attachments
    attached = PdfReader(
        io.BytesIO(render_pdf_paged(_forum_model(), _blobs, attach_attachments=True))
    )
    assert sorted(attached.attachments) == ["reply.docx", "topic.png"]
    assert attached.attachments["topic.png"] == [DATA]
    without_inline = PdfReader(
        io.BytesIO(
            render_pdf_paged(
                _forum_model_with_inline_attachment(),
                _inline_blobs,
                attach_attachments=True,
                attach_inline_images=False,
            )
        )
    )
    assert sorted(without_inline.attachments) == ["reply.docx"]
    annotation_names = []
    for page in attached.pages:
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            if annotation.get("/Subtype") == "/FileAttachment":
                assert annotation.get("/Contents") is None
                assert annotation["/AP"]["/N"].get_object().get_data() == b""
                annotation_names.append(str(annotation["/FS"].get_object()["/F"]))
    assert sorted(annotation_names) == ["reply.docx", "topic.png"]


@pytest.mark.skipif(not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium")
def test_pypdf_fallback_keeps_attachment_links_unobscured(monkeypatch):
    from pypdf import PdfReader

    from connections_export.pdf import join
    from connections_export.pdf.paged import render_pdf_paged

    def unsupported_streaming_join(*args, **kwargs):
        raise join.NotSimple("force the pypdf fallback")

    monkeypatch.setattr(join, "rewrite_links", unsupported_streaming_join)
    reader = PdfReader(
        io.BytesIO(render_pdf_paged(_forum_model(), _blobs, attach_attachments=True))
    )
    assert sorted(reader.attachments) == ["reply.docx", "topic.png"]
    annotations = [
        reference.get_object()
        for page in reader.pages
        for reference in page.get("/Annots", [])
        if reference.get_object().get("/Subtype") == "/FileAttachment"
    ]
    assert sorted(str(annotation["/FS"].get_object()["/F"]) for annotation in annotations) == [
        "reply.docx",
        "topic.png",
    ]
    assert all(annotation["/AP"]["/N"].get_object().get_data() == b"" for annotation in annotations)


@pytest.mark.skipif(not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium")
def test_pdf_attaches_inline_image_original_only_when_asked():
    from pypdf import PdfReader

    from connections_export.pdf.paged import render_pdf_paged

    model = _forum_model_with_inline_attachment()
    plain = PdfReader(io.BytesIO(render_pdf_paged(model, _inline_blobs)))
    assert not plain.attachments
    attached = PdfReader(
        io.BytesIO(render_pdf_paged(model, _inline_blobs, attach_attachments=True))
    )
    assert sorted(attached.attachments) == ["reply.docx", "topic.png"]
    assert attached.attachments["topic.png"] == [IMAGE_DATA]
