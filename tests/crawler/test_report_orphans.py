"""Orphan pages and truncation signatures surface
as warnings in the run's report; any failure/warning makes `ok` false.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import FakePage, FakeWiki, WikiSet
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.archive.conftest import FakeResponse
from tests.crawler.conftest import make_client


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _orphan_wikiset() -> WikiSet:
    page = FakePage(
        uuid="page-orphan-1",
        label="p0",
        title="Orphan Page",
        parent_uuid="ghost-parent-uuid-does-not-exist",
        ordinal=0,
        body_html="",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
    )
    wiki = FakeWiki(
        uuid="wiki-uuid-1",
        label="wiki0",
        title="Wiki Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        pages=[page],
    )
    return WikiSet(wikis=[wiki], base_timestamp_ms=1_700_000_000_000)


def test_orphan_page_surfaces_a_warning_and_appears_in_the_report(tmp_path):
    wikiset = _orphan_wikiset()
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append
    )

    orphan_warnings = [
        e for e in seen_events if isinstance(e, events.Warning) and e.kind == "orphan"
    ]
    assert any(w.ref == "page-orphan-1" for w in orphan_warnings)
    assert "page-orphan-1" in result.report.orphans
    assert result.ok is False


def test_nonzero_result_on_truncation_signature_warning(tmp_path):
    wikiset = synthesize(SynthSeed(seed=30, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    # Pre-seed a manifest record that carries the truncation signature
    # (item_count == page_size, no next link) -- archive.report flags
    # this over the whole manifest, whatever wrote it. A real crawler
    # that never paginates wouldn't organically produce this signature
    # against this fixture; seeding it directly exercises the
    # surfacing logic in isolation.
    truncated_url = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/feed?category=comment"
    archive.write_response(
        FakeResponse(url=truncated_url, method="GET", status=200, content=b"<feed/>"),
        fetched_at="2026-01-01T00:00:00Z",
        item_count=25,
        page_size=25,
        has_next=False,
    )

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=make_client(app), archive=archive, emit=seen_events.append
    )

    truncation_warnings = [
        e for e in seen_events if isinstance(e, events.Warning) and e.kind == "truncation"
    ]
    assert any(w.ref == truncated_url for w in truncation_warnings)
    assert truncated_url in result.report.possibly_truncated
    assert result.ok is False


def test_clean_run_is_ok(tmp_path):
    wikiset = synthesize(SynthSeed(seed=31, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=make_client(app), archive=archive)

    assert result.ok is True
    assert result.report.orphans == []
    assert result.report.possibly_truncated == []
