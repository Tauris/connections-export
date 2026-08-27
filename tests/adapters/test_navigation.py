"""Tasks 5.1-5.2: navigation-entry and nav-feed parsing, anchored first
against the verbatim reference samples (not the fake server).

`tests/adapters/fixtures/navigation_entry_verbatim.xml` is copied
byte-for-byte from the "Navigation entry —, 8.0 sample"
block in the published API reference.

`tests/adapters/fixtures/nav_feed_verbatim.json` is copied from the
"Navigation feed —, returns JSON" block in the same doc,
with one necessary normalization: the doc elides `"breadcrumbs": [... ]`
with a literal, unquoted ellipsis, which is not valid JSON. It was
replaced with `"breadcrumbs": []` -- no breadcrumb content was ever
shown to copy. Every other key, string value (including the
intentionally-truncated uuid strings like "3d9210be-...", which the
parser treats as opaque ids, never validated as well-formed uuids),
and structural nesting is verbatim.
"""

import json
from pathlib import Path

import pytest

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.wikis import parse_nav_feed, parse_navigation_entry

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_verbatim_navigation_entry():
    data = (FIXTURES / "navigation_entry_verbatim.xml").read_bytes()
    node = parse_navigation_entry(data)

    assert node.id == "06258acb-84a2-427b-85e5-9838ad536ff1"
    assert node.label == "page2"
    assert node.parent == "dabe2234-bc56-4be3-bc9b-44943a766158"


def test_verbatim_navigation_entry_has_only_rel_self():
    data = (FIXTURES / "navigation_entry_verbatim.xml").read_bytes()
    root = atom.parse_xml(data, expected_root="entry")
    links = atom.links_by_rel(root)
    assert set(links) == {"self"}


def test_parses_verbatim_nav_feed_synthetic_root():
    data = (FIXTURES / "nav_feed_verbatim.json").read_bytes()
    tree = parse_nav_feed(data)

    assert tree.root_id == "tree"
    # The synthetic root is recorded, not emitted as a page node.
    assert all(node.id != "tree" for node in tree.nodes)
    assert {node.id for node in tree.nodes} == {"5ab5beda-...", "3d9210be-..."}


def test_verbatim_nav_feed_sibling_order_and_parent():
    data = (FIXTURES / "nav_feed_verbatim.json").read_bytes()
    tree = parse_nav_feed(data)
    raw = json.loads(data)

    page3 = next(n for n in tree.nodes if n.id == "5ab5beda-...")
    page2 = next(n for n in tree.nodes if n.id == "3d9210be-...")

    assert page3.parent == "3d9210be-..."
    assert page2.parent == "dc1b52bb-..."

    # page2's `children` array in the raw feed has one `_reference`,
    # to page3 -- that's the sibling-order signal; page3's ordinal
    # (position 0 in page2's children list) reflects it.
    assert page3.ordinal == 0
    assert page2.child_ids == ["5ab5beda-..."]

    # Sanity check the fixture's own `childSize` field against what we
    # extracted as `children`, for the two raw items that carry both.
    raw_items = {item["id"]: item for item in raw["items"]}
    assert raw_items["3d9210be-..."]["childSize"] == len(page2.child_ids)
    assert raw_items["tree"]["childSize"] == 1  # the synthetic root's own childSize


def test_nav_feed_malformed_json_raises_adapter_error():
    with pytest.raises(AdapterError):
        parse_nav_feed(b"{not valid json")


# --- Real HCL 8.0 nav shape ---------
# The live feed carries the parent pointer as `parElem` (not `parent`), has
# no synthetic `root:"true"` item, and returns a flat `items[]` with empty
# `children` even under `tree=true`. Top-level pages' `parElem` points at the
# tree root (the feed's `identifier`).


def test_parses_real_80_nav_feed_parelem_hierarchy_and_order():
    from connections_export.crawler.report import detect_orphans

    data = (FIXTURES / "nav_feed_real_80.json").read_bytes()
    tree = parse_nav_feed(data)

    by_id = {n.id: n for n in tree.nodes}
    assert set(by_id) == {"page-a", "page-a1", "page-b", "page-orphan"}

    # top-level pages: parElem == the feed identifier -> parent None
    assert by_id["page-a"].parent is None and by_id["page-a"].is_root
    assert by_id["page-b"].parent is None and by_id["page-b"].is_root
    # a real child keeps its parElem parent
    assert by_id["page-a1"].parent == "page-a"

    # ordinals are per-parent, in items-array order (children come back empty):
    # page-a is top-level #0, page-b top-level #1, page-a1 is page-a's child #0.
    assert by_id["page-a"].ordinal == 0
    assert by_id["page-b"].ordinal == 1
    assert by_id["page-a1"].ordinal == 0

    # a parElem pointing at a missing page is still an orphan (not silently
    # promoted to top-level)
    assert by_id["page-orphan"].parent == "page-missing"
    assert detect_orphans(tree.nodes) == ["page-orphan"]
