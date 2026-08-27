"""Derive's page-walk safety cap must be controllable, and never silent.

The crawler treats its cap as a first-class thing: `MAX_PAGES` is read at each
call site and passed into `Fetcher.paginate(max_pages=...)` so a test can lower
it, and hitting it marks an issue and emits a `pagination_cap` warning --
"never a silent truncation, never an infinite loop" (engine.py).

Derive, which replays the crawler's walk from the archive, did neither. It
imported `MAX_PAGES` by value, so `monkeypatch.setattr(crawl, "MAX_PAGES", 3)`
could not reach it, and it stopped at the cap without recording anything. A
short read looked exactly like a complete one.

The cap now lives in `connections_export.paging`, below both readers, and both
read it through the module. The cap only bounds derive's LEGACY reconstruction
path -- a recorded walk is replayed in full, because the crawler already
decided where it ended.

These pin both halves: the cap is injectable, and reaching it is surfaced.
"""

from __future__ import annotations

import pytest

from connections_export import paging
from connections_export.adapters.wikis import parse_wikis_feed
from connections_export.archive.store import Archive
from connections_export.derive.index import ArchiveIndex


def _feed(*, entries: int, total: int, offset: int = 0) -> bytes:
    """A FULL page whose total promises more -- a feed that keeps a walk going."""
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
        self.headers = {"content-type": "application/atom+xml"}
        self.content = content


@pytest.fixture
def endless_feed(tmp_path):
    """Five archived pages, every one full and every one promising more.

    Nothing in the feed itself ever says "stop", so only the cap ends this
    walk -- which is what makes it able to observe the cap at all.
    """
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    for page in range(1, 6):
        store.write_response(
            _Response(
                f"{base}?ps=10&page={page}",
                _feed(entries=10, total=999, offset=(page - 1) * 10),
            ),
            fetched_at="2026-08-10T21:14:00Z",
        )
    return store, base


def test_the_cap_can_be_lowered_for_one_index(endless_feed):
    """Injectable, so a test need not walk 10,000 pages to exercise the cap.

    The crawler's cap is a call-time parameter for exactly this reason; derive
    read a module global imported by value, which no monkeypatch could reach.
    """
    store, base = endless_feed

    items = ArchiveIndex.open(store, max_pages=3).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 30, "the cap did not bound the walk at three pages"


def test_reaching_the_cap_is_recorded_rather_than_silent(endless_feed):
    """A truncated read must be distinguishable from a complete one.

    Without this the archive holds pages nothing read back and the derived
    model simply looks shorter -- the same silent gap the crawler refuses.
    """
    store, base = endless_feed
    index = ArchiveIndex.open(store, max_pages=3)

    index.paginate(base, parse_wikis_feed, page_size=10)

    assert index.hit_pagination_cap(base), "derive truncated at the cap and said nothing"


def test_a_walk_that_ends_on_its_own_is_not_reported_as_capped(tmp_path):
    """The counterweight: only truncation is news.

    A feed that ends because it ran out must not be flagged, or the signal
    means nothing.
    """
    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    store.write_response(
        _Response(f"{base}?ps=10&page=1", _feed(entries=4, total=4)),
        fetched_at="2026-08-10T21:14:00Z",
    )
    index = ArchiveIndex.open(store, max_pages=3)

    items = index.paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 4
    assert not index.hit_pagination_cap(base)


def test_the_default_cap_tracks_the_crawler_at_call_time(tmp_path, monkeypatch):
    """Derive's default must be the crawler's cap, read late enough to follow it.

    `from...paging import MAX_PAGES` would bind the int into derive's
    namespace, so the two drift apart the moment anything changes one. Both
    sides read `paging.MAX_PAGES` through the module, which is why patching it
    here reaches the crawler and derive at once.

    This assertion is unchanged from when the cap lived in `crawler.crawl` and
    derive reached it via `importlib.import_module` -- a string module
    reference no rename or linter would have followed. Only the patch target
    moved, from an alias to the definition.
    """

    store = Archive.open(tmp_path / "archive")
    base = "https://fake/wikis/basic/api/wikis/feed"
    for page in range(1, 4):
        store.write_response(
            _Response(
                f"{base}?ps=10&page={page}",
                _feed(entries=10, total=999, offset=(page - 1) * 10),
            ),
            fetched_at="2026-08-10T21:14:00Z",
        )
    monkeypatch.setattr(paging, "MAX_PAGES", 2)

    items = ArchiveIndex.open(store).paginate(base, parse_wikis_feed, page_size=10)

    assert len(items) == 20, "derive ignored the crawler's cap"
