"""An update's counters say what it actually did.

Updating an archive when nothing has changed sends a handful of requests and
serves everything else from the archive. Counting each cache hit as a fetch
and labelling it "bodies archived" reports the sum, so an update that
correctly avoided almost all the work looks like it redid all of it.

That is the number people judge an update by. Reporting it wrong makes a
working feature look broken -- and would hide a real regression behind a
number that never moves.
"""

from __future__ import annotations

import pathlib

import pytest

from connections_export.gui.demo import run_demo
from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()


def test_the_fetched_counter_excludes_what_came_from_the_archive():
    # The counter is split at the event, not merely mentioned somewhere: the
    # word "reused" appears in unrelated comments, so a looser match would
    # hold while proving nothing.
    assert "if (success && evt.from_cache)" in JS
    assert "reused += 1" in JS
    assert 'setNum($("k-reused"), reused)' in JS


def test_the_reused_count_is_shown_rather_than_dropped():
    """ "We skipped 162 things" is the good news an update has to deliver --
    it is the evidence that the cutoff worked."""
    assert 'id="k-reused"' in HTML
    assert "from the archive" in HTML


def test_cached_reads_are_not_shown_as_network_activity():
    start = JS.index("function liveFetched(evt)")
    body = JS[start : JS.index("\n  function liveRetried", start)]
    assert "if (!evt.from_cache)" in body
    assert 'evt.kind + (evt.from_cache ? " · cached" : "")' not in body


@pytest.mark.slow
def test_an_update_that_finds_nothing_new_fetches_almost_nothing(tmp_path: pathlib.Path):
    """The crawler's own behaviour, so the console's numbers have something
    true to report. Runs the real demo pipeline twice."""
    root = tmp_path / "archive"
    first: list = []
    second: list = []
    run_demo(first.append, archive_dir=root, delay=0)
    run_demo(second.append, archive_dir=root, delay=0, update=True)

    def over_the_wire(events):
        return sum(
            1
            for e in events
            if type(e).__name__ == "Fetched" and not getattr(e, "from_cache", False)
        )

    captured = over_the_wire(first)
    updated = over_the_wire(second)
    assert captured > 100, captured
    # A rescan still has to read the feeds and each wiki page's entry -- that
    # is how change is detected at all -- but nothing expensive should move.
    assert updated < captured / 3, f"{updated} of {captured} refetched with nothing changed"


def test_the_console_accounts_for_what_a_stop_abandoned():
    """`discovered = saved + reused + failed + not attempted`. Without the
    last term the totals could not balance, which is what a live ingest
    reported: 2,958 discovered, 2,957 concluded."""
    from tests.gui._served_assets import served_console_html

    assert 'id="k-stopped"' in served_console_html()
    assert "liveStopped" in JS
    assert "ok + reused + fail + stopped" in JS


def test_the_fetched_tile_no_longer_claims_they_are_all_bodies():
    """It counts feeds and metadata too."""
    from tests.gui._served_assets import served_console_html

    assert "bodies archived" not in served_console_html()
