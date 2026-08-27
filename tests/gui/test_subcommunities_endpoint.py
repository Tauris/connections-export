"""What the picker asks when it offers a community's children.

Discovery is a convenience: it prefills candidates. A community can always be
added by URL, which is the only way to add one that is merely related to
another rather than its child.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.fakeserver.model import DEMO_CHILD_COMMUNITY_UUID, DEMO_COMMUNITY_UUID
from connections_export.gui import make_app
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL


def _get(app, path, params):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path, params=params)

    return asyncio.run(go())


def test_the_demo_parent_offers_its_child():
    app = make_app(demo=True, demo_delay=0)

    response = _get(
        app,
        "/api/subcommunities",
        {"community_uuid": DEMO_COMMUNITY_UUID, "base_url": DEMO_SAMPLE_BASE_URL},
    )

    assert response.status_code == 200
    assert [child["id"] for child in response.json()["children"]] == [DEMO_CHILD_COMMUNITY_UUID]


def test_a_community_with_no_children_answers_with_an_empty_list():
    app = make_app(demo=True, demo_delay=0)

    response = _get(
        app,
        "/api/subcommunities",
        {"community_uuid": DEMO_CHILD_COMMUNITY_UUID, "base_url": DEMO_SAMPLE_BASE_URL},
    )

    assert response.status_code == 200
    assert response.json()["children"] == []


def test_a_missing_uuid_answers_empty_rather_than_failing_the_page():
    """The picker asks this while the user is still typing. A lookup never
    fails the screen around it -- the same contract every other lookup here
    keeps."""
    app = make_app(demo=True, demo_delay=0)

    response = _get(app, "/api/subcommunities", {"community_uuid": "", "base_url": ""})

    assert response.status_code == 200
    assert response.json()["children"] == []
