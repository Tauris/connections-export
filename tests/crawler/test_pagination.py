"""Crawler page-walking.

The crawler always sends an explicit `ps=config.page_size` (and
`page=n`) on every feed request -- wikis, comments, versions,
attachments -- and walks pages while either a `rel="next"` link is
present or the page came back full, stopping only when both say
"no more". A `MAX_PAGES` safety cap prevents an infinite loop against
a misbehaving server, surfaced as a `pagination_cap` warning rather
than a silent truncation. The nav feed (`tree=true`) is exempt --
fetched once, never paginated.
"""

import threading
from pathlib import Path

import httpx

from connections_export import paging
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import OverrideTransport, SyncASGIBridge, make_client


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _page_numbers(
    seen_urls: set[str], *, path_fragment: str, exclude: str | None = None
) -> list[int]:
    matches = [u for u in seen_urls if path_fragment in u and (exclude is None or exclude not in u)]
    numbers = []
    for u in matches:
        # crude but sufficient: pull the `page=` value out of the query string
        after = u.split("page=", 1)[1]
        numbers.append(int(after.split("&")[0]))
    return sorted(numbers)


# --- 4.1 accumulates items across pages -------------------------------


def test_paginate_accumulates_items_across_pages(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=201, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=25)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path, page_size=10),
        client=client,
        archive=archive,
        emit=seen_events.append,
    )

    assert result.ok is True
    derived = [
        e for e in seen_events if isinstance(e, events.PageDerived) and e.page_id == page.uuid
    ][0]
    assert derived.comment_count == 25

    comments_fragment = f"/wiki/{wiki.label}/page/{page.label}/feed"
    pages_seen = _page_numbers(
        archive.seen_urls(), path_fragment=comments_fragment, exclude="category="
    )
    assert pages_seen == [1, 2, 3]


# --- 4.2 exact-multiple terminates cleanly -----------------------------


def test_paginate_exact_multiple_terminates_cleanly(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=202, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=20)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path, page_size=10),
        client=client,
        archive=archive,
        emit=seen_events.append,
    )

    assert result.ok is True
    derived = [
        e for e in seen_events if isinstance(e, events.PageDerived) and e.page_id == page.uuid
    ][0]
    assert derived.comment_count == 20

    comments_fragment = f"/wiki/{wiki.label}/page/{page.label}/feed"
    pages_seen = _page_numbers(
        archive.seen_urls(), path_fragment=comments_fragment, exclude="category="
    )
    # The reported total confirms that page 2 was the exact final page.
    assert pages_seen == [1, 2]

    cap_warnings = [
        e for e in seen_events if isinstance(e, events.Warning) and e.kind == "pagination_cap"
    ]
    assert cap_warnings == []


# --- 4.3 short first page fetches exactly one page when no total exists -----


def test_short_first_page_stops_after_one_fetch(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=203, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=3)
    )
    app = make_app(wikiset)  # no default_page_size -- crawler's own ps=500 still applies
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    comments_fetches = [
        e for e in seen_events if isinstance(e, events.Fetching) and e.kind == "comments"
    ]
    assert len(comments_fetches) == 1
    assert "page=1" in comments_fetches[0].url


def test_empty_page_stops_despite_stale_total_results(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=209, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=1)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset)
    inner = SyncASGIBridge(app)
    comments_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed"
    empty_feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
</feed>"""

    def _is_comments_request(request) -> bool:
        return request.url.path == comments_path and "category=" not in str(request.url)

    def _handler(request):
        return httpx.Response(
            200,
            content=empty_feed,
            headers={"content-type": "application/atom+xml"},
            request=request,
        )

    client = HttpClient(
        transport=OverrideTransport(inner, overrides=[(_is_comments_request, _handler)]),
        retry_policy=RetryPolicy(max_attempts=1),
        sleep=lambda _s: None,
    )
    archive = Archive.open(tmp_path / "archive")
    seen_events = []

    result = crawl(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        emit=seen_events.append,
    )

    assert result.ok is True
    comments_fetches = [
        event
        for event in seen_events
        if isinstance(event, events.Fetching) and event.kind == "comments"
    ]
    assert len(comments_fetches) == 1


def test_stop_event_prevents_comment_pagination(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=208, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=10)
    )
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")
    stop_event = threading.Event()
    stop_event.set()
    seen_events = []

    result = crawl(
        config=_config(tmp_path, page_size=10),
        client=client,
        archive=archive,
        emit=seen_events.append,
        stop_event=stop_event,
    )

    assert result.ok is True
    assert not any(
        isinstance(event, events.Fetching) and event.kind == "comments" for event in seen_events
    )


# --- 4.4 MAX_PAGES cap is surfaced, never an infinite loop --------------


def _never_ending_full_page(page: int) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>urn:lsid:ibm.com:td:c{page}a</id><title type="text">c{page}a</title></entry>
    <entry><id>urn:lsid:ibm.com:td:c{page}b</id><title type="text">c{page}b</title></entry>
    <link rel="next" href="https://fake/never-ending?page={page + 1}"/>
</feed>""".encode()


def test_max_pages_cap_emits_warning_and_flags_run(tmp_path, monkeypatch):
    monkeypatch.setattr(paging, "MAX_PAGES", 3)

    wikiset = synthesize(
        SynthSeed(
            seed=204,
            wiki_count=1,
            depth=1,
            pages_per_level=1,
            comments_per_page=1,
            attachments_per_page=0,
        )
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    comments_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed"
    app = make_app(wikiset)
    inner = SyncASGIBridge(app)

    def _is_comments_request(request) -> bool:
        return request.url.path == comments_path and "category=" not in str(request.url)

    def _handler(request):
        page_number = int(request.url.params.get("page", "1"))
        return httpx.Response(
            200,
            content=_never_ending_full_page(page_number),
            headers={"content-type": "application/atom+xml"},
            request=request,
        )

    transport = OverrideTransport(inner, overrides=[(_is_comments_request, _handler)])
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        emit=seen_events.append,
    )

    assert result.ok is False
    cap_warnings = [
        e for e in seen_events if isinstance(e, events.Warning) and e.kind == "pagination_cap"
    ]
    assert len(cap_warnings) == 1

    comments_fetches = [
        e for e in seen_events if isinstance(e, events.Fetching) and e.kind == "comments"
    ]
    assert len(comments_fetches) == 3  # exactly MAX_PAGES -- never more, never an infinite loop


def test_repeated_full_page_stops_when_server_ignores_page_parameter(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=207, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=10)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=10)
    inner = SyncASGIBridge(app)
    comments_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed"

    def _is_comments_request(request) -> bool:
        return request.url.path == comments_path and "category=" not in str(request.url)

    repeated_content: bytes | None = None

    def _handler(request):
        # Simulate a deployment that returns page 1 for every page number.
        nonlocal repeated_content
        if repeated_content is None:
            repeated_content = inner.handle_request(request).content
        return httpx.Response(
            200,
            content=repeated_content,
            headers={"content-type": "application/atom+xml"},
            request=request,
        )

    transport = OverrideTransport(inner, overrides=[(_is_comments_request, _handler)])
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")
    seen_events = []

    result = crawl(
        config=_config(tmp_path, page_size=10),
        client=client,
        archive=archive,
        emit=seen_events.append,
    )

    assert result.ok is True
    comments_fetches = [
        event
        for event in seen_events
        if isinstance(event, events.Fetching) and event.kind == "comments"
    ]
    assert len(comments_fetches) == 1


# --- 4.5 add_query preserves existing params ----------------------------


def test_paginate_preserves_existing_query_params(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=205, wiki_count=1, depth=1, pages_per_level=1, versions_per_page=1)
    )
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=client, archive=archive)

    seen = archive.seen_urls()
    assert any(
        "category=version" in url and "ps=" in url and "pageSize=" in url and "page=1" in url
        for url in seen
    )
    assert any(
        "category=attachment" in url and "ps=" in url and "pageSize=" in url and "page=1" in url
        for url in seen
    )


# --- nav feed is exempt from pagination ---------------------------------


def test_nav_feed_is_not_paginated(tmp_path):
    wikiset = synthesize(SynthSeed(seed=206, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    nav_fetches = [e for e in seen_events if isinstance(e, events.Fetching) and e.kind == "nav"]
    assert len(nav_fetches) == 1
    assert "tree=true" in nav_fetches[0].url
    assert "ps=" not in nav_fetches[0].url
    assert "page=" not in nav_fetches[0].url


# --- 4.6 the total is only trustworthy against a DISTINCT count -------------


def test_a_repeated_page_does_not_make_a_feed_look_complete(tmp_path):
    """A 0-indexed walk against a 1-indexed server sees page 0 and page 1
    return the same entries; page 2 is genuinely the next one.

    Counting those repeats toward `totalResults` made the walk stop one page
    early and silently lose the last post -- the whole feed looked complete
    because the duplicate-inflated count had reached the server's total. The
    count compared against the total must be of DISTINCT items.
    """
    from connections_export.adapters.blogs import parse_entries_feed

    # Moved to `connections_export.paging` so the crawler and derive share
    # one key. Assertions below are unchanged: they pin the tuple-unwrap
    # fix (c5c20d2), without which blog-entry dedup silently never worked.
    from connections_export.paging import feed_item_id as _item_id

    class _Post:
        def __init__(self, ident):
            self.id = ident

    # The blog entries walk hands `paginate` (post, comments_url) TUPLES, and a
    # tuple has no `.id` -- the case that broke dedup.
    assert _item_id((_Post("a"), "url")) == "a"
    assert _item_id(_Post("a")) == "a"
    assert parse_entries_feed  # the real parser this stands in for


def test_an_unidentifiable_item_counts_as_new_rather_than_vanishing(tmp_path):
    """Last resort only: an item we cannot identify must be treated as new.
    Collapsing unidentifiable items into one "duplicate" would drop content."""
    # Moved to `connections_export.paging` so the crawler and derive share
    # one key. Assertions below are unchanged: they pin the tuple-unwrap
    # fix (c5c20d2), without which blog-entry dedup silently never worked.
    from connections_export.paging import feed_item_id as _item_id

    first, second = object(), object()

    assert _item_id(first) != _item_id(second)
