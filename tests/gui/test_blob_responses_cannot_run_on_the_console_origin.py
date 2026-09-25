"""`GET /api/blob/{hash}` serves archive bytes on the console's own origin.

The bytes and their declared content type both come from the archive, and an
archive is untrusted: its HTML was written by whoever used the deployment, and
a whole archive can be handed over by someone else. A blob declared
`text/html` -- or an SVG, which is a document that can carry script -- opened
directly in a tab would otherwise run with the console's origin, and script
there can call the console's local APIs (start crawls, delete archives).

So every blob response is made inert as a document: `nosniff` so the browser
never upgrades octet-stream to HTML, a sandboxing CSP so even a document that
IS rendered has no script and no origin, and `attachment` for everything that
is not a raster image. Raster images stay inline because the reader shows them
in `<img>`, which is the whole point of serving blobs at all.

Driven in-process via `httpx.ASGITransport`, like `test_model_endpoints.py`.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.gui.app import make_app
from connections_export.interchange.package import write_package

GENERATED_AT = "2026-07-20T12:00:00Z"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
HTML = b"<!doctype html><script>fetch('/api/delete-archive',{method:'POST'})</script>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _run(coro):
    return asyncio.run(coro)


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.get(path)


def _asset(digest: str) -> ResolvedAsset:
    return ResolvedAsset(
        original_href=f"att/{digest[:8]}",
        resolved_url=f"https://fake/att/{digest[:8]}",
        blob_hash=f"sha256:{digest}",
        present=True,
        scope="same",
    )


def _app_serving(tmp_path, blobs: dict[str, tuple[bytes, str | None]]):
    """A package whose one page carries each blob as an attachment, declared
    with the given content type (`None` = undeclared, so the server sniffs)."""
    archive = Archive.open(tmp_path / "archive")
    digests: dict[str, str] = {}
    attachments = []
    for name, (data, content_type) in blobs.items():
        digest = write_blob(archive.root, data)
        digests[name] = digest
        attachments.append(
            DerivedAttachment(
                id=name, filename=name, content_type=content_type, asset=_asset(digest)
            )
        )
    page = DerivedPage(id="p1", title="Page 1", content_html="<p>hi</p>", attachments=attachments)
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    dest = tmp_path / "package"
    write_package(
        Interchange(base_url="https://fake", wikis=[wiki]),
        archive,
        dest,
        generated_at=GENERATED_AT,
    )
    return make_app(demo=False, package_dir=dest), digests


@pytest.fixture
def served(tmp_path):
    return _app_serving(
        tmp_path,
        {
            "page.html": (HTML, "text/html"),
            "diagram.svg": (SVG, None),  # undeclared: sniffed to image/svg+xml
            "photo.png": (PNG, "image/png"),
            "report.pdf": (b"%PDF-1.4 fake", "application/pdf"),
            "blob.bin": (b"opaque", None),
        },
    )


def test_every_blob_response_forbids_mime_sniffing(served):
    """Without `nosniff` a browser may decide an octet-stream or text/plain
    blob "looks like" HTML and render it -- the archive would pick the type
    after all."""
    app, digests = served
    for name, digest in digests.items():
        response = _run(_get(app, f"/api/blob/{digest}"))
        assert response.status_code == 200, name
        assert response.headers["x-content-type-options"] == "nosniff", name


def test_every_blob_response_carries_a_sandboxing_csp(served):
    """If a blob IS rendered as a document -- navigated to directly, or a
    browser that ignores `attachment` -- the sandbox CSP gives it a unique
    opaque origin and no script, so it can reach none of the console's APIs."""
    app, digests = served
    for name, digest in digests.items():
        csp = _run(_get(app, f"/api/blob/{digest}")).headers["content-security-policy"]
        directives = [d.strip() for d in csp.split(";")]
        assert "sandbox" in directives, (name, csp)
        assert "default-src 'none'" in directives, (name, csp)
        assert "script-src" not in csp, (name, csp)


def test_an_html_blob_is_a_download_never_a_page(served):
    """The archive declared this attachment `text/html`. Rendering it inline on
    the console origin is exactly the attack, so it is served as a download."""
    app, digests = served
    response = _run(_get(app, f"/api/blob/{digests['page.html']}"))
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.content == HTML  # the bytes themselves are untouched


def test_an_svg_blob_is_a_download_never_a_page(served):
    """SVG is a document format that runs script when opened as a page. In the
    reader it only ever appears in `<img>`, where scripts never run and
    `Content-Disposition` is ignored -- so making it an attachment costs the
    reader nothing and removes the direct-navigation risk outright."""
    app, digests = served
    response = _run(_get(app, f"/api/blob/{digests['diagram.svg']}"))
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-disposition"].startswith("attachment")


def test_non_image_blobs_are_downloads(served):
    app, digests = served
    for name in ("report.pdf", "blob.bin"):
        response = _run(_get(app, f"/api/blob/{digests[name]}"))
        assert response.headers["content-disposition"].startswith("attachment"), name


def test_a_raster_image_stays_inline_so_the_reader_can_show_it(served):
    """A PNG cannot carry script, and the reader's whole use of blobs is showing
    images -- an `attachment` here would only make "open image in new tab"
    download instead of display."""
    app, digests = served
    response = _run(_get(app, f"/api/blob/{digests['photo.png']}"))
    assert response.headers["content-type"].startswith("image/png")
    assert "attachment" not in response.headers.get("content-disposition", "")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_disposition_names_no_file_so_the_readers_own_name_wins(served):
    """A `filename` in `Content-Disposition` overrides the `download="..."`
    attribute the reader puts on its file links -- so naming the hash there
    would turn every "Quarterly report.xlsx" download into 64 hex digits. A
    bare `attachment` keeps the reader's name, falls back to the hash from the
    URL, and puts nothing archive-controlled into a response header."""
    app, digests = served
    for name in ("page.html", "report.pdf", "blob.bin"):
        disposition = _run(_get(app, f"/api/blob/{digests[name]}")).headers["content-disposition"]
        assert disposition == "attachment", (name, disposition)
