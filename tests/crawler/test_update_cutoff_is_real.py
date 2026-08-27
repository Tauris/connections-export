"""An update's cutoff has to be readable from what a REAL run wrote.

A run stamp doubles as a run id, and a run id becomes a directory name, so
it cannot carry the colons of an ISO time on Windows. That makes the stamp a
format of its own, and the cutoff reader has to accept it: the CLI's `--into`
update and the console's "Extend & update" both begin by parsing it.

A test that supplies its own stamps proves nothing about that, however many
of them there are -- it proves the parser reads the format the test chose.
So these tests take the clock the product itself uses, which is the only way
the two can be shown to agree.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from connections_export.adapters.since import parse_cutoff
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.crawler.engine import default_clock
from connections_export.crawler.provenance import update_cutoff
from connections_export.crawler.session import resolve_update_plan
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=2)


@pytest.fixture
def captured(tmp_path: pathlib.Path) -> Archive:
    """A capture stamped by the product's own clock -- no injected stamps."""
    root = tmp_path / "archive"
    transport = httpx.MockTransport(SyncASGIBridge(make_app(synthesize(SEED))).handle_request)
    crawl(
        config=Config(base_url="https://fake", output_dir=root, fetch="resume"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    return Archive.open(root)


def test_the_clock_a_run_stamps_itself_with_is_a_date_we_can_read():
    """The property that was missing: the writer and the reader agree."""
    assert parse_cutoff(default_clock())


def test_a_real_capture_yields_a_cutoff(captured):
    cutoff = update_cutoff(captured)
    assert cutoff is not None
    assert parse_cutoff(cutoff)


def test_updating_a_real_archive_gets_that_cutoff(captured):
    """`--into` and the console's "Extend & update" both come through here.
    Without a cutoff an update asks for everything and reports success."""
    plan = resolve_update_plan(
        Config(base_url="https://fake", output_dir=captured.root, into=str(captured.root))
    )
    assert plan.fetch == "update"
    assert plan.since == update_cutoff(captured)
    assert plan.since is not None


def test_an_archive_written_before_this_still_updates(tmp_path, captured):
    """Archives already on disk hold the dashed stamp. Refusing to read them
    would turn a fixed bug into a bug that only new archives escape."""
    for path in captured.root.glob("run-*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        for field in ("started_at", "completed_at"):
            if record.get(field):
                date, _, time = record[field].partition("T")
                record[field] = f"{date}T{time.replace(':', '-')}"
        path.write_text(json.dumps(record), encoding="utf-8")

    assert update_cutoff(Archive.open(captured.root)) is not None
