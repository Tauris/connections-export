"""Tasks 4.1/4.2: Forums parsers, type discrimination (the TRAP),
flat-feed -> tree reconstruction, and URL builders, fixture-based (no
fakeserver/round-trip for Forums yet -- the design, "Testing"). Every
fixture's header comments its the published API reference
source section and sample status."""

from pathlib import Path

import pytest

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.forums import (
    ForumRef,
    attachment_filenames_from_html,
    attachments_from_html,
    build_reply_tree,
    entity_type,
    forums_list_url,
    parse_forums_feed,
    parse_replies_feed,
    parse_topics_feed,
    replies_url,
    reply_url,
    topic_url,
    topics_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
_BASE = "https://example.com"

_TOPIC_UUID = "88888888-aaaa-4bbb-8ccc-888888888888"


def _topics():
    return parse_topics_feed((FIXTURES / "forums_topics_feed.xml").read_bytes())


def _replies():
    return parse_replies_feed((FIXTURES / "forums_replies_feed_flat_chain.xml").read_bytes())


# --- Stage 2: parse_forums_feed (the forums-list feed, mirroring
# blogs.parse_blogs_feed) ---

_FORUMS_LIST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="text">All Forums</title>
  <entry>
    <id>urn:lsid:ibm.com:forum:11111111-aaaa-4bbb-8ccc-111111111111</id>
    <title type="text">Support</title>
    <link rel="self" href="https://example.com/forums/atom/topics?forumUuid=11111111-aaaa-4bbb-8ccc-111111111111"/>
  </entry>
  <entry>
    <id>urn:lsid:ibm.com:forum:22222222-aaaa-4bbb-8ccc-222222222222</id>
    <title type="text">Announcements</title>
    <link rel="self" href="https://example.com/forums/atom/topics?forumUuid=22222222-aaaa-4bbb-8ccc-222222222222"/>
  </entry>
</feed>"""


def test_parse_forums_feed_yields_refs():
    refs = parse_forums_feed(_FORUMS_LIST_FEED)
    assert [r.uuid for r in refs] == [
        "11111111-aaaa-4bbb-8ccc-111111111111",
        "22222222-aaaa-4bbb-8ccc-222222222222",
    ]
    assert [r.title for r in refs] == ["Support", "Announcements"]
    # self link is that forum's own topics feed -- the URL traversal
    # follows next (never rebuilt from the bare uuid).
    assert refs[0].self_url.endswith("topics?forumUuid=11111111-aaaa-4bbb-8ccc-111111111111")
    assert all(isinstance(r, ForumRef) for r in refs)


def test_parse_forums_feed_id_required():
    bad = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>x</title></entry></feed>'
    with pytest.raises(AdapterError):
        parse_forums_feed(bad)


def test_parse_forums_feed_degrades_missing_title_and_link():
    minimal = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><id>urn:lsid:ibm.com:forum:deadbeef</id></entry></feed>"
    )
    (ref,) = parse_forums_feed(minimal)
    assert ref.uuid == "deadbeef"
    assert ref.title is None
    assert ref.self_url is None


# --- THE TRAP: a topic is typed by its category, not by whether it
# carries a reply reference ---


def test_topic_with_in_reply_to_is_typed_as_topic_not_reply():
    """Both fixture topic entries carry <thr:in-reply-to> (their ref =
    the parent forum). Entity type MUST come from the sn/type category
    term, never from thr:in-reply-to's presence."""
    root = atom.parse_xml((FIXTURES / "forums_topics_feed.xml").read_bytes(), expected_root="feed")
    for entry in atom.entries(root):
        assert atom.in_reply_to(entry) is not None  # the trap is present...
        assert entity_type(entry) == "forum-topic"  # ... but type reads correctly anyway


def test_parse_topics_feed_does_not_misclassify_despite_in_reply_to():
    topics = _topics()
    assert len(topics) == 2
    # Every parsed topic is a ForumTopic (not silently dropped/errored
    # because thr:in-reply-to was present).
    assert all(t.id for t in topics)


# --- Topics feed field parsing ---


def test_parse_topics_feed_fields():
    topics = _topics()
    question = topics[0]
    assert question.id == _TOPIC_UUID
    assert question.title == "How do I configure SSO?"
    assert question.author == "Dana"
    assert question.content_html == "<p>Looking for SSO setup steps.</p>"
    assert question.published == "2010-03-01T10:00:00.000Z"
    assert question.provenance == f"urn:lsid:ibm.com:forum:{_TOPIC_UUID}"


def test_parse_topics_feed_forum_uuid_from_in_reply_to_ref():
    """The topic's own thr:in-reply-to/@ref (parent forum) is captured
    as forum_uuid -- legitimate *data*, just never used for typing."""
    topics = _topics()
    assert topics[0].forum_uuid == "99999999-aaaa-4bbb-8ccc-999999999999"


def test_parse_topics_feed_flags():
    topics = _topics()
    assert topics[0].flags == {"question", "answered"}
    assert topics[1].flags == {"pinned", "locked"}


# --- FLAT replies feed + arbitrary-depth tree reconstruction
# ---


def test_parse_replies_feed_is_flat():
    """The feed itself carries no nesting -- three sibling <entry>
    elements, not two levels of XML nesting."""
    replies = _replies()
    assert len(replies) == 3
    assert [r.id for r in replies] == [
        "bbbbbbb1-aaaa-4bbb-8ccc-bbbbbbbbbbb1",
        "ccccccc1-aaaa-4bbb-8ccc-ccccccccccc1",
        "ddddddd1-aaaa-4bbb-8ccc-ddddddddddd1",
    ]


def test_parse_replies_feed_ref_and_source_topic():
    reply1, reply2, reply3 = _replies()
    assert reply1.ref == _TOPIC_UUID
    assert reply1.source_topic == _TOPIC_UUID
    assert reply2.ref == reply1.id  # reply-to-reply, not reply-to-topic
    assert reply2.source_topic == _TOPIC_UUID
    assert reply3.ref == _TOPIC_UUID
    assert reply3.source_topic == _TOPIC_UUID


def test_parse_replies_feed_flags():
    reply1, reply2, reply3 = _replies()
    assert reply1.flags == set()
    assert reply3.flags == {"answer"}


def test_attachment_filename_parser_reads_rendered_forum_link():
    html = '<a href="/forums/ajax/download?nodeId=n1">answer.pdf</a>'
    assert attachment_filenames_from_html(html) == {"n1": "answer.pdf"}


def test_rendered_attachment_list_preserves_filename_and_download_url():
    html = (
        '<ul class="lotusList dfAttachments"><li uuid="u1" fileName=" report.txt">'
        '<ul><li class="dfAttachment"><a href="/download/u1/report.txt">report.txt</a>'
        "</li></ul></li></ul>"
    )
    attachments = attachments_from_html(html)
    assert [(a.uuid, a.filename, a.enclosure_href) for a in attachments] == [
        ("u1", "report.txt", "/download/u1/report.txt")
    ]


def test_build_reply_tree_reconstructs_full_depth():
    """reply2 -> reply1 -> topic, all inferred from ref alone across a
    flat feed. Two roots (reply1, reply3) since both reply directly to
    the topic; reply2 nests one level under reply1."""
    reply1, reply2, reply3 = _replies()
    tree = build_reply_tree([reply1, reply2, reply3])

    roots_by_id = {node.reply.id: node for node in tree}
    assert set(roots_by_id) == {reply1.id, reply3.id}

    reply1_node = roots_by_id[reply1.id]
    assert len(reply1_node.children) == 1
    assert reply1_node.children[0].reply.id == reply2.id
    assert reply1_node.children[0].children == []

    reply3_node = roots_by_id[reply3.id]
    assert reply3_node.children == []


def test_build_reply_tree_every_reply_records_source_topic():
    """the design: "each reply also records its top-level topic" -- via
    the reply's own source_topic field, present at every depth of the
    reconstructed tree."""
    replies = _replies()
    tree = build_reply_tree(replies)

    def all_nodes(nodes):
        for node in nodes:
            yield node
            yield from all_nodes(node.children)

    for node in all_nodes(tree):
        assert node.reply.source_topic == _TOPIC_UUID


def test_build_reply_tree_is_cycle_safe():
    """Adversarial/malformed input: two replies whose refs point at
    each other forms a closed loop with no reply pointing outside the
    reply set at all -- no ref value here is even a valid topic uuid,
    so nothing would naturally become a root. build_reply_tree must
    not hang, and must not silently drop either reply."""
    from connections_export.adapters.model import ForumReply

    a = ForumReply(id="a", ref="b", source_topic="t")
    b = ForumReply(id="b", ref="a", source_topic="t")

    tree = build_reply_tree([a, b])

    def all_ids(nodes):
        for node in nodes:
            yield node.reply.id
            yield from all_ids(node.children)

    assert set(all_ids(tree)) == {"a", "b"}


# --- URL builders (query-param addressing,
# Forums endpoint table) ---


def test_forums_list_url():
    assert forums_list_url(base_url=_BASE) == "https://example.com/forums/atom/forums"


def test_topics_url():
    assert topics_url(base_url=_BASE, forum_uuid="f1") == (
        "https://example.com/forums/atom/topics?forumUuid=f1"
    )


def test_topic_url():
    assert topic_url(base_url=_BASE, topic_uuid="t1") == (
        "https://example.com/forums/atom/topic?topicUuid=t1"
    )


def test_replies_url():
    assert replies_url(base_url=_BASE, topic_uuid="t1") == (
        "https://example.com/forums/atom/replies?topicUuid=t1"
    )


def test_reply_url():
    assert reply_url(base_url=_BASE, reply_uuid="r1") == (
        "https://example.com/forums/atom/reply?replyUuid=r1"
    )


# --- Resilience ---


_MALFORMED_XML = b"<feed><entry><unterminated-element"


@pytest.mark.parametrize("parser", [parse_topics_feed, parse_replies_feed])
def test_malformed_xml_raises_adapter_error(parser):
    with pytest.raises(AdapterError):
        parser(_MALFORMED_XML)


def test_topics_feed_wrong_root_raises_adapter_error():
    wrong_root = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom"><id>not-a-feed</id></entry>
    """
    with pytest.raises(AdapterError):
        parse_topics_feed(wrong_root)


def test_topics_feed_entry_missing_id_raises_adapter_error():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>urn:ibm.com:feed</id>
      <entry><title type="text">No id</title></entry>
    </feed>
    """
    with pytest.raises(AdapterError):
        parse_topics_feed(xml)


def test_replies_feed_entry_missing_id_raises_adapter_error():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>urn:ibm.com:feed</id>
      <entry><title type="text">No id</title></entry>
    </feed>
    """
    with pytest.raises(AdapterError):
        parse_replies_feed(xml)


def test_flags_drop_per_viewer_and_metric_noise():
    """`sn/flags` on a real deployment carries per-viewer UI state
    (`NotRecommendedByCurrentUser`) and metrics (`ThreadRecommendationCount`)
    alongside the meaningful markers; only the meaningful ones survive."""
    from lxml import etree

    from connections_export.adapters.forums import _flags

    flags = "http://www.ibm.com/xmlns/prod/sn/flags"
    terms = ["pinned", "answered", "NotRecommendedByCurrentUser", "ThreadRecommendationCount"]
    cats = "".join(f'<category scheme="{flags}" term="{t}"/>' for t in terms)
    xml = f'<entry xmlns="http://www.w3.org/2005/Atom">{cats}</entry>'.encode()
    assert _flags(etree.fromstring(xml)) == {"pinned", "answered"}


def test_recommendation_entries_are_not_parsed_as_content():
    """A forum feed can carry `recommendation` (like) entries whose author is
    the *liking* user. They must not be parsed as topics/replies -- else a like
    would pollute the thread and, worse, make the author filter count a like as
    authorship (the user's report)."""
    ns = 'xmlns="http://www.w3.org/2005/Atom"'
    tns = "http://www.ibm.com/xmlns/prod/sn/type"

    def entry(kind: str, author: str) -> str:
        return (
            f"<entry><id>urn:x:{author}</id>"
            f'<category scheme="{tns}" term="{kind}"/>'
            f"<author><name>{author}</name></author></entry>"
        )

    replies_xml = (
        f"<feed {ns}>{entry('forum-reply', 'Real Replier')}"
        f"{entry('recommendation', 'The Liker')}</feed>"
    ).encode()
    replies = parse_replies_feed(replies_xml)
    assert [r.author for r in replies] == ["Real Replier"]  # the like is gone

    topics_xml = (
        f"<feed {ns}>{entry('forum-topic', 'Real Poster')}"
        f"{entry('recommendation', 'The Liker')}</feed>"
    ).encode()
    topics = parse_topics_feed(topics_xml)
    assert [t.author for t in topics] == ["Real Poster"]
