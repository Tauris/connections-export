"""A linked Files document is captured like an embedded image.

The gap this closes: `assets.scan` read `//img/@src` and nothing else, so a
body that EMBEDS an image kept it while a body that LINKS a document kept the
href and lost the document. Read away from the deployment that href is
not a slow path to the content -- it is nothing.
"""

from __future__ import annotations

import httpx

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.engine import AssetCapture, begin_run
from connections_export.http.client import HttpClient

MEDIA = "/files/basic/anonymous/api/library/LIB-1/document/DOC-1/media/spec.pdf"


def _capture(tmp_path, body: bytes, *, hcl_hosts=()):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200, content=b"%PDF-1.4 pretend", headers={"content-type": "application/pdf"}
        )

    archive = Archive.open(tmp_path / "archive")
    session = begin_run(
        config=Config(
            base_url="https://fake", output_dir=tmp_path / "archive", hcl_hosts=list(hcl_hosts)
        ),
        client=HttpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None),
        archive=archive,
        emit=lambda _e: None,
        adapter_version="test-0",
        clock=lambda: "2026-08-23T00:00:00Z",
        run_id="test-run",
    )
    capture = AssetCapture(
        fetcher=session.fetcher,
        base_url="https://fake",
        hcl_hosts=list(hcl_hosts),
    )
    capture.scan_body(
        body, base="https://fake/wikis/page", discovered_from="https://fake/wikis/page"
    )
    return requested


def test_a_linked_document_is_fetched(tmp_path):
    requested = _capture(tmp_path, f'<a href="{MEDIA}">the spec</a>'.encode())

    assert any("/document/DOC-1/media/" in url for url in requested), (
        f"the linked document was never fetched: {requested}"
    )


def test_an_embedded_image_is_still_fetched(tmp_path):
    requested = _capture(tmp_path, b'<img src="/wikis/img/pic.png">')

    assert any("pic.png" in url for url in requested)


def test_a_link_to_the_open_internet_is_not_followed(tmp_path):
    """The guard that keeps this from becoming a web crawler. An external
    link is not ours to preserve and does not die with the deployment."""
    requested = _capture(
        tmp_path,
        b'<a href="https://example.com/files/basic/anonymous/api/library/L/document/D/media/x.pdf">x</a>',
    )

    assert not any("example.com" in url for url in requested), requested


def test_a_document_on_a_files_subdomain_is_followed(tmp_path):
    """`hcl_hosts` exists precisely because Files often lives on another
    subdomain of the same deployment."""
    href = "https://files.example.corp/files/basic/anonymous/api/library/L/document/D/media/x.pdf"
    requested = _capture(
        tmp_path, f'<a href="{href}">x</a>'.encode(), hcl_hosts=["files.example.corp"]
    )

    assert any("files.example.corp" in url for url in requested), requested


def test_an_ordinary_page_link_is_not_fetched(tmp_path):
    requested = _capture(tmp_path, b'<a href="/wikis/basic/api/wiki/w/page/p/entry">a page</a>')

    assert not any("/page/p/entry" in url for url in requested), requested


def test_a_document_detail_link_is_not_fetched_as_if_it_were_the_bytes(tmp_path):
    """`/files/app/file/{id}` is a viewer PAGE, not the document.

    Fetching it would archive HTML under the name of a file -- the same class
    of error as storing a login page as the document it was refused. Until the
    id can be resolved to its media URL, the link is recorded and left alone.
    """
    requested = _capture(tmp_path, b'<a href="/files/app/file/DOC-3">the spec</a>')

    assert not any("/files/app/file/" in url for url in requested), requested


def test_a_widget_detail_link_is_not_fetched_either(tmp_path):
    href = "/communities/service/html/communityview?communityUuid=C#fullpageWidgetId=W&file=DOC-4"
    requested = _capture(tmp_path, f'<a href="{href}">x</a>'.encode())

    assert not any("communityview" in url for url in requested), requested
