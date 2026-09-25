"""Resumability -- a re-crawl skips already-archived URLs
unless refresh is set; a prior failure is retried.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _manifest_line_count(archive: Archive) -> int:
    path = archive.root / "manifest.jsonl"
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def test_resumed_run_skips_seen_urls_and_reports_from_cache(tmp_path):
    wikiset = synthesize(SynthSeed(seed=20, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=make_client(app), archive=archive)
    first_count = _manifest_line_count(archive)
    assert first_count > 0

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=make_client(app), archive=archive, emit=seen_events.append
    )

    # Nothing new was written -- every URL was already seen.
    assert _manifest_line_count(archive) == first_count

    from_cache_events = [e for e in seen_events if isinstance(e, events.Fetched) and e.from_cache]
    assert len(from_cache_events) > 0
    assert result.report.pages_crawled == 1


def test_refresh_forces_a_re_fetch(tmp_path):
    wikiset = synthesize(SynthSeed(seed=21, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=make_client(app), archive=archive)
    first_count = _manifest_line_count(archive)

    seen_events = []
    crawl(
        config=_config(tmp_path, fetch="refresh"),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,
    )

    assert _manifest_line_count(archive) > first_count
    from_cache_events = [e for e in seen_events if isinstance(e, events.Fetched) and e.from_cache]
    assert from_cache_events == []


def test_a_prior_failure_is_retried_on_resume(tmp_path):
    wikiset = synthesize(SynthSeed(seed=22, wiki_count=1, depth=1, pages_per_level=1))
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/entry"
    faulty_app = make_app(wikiset, faults=Faults(status_for={entry_path: 500}))
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=make_client(faulty_app, max_attempts=1), archive=archive)
    assert not any(entry_path in url for url in archive.seen_urls())

    healthy_app = make_app(wikiset)
    result = crawl(config=_config(tmp_path), client=make_client(healthy_app), archive=archive)

    assert any(entry_path in url for url in archive.seen_urls())
    assert result.report.pages_crawled == 1


def test_an_entry_whose_hash_is_not_a_digest_is_fetched_again_not_fatal(tmp_path):
    """An archive handed over for updating may carry a manifest whose hash
    names a path (`sha256:../../...`). The blob readers refuse it; the update
    must treat that entry as not archived and fetch it, rather than stop."""
    import json

    wikiset = synthesize(SynthSeed(seed=21, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")
    crawl(config=_config(tmp_path), client=make_client(app), archive=archive)

    manifest = archive.root / "manifest.jsonl"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    tampered_url = None
    for i, line in enumerate(lines):
        record = json.loads(line)
        if record.get("body_hash") and tampered_url is None:
            tampered_url = record["url"]
            record["body_hash"] = "sha256:../../../outside"
            lines[i] = json.dumps(record)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    seen = []
    crawl(
        config=_config(tmp_path),
        client=make_client(app),
        archive=Archive.open(tmp_path / "archive"),
        emit=seen.append,
    )

    refetched = [e for e in seen if isinstance(e, events.Fetched) and e.url == tampered_url]
    assert refetched and not any(e.from_cache for e in refetched)
