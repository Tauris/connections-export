"""Blogs parsers and URL builders, fixture-based (no
fakeserver/round-trip for Blogs yet -- the design, "Testing"). Every
fixture's header comments its the published API reference
source section and sample status."""

from pathlib import Path

import pytest

from connections_export.adapters.blogs import (
    blogs_list_url,
    entries_feed_url,
    entry_comments_url,
    parse_blogs_feed,
    parse_entries_feed,
    parse_entry_comments_feed,
    service_doc_url,
    single_entry_subscription_url,
)
from connections_export.adapters.errors import AdapterError

FIXTURES = Path(__file__).parent / "fixtures"
_BASE = "https://example.com"


# --- Parsing ---


def test_parse_blogs_feed():
    data = (FIXTURES / "blogs_list_feed.xml").read_bytes()
    refs = parse_blogs_feed(data)
    assert [r.uuid for r in refs] == [
        "11111111-aaaa-4bbb-8ccc-111111111111",
        "22222222-aaaa-4bbb-8ccc-222222222222",
    ]
    assert refs[0].title == "Team Blog"
    assert refs[0].self_url == "https://example.com/blogs/teamblog/feed/entries/atom"


def test_parse_blogs_feed_carries_community_and_blog_type():
    data = (
        b'<feed xmlns="http://www.w3.org/2005/Atom" '
        b'xmlns:snx="http://www.ibm.com/xmlns/prod/sn">'
        b"<entry><id>urn:lsid:ibm.com:blogs:blog-1</id><title>Ideas</title>"
        b'<link rel="self" href="https://fake/blogs/ideas/feed/entries/atom"/>'
        b"<snx:communityUuid>community-1</snx:communityUuid>"
        b"<snx:blogType>ideationblog</snx:blogType></entry></feed>"
    )
    ref = parse_blogs_feed(data)[0]
    assert ref.community_uuid == "community-1"
    assert ref.blog_type == "ideationblog"


def test_parse_entries_feed_fields():
    """the design: "each post's id (uuid extracted from its LSID), title,
    author, content, and tags are available"."""
    data = (FIXTURES / "blogs_entries_feed.xml").read_bytes()
    posts = parse_entries_feed(data)
    assert len(posts) == 2

    post = posts[0]
    assert post.id == "33333333-aaaa-4bbb-8ccc-333333333333"
    assert post.title == "Sprint 42 Retrospective"
    assert post.author == "Jane Doe"
    assert post.author_userid == "jdoe"
    assert post.content_html == "<p>Sprint 42 went <b>well</b>.</p>"
    assert post.published == "2009-01-04T20:32:31.171Z"
    assert post.updated == "2009-01-05T09:15:00.000Z"
    assert post.tags == ["roadmap", "retrospective"]
    assert post.comments_enabled is True
    assert post.ranks == {"recommendations": 5, "comment": 2, "hit": 120}
    assert post.provenance == "urn:lsid:ibm.com:blogs:entry-33333333-aaaa-4bbb-8ccc-333333333333"


def test_parse_entries_feed_comments_disabled_and_no_tags():
    data = (FIXTURES / "blogs_entries_feed.xml").read_bytes()
    posts = parse_entries_feed(data)
    second = posts[1]
    assert second.comments_enabled is False
    assert second.tags == []
    assert second.ranks == {}


# --- One-level comment threading ---


def test_parse_entry_comments_feed_one_level_threading():
    data = (FIXTURES / "blogs_entry_comments_feed.xml").read_bytes()
    comments = parse_entry_comments_feed(data)
    assert len(comments) == 3

    top_level, reply, other_top_level = comments
    assert top_level.in_reply_to is None
    assert reply.in_reply_to == top_level.id
    assert other_top_level.in_reply_to is None


def test_parse_entry_comments_feed_content_and_author():
    data = (FIXTURES / "blogs_entry_comments_feed.xml").read_bytes()
    comments = parse_entry_comments_feed(data)
    assert comments[0].author == "Alice"
    assert comments[0].content_html == "<p>Great sprint!</p>"


# --- URL builders ---


def test_blogs_list_url():
    assert blogs_list_url(base_url=_BASE, homepage="teamblog") == (
        "https://example.com/blogs/teamblog/feed/blogs/atom"
    )


def test_entries_feed_url():
    assert entries_feed_url(base_url=_BASE, blog_uuid="98894b24-b081-412b-9d49-86a36b183c13") == (
        _BASE + "/blogs/roller-ui/rendering/feed/98894b24-b081-412b-9d49-86a36b183c13/entries/atom"
    )


def test_entry_comments_url():
    assert entry_comments_url(base_url=_BASE, handle="teamblog", entry_slug="sprint-42") == (
        "https://example.com/blogs/teamblog/feed/entrycomments/sprint-42/atom"
    )


def test_service_doc_url():
    assert service_doc_url(base_url=_BASE) == "https://example.com/blogs/api"


def test_single_entry_subscription_url_is_sample_tagged():
    """[SAMPLE]: the published API reference notes this path
    "appears in rel="self" links inside a documented sample feed, but
    is not specified in prose" -- the literal path text is documented,
    even though the sample feed itself isn't reproduced there."""
    url = single_entry_subscription_url(base_url=_BASE, handle="teamblog", entry_id="abc-123")
    assert url == "https://example.com/blogs/teamblog/feed/entry/atom?entryid=abc-123"


# --- Resilience ---


_MALFORMED_XML = b"<feed><entry><unterminated-element"


@pytest.mark.parametrize(
    "parser",
    [parse_blogs_feed, parse_entries_feed, parse_entry_comments_feed],
)
def test_malformed_xml_raises_adapter_error(parser):
    with pytest.raises(AdapterError):
        parser(_MALFORMED_XML)


def test_entries_feed_wrong_root_raises_adapter_error():
    wrong_root = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom"><id>not-a-feed</id></entry>
    """
    with pytest.raises(AdapterError):
        parse_entries_feed(wrong_root)


def test_entries_feed_entry_missing_id_raises_adapter_error():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>urn:ibm.com:feed</id>
      <entry><title type="text">No id</title></entry>
    </feed>
    """
    with pytest.raises(AdapterError):
        parse_entries_feed(xml)
