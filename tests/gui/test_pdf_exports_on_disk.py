"""PDF exports are written beside the archive, and can be several files.

A large export is hundreds of megabytes or several files; it is written to disk
beside its archive (`<archive>-pdf`, which the archive list knows is not an
archive), downloaded from there, and shown in the system's file manager on
request. Before anything renders, the console can ask what an export would
make: estimated pages, size and time, and the proposed files when split."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from connections_export.gui import exports
from connections_export.gui.app import make_app
from connections_export.gui.archives import list_archives
from connections_export.gui.demo import run_demo


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _app(tmp_path, renderer):
    archive_dir = tmp_path / "archives" / "my-archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    return make_app(demo=True, archive_dir=archive_dir, pdf_renderer=renderer), archive_dir


async def _wait(client, job):
    for _ in range(300):
        body = (await client.get(f"/api/pdf/jobs/{job}")).json()
        if body["state"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


def test_the_pdf_is_written_beside_the_archive_and_downloaded_from_there(tmp_path):
    app, archive_dir = _app(tmp_path, lambda model, blob_bytes: b"%PDF-1.4 on disk")

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            body = await _wait(c, job)
            return body, await c.get(f"/api/pdf/jobs/{job}/file")

    body, file = asyncio.run(go())
    folder = archive_dir.parent / "my-archive-pdf"
    [written] = list(folder.glob("my-archive *.pdf"))
    assert written.read_bytes() == b"%PDF-1.4 on disk"
    assert body["folder"] == str(folder)
    assert body["files"] == [{"name": written.name, "size": len(b"%PDF-1.4 on disk")}]
    assert file.status_code == 200 and file.content == b"%PDF-1.4 on disk"
    from urllib.parse import quote

    assert quote(written.name) in file.headers["content-disposition"]
    # The folder is not an archive.
    assert [a.name for a in list_archives(archive_dir.parent)] == ["my-archive"]


def test_two_exports_never_overwrite_each_other(tmp_path):
    folder = tmp_path
    first = exports.unique(folder / "a 2026-11-15 1200.pdf")
    first.write_bytes(b"1")
    assert exports.unique(folder / "a 2026-11-15 1200.pdf").name == "a 2026-11-15 1200 (2).pdf"


def test_show_in_folder_opens_only_the_folder_the_job_wrote(tmp_path, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr(exports, "reveal", lambda folder: opened.append(folder) or True)
    app, archive_dir = _app(tmp_path, lambda model, blob_bytes: b"%PDF-1.4")

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            await _wait(c, job)
            return await c.post(f"/api/pdf/jobs/{job}/reveal")

    answer = asyncio.run(go())
    assert answer.status_code == 200 and answer.json()["opened"] is True
    assert opened == [archive_dir.parent / "my-archive-pdf"]


def test_the_plan_estimates_and_proposes_files_without_rendering(tmp_path):
    def renderer(model, blob_bytes):  # pragma: no cover - must not be called
        raise AssertionError("planning must not render")

    app, _ = _app(tmp_path, renderer)

    async def go():
        async with _client(app) as c:
            whole = await c.get("/api/pdf/plan")
            split = await c.get("/api/pdf/plan", params={"split": "auto", "max_pages": 8})
            bad = await c.get("/api/pdf/plan", params={"split": "sideways"})
            return whole.json(), split.json(), bad

    whole, split, bad = asyncio.run(go())
    assert whole["estimate"]["pages"] > 10 and whole["estimate"]["seconds"] > 0
    assert whole["files"] == []
    assert len(split["files"]) > 3
    assert all(f["name"].endswith(".pdf") and f["pages"] >= 1 for f in split["files"])
    assert bad.status_code == 422


def test_a_split_job_writes_its_files_in_a_folder_and_zips_them(tmp_path, monkeypatch):
    from connections_export.pdf import sets

    def fake_set(model, blob_bytes, files, out_dir, progress=None, **_kwargs):
        out_dir.mkdir(parents=True)
        paths = []
        for plan in files:
            path = out_dir / plan.name
            path.write_bytes(b"%PDF-1.4 " + plan.name.encode())
            paths.append(path)
        return paths

    monkeypatch.setattr(sets, "render_pdf_set", fake_set)
    app, archive_dir = _app(tmp_path, None)
    # No browser is needed for the fake; skip the guard that checks for one.
    monkeypatch.setattr("connections_export.pdf.CHROMIUM_AVAILABLE", True)

    async def go():
        async with _client(app) as c:
            started = await c.post("/api/pdf/jobs", params={"split": "community"})
            assert started.status_code == 200, started.text
            job = started.json()["job"]
            body = await _wait(c, job)
            return body, await c.get(f"/api/pdf/jobs/{job}/file")

    body, file = asyncio.run(go())
    assert body["state"] == "done", body
    names = [f["name"] for f in body["files"]]
    assert names[0].startswith("1 - ") and len(names) >= 2
    assert Path(body["folder"]).parent == archive_dir.parent / "my-archive-pdf"
    assert file.headers["content-type"] == "application/zip"
    import io
    import zipfile

    assert sorted(zipfile.ZipFile(io.BytesIO(file.content)).namelist()) == sorted(names)


def test_the_plan_warns_before_an_export_that_cannot_fit_on_disk(tmp_path, monkeypatch):
    from connections_export.pdf import memory

    monkeypatch.setattr(memory, "free_disk", lambda _path: 10 * 2**20)
    monkeypatch.setattr(memory, "available_memory", lambda: 1 * 2**30)
    app, _ = _app(tmp_path, lambda model, blob_bytes: b"%PDF-1.4")

    async def go():
        async with _client(app) as c:
            return (await c.get("/api/pdf/plan")).json()

    checks = asyncio.run(go())["checks"]
    levels = {c["level"]: c["message"] for c in checks}
    assert "Not enough disk space" in levels["error"]
    assert "of memory is free" in levels["warning"]


def test_a_job_that_cannot_fit_on_disk_does_not_start(tmp_path, monkeypatch):
    from connections_export.pdf import memory

    monkeypatch.setattr(memory, "free_disk", lambda _path: 10 * 2**20)
    archive_dir = tmp_path / "archives" / "my-archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)
    monkeypatch.setattr("connections_export.pdf.CHROMIUM_AVAILABLE", True)

    async def go():
        async with _client(app) as c:
            return await c.post("/api/pdf/jobs")

    answer = asyncio.run(go())
    assert answer.status_code == 507
    assert "Not enough disk space" in answer.json()["detail"]
