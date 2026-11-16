"""A PDF export that runs in the background and reports its progress.

Driven in-process; the renderer is a double, so no browser is launched.
"""

from __future__ import annotations

import asyncio
import threading

import httpx

from connections_export.gui import settings_store
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo
from connections_export.pdf.progress import PdfOutOfMemoryError, PdfProgress


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _app(tmp_path, renderer):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    return make_app(demo=True, archive_dir=archive_dir, pdf_renderer=renderer)


async def _wait(client, job, states=("done", "failed", "cancelled")):
    for _ in range(200):
        body = (await client.get(f"/api/pdf/jobs/{job}")).json()
        if body["state"] in states:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_job_runs_reports_progress_and_serves_the_file(tmp_path):
    def renderer(model, blob_bytes, progress=None):
        progress(
            PdfProgress(stage="laying_out", pages_done=3, pages_total=10, message="Laying out")
        )
        return b"%PDF-1.4 job"

    app = _app(tmp_path, renderer)

    async def go():
        async with _client(app) as c:
            started = await c.post("/api/pdf/jobs")
            assert started.status_code == 200
            job = started.json()["job"]
            early = await c.get(f"/api/pdf/jobs/{job}/file")
            body = await _wait(c, job)
            file = await c.get(f"/api/pdf/jobs/{job}/file")
            return early, body, file

    early, body, file = asyncio.run(go())
    assert early.status_code in (200, 409)
    assert body["state"] == "done"
    assert body["progress"]["pages_done"] == 3 and body["progress"]["pages_total"] == 10
    assert body["out_of_memory"] is False
    assert file.status_code == 200 and file.content == b"%PDF-1.4 job"
    assert "attachment" in file.headers["content-disposition"]


def test_a_double_without_progress_keeps_the_old_contract(tmp_path):
    app = _app(tmp_path, lambda model, blob_bytes: b"%PDF-1.4 plain")

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            return await _wait(c, job)

    body = asyncio.run(go())
    assert body["state"] == "done" and body["progress"] is None


def test_a_failure_carries_its_detail(tmp_path):
    def renderer(model, blob_bytes):
        raise TimeoutError("renderer timed out")

    app = _app(tmp_path, renderer)

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            body = await _wait(c, job)
            return body, await c.get(f"/api/pdf/jobs/{job}/file")

    body, file = asyncio.run(go())
    assert body["state"] == "failed"
    assert "did not finish loading or paginating" in body["detail"]
    assert body["out_of_memory"] is False
    assert file.status_code == 409


def test_running_out_of_memory_is_flagged_with_its_own_message(tmp_path):
    def renderer(model, blob_bytes):
        raise PdfOutOfMemoryError("The browser ran out of memory. Lower the part size.")

    app = _app(tmp_path, renderer)

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            return await _wait(c, job)

    body = asyncio.run(go())
    assert body["state"] == "failed" and body["out_of_memory"] is True
    assert body["detail"] == "The browser ran out of memory. Lower the part size."


def test_cancelling_stops_the_render_at_its_next_report(tmp_path):
    release = threading.Event()
    escaped = []

    def renderer(model, blob_bytes, progress=None):
        progress(PdfProgress(stage="laying_out", message="one"))
        release.wait(5)
        progress(PdfProgress(stage="laying_out", message="two"))  # raises once cancelled
        escaped.append(True)
        return b"%PDF"

    app = _app(tmp_path, renderer)

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            await asyncio.sleep(0.1)
            # Still occupying the renderer, so a second start is refused...
            busy = await c.post("/api/pdf/jobs")
            cancelled = await c.delete(f"/api/pdf/jobs/{job}")
            busy_after_cancel = await c.post("/api/pdf/jobs")
            release.set()
            for _ in range(200):
                if app.state.pdf_jobs.get(job).finished:
                    break
                await asyncio.sleep(0.02)
            body = (await c.get(f"/api/pdf/jobs/{job}")).json()
            file = await c.get(f"/api/pdf/jobs/{job}/file")
            leaked = list(escaped)
            again = await c.post("/api/pdf/jobs")
            return busy, cancelled, busy_after_cancel, body, file, again, leaked

    busy, cancelled, busy_after, body, file, again, leaked = asyncio.run(go())
    assert busy.status_code == 409 and "still running" in busy.json()["detail"]
    assert cancelled.json()["state"] == "cancelled"
    assert busy_after.status_code == 409
    assert body["state"] == "cancelled" and not leaked
    assert file.status_code == 409
    assert again.status_code == 200


def test_an_empty_scope_is_refused_before_a_job_starts(tmp_path):
    app = _app(tmp_path, lambda model, blob_bytes: b"%PDF")

    async def go():
        async with _client(app) as c:
            return await c.post("/api/pdf/jobs?scope=item&kind=page&id=nope")

    assert asyncio.run(go()).status_code == 422


def test_an_unknown_job_is_a_404(tmp_path):
    app = _app(tmp_path, lambda model, blob_bytes: b"%PDF")

    async def go():
        async with _client(app) as c:
            return (await c.get("/api/pdf/jobs/zzz")).status_code

    assert asyncio.run(go()) == 404


def test_the_part_size_setting_round_trips_and_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv(settings_store.SETTINGS_DIR_ENV, str(tmp_path / "cfg"))
    app = _app(tmp_path, lambda model, blob_bytes: b"%PDF")
    headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}

    async def go():
        async with _client(app) as c:
            shown = (await c.get("/api/settings", headers=headers)).json()["pdf_part_size_mb"]
            saved = await c.put("/api/settings", json={"pdf_part_size_mb": 64}, headers=headers)
            after = (await c.get("/api/settings", headers=headers)).json()["pdf_part_size_mb"]
            bad = await c.put("/api/settings", json={"pdf_part_size_mb": -1}, headers=headers)
            zero = await c.put("/api/settings", json={"pdf_part_size_mb": 0}, headers=headers)
            auto = await c.put("/api/settings", json={"pdf_part_size_mb": None}, headers=headers)
            back = (await c.get("/api/settings", headers=headers)).json()["pdf_part_size_mb"]
            return (
                shown,
                saved.status_code,
                after,
                bad.status_code,
                zero.status_code,
                auto.status_code,
                back,
            )

    # None is "automatic": worked out from the memory free at each export.
    assert asyncio.run(go()) == (None, 200, 64, 422, 200, 200, None)
