"""The nav feed is JSON (not Atom). In the real 8.0 shape the parent
pointer is `parElem`, there is no
synthetic `tree` root, and `tree=true` returns a flat `items[]` with empty
`children` (sibling order = items-array order). `parent=<id>` still expands
a subtree; the bare feed returns top-level stubs.
"""

from connections_export.adapters.wikis import parse_nav_feed
from connections_export.fakeserver import navjson
from connections_export.fakeserver.synth import SynthSeed, synthesize


def _wiki():
    seed = SynthSeed(seed=21, wiki_count=1, depth=3, pages_per_level=2)
    wikiset = synthesize(seed)
    return wikiset, wikiset.wikis[0]


def test_tree_true_is_flat_parelem_items_with_no_synthetic_root():
    wikiset, wiki = _wiki()
    doc = navjson.nav_feed(wiki, tree=True, parent=None, timestamp=wikiset.base_timestamp_ms)

    assert doc["identifier"] == wiki.uuid
    assert doc["label"] == wiki.label
    assert doc["fullTree"] is True
    assert doc["timestamp"] == wikiset.base_timestamp_ms

    # no synthetic {"id": "tree", "root": "true"} item
    assert all(item.get("id") != "tree" for item in doc["items"])
    assert all(str(item.get("root")) != "true" for item in doc["items"])

    by_id = {item["id"]: item for item in doc["items"]}
    assert set(by_id) == {p.uuid for p in wiki.pages}
    for page in wiki.pages:
        item = by_id[page.uuid]
        assert item["parElem"] == (page.parent_uuid or wiki.uuid)
        assert item["children"] == []  # flat, even under tree=true
        assert item["childSize"] == len(wiki.children_of(page.uuid))
        assert item["type"] == "item"
        assert item["label"] == page.label
        assert item["title"] == page.title


def test_tree_true_round_trips_through_the_real_parser_to_the_right_hierarchy():
    wikiset, wiki = _wiki()
    doc = navjson.nav_feed(wiki, tree=True, parent=None, timestamp=wikiset.base_timestamp_ms)

    import json

    tree = parse_nav_feed(json.dumps(doc).encode())
    by_id = {n.id: n for n in tree.nodes}

    for page in wiki.pages:
        node = by_id[page.uuid]
        if page.parent_uuid is None:
            assert node.parent is None and node.is_root  # top-level, not an orphan
        else:
            assert node.parent == page.parent_uuid
    # no orphans in a well-formed tree
    from connections_export.crawler.report import detect_orphans

    assert detect_orphans(tree.nodes) == []


def test_default_feed_top_level_items_are_stubs():
    wikiset, wiki = _wiki()
    doc = navjson.nav_feed(wiki, tree=False, parent=None, timestamp=wikiset.base_timestamp_ms)

    assert all(item.get("id") != "tree" for item in doc["items"])
    top_ids = {p.uuid for p in wiki.top_level_pages()}
    assert {item["id"] for item in doc["items"]} == top_ids
    for item in doc["items"]:
        assert item["type"] == "stub"
        assert item["children"] == []
        assert item["parElem"] == wiki.uuid  # top-level -> the tree root


def test_parent_param_scopes_to_subtree():
    wikiset, wiki = _wiki()
    top_page = wiki.top_level_pages()[0]

    doc = navjson.nav_feed(
        wiki, tree=False, parent=top_page.uuid, timestamp=wikiset.base_timestamp_ms
    )

    expected_children = wiki.children_of(top_page.uuid)
    assert {item["id"] for item in doc["items"]} == {c.uuid for c in expected_children}
    for item in doc["items"]:
        assert item["parElem"] == top_page.uuid
