"""The adapter against the fake server, rather than against a fixture string.

`test_rte.py` proves the parsers handle the shape we BELIEVE the deployment
sends. This proves the parsers handle the shape the fake server actually
sends -- which is what every crawler test runs against, so a drift between the
two would make the whole demo pipeline agree with itself and with nothing else.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.adapters.rte import (
    parse_page_entry,
    parse_widget_layout,
    rich_content_page_url,
    widget_layout_url,
)
from connections_export.fakeserver import make_app
from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.fakeserver.synth import synthesize_rich_content
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.demo import demo_synth_seed

BASE = "http://fake"


@pytest.fixture
def client():
    rteset = synthesize_rich_content(demo_synth_seed(0), community_uuid=DEMO_COMMUNITY_UUID)
    app = make_app(build_prototype_wikiset(), rteset=rteset)
    # The fake runs in-process; `SyncASGIBridge` is how the rest of the suite
    # drives it from synchronous code.
    with httpx.Client(transport=SyncASGIBridge(app), base_url=BASE) as http:
        yield http


def _widgets(client):
    url = widget_layout_url(base_url=BASE, community_uuid=DEMO_COMMUNITY_UUID)
    return parse_widget_layout(client.get(url).content, community_uuid=DEMO_COMMUNITY_UUID)


def test_the_layout_the_fake_serves_parses(client):
    widgets = _widgets(client)

    assert widgets, "no rich content widgets discovered from the layout"


def test_other_applications_widgets_are_not_mistaken_for_pages(client):
    """The fake serves a Forums and a Files widget, each with a resource id of
    its own. Picking those up would mean fetching them as rich content."""
    widgets = _widgets(client)

    ids = [w.resource_id for w in widgets]
    assert "forum-widget-resource" not in ids
    assert "files-widget-resource" not in ids


def test_the_uninitialized_widget_survives_discovery(client):
    """Placed but never written into: no resource id, nothing to fetch, and it
    must still be counted so "placed" and "captured" can differ out loud."""
    widgets = _widgets(client)

    assert any(not w.initialized for w in widgets)
    assert any(w.initialized for w in widgets)


def test_every_initialized_widget_retrieves(client):
    """The ids discovery hands out must be the ids retrieval accepts -- the
    exact property the on-target capture was asked to confirm."""
    for widget in [w for w in _widgets(client) if w.initialized]:
        url = rich_content_page_url(
            base_url=BASE, community_uuid=DEMO_COMMUNITY_UUID, resource_id=widget.resource_id
        )
        response = client.get(url)

        assert response.status_code == 200, widget.resource_id
        page = parse_page_entry(response.content, resource_id=widget.resource_id)
        assert page.content_html, f"{widget.title} came back with no body"
        assert page.title


def test_an_uninitialized_widget_has_nothing_to_retrieve(client):
    """Guards against a crawler that "helpfully" substitutes something for the
    missing id: there is no page, and asking for one is a 404."""
    response = client.get(
        rich_content_page_url(
            base_url=BASE, community_uuid=DEMO_COMMUNITY_UUID, resource_id="not-a-real-resource"
        )
    )

    assert response.status_code == 404


def test_a_community_with_no_rich_content_gets_a_feed_not_an_error(client):
    """A community always HAS a widget layout; it may hold no RTE widgets.
    That is an empty result, not a failed run."""
    url = widget_layout_url(base_url=BASE, community_uuid="some-other-community")
    response = client.get(url)

    assert response.status_code == 200
    assert parse_widget_layout(response.content, community_uuid="some-other-community") == []
