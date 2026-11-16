"""An archive an older release left incomplete says so, and is captured again
into a NEW archive with the selections it recorded.

The forum reader before `forums-2` kept only attachments linked with a node
id -- pasted images -- and missed documents uploaded to a post. An archive it
made is flagged with what went wrong. "Capture again" repeats that archive's
deployment, components and author scope with no date cutoff into a new
archive; the old one is never written to, and once the new capture has
finished, the old one names it instead of carrying the warning.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib

import httpx
import pytest

from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app as make_fake
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from connections_export.gui.archives import list_archives, write_archive_summary
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

DEPLOYMENT = "https://connections.example.corp"
OLD = "connections-2026-08-25"


def _fake_client() -> HttpClient:
    fake = make_fake(synthesize(SynthSeed(seed=0, wiki_count=1, pages_per_level=2)))
    return HttpClient(
        transport=httpx.MockTransport(SyncASGIBridge(fake).handle_request), sleep=lambda _s: None
    )


@pytest.fixture
def old_archive(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    base = tmp_path / "archives"
    base.mkdir()
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", base)
    root = base / OLD
    crawl(
        config=Config(base_url=DEPLOYMENT, output_dir=root, fetch="resume"),
        client=_fake_client(),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    archive = Archive.open(root)
    # A forum run by the older reader, as an archive made before the fix has.
    archive.write_run_metadata(
        RunMetadata(
            run_id="old-forums",
            base_url=DEPLOYMENT,
            principal="someone",
            adapter_version="forums-1",
            source_system_version="8.0",
            started_at="2026-08-25T10:00:00Z",
            completed_at="2026-08-25T10:05:00Z",
        )
    )
    write_archive_summary(root, derive(archive))
    return root


def _fingerprint(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _start(app, body):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post("/api/start", json=body)

    return asyncio.run(go())


def test_an_archive_from_the_older_forum_reader_says_what_is_missing(old_archive):
    (info,) = list_archives(old_archive.parent)
    assert [issue["key"] for issue in info.issues] == ["forum-attachments"]
    explanation = info.issues[0]["explanation"]
    assert "uploaded" in explanation and "new archive" in explanation


def test_capture_again_makes_a_new_archive_and_leaves_the_old_one_alone(old_archive, monkeypatch):
    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", lambda *a, **k: _fake_client())
    before = _fingerprint(old_archive)
    app = make_app(demo=True, demo_delay=0)

    response = _start(app, {"again_from": OLD})
    assert response.status_code == 200, response.text
    for thread in app.state.run_threads:
        thread.join(timeout=120)

    assert _fingerprint(old_archive) == before
    new = [p for p in old_archive.parent.iterdir() if p.is_dir() and p.name != OLD]
    assert len(new) == 1, new
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in new[0].glob("run-*.json")]
    assert runs and all(run["base_url"] == DEPLOYMENT for run in runs)
    assert {run.get("captured_again_from") for run in runs} == {OLD}
    assert any(run.get("completed_at") for run in runs)
    assert derive(Archive.open(new[0])).wikis, "the new archive holds the same wiki"

    infos = {info.name: info for info in list_archives(old_archive.parent)}
    assert infos[OLD].issues == []
    assert infos[OLD].captured_again_as == new[0].name


def test_capture_again_names_what_it_will_repeat(old_archive):
    from connections_export.gui.routes.run import again_plan

    plan = again_plan(old_archive)
    assert plan["base_url"] == DEPLOYMENT
    assert [c["kind"] for c in plan["components"]] == ["wiki"]
    assert plan["author"] is None


def test_capture_again_and_update_at_once_is_refused(old_archive):
    app = make_app(demo=True, demo_delay=0)
    response = _start(app, {"again_from": OLD, "into": OLD})
    assert response.status_code == 400
