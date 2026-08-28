"""Which deployment is read comes from the URL, and from nothing else.

A mode would be a flag beside the address, having to agree with it, and
silently winning when it did not. The cost is specific: a real community,
correctly identified, with every request answering 200, reported as
containing nothing -- because the console decided it was reading a demo,
and the community was not in the demo's data.

The demo needs no mode. It is a deployment at a known address,
`DEMO_SAMPLE_BASE_URL`, which no real system can occupy. Reading that
address means reading the synthetic server; reading any other means
reading that one. The answerers already worked this way -- each returns
`None` for anything that is not the demo's -- so the flag in front of
them only ever overrode what the data already knew.

What survives is the useful half: the demo's URLs are always offered, so
there is something to try on a machine with no deployment.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

#: A community from a real deployment: not in the demo's data, ever.
REAL_UUID = "9f3a1b2c-0000-4d5e-8f01-abcdef123456"
REAL_BASE = "https://connections.example.corp"


@pytest.fixture(params=[True, False], ids=["started-with-demo", "started-without-demo"])
def client(request, tmp_path, monkeypatch):
    """Both ways of starting the server, because neither may decide anything."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    return TestClient(make_app(demo=request.param, demo_delay=0), base_url="http://127.0.0.1")


def _demo_community_uuid() -> str:
    from connections_export.gui.demo import demo_sample_urls

    for entry in demo_sample_urls():
        for part in entry["url"].replace("?", "&").split("&"):
            if part.startswith("communityUuid="):
                return part.split("=", 1)[1]
    pytest.skip("the demo offers no community chip")


def test_the_demos_own_community_is_answered_from_the_demo(client):
    """However the server was started."""
    answer = client.get(f"/api/community-components?community_uuid={_demo_community_uuid()}")

    assert answer.status_code == 200
    assert answer.json()["components"], "the demo stopped answering for its own community"


def test_a_real_community_is_never_answered_from_the_demo(client):
    """It may fail to reach the deployment -- there is none in a test -- but
    it must not report the demo's emptiness as the community's."""
    answer = client.get(
        f"/api/community-components?community_uuid={REAL_UUID}&base_url={REAL_BASE}"
    )

    body = answer.json()
    assert body.get("demo") is not True, (
        "a real community was answered by the demo, which is the whole bug"
    )
    if not body["components"]:
        assert body.get("detail"), "an empty answer with no reason given"


def test_the_demo_urls_are_always_offered(client):
    """The useful half of the old flag, kept: something to try on a machine
    with no deployment, whichever way the console was started."""
    urls = client.get("/api/demo-urls").json()["urls"]

    assert urls, "the demo URLs are not offered"
    assert all(entry["url"].startswith(DEMO_SAMPLE_BASE_URL) for entry in urls)


def test_a_run_against_a_real_url_is_not_the_demo_pipeline(client):
    """The same decision on the writing side. A real base URL must reach the
    real crawl even on a server started with the demo flag -- that mismatch
    is how fabricated content was written into a real archive."""
    from connections_export.gui.routes import run as run_routes

    started = {}

    def refuse(*args, **kwargs):
        started["demo_pipeline"] = True
        raise AssertionError("the demo pipeline ran for a real base URL")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_routes, "run_demo", refuse, raising=False)
        client.post("/api/start", json={"base_url": REAL_BASE, "app": "wiki"})

    assert not started.get("demo_pipeline")
