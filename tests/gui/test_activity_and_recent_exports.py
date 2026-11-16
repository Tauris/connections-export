"""The console can find running work again, and every export on disk.

A reload or a second tab used to lose sight of a running PDF export. The
activity endpoint lists running and recent jobs; recent exports come from the
`-pdf` folders on disk, so they survive a restart. Download and "Show in
folder" take names, never paths, and reach only those folders."""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import exports, support
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _app(tmp_path, monkeypatch, renderer=None):
    base = tmp_path / "archives"
    monkeypatch.setattr(support, "ARCHIVES_BASE", base)
    archive_dir = base / "my-archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    return make_app(demo=True, archive_dir=archive_dir, pdf_renderer=renderer), base


async def _wait(client, job):
    for _ in range(300):
        body = (await client.get(f"/api/pdf/jobs/{job}")).json()
        if body["state"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_finished_job_is_listed_with_what_it_was(tmp_path, monkeypatch):
    app, _ = _app(tmp_path, monkeypatch, lambda model, blob_bytes: b"%PDF-1.4")

    async def go():
        async with _client(app) as c:
            job = (await c.post("/api/pdf/jobs")).json()["job"]
            await _wait(c, job)
            return job, (await c.get("/api/activity")).json()

    job, activity = asyncio.run(go())
    assert activity["capture"] == {"running": False}
    [listed] = activity["pdf"]
    assert listed["id"] == job and listed["state"] == "done"
    assert listed["title"] == "Whole archive — one PDF"
    assert listed["archive"] == "my-archive" and listed["created_at"] > 0


def test_recent_exports_come_from_the_folders_on_disk(tmp_path, monkeypatch):
    app, base = _app(tmp_path, monkeypatch)
    folder = exports.pdf_folder(base / "my-archive", base)
    (folder / "my-archive 2026-11-15 1000.pdf").write_bytes(b"%PDF one")
    set_dir = folder / "my-archive 2026-11-15 1100"
    set_dir.mkdir()
    (set_dir / "1 - A.pdf").write_bytes(b"%PDF a")
    (set_dir / "2 - B.pdf").write_bytes(b"%PDF b")

    async def go():
        async with _client(app) as c:
            recent = (await c.get("/api/exports/recent")).json()["exports"]
            again = await c.get(
                "/api/exports/file",
                params={"folder": folder.name, "name": "my-archive 2026-11-15 1000.pdf"},
            )
            return recent, again

    recent, again = asyncio.run(go())
    by_name = {e["name"]: e for e in recent}
    assert by_name["my-archive 2026-11-15 1000.pdf"]["kind"] == "pdf"
    assert by_name["my-archive 2026-11-15 1100"]["kind"] == "set"
    assert by_name["my-archive 2026-11-15 1100"]["files"] == ["1 - A.pdf", "2 - B.pdf"]
    assert all(e["archive"] == "my-archive" for e in recent)
    assert again.status_code == 200 and again.content == b"%PDF one"


def test_nothing_outside_the_export_folders_can_be_fetched_or_opened(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(exports, "reveal", lambda folder: opened.append(folder) or True)
    app, base = _app(tmp_path, monkeypatch)
    exports.pdf_folder(base / "my-archive", base)
    (tmp_path / "secret.pdf").write_bytes(b"%PDF secret")

    async def go():
        async with _client(app) as c:
            return [
                (await c.get("/api/exports/file", params={"folder": f, "name": n})).status_code
                for f, n in [
                    ("..", "secret.pdf"),
                    ("my-archive", "manifest.jsonl"),
                    ("my-archive-pdf", "../../secret.pdf"),
                    ("my-archive-pdf", "missing.pdf"),
                ]
            ] + [
                (await c.post("/api/exports/reveal", json={"folder": "my-archive"})).status_code,
                (await c.post("/api/exports/reveal", json={"folder": "../x-pdf"})).status_code,
            ]

    assert asyncio.run(go()) == [404, 404, 404, 404, 404, 404]
    assert opened == []


def test_contents_groups_the_archive_as_a_reader_would(tmp_path, monkeypatch):
    app, _ = _app(tmp_path, monkeypatch)

    async def go():
        async with _client(app) as c:
            return (await c.get("/api/contents", params={"largest": 3})).json()

    body = asyncio.run(go())
    tree = body["tree"]
    assert tree["level"] == "root" and tree["pages"] > 10
    communities = {c["title"]: c for c in tree["children"]}
    platform = communities["Platform Engineering"]
    # The subcommunity sits inside its parent, after the parent's own apps.
    assert platform["children"][-1]["title"] == "Platform Engineering — Tooling"
    assert {a["title"] for a in platform["children"][:-1]} >= {"Wiki", "Blogs", "Forums"}
    assert len(body["largest"]) == 3
    sizes = [item["size"] for item in body["largest"]]
    assert sizes == sorted(sizes, reverse=True) and all(i["in"] for i in body["largest"])
