"""What a finished run leaves behind, so the next one can act on it.

The cutoff an update works from has to come from somewhere. It comes from the
archive's own record of its last successful capture -- which means every run
has to write that record, and has to distinguish "I finished" from "I was
killed halfway".

A run that died must never anchor an update: its feeds were read at wildly
different moments and anything after the point of death was never looked at, so
treating its start as "everything before here is captured" would lose whatever
fell in that window, permanently and silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.crawler.provenance import last_successful_run, update_cutoff
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
BASE = "https://fake"


@pytest.fixture
def captured(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    stamps = iter(["2026-08-10T21:14:00Z"] + [f"2026-08-10T21:{m:02d}:00Z" for m in range(15, 59)])
    crawl(
        config=Config(base_url=BASE, output_dir=tmp_path),
        client=HttpClient(
            transport=SyncASGIBridge(make_app(synthesize(SEED))), sleep=lambda _s: None
        ),
        archive=archive,
        emit=lambda _e: None,
        clock=lambda: next(stamps),
    )
    return archive


def _runs(archive) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(archive.root.glob("run-*.json"))
    ]


def test_a_run_records_when_it_started(captured):
    assert _runs(captured)[0]["started_at"] == "2026-08-10T21:14:00Z"


def test_a_finished_run_records_that_it_finished(captured):
    """The difference between a run that may anchor an update and one that may
    not."""
    assert _runs(captured)[0]["completed_at"]


def test_the_cutoff_comes_from_the_start_minus_a_minute(captured):
    """The start, not the end: an item edited while the capture was running,
    after its own feed page had been read, would otherwise fall between this
    run's end and the next run's cutoff and never be seen again."""
    assert update_cutoff(captured) == "2026-08-10T21:13:00Z"


def test_an_unfinished_run_cannot_anchor_an_update(tmp_path):
    """Its feeds were read at wildly different moments and everything after
    the point of death was never looked at. Trusting its start would lose that
    window silently and permanently."""
    from connections_export.archive.records import RunMetadata

    archive = Archive.open(tmp_path / "archive")
    archive.write_run_metadata(
        RunMetadata(
            run_id="r-died",
            base_url=BASE,
            principal="j.doe",
            adapter_version="wikis-1",
            source_system_version="8.0",
            started_at="2026-08-10T21:14:00Z",
        )
    )

    assert last_successful_run(archive) is None
    assert update_cutoff(archive) is None


def test_the_latest_finished_run_wins(captured, tmp_path):
    """Several runs share an archive. The cutoff must come from the most
    recent one that actually finished -- not the first, and not a later one
    that died."""
    from connections_export.archive.records import RunMetadata

    captured.write_run_metadata(
        RunMetadata(
            run_id="r-later",
            base_url=BASE,
            principal="j.doe",
            adapter_version="wikis-1",
            source_system_version="8.0",
            started_at="2026-09-01T09:00:00Z",
            completed_at="2026-09-01T10:00:00Z",
        )
    )
    captured.write_run_metadata(
        RunMetadata(
            run_id="r-died-after",
            base_url=BASE,
            principal="j.doe",
            adapter_version="wikis-1",
            source_system_version="8.0",
            started_at="2026-09-02T09:00:00Z",
        )
    )

    assert last_successful_run(captured).run_id == "r-later"
    assert update_cutoff(captured) == "2026-09-01T08:59:00Z"


def test_an_archive_with_no_runs_offers_no_cutoff(tmp_path):
    assert update_cutoff(Archive.open(tmp_path / "empty")) is None


def _run_file(archive, *, run_id, started_at, completed_at, components):
    """Write one run record directly. The crawler writes these itself; building
    them here keeps the test about what `components_captured` reads, not about
    how many crawls it takes to produce two finished runs."""
    from connections_export.archive.records import ComponentSelection, RunMetadata

    meta = RunMetadata(
        run_id=run_id,
        base_url="https://fake",
        principal="tester",
        adapter_version="wikis-1",
        source_system_version="8.0",
        started_at=started_at,
        completed_at=completed_at,
        components=[ComponentSelection(kind=k, id=i) for k, i in components],
    )
    (Path(archive.root) / f"run-{run_id}.json").write_text(meta.model_dump_json(), encoding="utf-8")


def test_components_captured_reports_each_component_once_most_recent_first(tmp_path):
    """What "extend" offers is whatever is NOT in here, so a component listed
    twice, or attributed to the wrong run, makes the offer wrong.

    This function had no caller and no test when it was written -- it was built
    for a feature still in flight. Untested code written ahead of its consumer
    is the shape that quietly stops matching it.
    """
    from connections_export.crawler.provenance import components_captured

    archive = Archive.open(tmp_path / "archive")
    _run_file(
        archive,
        run_id="r-early",
        started_at="2026-08-01T00:00:00Z",
        completed_at="2026-08-01T00:10:00Z",
        components=[("wiki", "eng")],
    )
    _run_file(
        archive,
        run_id="r-late",
        started_at="2026-08-10T00:00:00Z",
        completed_at="2026-08-10T00:10:00Z",
        components=[("wiki", "eng"), ("blog", "b-1")],
    )

    captured = components_captured(archive)

    assert {(c["kind"], c["id"]) for c in captured} == {("wiki", "eng"), ("blog", "b-1")}
    # The most recent finished run is the one that dates a component.
    by_key = {(c["kind"], c["id"]): c for c in captured}
    assert by_key[("wiki", "eng")]["captured_at"] == "2026-08-10T00:00:00Z"


def test_a_run_that_never_finished_contributes_no_components(tmp_path):
    """Same rule as the cutoff: a run that died is not evidence of what an
    archive holds. Offering to "extend" a component a crashed run was merely
    asked for would skip capturing it."""
    from connections_export.crawler.provenance import components_captured

    archive = Archive.open(tmp_path / "archive")
    _run_file(
        archive,
        run_id="r-died",
        started_at="2026-08-01T00:00:00Z",
        completed_at=None,
        components=[("wiki", "eng")],
    )

    assert components_captured(archive) == []
