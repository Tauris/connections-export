"""What a feed walk actually fetched, written down.

Derive replays exactly the pages the crawler archived. The alternative is
re-implementing the crawler's stop condition and reconstructing the page
sequence from URL strings, guessing which `?since=` URLs belong to which
logical feed by substring match -- two rules that then drift apart.

The crawler knows the answer while it walks. Recording it turns a
re-derivation into a lookup, and there is nothing left to drift.
"""

from __future__ import annotations

from connections_export.archive.feeds import FeedPage
from connections_export.archive.store import Archive


def _page(feed_id: str, page: int, window: str | None = None, **kw) -> FeedPage:
    return FeedPage(
        feed_id=feed_id,
        page=page,
        window=window,
        url=f"https://host/{feed_id}?page={page}",
        item_count=kw.get("item_count", 5),
        page_size=kw.get("page_size", 5),
        has_next=kw.get("has_next", False),
        total_results=kw.get("total_results"),
        run_id=kw.get("run_id", "r-1"),
    )


def test_pages_come_back_canonical_window_first_then_each_update_window(tmp_path):
    """Order is load-bearing: a reader dedupes by item id, so the canonical
    copy of anything two windows overlap on is the one kept."""
    archive = Archive.open(tmp_path / "a")
    # Written out of order on purpose: an update runs after the first capture,
    # and page 2 of one window can land before page 1 of another.
    archive.record_feed_page(_page("blogs/entries", 2, window="2026-08-10T00:00:00Z"))
    archive.record_feed_page(_page("blogs/entries", 1))
    archive.record_feed_page(_page("blogs/entries", 1, window="2026-08-10T00:00:00Z"))
    archive.record_feed_page(_page("blogs/entries", 2))

    got = [(p.page, p.window) for p in archive.feed_pages("blogs/entries")]

    assert got == [
        (1, None),
        (2, None),
        (1, "2026-08-10T00:00:00Z"),
        (2, "2026-08-10T00:00:00Z"),
    ]


def test_one_feeds_pages_are_not_another_feeds(tmp_path):
    archive = Archive.open(tmp_path / "a")
    archive.record_feed_page(_page("blogs/entries", 1))
    archive.record_feed_page(_page("forums/topics", 1))

    assert [p.feed_id for p in archive.feed_pages("blogs/entries")] == ["blogs/entries"]


def test_a_feed_nobody_walked_has_no_pages(tmp_path):
    archive = Archive.open(tmp_path / "a")

    assert archive.feed_pages("wikis/nav") == []


def test_a_full_final_page_with_no_next_and_no_total_is_flagged(tmp_path):
    """The shape a deployment silently truncates with: exactly what was asked
    for, and no admission there is more."""
    archive = Archive.open(tmp_path / "a")
    archive.record_feed_page(_page("wikis/feed", 9, item_count=5, page_size=5, has_next=False))

    assert archive.report().possibly_truncated == ["https://host/wikis/feed?page=9"]


def test_a_feed_that_reported_its_own_total_is_not_flagged(tmp_path):
    """An exact-boundary page is only suspicious when nothing corroborates it.
    Without this the heuristic fires on every feed whose count is an exact
    multiple of the page size -- which is what happened the moment it was
    given real walks instead of hand-written records."""
    archive = Archive.open(tmp_path / "a")
    archive.record_feed_page(
        _page("wikis/feed", 2, item_count=5, page_size=5, has_next=False, total_results=10)
    )

    assert archive.report().possibly_truncated == []
