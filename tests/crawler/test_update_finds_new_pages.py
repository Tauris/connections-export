"""An update finds what appeared since the last capture.

The navigation feed is the authority on which pages a wiki has. It was
fetched with the default policy -- `IF_MISSING` -- so an update read the
ARCHIVED copy of it and could only ever see the pages that were already
there. A page written since the last capture was never discovered, and the
run reported success.

`CachePolicy`'s own docstring names this: "ALWAYS for the documents that
REVEAL change -- feeds, search result pages... Serving these from the archive
would make every update find nothing at all."

The demo could not have caught it: every wiki page in the demo dataset is
wave 0, so its update story never adds one.
"""

from __future__ import annotations

import copy

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
    return synthesize(SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=3))


def _crawl(dataset, root, fetch):
    transport = httpx.MockTransport(SyncASGIBridge(make_app(dataset)).handle_request)
    crawl(
        config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )


def _with_extra_page(dataset, *, label="brand-new-page"):
    grown = copy.deepcopy(dataset)
    wiki = grown.wikis[0]
    page = copy.deepcopy(wiki.pages[-1])
    page.label = label
    page.title = "Brand new page"
    page.uuid = f"page-{label}"
    wiki.pages.append(page)
    return grown


def _labels(root):
    return {page.label for wiki in derive(Archive.open(root)).wikis for page in wiki.pages.values()}


def test_a_page_written_since_the_last_capture_is_captured(tmp_path, dataset):
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")
    assert len(_labels(root)) == 3

    _crawl(_with_extra_page(dataset), root, "update")

    labels = _labels(root)
    assert "brand-new-page" in labels, "an update missed a page added since the last capture"
    assert len(labels) == 4


def test_the_pages_already_there_are_not_refetched(tmp_path, dataset):
    """The fix must not turn an update into a re-crawl: the nav feed is read
    again because it reveals change, and everything it points at is still
    judged on its own date."""
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")

    events: list = []
    grown = _with_extra_page(dataset)
    transport = httpx.MockTransport(SyncASGIBridge(make_app(grown)).handle_request)
    crawl(
        config=Config(base_url="https://fake", output_dir=root, fetch="update"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=events.append,
    )

    bodies = [
        e
        for e in events
        if type(e).__name__ == "Fetched" and "/media" in e.url and not e.from_cache
    ]
    # The new page's body, and no others -- the three unchanged ones come from
    # the archive.
    assert len(bodies) == 1, [e.url for e in bodies]


# --- the same class of feed, one level up and in the other apps -------------
#
# `paginate` defaults to `IF_MISSING`, so any feed that ENUMERATES things --
# which wikis exist, which blogs, which forums -- was read from the archive on
# an update and could only ever list what was already captured. Files had it
# right already, with a comment naming the reason; the rest inherited the
# default. These are behavioural, because a structural check ("every list feed
# passes a policy") would pass the moment someone passed the wrong one.


def test_an_update_finds_a_blog_created_since_the_last_capture(tmp_path, dataset):
    from connections_export.crawler import crawl_blogs
    from connections_export.fakeserver.synth import synthesize_blogs

    blogset = synthesize_blogs(SynthSeed(seed=0))
    root = tmp_path / "archive"

    def run(blogs, fetch):
        transport = httpx.MockTransport(
            SyncASGIBridge(make_app(dataset, blogset=blogs)).handle_request
        )
        crawl_blogs(
            config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
            client=HttpClient(transport=transport, sleep=lambda _s: None),
            archive=Archive.open(root),
            emit=lambda _e: None,
            blogs_homepage=blogs.homepage,
        )

    run(blogset, "resume")
    grown = copy.deepcopy(blogset)
    second = copy.deepcopy(blogset.blogs[0])
    second.handle = "second-blog"
    second.uuid = "blog-second"
    second.title = "A Second Blog"
    grown.blogs.append(second)
    run(grown, "update")

    # Identified by uuid: a derived blog's `handle` is only filled when the
    # feed named one, and falls back to the uuid otherwise.
    found = {b.id for b in derive(Archive.open(root)).blogs} | {
        b.handle for b in derive(Archive.open(root)).blogs
    }
    assert "blog-second" in found, "an update missed a blog created since the last capture"


def test_an_update_finds_a_forum_created_since_the_last_capture(tmp_path, dataset):
    from connections_export.crawler import crawl_forums
    from connections_export.fakeserver.synth import synthesize_forums

    forumset = synthesize_forums(SynthSeed(seed=0))
    root = tmp_path / "archive"

    def run(forums, fetch):
        transport = httpx.MockTransport(
            SyncASGIBridge(make_app(dataset, forumset=forums)).handle_request
        )
        crawl_forums(
            config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
            client=HttpClient(transport=transport, sleep=lambda _s: None),
            archive=Archive.open(root),
            emit=lambda _e: None,
        )

    run(forumset, "resume")
    grown = copy.deepcopy(forumset)
    second = copy.deepcopy(forumset.forums[0])
    second.uuid = "forum-second"
    second.title = "A Second Forum"
    grown.forums.append(second)
    run(grown, "update")

    titles = {f.title for f in derive(Archive.open(root)).forums}
    assert "A Second Forum" in titles, "an update missed a forum created since the last capture"


def test_an_update_finds_a_wiki_that_did_not_exist_before(tmp_path, dataset):
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")
    assert [w.label for w in derive(Archive.open(root)).wikis] == [dataset.wikis[0].label]

    grown = copy.deepcopy(dataset)
    second = copy.deepcopy(dataset.wikis[0])
    second.label = "second-wiki"
    second.uuid = "wiki-second"
    second.title = "A Second Wiki"
    grown.wikis.append(second)
    _crawl(grown, root, "update")

    labels = {w.label for w in derive(Archive.open(root)).wikis}
    assert "second-wiki" in labels, "an update missed a wiki created since the last capture"


def test_an_update_sees_a_wiki_renamed_since_the_last_capture(tmp_path, dataset):
    """The list feed carries the title. Read from the archive, an update kept
    reporting the old one."""
    root = tmp_path / "archive"
    _crawl(dataset, root, "resume")

    renamed = copy.deepcopy(dataset)
    renamed.wikis[0].title = "A Completely New Title"
    _crawl(renamed, root, "update")

    assert [w.title for w in derive(Archive.open(root)).wikis] == ["A Completely New Title"]


def test_an_update_finds_a_reply_added_to_an_existing_topic(tmp_path, dataset):
    """The topics feed is the change filter --
    a reply moves its topic's `updated` and the dated feed returns the topic.
    The replies themselves were `IF_MISSING`, which never refetches once
    present, so the topic came back and its replies came out of the archive.
    A reply to an old thread was lost, silently.
    """
    from connections_export.crawler import crawl_forums
    from connections_export.fakeserver.synth import synthesize_forums

    forumset = synthesize_forums(SynthSeed(seed=0))
    root = tmp_path / "archive"

    def run(forums, fetch):
        transport = httpx.MockTransport(
            SyncASGIBridge(make_app(dataset, forumset=forums)).handle_request
        )
        crawl_forums(
            config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
            client=HttpClient(transport=transport, sleep=lambda _s: None),
            archive=Archive.open(root),
            emit=lambda _e: None,
        )

    run(forumset, "resume")

    grown = copy.deepcopy(forumset)
    topic = grown.forums[0].topics[0]
    reply = copy.deepcopy(topic.replies[0])
    reply.uuid = "reply-brand-new"
    reply.content_html = "<p>A REPLY ADDED LATER</p>"
    topic.replies.append(reply)
    run(grown, "update")

    bodies = [
        r.content_html or ""
        for f in derive(Archive.open(root)).forums
        for t in f.topics.values()
        for r in t.replies.values()
    ]
    assert any("A REPLY ADDED LATER" in b for b in bodies), (
        "an update missed a reply added to an existing topic"
    )
