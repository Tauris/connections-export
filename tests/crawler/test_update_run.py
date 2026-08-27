"""Capture, let time pass, update -- the whole point, end to end.

Each app answers "what changed" differently, and these prove the difference is
real rather than asserted: blogs and forums are asked server-side with a date,
wikis are rescanned and compared client-side, and rich content is small enough
to re-read whole.

The measurements that matter are what the archive GAINED and what the run had
to ASK FOR. An update that finds everything by re-downloading everything is not
an update.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl, crawl_blogs, crawl_forums
from connections_export.crawler.events import BlogPostDerived, ForumTopicDerived
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(
    seed=0, wiki_count=1, depth=2, pages_per_level=2, human_names=True, late_reply=True
)
BASE = "https://fake"


class _CountingTransport(httpx.BaseTransport):
    """Wraps the in-process fake so a test can say how many requests a run
    made -- the number that decides whether an update is cheap."""

    def __init__(self, app):
        self._inner = SyncASGIBridge(app)
        self.urls: list[str] = []

    def handle_request(self, request):
        self.urls.append(str(request.url))
        return self._inner.handle_request(request)


@pytest.fixture
def deployment(tmp_path):
    wikiset = synthesize(SEED)
    blogset = synthesize_blogs(SEED)
    forumset = synthesize_forums(SEED)
    app = make_app(wikiset, blogset=blogset, forumset=forumset, visible_wave=0)
    transport = _CountingTransport(app)
    archive = Archive.open(tmp_path / "archive")
    return transport, archive, wikiset, blogset, forumset


def _client(transport) -> HttpClient:
    return HttpClient(transport=transport, sleep=lambda _s: None)


def _config(tmp_path, **kwargs) -> Config:
    return Config(base_url=BASE, output_dir=tmp_path, **kwargs)


def _advance(transport) -> None:
    """A week passes on the live system."""
    with httpx.Client(transport=transport, base_url=BASE) as http:
        http.post("/_demo/advance")


# --- blogs: asked server-side with a date ----------------------------------


def test_an_update_finds_the_posts_that_arrived_since(deployment, tmp_path):
    transport, archive, _w, blogset, _f = deployment
    first: list = []
    crawl_blogs(
        config=_config(tmp_path),
        client=_client(transport),
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=first.append,
    )
    captured = {e.post_id for e in first if isinstance(e, BlogPostDerived)}

    _advance(transport)
    second: list = []
    crawl_blogs(
        config=_config(tmp_path, fetch="update"),
        client=_client(transport),
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=second.append,
        since="2023-01-01T00:00:00Z",
    )
    found = {e.post_id for e in second if isinstance(e, BlogPostDerived)}

    later = {p.uuid for b in blogset.blogs for p in b.posts if p.wave == 1}
    assert later, "the fixture has nothing later to find"
    assert later <= found, "the update missed content that arrived after the capture"
    assert captured & later == set(), "the first capture already saw the later wave"


def test_a_dated_update_does_not_re_derive_the_whole_blog(deployment, tmp_path):
    """The server was asked for changes, so the run should hear about a
    handful of posts -- not every post it already had."""
    transport, archive, _w, blogset, _f = deployment
    crawl_blogs(
        config=_config(tmp_path),
        client=_client(transport),
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=lambda _e: None,
    )
    total = sum(len(b.posts) for b in blogset.blogs)

    _advance(transport)
    events: list = []
    crawl_blogs(
        config=_config(tmp_path, fetch="update"),
        client=_client(transport),
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=events.append,
        since="2023-01-01T00:00:00Z",
    )

    derived = len([e for e in events if isinstance(e, BlogPostDerived)])
    assert 0 < derived < total


# --- forums -----------------------------------------------------------------


def test_an_update_finds_the_topics_that_arrived_since(deployment, tmp_path):
    transport, archive, _w, _b, forumset = deployment
    crawl_forums(
        config=_config(tmp_path),
        client=_client(transport),
        archive=archive,
        emit=lambda _e: None,
    )

    _advance(transport)
    events: list = []
    crawl_forums(
        config=_config(tmp_path, fetch="update"),
        client=_client(transport),
        archive=archive,
        emit=events.append,
        since="2023-01-01T00:00:00Z",
    )

    found = {e.topic_id for e in events if isinstance(e, ForumTopicDerived)}
    later = {t.uuid for f in forumset.forums for t in f.topics if t.wave == 1}
    assert later <= found


# --- wikis: no date to ask with, so rescan and compare ----------------------


def test_a_wiki_update_re_reads_the_feeds_but_not_every_page(deployment, tmp_path):
    """The wiki feed cannot be asked "what changed", so the run re-reads it and
    compares each page's own `updated`. The saving is that unchanged pages cost
    no body, comments, versions or asset requests -- which is the whole
    difference between a rescan and a re-download."""
    transport, archive, wikiset, _b, _f = deployment
    crawl(
        config=_config(tmp_path),
        client=_client(transport),
        archive=archive,
        emit=lambda _e: None,
    )
    first_run_requests = len(transport.urls)
    transport.urls.clear()

    crawl(
        config=_config(tmp_path, fetch="update"),
        client=_client(transport),
        archive=archive,
        emit=lambda _e: None,
    )
    update_requests = len(transport.urls)

    assert update_requests < first_run_requests, (
        "an update cost as much as the original capture -- nothing was reused"
    )
    assert update_requests > 0, "an update fetched nothing at all, not even a feed"


# --- files and rich content: re-read whole, because they have no cutoff ------


@pytest.fixture
def community_deployment(tmp_path):
    """A community whose Files and Highlights both hold back a second wave."""
    from connections_export.fakeserver.synth import synthesize_files, synthesize_rich_content

    wikiset = synthesize(SEED)
    fileset = synthesize_files(SEED, community_uuid=COMMUNITY)
    rteset = synthesize_rich_content(SEED, community_uuid=COMMUNITY)
    app = make_app(wikiset, fileset=fileset, rteset=rteset, visible_wave=0)
    transport = _CountingTransport(app)
    archive = Archive.open(tmp_path / "archive")
    return transport, archive, fileset, rteset


COMMUNITY = "community-under-test"


def test_an_update_finds_the_files_that_arrived_since(community_deployment, tmp_path):
    """A community's Files feed carries no cutoff this crawl sends, so the
    library feed is the document that REVEALS change and must be re-read.

    Serving it from the archive makes the update find nothing at all -- which
    is the exact failure `CachePolicy`'s own docstring warns about, and it
    reports success while doing it.
    """
    from connections_export.crawler import crawl_files

    transport, archive, fileset, _ = community_deployment
    client = _client(transport)

    crawl_files(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    first = _file_ids(archive)
    assert first, "the first capture found no files at all"

    _advance(transport)
    crawl_files(
        config=_config(tmp_path, fetch="update"),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    after = _file_ids(archive)

    everything = {f.name for f in fileset.libraries[0].files}
    assert after > first, (
        f"the update found no new files. Had {len(first)}, still {len(after)}, "
        f"while the deployment now offers {len(everything)}."
    )


def test_an_update_finds_the_rich_content_that_arrived_since(community_deployment, tmp_path):
    """The widget layout is named in `CachePolicy`'s docstring as a document
    that reveals change. Same failure as Files if it is served from the
    archive."""
    from connections_export.crawler import crawl_rich_content

    transport, archive, _, rteset = community_deployment
    client = _client(transport)

    crawl_rich_content(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    first = _rte_ids(archive)
    assert first, "the first capture found no rich content at all"

    _advance(transport)
    crawl_rich_content(
        config=_config(tmp_path, fetch="update"),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    after = _rte_ids(archive)

    assert after > first, (
        f"the update found no new rich-content pages. Had {len(first)}, still {len(after)}."
    )


def _file_ids(archive) -> set[str]:
    from connections_export.derive import derive

    model = derive(archive)
    return {f.name for library in model.file_libraries for f in library.files.values() if f.name}


def _rte_ids(archive) -> set[str]:
    from connections_export.derive import derive

    model = derive(archive)
    return {page.id for container in model.rich_content for page in container.pages.values()}


def test_a_files_update_re_reads_the_feeds_but_not_every_download(community_deployment, tmp_path):
    """An update that finds everything by re-downloading everything is not an
    update. The library feed must be re-read; the bytes behind a file that did
    not move must not be fetched again."""
    from connections_export.crawler import crawl_files

    transport, archive, _, _ = community_deployment
    client = _client(transport)

    crawl_files(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    _advance(transport)
    before = len(transport.urls)
    crawl_files(
        config=_config(tmp_path, fetch="update"),
        client=client,
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    asked = transport.urls[before:]

    feeds = [u for u in asked if "/feed" in u]
    downloads = [u for u in asked if "/media" in u or "/document" in u]

    assert feeds, "the update did not re-read the library feed"
    # Only the wave-1 files moved, so only those bytes are worth fetching.
    assert len(downloads) <= 2, (
        f"the update re-downloaded {len(downloads)} files; only the new ones "
        f"should have been fetched"
    )
