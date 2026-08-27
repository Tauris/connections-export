"""Derive replays what the crawler recorded, rather than re-deriving it.

The two paginators implemented one contract from opposite ends and drifted
four separate ways: an empty page was authoritative in the crawler and not in
derive; `totalResults` was weighed by one and not the other; tuple items were
unwrapped for dedup by one and not the other; and the page cap was bound by
value into two namespaces. Three of the four produced a model that was
silently short while the run reported success.

Sharing the rule keeps two implementations honest. Recording the answer means
there is only one. These pin that the recording is what derive actually uses.
"""

from __future__ import annotations

from connections_export.adapters.wikis import parse_wikis_feed
from connections_export.archive.feeds import FeedPage
from connections_export.archive.store import Archive
from connections_export.derive.index import ArchiveIndex
from connections_export.paging import feed_identity

BASE = "https://fake/wikis/basic/api/wikis/feed"


def _feed(*, entries: int, total: int, offset: int = 0) -> bytes:
    items = "".join(
        f"<entry><id>urn:lsid:ibm.com:wikis:wiki-{offset + i}</id>"
        f"<title>Wiki {offset + i}</title><td:label>w{offset + i}</td:label></entry>"
        for i in range(entries)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td"'
        ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        f"<opensearch:totalResults>{total}</opensearch:totalResults>"
        f"{items}</feed>"
    ).encode()


class _Response:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.method = "GET"
        self.status = 200
        self.content = content
        self.headers = {"content-type": "application/atom+xml"}
        self.request_headers = {}
        self.elapsed_ms = 1
        self.redirect_location = None


def _archive_with_recorded_walk(tmp_path) -> Archive:
    """Two pages, recorded as a walk. Page 1 is SHORT and its total says more
    remain -- the exact shape that used to make derive stop early."""
    store = Archive.open(tmp_path / "a")
    pages = [
        (f"{BASE}?ps=10&page=1", _feed(entries=3, total=6, offset=0), 1),
        (f"{BASE}?ps=10&page=2", _feed(entries=3, total=6, offset=3), 2),
    ]
    for url, body, number in pages:
        store.write_response(_Response(url, body), fetched_at="2026-08-10T21:14:00Z")
        store.record_feed_page(
            FeedPage(
                feed_id=feed_identity(url),
                page=number,
                url=url,
                item_count=3,
                page_size=10,
                has_next=False,
                total_results=6,
                run_id="r-1",
            )
        )
    return store


def test_a_recorded_walk_is_replayed_in_full(tmp_path):
    store = _archive_with_recorded_walk(tmp_path)

    items = ArchiveIndex.open(store).paginate(BASE, parse_wikis_feed, page_size=10)

    assert len(items) == 6, "derive did not read back every page the crawler recorded"


def test_the_replay_path_is_the_one_taken(tmp_path):
    """Not just the right answer -- the right route. The reconstruction path
    would give the same result here, so a behavioural assertion alone would
    not tell us the recording is being used at all."""
    store = _archive_with_recorded_walk(tmp_path)
    index = ArchiveIndex.open(store)

    assert index._recorded.get(feed_identity(BASE)), "the recorded walk was not loaded"

    calls: list = []
    original = index._reconstruct
    index._reconstruct = lambda *a, **kw: calls.append(a) or original(*a, **kw)  # type: ignore[method-assign]

    index.paginate(BASE, parse_wikis_feed, page_size=10)

    assert not calls, "derive reconstructed the sequence instead of replaying it"


def test_an_archive_with_no_recorded_walk_still_derives(tmp_path):
    """Archives captured before the crawler recorded its walks must keep
    working -- through the same shared stop rule, so the fallback cannot drift
    the way its predecessor did."""
    store = Archive.open(tmp_path / "a")
    store.write_response(
        _Response(f"{BASE}?ps=10&page=1", _feed(entries=3, total=3)),
        fetched_at="2026-08-10T21:14:00Z",
    )

    items = ArchiveIndex.open(store).paginate(BASE, parse_wikis_feed, page_size=10)

    assert len(items) == 3


def test_a_page_recorded_but_missing_from_the_manifest_does_not_stop_the_replay(tmp_path):
    """A gap in the archive is a gap, not a signal to stop reading. The
    remaining pages were still fetched and still belong in the model -- the
    opposite of the reconstruction path, where a missing page is the only way
    to know the sequence ended."""
    store = _archive_with_recorded_walk(tmp_path)
    store.record_feed_page(
        FeedPage(
            feed_id=feed_identity(BASE),
            page=0,
            url=f"{BASE}?ps=10&page=0",
            item_count=3,
            page_size=10,
            has_next=True,
            run_id="r-1",
        )
    )

    items = ArchiveIndex.open(store).paginate(BASE, parse_wikis_feed, page_size=10)

    assert len(items) == 6, "a missing recorded page truncated the replay"
