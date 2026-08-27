"""Updating a real archive must never run the demo into it.

"Extend & update" on a real ingested
archive started importing demo data, having shown the archive's real
components a moment earlier.

`/api/start` decides between the real crawler and the in-process fakeserver
with `use_demo = body.demo or not effective_base_url or...`, and
`effective_base_url` comes from the request, then the saved settings, then
the config file -- but never from THE ARCHIVE BEING UPDATED. Extending an
archive means picking it from a list, not retyping the deployment's address,
so that field is normally blank; with a blank saved setting too, an update of
a real archive fell through to the demo.

The archive has always known where it came from: every run records
`base_url`. And the failure mode is the worst one available -- fabricated
content written into an archive whose only value is that it is not
fabricated -- so this must also fail LOUDLY rather than quietly do something
else when the answer cannot be found.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app as make_fake
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

REAL_DEPLOYMENT = "https://connections.example.corp"


@pytest.fixture
def real_archive(tmp_path: pathlib.Path, monkeypatch) -> str:
    """An archive captured from a real deployment address, in the console's
    own archives directory so `into` can name it."""
    base = tmp_path / "archives"
    base.mkdir()
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", base)
    root = base / "connections-2026-08-25"
    fake = make_fake(synthesize(SynthSeed(seed=0, wiki_count=1, pages_per_level=2)))
    transport = httpx.MockTransport(SyncASGIBridge(fake).handle_request)
    crawl(
        config=Config(base_url=REAL_DEPLOYMENT, output_dir=root, fetch="resume"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    return root.name


def _start(app, body):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post("/api/start", json=body)

    return asyncio.run(go())


def _recorded_base_urls(archive_root: pathlib.Path) -> set[str]:
    return {
        json.loads(path.read_text(encoding="utf-8")).get("base_url")
        for path in archive_root.glob("run-*.json")
    }


def test_the_archives_own_deployment_is_used_when_the_field_is_blank(real_archive, tmp_path):
    """The address is in the archive. Nothing was reading it."""
    app = make_app(demo=True, demo_delay=0)

    response = _start(app, {"into": real_archive, "base_url": "", "demo": False})
    assert response.status_code == 200, response.text
    for thread in app.state.run_threads:
        thread.join(timeout=60)

    root = pathlib.Path(gui_support.ARCHIVES_BASE) / real_archive
    bases = {b for b in _recorded_base_urls(root) if b}
    assert bases == {REAL_DEPLOYMENT}, bases


def test_no_demo_run_is_ever_written_into_a_real_archive(real_archive):
    """The failure this guards: fabricated content in an archive whose whole
    value is that it is not fabricated."""
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    app = make_app(demo=True, demo_delay=0)

    _start(app, {"into": real_archive, "base_url": "", "demo": False})
    for thread in app.state.run_threads:
        thread.join(timeout=60)

    root = pathlib.Path(gui_support.ARCHIVES_BASE) / real_archive
    assert DEMO_SAMPLE_BASE_URL not in {b for b in _recorded_base_urls(root) if b}


def test_an_archive_that_will_not_say_where_it_came_from_is_refused(tmp_path, monkeypatch):
    """Not knowing is a reason to stop, not a reason to invent."""
    base = tmp_path / "archives"
    (base / "mystery").mkdir(parents=True)
    (base / "mystery" / "manifest.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", base)

    app = make_app(demo=True, demo_delay=0)
    response = _start(app, {"into": "mystery", "base_url": "", "demo": False})

    assert response.status_code == 409, response.text
    assert "refusing" in response.json()["detail"]


def test_extending_a_demo_archive_still_works(tmp_path, monkeypatch):
    """The exemption: a demo archive says it is one, and extending it is a
    real thing to want."""
    from connections_export.gui.demo import run_demo

    base = tmp_path / "archives"
    base.mkdir()
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", base)
    run_demo(lambda _e: None, archive_dir=base / "demo-archive", delay=0)

    app = make_app(demo=True, demo_delay=0)
    response = _start(app, {"into": "demo-archive", "base_url": "", "demo": False})

    assert response.status_code == 200, response.text


def test_pressing_extend_says_something_before_the_deployment_answers():
    """The ledger call asks the deployment whether it answers, which on a real
    one takes seconds. Everything visible waited for it, so the button did
    nothing at all for those seconds -- indistinguishable from a dead one."""
    import re

    from tests.gui._served_assets import served_console_js

    js = served_console_js()
    match = re.search(r"function openArchiveForUpdate\(.*?\n  \}\n", js, re.S)
    assert match, "openArchiveForUpdate not found"
    body = match.group(0)

    # The screen opens, and says what is happening, BEFORE the fetch.
    assert body.index('showSection("select")') < body.index("fetch(")
    assert body.index('class="spin"') < body.index("fetch(")


def test_an_archive_a_demo_already_polluted_still_knows_its_real_deployment(real_archive, tmp_path):
    """The situation the report leaves behind: a real archive with a demo run
    written into it, and that demo run is the most recent finished one. Its
    `base_url` is the fakeserver's own host, which cannot be asked for
    anything -- so answering with it would point the next update at a host
    that does not exist."""
    from connections_export.crawler.provenance import archive_base_url
    from connections_export.gui.demo import run_demo

    root = pathlib.Path(gui_support.ARCHIVES_BASE) / real_archive
    run_demo(lambda _e: None, archive_dir=root, delay=0)  # the damage, reproduced

    assert archive_base_url(Archive.open(root)) == REAL_DEPLOYMENT
