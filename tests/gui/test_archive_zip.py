"""`POST /api/archive-zip` writes an archive out as one .zip BESIDE it on disk
(archives are local and can be large, so nothing is streamed through the
browser), and the .zip it writes opens straight back in the Reader.

Driven in-process via `httpx.ASGITransport` with a localhost Host. `ARCHIVES_BASE`
is the per-test sandbox from conftest.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import support
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo
from connections_export.gui.model_source import ModelSource


def _post(app, path: str, body: dict) -> httpx.Response:
    async def _do():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            return await client.post(path, json=body)

    return asyncio.run(_do())


def test_zip_is_written_beside_the_archive_and_reopens(tmp_path):
    archive_dir = support.ARCHIVES_BASE / "cap"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, "/api/archive-zip", {"name": "cap"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # Written next to the archive folder, in the archives directory.
    dest = support.ARCHIVES_BASE / "cap.zip"
    assert body["path"] == str(dest)
    assert dest.is_file()
    assert body["size"] == dest.stat().st_size

    # The whole point: the written zip opens back as a (read-only) archive.
    assert ModelSource.from_archive(dest).get_model() is not None


def test_a_missing_archive_is_404(tmp_path):
    app = make_app(demo=True)
    response = _post(app, "/api/archive-zip", {"name": "does-not-exist"})
    assert response.status_code == 404


def test_an_already_zipped_archive_needs_nothing(tmp_path):
    # Make a real zip archive first, then ask to zip it again.
    archive_dir = support.ARCHIVES_BASE / "cap"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)
    _post(app, "/api/archive-zip", {"name": "cap"})

    response = _post(app, "/api/archive-zip", {"name": "cap.zip"})

    assert response.status_code == 200
    assert response.json().get("already_zip") is True
