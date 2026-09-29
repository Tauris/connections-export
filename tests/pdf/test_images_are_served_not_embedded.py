"""The PDF renderers hand images to the browser on request, not inside the
document.

Each image used to travel as base64 text inside the one HTML document the
browser loads. Loading grows much faster than the document: 10 MB of images
loaded in about a second, 50 MB in about 18 seconds, and at 150 MB the browser
crashed -- so an ordinary community with a few dozen photos exceeded the
30-second load limit, external images on or off. Served on request from a
reserved address, the same 150 MB loads and prints in about 3 seconds.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from connections_export.derive.model import (
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf.html import SERVED_IMAGE_ORIGIN, render_html, serving_images

DIGEST = "a" * 64


def _png(width: int = 4, height: int = 4) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PNG = _png()


def _with_image(times: int = 1) -> Interchange:
    asset = ResolvedAsset(
        original_href="cid:photo.png",
        resolved_url="https://example.com/photo.png",
        blob_hash=f"sha256:{DIGEST}",
        present=True,
        scope="same",
    )
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Photos",
        content_html='<img src="cid:photo.png" alt="photo">' * times,
        assets=[asset],
    )
    wiki = DerivedWiki(id="w1", label="w1", title="Wiki", root_page_ids=["p1"], pages={"p1": page})
    return Interchange(wikis=[wiki])


def test_a_served_render_keeps_the_images_out_of_the_document():
    with serving_images() as served:
        html = render_html(_with_image(), blob_bytes={DIGEST: PNG}.get)

    assert "data:image" not in html
    [(url, (content_type, data))] = served.items()
    assert url.startswith(SERVED_IMAGE_ORIGIN)
    assert f'src="{url}"' in html
    assert (content_type, data) == ("image/png", PNG)


def test_an_image_used_twice_is_served_once():
    with serving_images() as served:
        render_html(_with_image(times=2), blob_bytes={DIGEST: PNG}.get)

    assert len(served) == 1


def test_without_serving_the_document_stays_self_contained():
    html = render_html(_with_image(), blob_bytes={DIGEST: PNG}.get)

    assert "data:image/png;base64," in html
    assert SERVED_IMAGE_ORIGIN not in html


needs_browser = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")


def _image_count(pdf: bytes) -> int:
    from pypdf import PdfReader

    return sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages)


@needs_browser
@pytest.mark.skipif(not PAGED_AVAILABLE, reason="needs paged.js")
def test_the_paged_pdf_shows_the_served_image():
    from connections_export.pdf.paged import render_pdf_paged

    pdf = render_pdf_paged(_with_image(), {DIGEST: PNG}.get)

    assert _image_count(pdf) >= 1


@needs_browser
def test_the_simple_pdf_shows_the_served_image():
    from connections_export.pdf.browser import render_pdf

    pdf = render_pdf(_with_image(), {DIGEST: PNG}.get)

    assert _image_count(pdf) >= 1


@needs_browser
def test_the_browser_fidelity_pdf_shows_the_served_image():
    from connections_export.pdf.browser_fidelity import render_pdf_browser

    pdf = render_pdf_browser(_with_image(), {DIGEST: PNG}.get)

    assert _image_count(pdf) >= 1


def test_a_body_cannot_name_a_served_address_the_render_did_not_serve():
    forged = SERVED_IMAGE_ORIGIN + "b" * 64
    page = DerivedPage(id="p1", label="p1", title="T", content_html=f'<img src="{forged}">')
    wiki = DerivedWiki(id="w1", label="w1", title="W", root_page_ids=["p1"], pages={"p1": page})

    with serving_images():
        html = render_html(Interchange(wikis=[wiki]), blob_bytes=lambda _d: None)

    assert f'src="{forged}"' not in html
