"""The crawler writes run metadata at run
start -- `archive.write_run_metadata(...)` right after `base_url` is
resolved and before traversal (crawler -- write it at run
start). Makes the archive self-describing about the deployment it
came from.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import ADAPTER_VERSION, crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url=BASE_URL, output_dir=tmp_path / "archive", **overrides)


def test_crawl_writes_run_metadata_with_base_url_and_source_version(tmp_path):
    wikiset = synthesize(SynthSeed(seed=1, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(
        config=_config(tmp_path, source_version="7.0"), client=make_client(app), archive=archive
    )

    run = archive.read_run_metadata(result.run_id)
    assert run.base_url == BASE_URL
    assert run.source_system_version == "7.0"
    assert run.adapter_version == ADAPTER_VERSION


def test_crawl_writes_run_metadata_carrying_the_configured_hcl_hosts(tmp_path):
    wikiset = synthesize(SynthSeed(seed=2, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(
        config=_config(tmp_path, hcl_hosts=["files.example.corp"]),
        client=make_client(app),
        archive=archive,
    )

    run = archive.read_run_metadata(result.run_id)
    assert run.hcl_hosts == ["files.example.corp"]


def test_crawl_writes_run_metadata_with_empty_hcl_hosts_by_default(tmp_path):
    wikiset = synthesize(SynthSeed(seed=3, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=make_client(app), archive=archive)

    run = archive.read_run_metadata(result.run_id)
    assert run.hcl_hosts == []


def test_crawl_writes_run_metadata_with_best_effort_unknown_principal(tmp_path):
    # `Config` has no `principal` field (out of scope:
    # "A `principal` field on `Config` / real principal discovery from
    # auth" -- deferred), so the crawler always falls back to
    # "unknown" today.
    wikiset = synthesize(SynthSeed(seed=4, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=make_client(app), archive=archive)

    run = archive.read_run_metadata(result.run_id)
    assert run.principal == "unknown"


def test_run_metadata_is_written_even_when_the_wikis_feed_itself_fails(tmp_path):
    """Proves the write happens "right after resolving base_url and
    before traversal" -- not merely as a side effect of a
    successful traversal. Even a run that fails at the very first
    fetch (the wikis feed) still leaves provenance behind."""
    wikiset = synthesize(SynthSeed(seed=5, wiki_count=1, depth=1, pages_per_level=1))
    faulty_app = make_app(wikiset, faults=Faults(status_for={"/wikis/basic/api/wikis/feed": 500}))
    archive = Archive.open(tmp_path / "archive")

    result = crawl(
        config=_config(tmp_path), client=make_client(faulty_app, max_attempts=1), archive=archive
    )

    assert result.ok is False  # the run had an issue
    run = archive.read_run_metadata(result.run_id)  # but provenance was still recorded
    assert run.base_url == BASE_URL


def test_resumed_run_with_explicit_run_id_overwrites_the_same_run_file(tmp_path):
    """the design: "Idempotent on a resumed run (overwrite the same
    `run-*.json`, or skip if identical)." -- a second crawl reusing the
    same explicit `run_id` overwrites rather than accumulating a second
    file, and reflects whatever config that second run used."""
    wikiset = synthesize(SynthSeed(seed=6, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")
    fixed_run_id = "2026-07-20T180000Z"

    crawl(
        config=_config(tmp_path, hcl_hosts=["a.example.corp"]),
        client=make_client(app),
        archive=archive,
        run_id=fixed_run_id,
    )
    crawl(
        config=_config(tmp_path, hcl_hosts=["b.example.corp"]),
        client=make_client(app),
        archive=archive,
        run_id=fixed_run_id,
    )

    run_files = list((tmp_path / "archive").glob("run-*.json"))
    assert len(run_files) == 1

    run = archive.read_run_metadata(fixed_run_id)
    assert run.hcl_hosts == ["b.example.corp"]  # the second run's config won
