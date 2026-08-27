"""Pointing a run at an existing archive.

`--into` is the whole feature from the command line: add to this archive, work
out for yourself what has already been captured and since when. A scheduled
monthly re-run must stay correct without anyone editing a date in a script --
if the cutoff had to be written down, it would be wrong the first time the
schedule slipped.
"""

from __future__ import annotations

import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.crawler.provenance import update_cutoff
from connections_export.crawler.session import resolve_update_plan
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
BASE = "https://fake"


@pytest.fixture
def archive(tmp_path):
    stamps = iter([f"2026-08-10T21:{m:02d}:00Z" for m in range(14, 59)])
    store = Archive.open(tmp_path / "archive")
    crawl(
        config=Config(base_url=BASE, output_dir=tmp_path),
        client=HttpClient(
            transport=SyncASGIBridge(make_app(synthesize(SEED))), sleep=lambda _s: None
        ),
        archive=store,
        emit=lambda _e: None,
        clock=lambda: next(stamps),
    )
    return store


def test_pointing_at_an_archive_makes_the_run_an_update(archive, tmp_path):
    plan = resolve_update_plan(Config(base_url=BASE, into=archive.root))

    assert plan.fetch == "update"


def test_the_cutoff_is_read_from_the_archive_itself(archive, tmp_path):
    """Nobody has to write a date into a script for this to stay correct."""
    plan = resolve_update_plan(Config(base_url=BASE, into=archive.root))

    assert plan.since == update_cutoff(archive)
    assert plan.since == "2026-08-10T21:13:00Z"


def test_an_explicit_date_wins_over_the_archive(archive, tmp_path):
    plan = resolve_update_plan(
        Config(base_url=BASE, into=archive.root, since_override="2026-01-01T00:00:00Z")
    )

    assert plan.since == "2026-01-01T00:00:00Z"


def test_the_run_writes_into_the_archive_it_was_pointed_at(archive, tmp_path):
    plan = resolve_update_plan(Config(base_url=BASE, into=archive.root))

    assert plan.output_dir == archive.root


def test_an_archive_that_never_finished_a_run_offers_no_cutoff(tmp_path):
    """Not an error: the run still happens, it simply cannot filter by date
    and has to look at everything. Better slow than silently partial."""
    empty = Archive.open(tmp_path / "empty")

    plan = resolve_update_plan(Config(base_url=BASE, into=empty.root))

    assert plan.fetch == "update"
    assert plan.since is None


def test_without_into_nothing_changes(tmp_path):
    """Every existing caller must be unaffected."""
    plan = resolve_update_plan(Config(base_url=BASE, output_dir=tmp_path))

    assert plan.fetch == "resume"
    assert plan.since is None
    assert plan.output_dir == tmp_path
