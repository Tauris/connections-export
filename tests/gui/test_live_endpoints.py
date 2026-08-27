"""`/api/model` serves a live partial
snapshot with an `X-HCL-Model-State` header, and `POST /api/start`
attaches the run's archive so the model becomes browsable while/after
the run. Driven in-process via `httpx.ASGITransport`; `base_url` is a
localhost Host so the security guard admits it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from connections_export.archive.store import Archive
from connections_export.crawler.events import Event
from connections_export.derive import derive
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _run(coro):
    return asyncio.run(coro)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _demo_archive(tmp_path: Path) -> Path:
    archive_dir = tmp_path / "archive"
    events: list[Event] = []
    run_demo(events.append, seed=5, archive_dir=archive_dir, delay=0)
    return archive_dir


def test_model_endpoint_reports_pending_partial_and_complete(tmp_path):
    archive_dir = _demo_archive(tmp_path)
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            before = await client.get("/api/model")

            # Live-attach the in-progress run's archive: partial.
            app.state.model_source.attach_live(archive_dir)
            partial = await client.get("/api/model")

            # Run finished -> final model stashed: complete.
            final = derive(Archive.open(archive_dir))
            app.state.model_source.stash(final, archive_dir=archive_dir)
            complete = await client.get("/api/model")
            return before, partial, complete

    before, partial, complete = _run(_do())

    assert before.status_code == 503
    assert before.json()["status"] == "pending"

    assert partial.status_code == 200
    assert partial.headers["x-hcl-model-state"] == "partial"
    assert partial.json()["wikis"]

    assert complete.status_code == 200
    assert complete.headers["x-hcl-model-state"] == "complete"


def test_start_demo_makes_the_model_live_and_then_complete(tmp_path):
    app = make_app(demo=True, demo_seed=6, demo_delay=0)

    async def _do():
        async with _client(app) as client:
            start = await client.post("/api/start", json={"demo": True})
            assert start.status_code == 200
            # Drain the stream so the run completes and stashes.
            async with client.stream("GET", "/events") as response:
                async for _chunk in response.aiter_text():
                    pass
            model = await client.get("/api/model")
            return model

    model = _run(_do())
    assert model.status_code == 200
    assert model.headers["x-hcl-model-state"] == "complete"
    body = json.loads(model.text)
    assert body["wikis"], "a completed demo run must expose its derived wikis"

    # A body-image blob resolves from the run's archive.
    async def _blob():
        async with _client(app) as client:
            first_wiki = body["wikis"][0]
            for page in first_wiki["pages"].values():
                for asset in page["assets"]:
                    if asset.get("present") and asset.get("blob_hash"):
                        hex_hash = asset["blob_hash"].split(":")[-1]
                        return await client.get(f"/api/blob/{hex_hash}")
            return None

    blob = _run(_blob())
    assert blob is not None and blob.status_code == 200 and blob.content
