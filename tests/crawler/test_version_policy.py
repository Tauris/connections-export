"""`config.versions` governs whether version bodies are
fetched -- "list" (feed metadata only), "full" (feed + every version's
body), "none" (skip version history entirely).
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import FakePage, FakeVersion, FakeWiki, WikiSet
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import (
    OverrideTransport,
    SyncASGIBridge,
    make_client,
    path_is,
    static_response,
)

VERSIONS_PATH = "/wikis/basic/api/wiki/wiki0/page/p0/feed"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _wikiset() -> WikiSet:
    page = FakePage(
        uuid="page-uuid-1",
        label="p0",
        title="Page Zero",
        parent_uuid=None,
        ordinal=0,
        body_html="",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
        versions=[
            FakeVersion(
                uuid="ver-uuid-1",
                version_label=1,
                author="Fake Author",
                created="2026-01-01T00:00:00Z",
                content_html="<p>v1</p>",
            )
        ],
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


def test_versions_list_archives_feed_but_no_version_body(tmp_path):
    wikiset = _wikiset()
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path, versions="list"), client=make_client(app), archive=archive)

    seen = archive.seen_urls()
    assert any("category=version" in url for url in seen)


def test_versions_none_skips_the_versions_feed_entirely(tmp_path):
    wikiset = _wikiset()
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path, versions="none"), client=make_client(app), archive=archive)

    seen = archive.seen_urls()
    assert not any("category=version" in url for url in seen)


def test_versions_full_fetches_each_version_body(tmp_path):
    wikiset = _wikiset()
    app = make_app(wikiset)

    # The fake's version entries inline content rather than link out
    # (no `src` attribute -- Q9), so there is nothing for "full" to
    # fetch against the real fixture. Override the versions feed with
    # one that *does* carry a content src, to prove the "full" policy
    # follows it, without touching fakeserver code.
    version_body_url = "https://fake/versions/media/ver-uuid-1"
    versions_feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">
  <entry>
    <id>urn:lsid:ibm.com:td:ver-uuid-1</id>
    <title type="text">version 1</title>
    <td:versionLabel>1</td:versionLabel>
    <content type="text/html" src="{version_body_url}"/>
    <author><name>Fake Author</name></author>
    <published>2026-01-01T00:00:00Z</published>
    <updated>2026-01-01T00:00:00Z</updated>
  </entry>
</feed>""".encode()
    version_body_bytes = b"<p>archived version body</p>"

    transport = OverrideTransport(
        SyncASGIBridge(app),
        overrides=[
            (
                path_is(VERSIONS_PATH, query_contains="category=version"),
                static_response(200, versions_feed_xml, content_type="application/atom+xml"),
            ),
            (
                path_is("/versions/media/ver-uuid-1"),
                static_response(200, version_body_bytes, content_type="text/html"),
            ),
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path, versions="full"), client=client, archive=archive)

    assert version_body_url in archive.seen_urls()
