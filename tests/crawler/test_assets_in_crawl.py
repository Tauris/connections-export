"""Assets discovered from page bodies during a crawl, and
attachment enclosures.

Also covers the same-origin media rule extended to Blogs & Forums
(#27, "Extend same-origin media capture to blogs and forums"): a blog
post/comment body or a forum topic/reply body is scanned exactly like
a wiki page body -- a same-deployment `<img>` is fetched and archived,
an external one is recorded (a `Warning`) and never fetched.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl, crawl_blogs, crawl_forums
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import (
    BlogSet,
    FakeAttachment,
    FakeBlog,
    FakeBlogComment,
    FakeBlogPost,
    FakeForum,
    FakeForumReply,
    FakeForumTopic,
    FakePage,
    FakeWiki,
    ForumSet,
    WikiSet,
)
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import (
    OverrideTransport,
    SyncASGIBridge,
    make_client,
    path_is,
    static_response,
)


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _wikiset_with_body(
    body_html: str, *, attachments: list[FakeAttachment] | None = None
) -> WikiSet:
    page = FakePage(
        uuid="page-uuid-1",
        label="p0",
        title="Page Zero",
        parent_uuid=None,
        ordinal=0,
        body_html=body_html,
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
        attachments=attachments or [],
    )
    wiki = FakeWiki(
        uuid="wiki-uuid-1",
        label="wiki0",
        title="Wiki Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        pages=[page],
    )
    return WikiSet(wikis=[wiki], base_timestamp_ms=1_700_000_000_000)


def test_same_deployment_body_image_is_archived_and_external_is_skipped_with_warning(tmp_path):
    same_host_img = "/wikis/basic/api/wiki/wiki0/page/p0/media/img/pic.png"
    external_img = "https://example.com/logo.png"
    body = f'<div><img src="{same_host_img}"/><img src="{external_img}"/></div>'
    wikiset = _wikiset_with_body(body)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    seen = archive.seen_urls()
    assert any(url.endswith(same_host_img) for url in seen)
    assert not any("example.com" in url for url in seen)

    warnings = [
        e
        for e in seen_events
        if isinstance(e, events.Warning) and e.kind == "external_asset_skipped"
    ]
    assert len(warnings) == 1
    assert warnings[0].ref == external_img

    asset_fetches = [
        e
        for e in seen_events
        if isinstance(e, events.Fetched) and e.kind == "asset" and e.url.endswith(same_host_img)
    ]
    assert len(asset_fetches) == 1


def test_same_deployment_attachment_enclosure_is_archived(tmp_path):
    attachment = FakeAttachment(
        uuid="att-uuid-1",
        filename="report.bin",
        content_type="application/octet-stream",
        size=42,
        author="Fake Author",
        created="2026-01-01T00:00:00Z",
    )
    wikiset = _wikiset_with_body("<div>no assets here</div>", attachments=[attachment])
    app = make_app(wikiset)

    # The fake only ever emits a symbolic `urn:fake:attachment:...`
    # enclosure href (attachment entry contents are undocumented --
    # Q13); override just the attachments feed and a same-deployment
    # enclosure URL so this test can prove the crawler *would* fetch a
    # real same-deployment enclosure, without touching fakeserver code.
    enclosure_url = "https://fake/attachments/media/att-uuid-1"
    attachments_feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:lsid:ibm.com:td:{attachment.uuid}</id>
    <title type="text">{attachment.filename}</title>
    <link rel="enclosure" type="{attachment.content_type}" href="{enclosure_url}"/>
    <author><name>{attachment.author}</name></author>
    <published>{attachment.created}</published>
    <updated>{attachment.created}</updated>
  </entry>
</feed>""".encode()
    enclosure_bytes = b"attachment bytes"

    transport = OverrideTransport(
        SyncASGIBridge(app),
        overrides=[
            (
                path_is(
                    "/wikis/basic/api/wiki/wiki0/page/p0/feed", query_contains="category=attachment"
                ),
                static_response(200, attachments_feed_xml, content_type="application/atom+xml"),
            ),
            (
                lambda request: request.url.path == "/attachments/media/att-uuid-1",
                static_response(200, enclosure_bytes),
            ),
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    assert enclosure_url in archive.seen_urls()

    fetched = [e for e in seen_events if isinstance(e, events.Fetched) and e.url == enclosure_url]
    assert len(fetched) == 1
    assert fetched[0].kind == "attachments"


# --------------------------------------------------------------------
# #27 -- the same rule, extended to Blogs & Forums body-embedded media.
# The "elsewhere on the same host" fixture is the fake's existing
# cross-app Files image route (app.py's `cross_app_image_route`) --
# reused as-is, no fakeserver wiring needed for Blogs/Forums bodies.
# --------------------------------------------------------------------

_SAME_HOST_IMG = "/files/basic/api/library/lib1/document/doc1/media/logo.png"
_EXTERNAL_IMG = "https://example.com/logo.png"


def _blogset_with_bodies(post_body: str, *, comment_body: str | None = None) -> BlogSet:
    comments = []
    if comment_body is not None:
        comments.append(
            FakeBlogComment(
                uuid="comment-uuid-1",
                author="Fake Commenter",
                author_userid="commenter1",
                content_html=comment_body,
                published="2026-01-01T00:00:00Z",
            )
        )
    post = FakeBlogPost(
        uuid="post-uuid-1",
        slug="post-one",
        title="Post One",
        author="Fake Author",
        author_userid="author1",
        body_html=post_body,
        published="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
        comments=comments,
    )
    blog = FakeBlog(
        uuid="blog-uuid-1",
        handle="blog0",
        title="Blog Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        posts=[post],
    )
    return BlogSet(homepage="blog0", blogs=[blog], base_timestamp_ms=1_700_000_000_000)


def _forumset_with_bodies(topic_body: str, *, reply_body: str | None = None) -> ForumSet:
    replies = []
    if reply_body is not None:
        replies.append(
            FakeForumReply(
                uuid="reply-uuid-1",
                author="Fake Replier",
                content_html=reply_body,
                published="2026-01-01T00:00:00Z",
                ref="topic-uuid-1",
                source_topic="topic-uuid-1",
            )
        )
    topic = FakeForumTopic(
        uuid="topic-uuid-1",
        forum_uuid="forum-uuid-1",
        title="Topic One",
        author="Fake Author",
        content_html=topic_body,
        published="2026-01-01T00:00:00Z",
        replies=replies,
    )
    forum = FakeForum(
        uuid="forum-uuid-1",
        title="Forum Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        topics=[topic],
    )
    return ForumSet(forums=[forum], base_timestamp_ms=1_700_000_000_000)


def _empty_wikiset() -> WikiSet:
    return WikiSet(wikis=[], base_timestamp_ms=1_700_000_000_000)


def test_blog_post_body_same_deployment_image_is_archived_and_external_is_skipped(tmp_path):
    body = f'<div><img src="{_SAME_HOST_IMG}"/><img src="{_EXTERNAL_IMG}"/></div>'
    blogset = _blogset_with_bodies(body)
    app = make_app(_empty_wikiset(), blogset=blogset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl_blogs(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=seen_events.append,
    )

    seen = archive.seen_urls()
    assert any(url.endswith(_SAME_HOST_IMG) for url in seen)
    assert not any("example.com" in url for url in seen)

    warnings = [
        e
        for e in seen_events
        if isinstance(e, events.Warning) and e.kind == "external_asset_skipped"
    ]
    assert len(warnings) == 1
    assert warnings[0].ref == _EXTERNAL_IMG

    asset_fetches = [
        e
        for e in seen_events
        if isinstance(e, events.Fetched) and e.kind == "asset" and e.url.endswith(_SAME_HOST_IMG)
    ]
    assert len(asset_fetches) == 1


def test_blog_comment_body_same_deployment_image_is_archived(tmp_path):
    comment_only_img = "/files/basic/api/library/lib1/document/doc2/media/pic.png"
    comment_body = f'<p>Nice post <img src="{comment_only_img}"/></p>'
    blogset = _blogset_with_bodies("<p>no images here</p>", comment_body=comment_body)
    app = make_app(_empty_wikiset(), blogset=blogset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    crawl_blogs(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
    )

    seen = archive.seen_urls()
    assert any(url.endswith(comment_only_img) for url in seen)


def test_forum_topic_body_same_deployment_image_is_archived_and_external_is_skipped(tmp_path):
    body = f'<div><img src="{_SAME_HOST_IMG}"/><img src="{_EXTERNAL_IMG}"/></div>'
    forumset = _forumset_with_bodies(body)
    app = make_app(_empty_wikiset(), forumset=forumset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl_forums(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    seen = archive.seen_urls()
    assert any(url.endswith(_SAME_HOST_IMG) for url in seen)
    assert not any("example.com" in url for url in seen)

    warnings = [
        e
        for e in seen_events
        if isinstance(e, events.Warning) and e.kind == "external_asset_skipped"
    ]
    assert len(warnings) == 1
    assert warnings[0].ref == _EXTERNAL_IMG


def test_forum_reply_body_same_deployment_image_is_archived(tmp_path):
    reply_only_img = "/files/basic/api/library/lib1/document/doc3/media/pic.png"
    reply_body = f'<p>Thanks <img src="{reply_only_img}"/></p>'
    forumset = _forumset_with_bodies("<p>no images here</p>", reply_body=reply_body)
    app = make_app(_empty_wikiset(), forumset=forumset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    crawl_forums(config=_config(tmp_path), client=client, archive=archive)

    seen = archive.seen_urls()
    assert any(url.endswith(reply_only_img) for url in seen)
