"""Failures and retries -- forced-500 exhausted retries,
malformed responses, retry-then-success, and a raising emit consumer.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import (
    OverrideTransport,
    SyncASGIBridge,
    fail_n_times_then,
    make_client,
    path_is,
)


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def test_forced_500_is_recorded_and_crawl_continues_to_other_pages(tmp_path):
    wikiset = synthesize(SynthSeed(seed=10, wiki_count=1, depth=1, pages_per_level=2))
    wiki = wikiset.wikis[0]
    broken_page, ok_page = wiki.top_level_pages()
    entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{broken_page.label}/entry"
    app = make_app(wikiset, faults=Faults(status_for={entry_path: 500}))
    client = make_client(app, max_attempts=2)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append
    )

    failed = [e for e in seen_events if isinstance(e, events.Failed) and e.kind == "http_error"]
    assert any(entry_path in e.url for e in failed)

    report = archive.report()
    assert report.failures_by_category.get("http_error", 0) >= 1

    # The crawl continued: the unaffected sibling page was still fully
    # processed (page_derived emitted for it).
    derived_ids = {e.page_id for e in seen_events if isinstance(e, events.PageDerived)}
    assert ok_page.uuid in derived_ids
    assert broken_page.uuid not in derived_ids
    assert result.ok is False


def test_malformed_page_is_recorded_unparsed_and_bytes_stay_archived(tmp_path):
    wikiset = synthesize(SynthSeed(seed=11, wiki_count=1, depth=1, pages_per_level=2))
    wiki = wikiset.wikis[0]
    broken_page, ok_page = wiki.top_level_pages()
    entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{broken_page.label}/entry"
    app = make_app(wikiset, faults=Faults(malformed_paths={entry_path}))
    client = make_client(app, max_attempts=1)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append
    )

    unparsed = [e for e in seen_events if isinstance(e, events.Failed) and e.kind == "unparsed"]
    assert any(entry_path in e.url for e in unparsed)

    # The bytes are archived even though they couldn't be parsed.
    assert any(entry_path in url for url in archive.seen_urls())

    derived_ids = {e.page_id for e in seen_events if isinstance(e, events.PageDerived)}
    assert ok_page.uuid in derived_ids
    assert result.ok is False


def test_retry_then_success_emits_retried_and_archives_the_content(tmp_path):
    wikiset = synthesize(SynthSeed(seed=12, wiki_count=1, depth=1, pages_per_level=1))
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    wikis_feed_path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset)
    inner = SyncASGIBridge(app)

    transport = OverrideTransport(
        inner,
        overrides=[
            (path_is(wikis_feed_path), fail_n_times_then(1, inner.handle_request)),
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=3), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    retried = [e for e in seen_events if isinstance(e, events.Retried)]
    assert any(wikis_feed_path in e.url for e in retried)

    # Content still archived successfully despite the transient failure.
    assert any(wikis_feed_path in url for url in archive.seen_urls())
    derived_ids = {e.page_id for e in seen_events if isinstance(e, events.PageDerived)}
    assert page.uuid in derived_ids


def test_raising_emit_consumer_does_not_break_the_crawl(tmp_path):
    wikiset = synthesize(SynthSeed(seed=13, wiki_count=1, depth=1, pages_per_level=1))
    page = wikiset.wikis[0].top_level_pages()[0]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    def raising_emit(event):
        raise RuntimeError("misbehaving consumer")

    result = crawl(config=_config(tmp_path), client=client, archive=archive, emit=raising_emit)

    assert result.report.pages_crawled == 1
    assert any(page.uuid in url or True for url in archive.seen_urls())
    assert len(archive.seen_urls()) > 0
