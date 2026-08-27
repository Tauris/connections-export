"""Re-checking the discussion under an item that did not itself change.

The hole this closes: a `since`-filtered entries or topics feed reports items
that MOVED. A new comment on an untouched post may not move it, so an update
that trusts the entry feed alone loses the comment. For wikis the same question applies to a
page's date.

a forum reply moves its
topic and the dated feed returns it; a wiki comment moves `atom:updated`,
which is the field the crawler gates on; a blog comment moves nothing at all
and a dated entries feed returns nothing. So blogs do not get an option --
they are re-checked always, because the alternative is losing discussion
silently -- while wikis and forums keep one, where the cost is visible and
the completeness is not at stake.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(
    seed=0, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=2, human_names=True
)
BASE = "https://fake"


class _Counting(httpx.BaseTransport):
    def __init__(self, app):
        self._inner = SyncASGIBridge(app)
        self.urls: list[str] = []

    def handle_request(self, request):
        self.urls.append(str(request.url))
        return self._inner.handle_request(request)


@pytest.fixture
def deployment(tmp_path):
    transport = _Counting(make_app(synthesize(SEED)))
    return transport, Archive.open(tmp_path / "archive")


def _is_comments_feed(url: str) -> bool:
    """A page's comments feed.

    It comes from the entry's own `rel="replies"` link, which carries NO
    `category` -- the reference states an omitted category means comments. So
    it is the page feed without a category, which is exactly what
    distinguishes it from the version and attachment feeds.
    """
    return "/page/" in url and "/feed?" in url and "category=" not in url


def _run(transport, archive, tmp_path, **kwargs) -> int:
    crawl(
        config=Config(base_url=BASE, output_dir=tmp_path, **kwargs),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=archive,
        emit=lambda _e: None,
    )
    return len([u for u in transport.urls if _is_comments_feed(u)])


def test_the_option_is_off_by_default(tmp_path):
    """Off means an update is cheap. The interface is where the trade is
    explained; the default here just has to be predictable."""
    assert Config().recheck_comments is False


def test_an_update_skips_comment_feeds_for_pages_that_did_not_move(deployment, tmp_path):
    transport, archive = deployment
    _run(transport, archive, tmp_path)
    transport.urls.clear()

    comment_requests = _run(transport, archive, tmp_path, fetch="update")

    assert comment_requests == 0


def test_re_checking_asks_for_them_anyway(deployment, tmp_path):
    """The point of the option: pay one request per item to be sure a new
    comment on an otherwise-untouched item is not missed."""
    transport, archive = deployment
    _run(transport, archive, tmp_path)
    transport.urls.clear()

    comment_requests = _run(transport, archive, tmp_path, fetch="update", recheck_comments=True)

    assert comment_requests > 0


def test_re_checking_costs_less_than_a_full_refresh(deployment, tmp_path):
    """It must remain an update, not a re-download: bodies, versions and
    attachments of unchanged pages are still left alone."""
    transport, archive = deployment
    _run(transport, archive, tmp_path)

    transport.urls.clear()
    _run(transport, archive, tmp_path, fetch="update", recheck_comments=True)
    rechecked = len(transport.urls)

    transport.urls.clear()
    _run(transport, archive, tmp_path, fetch="refresh")
    refreshed = len(transport.urls)

    assert rechecked < refreshed


def test_re_checking_does_nothing_outside_an_update(deployment, tmp_path):
    """Under `resume` the archive answers everything -- the option must not
    quietly turn finishing an interrupted crawl into a partial re-crawl."""
    transport, archive = deployment
    _run(transport, archive, tmp_path)
    transport.urls.clear()

    comment_requests = _run(transport, archive, tmp_path, recheck_comments=True)

    assert comment_requests == 0


def _blog_deployment(tmp_path):
    from connections_export.fakeserver.synth import synthesize_blogs

    blogset = synthesize_blogs(SEED)
    transport = _Counting(make_app(synthesize(SEED), blogset=blogset))
    return transport, Archive.open(tmp_path / "archive"), blogset


def _is_blog_comments_feed(url: str) -> bool:
    return "/blogs/" in url and "comments" in url


def test_a_blog_update_re_checks_comments_even_with_the_option_off(tmp_path):
    """The one app where this cannot be a choice.

    A comment on a blog post need not move the post's date or the dated
    entries feed, so an update that respects the option would lose new
    discussion on old posts, report success, and leave nothing downstream
    able to notice.
    """
    from connections_export.crawler import crawl_blogs

    transport, archive, blogset = _blog_deployment(tmp_path)
    client = HttpClient(transport=transport, sleep=lambda _s: None)
    crawl_blogs(
        config=Config(base_url=BASE, output_dir=tmp_path),
        client=client,
        archive=archive,
        emit=lambda _e: None,
        blogs_homepage=blogset.homepage,
    )
    transport.urls.clear()

    crawl_blogs(
        config=Config(base_url=BASE, output_dir=tmp_path, fetch="update"),
        client=client,
        archive=archive,
        emit=lambda _e: None,
        blogs_homepage=blogset.homepage,
    )

    assert [u for u in transport.urls if _is_blog_comments_feed(u)], (
        "a blog update asked for no comment feeds; new comments on old posts would be lost"
    )
