"""The demo must exercise the REAL discovery path, not only its own shortcut.

In demo mode `/api/community-components` answers from the synthesized data
directly, because the fake server is in-process and unreachable at the demo's
placeholder host. That shortcut is correct -- and it meant
`discover_components`, the code that actually runs against a deployment, had no
demo coverage at all.

Rich Content shipped that way: fully supported end to end, offered by the demo
picker, and never offered by live discovery. The demo agreed with itself and
with nothing else.

So: run the real discovery against the fake server and require it to find what
the demo promises. Anything the shortcut offers and discovery cannot is a
component a user will not see on their own deployment.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.crawler.community import discover_components
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID, assign_community
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.fakeserver.synth import (
    synthesize_blogs,
    synthesize_files,
    synthesize_forums,
    synthesize_rich_content,
)
from connections_export.gui.app import _demo_community_components
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.demo import demo_synth_seed

BASE = "https://fake"


@pytest.fixture
def discovered():
    """`discover_components` run against the demo dataset, for real."""
    seed = demo_synth_seed(0)
    wikiset = build_prototype_wikiset()
    blogset = synthesize_blogs(seed)
    forumset = synthesize_forums(seed)
    assign_community(wikiset=wikiset, blogset=blogset, forumset=forumset)
    app = make_app(
        wikiset,
        blogset=blogset,
        forumset=forumset,
        fileset=synthesize_files(seed, community_uuid=DEMO_COMMUNITY_UUID),
        rteset=synthesize_rich_content(seed, community_uuid=DEMO_COMMUNITY_UUID),
    )
    with httpx.Client(transport=SyncASGIBridge(app), base_url=BASE) as client:

        def fetch(url: str) -> bytes | None:
            try:
                response = client.get(url)
            except Exception:  # noqa: BLE001 - a dead endpoint is "not found"
                return None
            return response.content if response.status_code == 200 else None

        yield discover_components(community_uuid=DEMO_COMMUNITY_UUID, base_url=BASE, fetch=fetch)


def test_live_discovery_finds_rich_content_in_the_demo_data(discovered):
    found = [c for c in discovered["components"] if c["kind"] == "rich_content"]

    assert len(found) == 1, discovered.get("rich_content_note", "no note given")
    assert found[0]["count"] > 0


def test_live_discovery_reports_the_placed_count_too(discovered):
    found = next(c for c in discovered["components"] if c["kind"] == "rich_content")

    assert found["placed"] > found["count"]


def test_live_discovery_finds_the_file_library(discovered):
    assert [c for c in discovered["components"] if c["kind"] == "files"]


def test_nothing_the_demo_offers_is_invisible_to_live_discovery():
    """The parity check itself. The demo shortcut and the deployment code path
    must offer the same KINDS -- if they diverge, the demo is reassuring users
    about something that will not happen on their system."""
    seed = demo_synth_seed(0)
    wikiset = build_prototype_wikiset()
    blogset = synthesize_blogs(seed)
    forumset = synthesize_forums(seed)
    assign_community(wikiset=wikiset, blogset=blogset, forumset=forumset)
    app = make_app(
        wikiset,
        blogset=blogset,
        forumset=forumset,
        fileset=synthesize_files(seed, community_uuid=DEMO_COMMUNITY_UUID),
        rteset=synthesize_rich_content(seed, community_uuid=DEMO_COMMUNITY_UUID),
    )
    with httpx.Client(transport=SyncASGIBridge(app), base_url=BASE) as client:

        def fetch(url: str) -> bytes | None:
            try:
                response = client.get(url)
            except Exception:  # noqa: BLE001
                return None
            return response.content if response.status_code == 200 else None

        live = discover_components(community_uuid=DEMO_COMMUNITY_UUID, base_url=BASE, fetch=fetch)

    shortcut = _demo_community_components(DEMO_COMMUNITY_UUID)
    promised = {c["kind"] for c in shortcut["components"]}
    reachable = {c["kind"] for c in live["components"]}

    assert promised - reachable == set(), (
        f"the demo offers {sorted(promised - reachable)} that live discovery cannot find"
    )
