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
from connections_export.fakeserver.synth import SynthSeed, synthesize_forums
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import OverrideTransport, SyncASGIBridge
from tests.crawler.test_assets_in_crawl import _empty_wikiset, _forumset_with_bodies

IMAGE = "https://fake/forums/ajax/download?nodeId=img-1"
DOCUMENT = "https://fake/forums/ajax/download/reply-uuid-1/doc-1/Anleitung%20Export.docx"
FIELD_DOCUMENT = "https://fake/forums/atom/download/Anleitung%20Export.docx?nodeId=doc-1"
REPLY_PAGE = "https://fake/forums/html/threadTopic?id=reply-uuid-1"

IMAGE_LINK = f'<link rel="related" href="{IMAGE}" nodeId="img-1" name="blob" type="image/png"/>'
DOCUMENT_LINK = (
    f'<link rel="enclosure" href="{DOCUMENT}" title="Anleitung Export.docx" '
    'type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>'
)
DOCUMENT_FIELD = (
    '<snx:field name="Anleitung Export.docx" fid="doc-1" type="file">'
    f'<link name="Anleitung Export.docx" type="application/msword" href="{FIELD_DOCUMENT}"/>'
    "</snx:field>"
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
        if request.url.path.startswith("/forums/atom/download"):
            return httpx.Response(404, request=request)
        content = b"PK docx bytes" if "doc-1" in str(request.url) else b"\x89PNG image bytes"
        return httpx.Response(200, content=content, request=request)

    transport = OverrideTransport(
        inner,
        overrides=[
            (lambda r: r.url.path == "/forums/atom/replies", replies),
            (
                lambda r: r.url.path.startswith(("/forums/ajax/download", "/forums/atom/download")),
                download,
            ),
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
    inline_image = next(asset for asset in reply.assets if asset.original_href == IMAGE)
    pasted_image = next(attachment for attachment in reply.attachments if attachment.id == "img-1")
    assert inline_image.present
    assert inline_image.blob_hash == pasted_image.asset.blob_hash


def test_a_document_only_on_the_reply_page_is_kept(tmp_path):
    archive, reply = _crawl(
        tmp_path, feed_links=IMAGE_LINK + PAGE_LINK, page=_reply_page(with_document=True)
    )
    assert DOCUMENT in archive.seen_urls()
    names = {a.filename: a.asset.present for a in reply.attachments}
    assert names.get("Anleitung Export.docx") is True, names


def test_a_file_field_in_the_reply_feed_is_archived_once(tmp_path):
    archive, reply = _crawl(
        tmp_path,
        feed_links=IMAGE_LINK + DOCUMENT_FIELD + PAGE_LINK,
        page=_reply_page(with_document=True),
    )
    assert DOCUMENT in archive.seen_urls()
    assert FIELD_DOCUMENT not in archive.seen_urls()
    documents = [a for a in reply.attachments if a.filename == "Anleitung Export.docx"]
    assert len(documents) == 1
    assert documents[0].asset.present


def test_a_reply_with_only_a_pasted_image_gains_nothing(tmp_path):
    archive, reply = _crawl(
        tmp_path, feed_links=IMAGE_LINK + PAGE_LINK, page=_reply_page(with_document=False)
    )
    assert DOCUMENT not in archive.seen_urls()
    assert [a.asset.present for a in reply.attachments] == [True]


def test_fake_server_nested_file_field_uses_the_rendered_download_url(tmp_path):
    forumset = synthesize_forums(
        SynthSeed(
            seed=81,
            forum_count=1,
            topics_per_forum=1,
            replies_per_topic=1,
            reply_tree_depth=0,
            forum_reply_attachment=True,
        )
    )
    source_topic = forumset.forums[0].topics[0]
    source_reply = source_topic.replies[0]
    [source_attachment] = source_reply.attachments
    app = make_app(_empty_wikiset(), forumset=forumset)
    client = HttpClient(
        transport=SyncASGIBridge(app),
        retry_policy=RetryPolicy(max_attempts=1),
        sleep=lambda _s: None,
    )
    config = Config(base_url="https://fake", output_dir=tmp_path / "archive")
    archive = Archive.open(tmp_path / "archive")

    crawl_forums(config=config, client=client, archive=archive)

    reply = derive(archive).forums[0].topics[source_topic.uuid].replies[source_reply.uuid]
    [attachment] = reply.attachments
    assert attachment.filename == source_attachment.filename
    assert attachment.asset.present
    assert any(
        f"/forums/ajax/download/{source_reply.uuid}/{source_attachment.uuid}/" in url
        for url in archive.seen_urls()
    )
    assert not any("/forums/atom/download/" in url for url in archive.seen_urls())
