"""A forum reply's uploaded document is kept next to its pasted image.

A reply with a screenshot pasted in and a Word document uploaded carries two
downloads. The pasted image comes as a link with a ``nodeId`` and the name
``blob``; the document is a ``/forums/ajax/download/...`` link without a
``nodeId``, whose name is in its address and what hovering over it shows.
Both are attachments. The reply's page is read even when the feed already
named an attachment, and what the page lists becomes part of the archive's
model, not only of the crawl.

Both shapes of the document are covered: named in the feed, and listed only
on the reply's own page.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl_forums
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import OverrideTransport, SyncASGIBridge
from tests.crawler.test_assets_in_crawl import _empty_wikiset, _forumset_with_bodies

IMAGE = "https://fake/forums/ajax/download?nodeId=img-1"
DOCUMENT = "https://fake/forums/ajax/download/reply-uuid-1/doc-1/Anleitung%20Export.docx"
REPLY_PAGE = "https://fake/forums/html/threadTopic?id=reply-uuid-1"

IMAGE_LINK = f'<link rel="related" href="{IMAGE}" nodeId="img-1" name="blob" type="image/png"/>'
DOCUMENT_LINK = (
    f'<link rel="enclosure" href="{DOCUMENT}" title="Anleitung Export.docx" '
    'type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>'
)
PAGE_LINK = f'<link rel="alternate" type="text/html" href="{REPLY_PAGE}"/>'


def _reply_page(*, with_document: bool) -> bytes:
    document = (
        f'<a href="{DOCUMENT}" title="Anleitung Export.docx">Herunterladen</a>'
        if with_document
        else ""
    )
    return (
        "<html><body>"
        f'<div class="lotusPost" id="reply-uuid-1"><p>Siehe Anhang</p>'
        f'<img src="{IMAGE}" alt="blob"/>'
        f'<ul class="dfAttachments">{document}</ul></div>'
        "</body></html>"
    ).encode()


def _crawl(tmp_path: Path, *, feed_links: str, page: bytes):
    forumset = _forumset_with_bodies(
        "<p>Frage</p>", reply_body=f'<p>Siehe Anhang <img src="{IMAGE}"/></p>'
    )
    inner = SyncASGIBridge(make_app(_empty_wikiset(), forumset=forumset))

    def replies(request: httpx.Request) -> httpx.Response:
        served = inner.handle_request(request)
        served.read()
        body = served.content.replace(b"</entry>", feed_links.encode() + b"</entry>", 1)
        return httpx.Response(
            200, content=body, headers={"content-type": "application/atom+xml"}, request=request
        )

    def download(request: httpx.Request) -> httpx.Response:
        content = b"PK docx bytes" if "doc-1" in str(request.url) else b"\x89PNG image bytes"
        return httpx.Response(200, content=content, request=request)

    transport = OverrideTransport(
        inner,
        overrides=[
            (lambda r: r.url.path == "/forums/atom/replies", replies),
            (lambda r: r.url.path.startswith("/forums/ajax/download"), download),
            (
                lambda r: r.url.path == "/forums/html/threadTopic",
                lambda r: httpx.Response(200, content=page, request=r),
            ),
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    config = Config(base_url="https://fake", output_dir=tmp_path / "archive")
    archive = Archive.open(tmp_path / "archive")
    crawl_forums(config=config, client=client, archive=archive)
    model = derive(archive)
    reply = model.forums[0].topics["topic-uuid-1"].replies["reply-uuid-1"]
    return archive, reply


def test_a_document_named_in_the_feed_is_kept_with_the_pasted_image(tmp_path):
    archive, reply = _crawl(
        tmp_path,
        feed_links=IMAGE_LINK + DOCUMENT_LINK + PAGE_LINK,
        page=_reply_page(with_document=True),
    )
    assert DOCUMENT in archive.seen_urls()
    names = {a.filename: a.asset.present for a in reply.attachments}
    assert names.get("Anleitung Export.docx") is True, names
    assert len(reply.attachments) == 2, names


def test_a_document_only_on_the_reply_page_is_kept(tmp_path):
    archive, reply = _crawl(
        tmp_path, feed_links=IMAGE_LINK + PAGE_LINK, page=_reply_page(with_document=True)
    )
    assert DOCUMENT in archive.seen_urls()
    names = {a.filename: a.asset.present for a in reply.attachments}
    assert names.get("Anleitung Export.docx") is True, names


def test_a_reply_with_only_a_pasted_image_gains_nothing(tmp_path):
    archive, reply = _crawl(
        tmp_path, feed_links=IMAGE_LINK + PAGE_LINK, page=_reply_page(with_document=False)
    )
    assert DOCUMENT not in archive.seen_urls()
    assert [a.asset.present for a in reply.attachments] == [True]
