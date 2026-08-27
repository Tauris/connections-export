"""A community's children, from the feed the deployment actually serves.

The feed exists, returns Atom,
and its entries carry the child's title and community UUID. A child is an
ordinary community -- component discovery treats it like any other -- so the
parent/child edge is how a person recognises a set, never a containment rule.
"""

from __future__ import annotations

from connections_export.adapters.communities import (
    SubCommunityRef,
    parse_subcommunities,
    subcommunities_url,
)

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry>
    <title type="text">ST-FIR Engineering Sandbox</title>
    <updated>2026-08-01T09:00:00.000Z</updated>
    <snx:communityUuid>11111111-1111-4111-8111-111111111111</snx:communityUuid>
  </entry>
  <entry>
    <title type="text">ST-FIR Engineering ENG3/ENG4</title>
    <updated>2026-07-02T08:00:00.000Z</updated>
    <snx:communityUuid>22222222-2222-4222-8222-222222222222</snx:communityUuid>
  </entry>
</feed>
"""


def test_the_url_is_the_one_the_deployment_serves():
    assert subcommunities_url(
        base_url="https://fake", community_uuid="076a9d6c-f137-46b7-a359-e164ae608a34"
    ) == (
        "https://fake/communities/service/atom/community/subcommunities"
        "?communityUuid=076a9d6c-f137-46b7-a359-e164ae608a34"
    )


def test_every_child_comes_back_with_its_uuid_and_title():
    children = parse_subcommunities(FEED)

    assert children == [
        SubCommunityRef(
            uuid="11111111-1111-4111-8111-111111111111",
            title="ST-FIR Engineering Sandbox",
            updated="2026-08-01T09:00:00.000Z",
        ),
        SubCommunityRef(
            uuid="22222222-2222-4222-8222-222222222222",
            title="ST-FIR Engineering ENG3/ENG4",
            updated="2026-07-02T08:00:00.000Z",
        ),
    ]


def test_a_community_with_no_children_is_an_empty_list_not_an_error():
    assert parse_subcommunities(b'<feed xmlns="http://www.w3.org/2005/Atom"/>') == []


def test_nothing_fetched_is_an_empty_list():
    """A deployment that does not serve the feed is not a failed run: a
    community can always be added by URL instead."""
    assert parse_subcommunities(None) == []


def test_unparseable_xml_is_an_empty_list():
    """Discovery runs against a live deployment where any request can come
    back as a login page. That is no children, never a crash."""
    assert parse_subcommunities(b"<html>login</html>") == []


def test_an_entry_without_a_uuid_is_skipped_rather_than_half_kept():
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title type="text">No uuid here</title></entry></feed>"""

    assert parse_subcommunities(feed) == []
