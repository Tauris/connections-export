"""A run has one end, whatever it is made of.

Every component's crawl finishes with its own `RunComplete`, so a community
capture of five components emits five. The console took the FIRST as the end
of the run: it announced "Complete", re-enabled Start, and the ingest carried
on behind it.

That is what made a Stop look ignored -- the screen said finished while the
work went on, and then showed a live Start again. on a run that should not have been running at all.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL
from tests.gui._served_assets import served_console_js


def _events(app, body, timeout=120):
    import time

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            started = await client.post("/api/start", json=body)
            assert started.status_code == 200, started.text
            seen = []
            begun = time.time()
            async with client.stream("GET", "/events") as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        seen.append(json.loads(line[6:]))
                        if time.time() - begun > timeout:
                            break
            return seen

    return asyncio.run(go())


def _component_values(app):
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.get(
                "/api/community-components",
                params={"community_uuid": DEMO_COMMUNITY_UUID, "base_url": DEMO_SAMPLE_BASE_URL},
            )
            return [f"{c['kind']}:{c['id']}" for c in response.json()["components"]]

    return asyncio.run(go())


def test_a_many_component_run_declares_itself_over_exactly_once(tmp_path, monkeypatch):
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)
    values = _component_values(app)
    assert len(values) > 1, "this needs a run made of several components"

    events = _events(
        app,
        {
            "base_url": DEMO_SAMPLE_BASE_URL,
            "auth_mode": "sspi",
            "app": "community",
            "community_uuid": DEMO_COMMUNITY_UUID,
            "community_components": values,
            "demo": False,
        },
    )

    completions = [e for e in events if e.get("type") == "run_complete"]
    assert completions, "the run never ended at all"
    finals = [e for e in completions if e.get("final")]
    assert len(finals) == 1, (
        f"{len(finals)} of {len(completions)} completions claimed to end the run"
    )
    # And it is the last thing said, not something in the middle.
    assert completions[-1].get("final") is True


def test_the_console_only_finishes_on_the_final_one():
    js = served_console_js()
    match = re.search(r"function liveRunComplete\(.*?\n    liveRunning = false;", js, re.S)
    assert match, "liveRunComplete not found"
    assert "evt.final === false" in match.group(0), (
        "the console still treats any completion as the end of the run"
    )
