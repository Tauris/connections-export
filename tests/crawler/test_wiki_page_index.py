"""An update should not cost one request per page just to learn the dates.

A wiki cannot be asked "what changed since" -- `profiles.WIKIS.since_encoding`
is `"unsupported"` -- so the crawler read every page's own entry to learn its
date, and did that whether or not the page had moved. On a real deployment
that measured 23 entry requests for a 23-page update, which was the single
largest remaining cost in a wiki update.

The wiki's own feed carries the same entries: `/wikis/{auth}/api/wiki/{label}/feed`
returned `atom:updated`, `td:label`, `td:modified`, `atom:content/@src` and
`rel="replies"` per page on a real deployment. One request for the
whole wiki answers what N requests answered before, so the per-page entry is
read again only for the pages the index says moved.
"""

from __future__ import annotations

import copy
import re

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient


@pytest.fixture
def dataset():
    return synthesize(SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=4))


def _crawl(dataset, root, fetch, events=None):
    transport = httpx.MockTransport(SyncASGIBridge(make_app(dataset)).handle_request)
    crawl(
        config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=(events.append if events is not None else (lambda _e: None)),
    )


def _entry_requests(events) -> list[str]:
    return [
        e.url
        for e in events
        if type(e).__name__ == "Fetched" and e.url.endswith("/entry") and not e.from_cache
    ]


def _edit_one_page(dataset):
    """Move one page's date, the way an edit does."""
    grown = copy.deepcopy(dataset)
    page = grown.wikis[0].pages[1]
    page.title = f"{page.title} (edited)"
    page.content_html = "<p>edited</p>"
    page.modified = "2027-01-01T00:00:00Z"
    page.updated = "2027-01-01T00:00:00Z"
    return grown, page


def test_an_update_reads_the_entry_only_for_pages_that_moved(tmp_path, dataset):
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")

    grown, edited = _edit_one_page(dataset)
    events: list = []
    _crawl(grown, root, "update", events)

    entries = _entry_requests(events)
    assert len(entries) == 1, entries
    assert edited.label in entries[0]


def test_an_unchanged_page_does_not_read_cached_artifact_feeds(tmp_path, dataset):
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")
    events: list = []
    _crawl(dataset, root, "update", events)

    cached_artifacts = [
        e.url
        for e in events
        if type(e).__name__ == "Fetched"
        and e.from_cache
        and any(
            f"category={category}" in e.url
            for category in ("comment", "version", "attachment", "tag")
        )
    ]
    assert cached_artifacts == []


def test_the_page_that_moved_is_still_captured(tmp_path, dataset):
    """Cheaper is only worth anything if it still sees the change."""
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")

    grown, edited = _edit_one_page(dataset)
    _crawl(grown, root, "update")

    model = derive(Archive.open(root))
    titles = [page.title for wiki in model.wikis for page in wiki.pages.values()]
    assert edited.title in titles


def test_a_page_that_appeared_is_still_fetched(tmp_path, dataset):
    """An index is a way to skip work, never a way to miss a page."""
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")

    grown = copy.deepcopy(dataset)
    wiki = grown.wikis[0]
    new_page = copy.deepcopy(wiki.pages[-1])
    new_page.label, new_page.title, new_page.uuid = ("fresh-page", "Fresh page", "page-fresh")
    wiki.pages.append(new_page)

    events: list = []
    _crawl(grown, root, "update", events)

    assert any("fresh-page" in url for url in _entry_requests(events))
    model = derive(Archive.open(root))
    assert "Fresh page" in [p.title for w in model.wikis for p in w.pages.values()]


def test_a_first_capture_does_not_pay_for_an_index(tmp_path, dataset):
    """`resume` serves archived entries whatever the policy says and `refresh`
    refetches them whatever it says, so in both the index is a request that
    buys nothing. It is read only where it can save something."""
    root = tmp_path / "archive"
    events: list = []
    _crawl(dataset, root, "resume", events)

    index_reads = [
        e.url
        for e in events
        if type(e).__name__ == "Fetched" and re.search(r"/api/wiki/[^/]+/feed\?", e.url)
    ]
    assert index_reads == []
