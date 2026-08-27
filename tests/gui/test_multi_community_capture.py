"""Two communities, one archive -- which is what makes their cross-links resolve.

`derive/crosslink.py` upgrades a deployment link to an in-export link only when
its target is present in the SAME export. Capturing the second community into
a second archive would leave each knowing the other's content as a dead URL,
which is the outcome this feature exists to prevent.
"""

from __future__ import annotations

import httpx

from connections_export.fakeserver.model import DEMO_CHILD_COMMUNITY_UUID, DEMO_COMMUNITY_UUID
from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL
from connections_export.gui.requests import CommunitySelection, _StartRequest
from connections_export.gui.routes.run import selected_communities, selection_map


def test_a_flat_component_list_becomes_a_dispatcher_mapping():
    """ "kind:id" strings are what the picker posts; the dispatcher wants a
    mapping. Split on the FIRST colon only -- an id may contain one, a kind
    never does."""
    assert selection_map(["wiki:eng-handbook", "forum:a:b", "files:xyz"]) == {
        "wiki": ["eng-handbook"],
        "forum": ["a:b"],
        "files": ["xyz"],
    }


def test_a_component_with_no_id_is_dropped_rather_than_widening_the_run():
    """A bare "wiki:" would otherwise reach the dispatcher as "every wiki"."""
    assert selection_map(["wiki:", "blog:b1"]) == {"blog": ["b1"]}


def test_every_selected_community_contributes_its_own_mapping():
    body = _StartRequest(
        app="community",
        communities=[
            CommunitySelection(uuid="parent", components=["wiki:w1", "files:parent"]),
            CommunitySelection(uuid="child", components=["forum:f1"]),
        ],
    )

    mappings = [selection_map(c.components) for c in selected_communities(body)]

    assert mappings == [{"wiki": ["w1"], "files": ["parent"]}, {"forum": ["f1"]}]


def _events(app, body, timeout=180):
    """Start a run and read its event stream to completion."""
    import asyncio
    import json
    import time

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            started = await client.post("/api/start", json=body)
            assert started.status_code == 200, started.text
            seen = []
            began = time.time()
            async with client.stream("GET", "/events") as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        seen.append(json.loads(line[6:]))
                        if seen[-1].get("type") == "run_complete":
                            break
                        if time.time() - began > timeout:
                            break
            return seen

    return asyncio.run(go())


def test_two_communities_land_in_one_archive(tmp_path, monkeypatch):
    """The point of the whole feature: one archive, so `crosslink` can see
    both and upgrade the links between them."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)
    body = {
        "base_url": DEMO_SAMPLE_BASE_URL,
        "app": "community",
        "demo": False,
        "communities": [
            {"uuid": DEMO_COMMUNITY_UUID, "components": [f"files:{DEMO_COMMUNITY_UUID}"]},
            {"uuid": DEMO_CHILD_COMMUNITY_UUID, "components": ["wiki:eng-handbook"]},
        ],
    }

    events = _events(app, body)

    assert any(e.get("type") == "run_complete" for e in events), "the run never finished"
    archives = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(archives) == 1, f"expected one archive, got {[p.name for p in archives]}"
    kinds = {e.get("kind") for e in events if e.get("type") == "fetched"}
    assert "community_files" in kinds, f"the first community was not captured: {sorted(kinds)}"
    assert "page" in kinds, f"the second community was not captured: {sorted(kinds)}"
