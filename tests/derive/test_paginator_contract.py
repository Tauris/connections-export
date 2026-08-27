"""Derive must read back every page the crawler archived.

The two paginators implement one contract from opposite ends: the crawler
decides how far to walk a feed, and derive replays exactly that walk from the
archive. When their stop conditions disagree, the archive holds bytes nothing
ever reads -- the crawl reports success, the manifest is complete, and the
derived model is short.

The specific divergence these pin: the crawler continues past a SHORT page when
the feed's own `totalResults` says more remain. Derive stopped at the short
page, so everything the crawler fetched after it was archived and never seen.

Found by a peer session reading both sides; verified here from the archive
outward.
"""

from __future__ import annotations

import pytest

from connections_export.adapters.wikis import parse_wikis_feed
from connections_export.archive.store import Archive
from connections_export.derive.index import ArchiveIndex


def _feed(*, entries: int, total: int, offset: int = 0, next_link: bool = False) -> bytes:
    items = "".join(
        f"<entry><id>urn:lsid:ibm.com:wikis:wiki-{offset + i}</id>"
        f"<title>Wiki {offset + i}</title><td:label>w{offset + i}</td:label></entry>"
        for i in range(entries)
    )
    nxt = '<link rel="next" href="https://fake/next"/>' if next_link else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td"'
        ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        f"<opensearch:totalResults>{total}</opensearch:totalResults>"
        f"{nxt}{items}</feed>"
    ).encode()


class _Response:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.method = "GET"
        self.status = 200
        self.headers = {"content-type": "application/atom+xml"}
        self.content = content


@pytest.fixture
def archive(tmp_path):
    """A feed whose first page is SHORT but whose total promises more.

    A deployment that under-delivers per page does exactly this, which is why
    the crawler has a `totalResults` term at all.
    """
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    store.write_response(
        _Response(f"{base}?ps=10&page=1", _feed(entries=4, total=7)),
        fetched_at="2026-08-10T21:14:00Z",
    )
    store.write_response(
        _Response(f"{base}?ps=10&page=2", _feed(entries=3, total=7, offset=4)),
        fetched_at="2026-08-10T21:15:00Z",
    )
    return store, base


def test_derive_reads_past_a_short_page_the_total_contradicts(archive):
    """The bug: derive stopped at page 1 because it was short and carried no
    `rel="next"`, leaving page 2 archived and unread."""
    store, base = archive

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 7, "derive stopped early and lost what the crawler archived"


def test_a_short_page_still_ends_the_walk_when_the_total_agrees(tmp_path):
    """The counterweight. A short final page whose total is satisfied must
    still stop -- otherwise every feed walks to the safety cap."""
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    store.write_response(
        _Response(f"{base}?ps=10&page=1", _feed(entries=4, total=4)),
        fetched_at="2026-08-10T21:14:00Z",
    )

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 4


def test_a_feed_with_no_total_falls_back_to_the_old_rule(tmp_path):
    """Wikis are documented as not reliably carrying `totalResults`, so its
    absence must keep the previous behaviour rather than walking forever."""
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">'
        b"<entry><id>urn:lsid:ibm.com:wikis:wiki-0</id><title>Wiki 0</title>"
        b"<td:label>w0</td:label></entry></feed>"
    )
    store.write_response(_Response(f"{base}?ps=10&page=1", body), fetched_at="2026-08-10T21:14:00Z")

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 1


def test_a_missing_next_page_ends_the_walk_even_if_the_total_wants_more(tmp_path):
    """An over-stated total must not make derive loop to the safety cap: the
    archive is the authority on what was actually fetched."""
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    store.write_response(
        _Response(f"{base}?ps=10&page=1", _feed(entries=4, total=99)),
        fetched_at="2026-08-10T21:14:00Z",
    )

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 4


# --- the same rule, inside an update's own window ---------------------------
#
# `paginate` shares one `all_items` across sequences, so by the time an
# update window is walked it already holds the canonical items. Comparing that
# accumulated count against the WINDOW's own (smaller) total makes the
# continuation rule inert exactly where the update path needs it -- and an
# update's date-filtered feed can page short-then-more for the same reason the
# canonical feed can.


def test_an_update_window_also_reads_past_a_short_page(tmp_path):
    """The canonical run and the update run must obey the same rule. Fixing
    only the first leaves the update path with the bug it was fixed for."""
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    # The original capture: one full-enough page, total satisfied.
    store.write_response(
        _Response(f"{base}?ps=10&page=1", _feed(entries=2, total=2)),
        fetched_at="2026-08-10T21:14:00Z",
    )
    # An update window that under-delivers: 1 of 3 on its first page.
    window = "since=2026-08-10T21%3A13%3A00.000Z&sortBy=modified"
    store.write_response(
        _Response(f"{base}?{window}&ps=10&page=1", _feed(entries=1, total=3, offset=10)),
        fetched_at="2026-09-01T09:00:00Z",
    )
    store.write_response(
        _Response(f"{base}?{window}&ps=10&page=2", _feed(entries=2, total=3, offset=11)),
        fetched_at="2026-09-01T09:01:00Z",
    )

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 5, "the update window stopped at its short first page"
