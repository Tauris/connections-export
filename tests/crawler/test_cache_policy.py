"""When a request may be answered from the archive, and when it must not be.

`resume` and `refresh` decided this globally: use the cache for everything, or
for nothing. An update needs the decision made per request, because the two
kinds of request play opposite roles:

  * the documents that REVEAL change -- feeds, search result pages, the rich
    content layout -- must always be refetched, or the run cannot see what
    moved;
  * the expensive parts of an item -- body, comments, versions, assets -- must
    be refetched only when that item's own date says it moved.

The second comparison is content date against content date. Neither this
machine's clock nor the server's enters into it, so a skewed clock cannot cost
content.
"""

from __future__ import annotations

import httpx

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.engine import CachePolicy, begin_run
from connections_export.http.client import HttpClient

URL = "https://fake/wikis/basic/api/wiki/w/feed"


def _serving(body: bytes, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"content-type": "application/atom+xml"})

    return HttpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None)


def _fetcher(tmp_path, *, fetch: str, calls: list, body: bytes = b"<feed/>") -> tuple:
    archive = Archive.open(tmp_path / "archive")
    session = begin_run(
        config=Config(base_url="https://fake", fetch=fetch, output_dir=tmp_path),
        client=_serving(body, calls),
        archive=archive,
        emit=lambda _e: None,
        clock=lambda: "2026-08-10T21:14:00Z",
        adapter_version="test-1",
        run_id="r1",
    )
    return session.fetcher, archive


def _prime(tmp_path, calls, body=b"<feed/>"):
    """One run, so the archive already holds the URL."""
    fetcher, _archive = _fetcher(tmp_path, fetch="resume", calls=calls, body=body)
    fetcher.get_content(URL, "wiki", None)
    return len(calls)


def test_resume_answers_a_known_url_from_the_archive(tmp_path):
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="resume", calls=calls)
    before = len(calls)
    fetcher.get_content(URL, "wiki", None)

    assert len(calls) == before, "resume went back to the network for a URL it had"


def test_update_always_refetches_a_feed(tmp_path):
    """A feed is the instrument the run detects change with. Serving it from
    the archive would make an update find nothing, every time."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="update", calls=calls)
    before = len(calls)
    fetcher.get_content(URL, "wiki", None, policy=CachePolicy.ALWAYS)

    assert len(calls) == before + 1


def test_update_leaves_an_unchanged_item_alone(tmp_path):
    """The archived copy carries the item's date. The live feed says the same
    date. There is nothing to fetch."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="update", calls=calls)
    fetcher.remember_item_date(URL, "2026-08-01T00:00:00Z")
    before = len(calls)
    fetcher.get_content(
        URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date="2026-08-01T00:00:00Z"
    )

    assert len(calls) == before


def test_update_refetches_an_item_whose_own_date_moved(tmp_path):
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="update", calls=calls)
    fetcher.remember_item_date(URL, "2026-08-01T00:00:00Z")
    before = len(calls)
    fetcher.get_content(
        URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date="2026-08-09T12:00:00Z"
    )

    assert len(calls) == before + 1


def test_an_item_with_no_recorded_date_is_refetched(tmp_path):
    """Not knowing when the archived copy was written is not evidence that it
    is current. The safe reading of "I cannot tell" is "fetch it"."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="update", calls=calls)
    before = len(calls)
    fetcher.get_content(
        URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date="2026-08-09T12:00:00Z"
    )

    assert len(calls) == before + 1


def test_an_item_the_live_feed_gives_no_date_for_is_refetched(tmp_path):
    """Same rule from the other side: a live entry with no date tells us
    nothing, so it is not evidence of being unchanged."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="update", calls=calls)
    fetcher.remember_item_date(URL, "2026-08-01T00:00:00Z")
    before = len(calls)
    fetcher.get_content(URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date=None)

    assert len(calls) == before + 1


def test_refresh_ignores_every_policy(tmp_path):
    """`refresh` means what it always meant. A policy that could re-enable the
    cache under it would make "refetch everything" a lie."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="refresh", calls=calls)
    fetcher.remember_item_date(URL, "2026-08-01T00:00:00Z")
    before = len(calls)
    fetcher.get_content(
        URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date="2026-08-01T00:00:00Z"
    )

    assert len(calls) == before + 1


def test_resume_ignores_policies_too(tmp_path):
    """Under `resume`, ALWAYS must not start refetching feeds -- that would
    turn finishing an interrupted crawl into a partial re-crawl."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, _ = _fetcher(tmp_path, fetch="resume", calls=calls)
    before = len(calls)
    fetcher.get_content(URL, "wiki", None, policy=CachePolicy.ALWAYS)

    assert len(calls) == before


def test_a_refetched_item_records_its_new_date(tmp_path):
    """So the NEXT update can compare against it. An update that fetched
    without recording the date would refetch the same item forever."""
    calls: list = []
    _prime(tmp_path, calls)

    fetcher, archive = _fetcher(tmp_path, fetch="update", calls=calls)
    fetcher.get_content(
        URL, "wiki", None, policy=CachePolicy.IF_CHANGED, item_date="2026-08-09T12:00:00Z"
    )

    dates = [r.item_date for r in archive._read_records() if r.url == URL and r.item_date]
    assert "2026-08-09T12:00:00Z" in dates
