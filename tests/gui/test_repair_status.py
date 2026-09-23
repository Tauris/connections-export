"""An archive captured before the blog-comment fix is offered a repair.

The fix bumped the blog adapter to `blogs-2`. `archive_repair_status` reads
only run metadata (no derivation, no network): a completed blog run recorded
as `blogs-1` is repairable, a `blogs-2` one is not. Uncertainty about a
pre-metadata archive becomes an offered repair, never a claim of
completeness.
"""

from __future__ import annotations

import json
from pathlib import Path

from connections_export.gui.archives import archive_repair_status


def _archive_with_blog_run(tmp_path: Path, adapter_version: str) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    (root / "manifest.jsonl").write_text("", encoding="utf-8")
    run = {
        "schema_version": 2,
        "run_id": "2026-01-01T00-00-00Z",
        "base_url": "https://fake",
        "principal": "someone",
        "adapter_version": adapter_version,
        "source_system_version": "8.0",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:05:00Z",
        "components": [{"kind": "blog", "id": "b1", "action": "capture"}],
    }
    (root / "run-2026-01-01T00-00-00Z.json").write_text(json.dumps(run), encoding="utf-8")
    return root


def test_a_blogs1_capture_is_offered_a_repair(tmp_path):
    status = archive_repair_status(_archive_with_blog_run(tmp_path, "blogs-1"))
    assert status["repair_needed"] is True
    assert "comment" in status["repair_reason"].lower()


def test_a_blogs2_capture_is_not(tmp_path):
    status = archive_repair_status(_archive_with_blog_run(tmp_path, "blogs-2"))
    assert status["repair_needed"] is False
    assert status["repair_reason"] is None


def test_an_incomplete_blogs2_repair_is_offered_again(tmp_path):
    root = _archive_with_blog_run(tmp_path, "blogs-2")
    run_path = root / "run-2026-01-01T00-00-00Z.json"
    data = json.loads(run_path.read_text(encoding="utf-8"))
    data.pop("completed_at", None)
    run_path.write_text(json.dumps(data), encoding="utf-8")

    status = archive_repair_status(root)

    assert status["repair_needed"] is True
    assert "did not finish" in status["repair_reason"]


def test_repair_stop_is_a_noop_when_nothing_is_running():
    """The Stop control is safe to press with no repair in flight."""
    import asyncio
    import inspect

    import httpx

    from connections_export.cli import crawl_main
    from connections_export.gui.app import make_app

    # crawl_main must accept the stop signal the repair worker threads in.
    assert "stop_event" in inspect.signature(crawl_main).parameters

    app = make_app(demo=True)

    async def _do():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            return await client.post("/api/repair-stop")

    response = asyncio.run(_do())
    assert response.status_code == 200
    assert response.json()["stopped"] is False


def _write_run(root: Path, run_id: str, adapter_version: str, *, completed: bool) -> None:
    run = {
        "schema_version": 2,
        "run_id": run_id,
        "base_url": "https://fake",
        "principal": "someone",
        "adapter_version": adapter_version,
        "source_system_version": "8.0",
        "started_at": run_id.replace("run-", "").replace("Z", ":00Z")[:19] + "Z",
        "components": [],  # a real blog run records an empty component list
    }
    if completed:
        run["completed_at"] = run["started_at"]
    (root / f"{run_id}.json").write_text(json.dumps(run), encoding="utf-8")


def _failed_page1_manifest(root: Path) -> None:
    """The append-only manifest of an archive captured before the fix: entries
    fetched, and the per-post comment feed read at page 1 (which failed on the
    real server) -- records that never leave the manifest."""
    lines = [
        {
            "url": "https://fake/blogs/roller-ui/rendering/feed/b1/entries/atom?page=0",
            "outcome": "ok",
        },
        {
            "url": "https://fake/blogs/handle/feed/entrycomments/slug/atom?page=1",
            "outcome": "error",
        },
    ]
    (root / "manifest.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


def test_a_repaired_archive_stops_offering_a_repair(tmp_path):
    """The user-reported bug: a blogs-1 archive was repaired (a completed
    blogs-2 run was written), yet it kept offering the repair -- because the
    append-only manifest still held the old failed page-1 comment requests.
    The latest completed blog run must settle it."""
    root = tmp_path / "archive"
    root.mkdir()
    _failed_page1_manifest(root)  # old failures live here forever
    _write_run(root, "run-2026-01-01T00-00-00Z", "blogs-1", completed=True)  # old capture
    _write_run(root, "run-2026-02-01T00-00-00Z", "blogs-2", completed=True)  # the repair

    status = archive_repair_status(root)

    assert status["repair_needed"] is False
    assert status["repair_reason"] is None


def test_a_repair_that_finished_after_an_interrupted_one_is_not_offered(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    _failed_page1_manifest(root)
    _write_run(root, "run-2026-01-01T00-00-00Z", "blogs-1", completed=True)
    _write_run(root, "run-2026-02-01T00-00-00Z", "blogs-2", completed=False)  # interrupted
    _write_run(root, "run-2026-03-01T00-00-00Z", "blogs-2", completed=True)  # then finished

    status = archive_repair_status(root)

    assert status["repair_needed"] is False


def test_an_interrupted_repair_after_the_last_completed_one_is_still_offered(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    _failed_page1_manifest(root)
    _write_run(root, "run-2026-01-01T00-00-00Z", "blogs-1", completed=True)
    _write_run(root, "run-2026-02-01T00-00-00Z", "blogs-2", completed=True)
    _write_run(root, "run-2026-03-01T00-00-00Z", "blogs-2", completed=False)  # latest, interrupted

    status = archive_repair_status(root)

    assert status["repair_needed"] is True
    assert "did not finish" in status["repair_reason"]
