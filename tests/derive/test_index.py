"""`ArchiveIndex` reads manifest + blobs, returns the latest
`ok` response (bytes + content-type) per URL, and exposes failure URLs
so assembly can tell "never fetched" from "fetched but failed" apart. Also covers the
pagination-aware page
walk -- every page, not just page 1 -- mirrored from
`crawler.crawl.paginate`, read-only, from the archive.
"""

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from connections_export.derive.index import ArchiveIndex
from tests.archive.conftest import FakeResponse

BASE = "https://fake/wikis/basic/api"


def _archive(tmp_path) -> Archive:
    return Archive.open(tmp_path / "archive")


def test_get_returns_bytes_and_content_type_for_an_ok_record(tmp_path):
    archive = _archive(tmp_path)
    archive.write_response(
        FakeResponse(
            url=f"{BASE}/wikis/feed",
            method="GET",
            status=200,
            headers={"Content-Type": "application/atom+xml"},
            content=b"<feed/>",
        ),
        fetched_at="2026-01-01T00:00:00Z",
    )

    index = ArchiveIndex.open(archive)
    response = index.get(f"{BASE}/wikis/feed")

    assert response is not None
    assert response.content == b"<feed/>"
    assert response.content_type == "application/atom+xml"
    assert response.body_hash.startswith("sha256:")


def test_get_returns_none_for_a_url_never_recorded(tmp_path):
    archive = _archive(tmp_path)
    index = ArchiveIndex.open(archive)

    assert index.get(f"{BASE}/nope") is None


def test_get_prefers_the_latest_ok_record_for_a_repeated_url(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/wiki/wiki0/page/p0/entry"
    archive.write_response(
        FakeResponse(url=url, method="GET", status=200, content=b"<entry>old</entry>"),
        fetched_at="2026-01-01T00:00:00Z",
    )
    archive.write_response(
        FakeResponse(url=url, method="GET", status=200, content=b"<entry>new</entry>"),
        fetched_at="2026-01-02T00:00:00Z",
    )

    index = ArchiveIndex.open(archive)

    assert index.get(url).content == b"<entry>new</entry>"


def test_failure_exposes_a_url_that_only_ever_failed(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/wiki/wiki0/page/broken/entry"
    archive.write_failure(
        url=url,
        method="GET",
        fetched_at="2026-01-01T00:00:00Z",
        outcome=Outcome.transport_error,
        error="connection reset",
    )

    index = ArchiveIndex.open(archive)

    assert index.get(url) is None
    failure = index.failure(url)
    assert failure is not None
    assert failure.error == "connection reset"
    assert failure.outcome == Outcome.transport_error


def test_failure_is_none_for_a_url_that_succeeded(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/ok"
    archive.write_response(
        FakeResponse(url=url, method="GET", status=200, content=b"ok"),
        fetched_at="2026-01-01T00:00:00Z",
    )

    index = ArchiveIndex.open(archive)

    assert index.failure(url) is None


def test_failure_is_none_for_a_url_never_seen_at_all(tmp_path):
    archive = _archive(tmp_path)
    index = ArchiveIndex.open(archive)

    assert index.failure(f"{BASE}/never-touched") is None


def test_index_over_an_empty_archive_has_nothing(tmp_path):
    archive = _archive(tmp_path)
    index = ArchiveIndex.open(archive)

    assert index.get(f"{BASE}/wikis/feed") is None
    assert index.failure(f"{BASE}/wikis/feed") is None


# --- pagination-aware collection -------------------------------


def _write_feed_page(archive, url, *, page, items_xml, next_page=None):
    entries = "".join(f"<id>urn:lsid:ibm.com:td:{i}</id>" for i in items_xml)
    next_link = f'<link rel="next" href="{url}?ps=2&amp;page={next_page}"/>' if next_page else ""
    body = f"<feed>{entries}{next_link}</feed>".encode()
    archive.write_response(
        FakeResponse(url=f"{url}?ps=2&page={page}", method="GET", status=200, content=body),
        fetched_at="2026-01-01T00:00:00Z",
    )


def _dumb_parser(content: bytes) -> list[str]:
    from lxml import etree

    root = etree.fromstring(content)
    return [el.text for el in root.findall("id")]


def test_paginate_walks_every_archived_page(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/wiki/wiki0/page/p0/feed"
    _write_feed_page(archive, url, page=1, items_xml=["a", "b"], next_page=2)
    _write_feed_page(archive, url, page=2, items_xml=["c"], next_page=None)

    index = ArchiveIndex.open(archive)
    items = index.paginate(url, _dumb_parser, page_size=2)

    assert items == ["urn:lsid:ibm.com:td:a", "urn:lsid:ibm.com:td:b", "urn:lsid:ibm.com:td:c"]


def test_paginate_stops_at_the_first_page_missing_from_the_archive(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/wiki/wiki0/page/p0/feed"
    # Only page 1 was ever archived (e.g. the crawl failed thereafter).
    _write_feed_page(archive, url, page=1, items_xml=["a", "b"], next_page=2)

    index = ArchiveIndex.open(archive)
    items = index.paginate(url, _dumb_parser, page_size=2)

    assert items == ["urn:lsid:ibm.com:td:a", "urn:lsid:ibm.com:td:b"]


def test_paginate_returns_empty_list_when_nothing_was_ever_archived(tmp_path):
    archive = _archive(tmp_path)
    index = ArchiveIndex.open(archive)

    assert index.paginate(f"{BASE}/wiki/wiki0/page/p0/feed", _dumb_parser, page_size=2) == []


def test_paginate_preserves_existing_query_params_like_category(tmp_path):
    archive = _archive(tmp_path)
    url = f"{BASE}/wiki/wiki0/page/p0/feed?category=version"
    body = b"<feed><id>urn:lsid:ibm.com:td:v1</id></feed>"
    archive.write_response(
        FakeResponse(url=f"{url}&ps=2&page=1", method="GET", status=200, content=body),
        fetched_at="2026-01-01T00:00:00Z",
    )

    index = ArchiveIndex.open(archive)
    items = index.paginate(url, _dumb_parser, page_size=2)

    assert items == ["urn:lsid:ibm.com:td:v1"]
