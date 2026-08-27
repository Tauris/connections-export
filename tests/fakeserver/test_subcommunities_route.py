"""The demo deployment has a parent community with a child.

Without this the multi-community work could not be tested at all: the demo is
the only deployment the tests have, and a fixture that cannot produce the case
would leave every assertion below it describing a fiction.
"""

from __future__ import annotations

import httpx

from connections_export.adapters.communities import parse_subcommunities, subcommunities_url
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import (
    DEMO_CHILD_COMMUNITY_TITLE,
    DEMO_CHILD_COMMUNITY_UUID,
    DEMO_COMMUNITY_UUID,
)
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge


def _client():
    app = make_app(synthesize(SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1)))
    return httpx.Client(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request),
        base_url="https://fake",
    )


def test_the_demo_community_has_one_child():
    with _client() as client:
        response = client.get(
            subcommunities_url(base_url="https://fake", community_uuid=DEMO_COMMUNITY_UUID)
        )

    assert response.status_code == 200
    children = parse_subcommunities(response.content)
    assert [child.uuid for child in children] == [DEMO_CHILD_COMMUNITY_UUID]
    assert children[0].title == DEMO_CHILD_COMMUNITY_TITLE


def test_the_child_has_no_children_of_its_own():
    """One level, which is what the real deployment does. Nesting
    deeper is a decision, not something that happens by accident here."""
    with _client() as client:
        response = client.get(
            subcommunities_url(base_url="https://fake", community_uuid=DEMO_CHILD_COMMUNITY_UUID)
        )

    assert response.status_code == 200
    assert parse_subcommunities(response.content) == []


def test_an_unknown_community_is_an_empty_feed_not_a_404():
    """A community with no children still has a subcommunities feed. A 404
    here would make "no children" indistinguishable from "no such community",
    and the crawler must not treat the first as a failure."""
    with _client() as client:
        response = client.get(
            subcommunities_url(base_url="https://fake", community_uuid="not-a-community")
        )

    assert response.status_code == 200
    assert parse_subcommunities(response.content) == []
