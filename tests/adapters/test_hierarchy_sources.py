"""Spec requirement "Both hierarchy sources are exposed, not
reconciled": the page entry's `td:parentUuid` and the nav feed's
`parent` are both readable independently, and the adapter itself never
compares them -- that cross-check is explicitly deferred to a later
layer."""

import json

from connections_export.adapters.wikis import parse_nav_feed, parse_page_entry
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver import navjson as fake_navjson
from connections_export.fakeserver.synth import SynthSeed, synthesize

_AUTH_ROOT = "basic"
_BASE_URL = "https://fake"


def test_parent_available_from_page_entry_and_nav_feed_independently():
    seed = SynthSeed(seed=55, wiki_count=1, depth=2, pages_per_level=2)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    child_page = next(p for p in wiki.pages if p.parent_uuid is not None)

    entry_xml = fake_atom.page_entry(wiki, child_page, auth_root=_AUTH_ROOT, base_url=_BASE_URL)
    page = parse_page_entry(entry_xml)

    nav_data = fake_navjson.nav_feed(wiki, tree=True, timestamp=wikiset.base_timestamp_ms)
    nav_tree = parse_nav_feed(json.dumps(nav_data).encode("utf-8"))
    node = next(n for n in nav_tree.nodes if n.id == child_page.uuid)

    # Each source, independently, yields the same page's parent -- but
    # nothing in parse_page_entry / parse_nav_feed reads or depends on
    # the other's output to produce this.
    assert page.parent_uuid == child_page.parent_uuid
    assert node.parent == child_page.parent_uuid


def test_adapter_does_not_raise_when_sources_would_differ():
    """The adapter has no cross-check at all -- parsing a page entry
    whose td:parentUuid disagrees with an (independently constructed)
    nav node's parent must not raise. Simulated here by hand-editing a
    fake-served page entry's parentUuid after the fact, since the fake
    itself always keeps the two sources consistent by construction."""
    seed = SynthSeed(seed=56, wiki_count=1, depth=2, pages_per_level=2)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    child_page = next(p for p in wiki.pages if p.parent_uuid is not None)

    entry_xml = fake_atom.page_entry(wiki, child_page, auth_root=_AUTH_ROOT, base_url=_BASE_URL)
    tampered = entry_xml.replace(
        f"<td:parentUuid>{child_page.parent_uuid}</td:parentUuid>".encode(),
        b"<td:parentUuid>some-other-uuid-entirely</td:parentUuid>",
    )
    assert tampered != entry_xml  # the tamper actually took effect

    page = parse_page_entry(tampered)  # must not raise
    assert page.parent_uuid == "some-other-uuid-entirely"
