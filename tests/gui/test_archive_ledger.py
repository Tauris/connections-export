"""What an archive can say about itself, so a later visit can act on it.

The ledger is the screen's whole content in archive mode: for each component,
what the archive already holds, when it was captured, and what is on the live
system now. Everything else -- the cost verdicts, the estimate, whether a row
offers "Update" or "Add now" -- is derived from those three facts.

Read from the archive alone, without deriving the full model: the list has to
stay fast enough to render a directory of archives.
"""

from __future__ import annotations

import pytest

from connections_export.gui.archives import archive_ledger, model_summary


@pytest.fixture
def captured(tmp_path):
    """A demo capture, so the ledger is read from a real archive."""
    from connections_export.gui.demo import run_demo

    return run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, wave=0)


def test_the_summary_records_when_each_component_was_captured(captured):
    """A per-group date, because components can arrive in different visits and
    a single archive-level date would be wrong for all but the last."""
    from connections_export.gui.archives import read_archive_summary

    summary = read_archive_summary(captured.archive_dir)

    assert summary["groups"], "nothing was captured"
    assert all(group.get("captured_at") for group in summary["groups"])


def test_the_ledger_reports_what_the_archive_holds(captured):
    ledger = archive_ledger(captured.archive_dir)

    kinds = {row["kind"] for row in ledger["rows"]}
    assert {"wiki", "blog", "forum"} <= kinds
    assert all(row["in_archive"] for row in ledger["rows"])


def test_every_row_says_how_it_would_be_updated(captured):
    """The per-app asymmetry is the thing a user must be able to see without
    reading a manual: some components can be asked for changes, one cannot."""
    ledger = archive_ledger(captured.archive_dir)
    by_kind = {row["kind"]: row for row in ledger["rows"]}

    assert by_kind["blog"]["update_method"] == "since"
    assert by_kind["forum"]["update_method"] == "since"
    assert by_kind["wiki"]["update_method"] == "rescan"


def test_the_wiki_row_carries_the_cost_that_makes_it_a_decision(captured):
    """ "Leave as is" is only a real choice if its alternative has a visible
    price."""
    ledger = archive_ledger(captured.archive_dir)
    wiki = next(row for row in ledger["rows"] if row["kind"] == "wiki")

    assert wiki["floor_requests"] > 0
    assert wiki["floor_requests"] >= wiki["count"] // 2 or wiki["count"] == 0


def test_the_ledger_carries_the_cutoff_an_update_would_use(captured):
    ledger = archive_ledger(captured.archive_dir)

    assert ledger["since"], "no cutoff, so an update could not be dated"
    assert ledger["anchor"], "no anchor, so the cutoff cannot be explained"


def test_an_archive_whose_run_never_finished_offers_no_cutoff(tmp_path):
    """Not an error. The run can still happen; it simply cannot filter."""
    from connections_export.archive.records import RunMetadata
    from connections_export.archive.store import Archive

    archive = Archive.open(tmp_path / "archive")
    archive.write_run_metadata(
        RunMetadata(
            run_id="r1",
            base_url="https://fake",
            principal="j.doe",
            adapter_version="wikis-1",
            source_system_version="8.0",
            started_at="2026-08-10T21:14:00Z",
        )
    )

    ledger = archive_ledger(archive.root)

    assert ledger["since"] is None


def test_the_summary_still_works_for_an_archive_with_nothing_in_it(tmp_path):
    from connections_export.derive.model import Interchange

    summary = model_summary(Interchange())

    assert summary["groups"] == []
