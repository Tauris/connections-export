"""Tasks 8.1-8.3: synthesize -> serve (call the fake's serializers
directly, no HTTP) -> parse back with the adapter -> assert structure
survives. This is the structural-coverage axis; the independent anchor against the reference doc's
own
verbatim bytes lives in test_page_entry.py / test_navigation.py.
"""

import json

from connections_export.adapters.wikis import (
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_page_entry,
    parse_versions_feed,
    parse_wikis_feed,
)
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver import navjson as fake_navjson
from connections_export.fakeserver.synth import SynthSeed, synthesize

_AUTH_ROOT = "basic"
_BASE_URL = "https://fake"


def _assert_wiki_roundtrips(wikiset, wiki) -> None:
    # -- wikis feed: labels/uuids survive.
    wikis_xml = fake_atom.wikis_feed(wikiset, auth_root=_AUTH_ROOT, base_url=_BASE_URL)
    refs = {r.uuid: r for r in parse_wikis_feed(wikis_xml)}
    assert refs[wiki.uuid].label == wiki.label
    assert refs[wiki.uuid].title == wiki.title

    # -- every page entry: uuid, label, parent_uuid, and the
    # comment/version/attachment counts (via the artifacts feeds and
    # the page entry's own thr:count) survive.
    for page in wiki.pages:
        entry_xml = fake_atom.page_entry(wiki, page, auth_root=_AUTH_ROOT, base_url=_BASE_URL)
        parsed_page = parse_page_entry(entry_xml)

        assert parsed_page.uuid == page.uuid
        assert parsed_page.label == page.label
        assert parsed_page.title == page.title
        assert parsed_page.parent_uuid == page.parent_uuid
        assert parsed_page.version_label == page.version_label

        comments_xml = fake_atom.artifacts_feed(
            wiki, page, category="comment", auth_root=_AUTH_ROOT, base_url=_BASE_URL
        )
        parsed_comments = parse_comments_feed(comments_xml)
        assert len(parsed_comments) == len(page.comments)
        # thr:count on the page entry's replies link agrees with the
        # actual number of parsed comments (the design, "Comment count
        # agrees with the replies link").
        assert parsed_page.replies_count == len(parsed_comments)

        versions_xml = fake_atom.artifacts_feed(
            wiki, page, category="version", auth_root=_AUTH_ROOT, base_url=_BASE_URL
        )
        assert len(parse_versions_feed(versions_xml)) == len(page.versions)

        attachments_xml = fake_atom.artifacts_feed(
            wiki, page, category="attachment", auth_root=_AUTH_ROOT, base_url=_BASE_URL
        )
        assert len(parse_attachments_feed(attachments_xml)) == len(page.attachments)

    # -- nav feed: parent uuids and sibling ordinals (served order,
    # i.e. FakeWiki.children_of's stable sort by explicit ordinal --
    # the same order the fake itself puts into each node's `children`
    # array) survive.
    nav_data = fake_navjson.nav_feed(wiki, tree=True, timestamp=wikiset.base_timestamp_ms)
    nav_tree = parse_nav_feed(json.dumps(nav_data).encode("utf-8"))
    # real 8.0 has no synthetic root; the tree root is the feed identifier
    assert nav_tree.root_id == nav_data["identifier"] == wiki.uuid

    nodes_by_id = {n.id: n for n in nav_tree.nodes}
    assert set(nodes_by_id) == {p.uuid for p in wiki.pages}

    for page in wiki.pages:
        node = nodes_by_id[page.uuid]
        assert node.parent == page.parent_uuid
        expected_siblings = wiki.children_of(page.parent_uuid)
        expected_ordinal = [p.uuid for p in expected_siblings].index(page.uuid)
        assert node.ordinal == expected_ordinal


def test_roundtrip_basic_seed():
    seed = SynthSeed(
        seed=100,
        wiki_count=2,
        depth=3,
        pages_per_level=3,
        comments_per_page=2,
        versions_per_page=2,
        attachments_per_page=1,
        tags_per_page=1,
    )
    wikiset = synthesize(seed)
    for wiki in wikiset.wikis:
        _assert_wiki_roundtrips(wikiset, wiki)


def test_roundtrip_nasty_case_seed():
    """the design / the design: deep nesting, high comment count,
    unicode/RTL titles, an empty page, and duplicate sibling ordinals,
    all in one dataset, must all survive the round trip."""
    seed = SynthSeed(
        seed=999,
        wiki_count=1,
        depth=2,
        pages_per_level=3,
        comments_per_page=1,
        versions_per_page=2,
        attachments_per_page=1,
        tags_per_page=1,
        deep_nesting=True,
        deep_nesting_depth=14,
        high_comment_count=True,
        high_comment_count_n=250,
        unicode_rtl=True,
        empty_pages=True,
        duplicate_ordinals=True,
    )
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]

    # Sanity: the nasty-case toggles actually produced what they claim
    # to, so this test would fail loudly if synth.py's behavior ever
    # drifted out from under it.
    assert any(len(p.comments) >= 250 for p in wiki.pages)
    assert any(p.body_html == "" for p in wiki.pages)
    top_level_ordinals = [p.ordinal for p in wiki.top_level_pages()]
    assert len(top_level_ordinals) != len(set(top_level_ordinals))
    max_chain = 0
    for page in wiki.pages:
        if wiki.children_of(page.uuid):
            continue
        depth = 1
        cur = page
        while cur.parent_uuid is not None:
            cur = wiki.page_by_uuid(cur.parent_uuid)
            depth += 1
        max_chain = max(max_chain, depth)
    assert max_chain >= 14

    _assert_wiki_roundtrips(wikiset, wiki)
