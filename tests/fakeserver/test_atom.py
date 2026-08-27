"""Atom serializers match the reference shapes.

Namespaces, page-entry fields, navigation-entry fields, artifacts-feed
category switching, and the truncation signature are all transcribed
from the published API reference.
"""

from xml.etree import ElementTree as ET

from connections_export.fakeserver import atom
from connections_export.fakeserver.synth import SynthSeed, synthesize

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "td": "urn:ibm.com/td",
    "snx": "http://www.ibm.com/xmlns/prod/sn",
    "thr": "http://purl.org/syndication/thread/1.0",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

BASE = "https://fake"
AUTH = "basic"


def _wiki_and_page(seed_kwargs=None):
    seed = SynthSeed(seed=1, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=2)
    for k, v in (seed_kwargs or {}).items():
        setattr(seed, k, v)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    return wikiset, wiki, page


def test_page_entry_is_well_formed_and_namespaced():
    _, wiki, page = _wiki_and_page()
    xml_bytes = atom.page_entry(wiki, page, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)

    assert root.tag == "{http://www.w3.org/2005/Atom}entry"
    # Namespace declarations must be present (ElementTree exposes them
    # via tag prefixes it can resolve; we assert by finding elements
    # under each namespace).
    assert root.find("td:label", NS) is not None
    assert root.find("td:versionLabel", NS) is not None
    assert root.find("td:parentUuid", NS) is not None
    assert root.find("atom:content", NS) is not None
    assert root.find("atom:title", NS) is not None
    assert root.find("atom:summary", NS) is not None


def test_page_entry_has_content_src_pointing_at_media():
    _, wiki, page = _wiki_and_page()
    xml_bytes = atom.page_entry(wiki, page, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    content = root.find("atom:content", NS)
    assert content.get("type") == "text/html"
    assert content.get("src").endswith(
        f"/wikis/{AUTH}/api/wiki/{wiki.label}/page/{page.label}/media"
    )


def test_page_entry_carries_label_versionlabel_and_parent_uuid():
    _, wiki, page = _wiki_and_page()
    child = wiki.children_of(page.uuid)[0]
    xml_bytes = atom.page_entry(wiki, child, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    assert root.find("td:label", NS).text == child.label
    assert root.find("td:versionLabel", NS).text == str(child.version_label)
    assert root.find("td:parentUuid", NS).text == child.parent_uuid


def test_page_entry_links_and_rank_and_category():
    _, wiki, page = _wiki_and_page()
    xml_bytes = atom.page_entry(wiki, page, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)

    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert {"self", "edit", "edit-media", "enclosure", "replies"} <= rels

    replies = next(link for link in root.findall("atom:link", NS) if link.get("rel") == "replies")
    assert replies.get("{http://purl.org/syndication/thread/1.0}count") == str(len(page.comments))

    rank_schemes = {rank.get("scheme") for rank in root.findall("snx:rank", NS)}
    for suffix in ("recommendations", "comment", "hit", "anonymous_hit", "attachments", "versions"):
        assert any(s.endswith(suffix) for s in rank_schemes), suffix

    category = root.find("atom:category", NS)
    assert category.get("scheme") == "tag:ibm.com,2006:td/type"
    assert category.get("term") == "page"


def test_page_entry_never_expresses_parentage_as_rel_parent_link():
    _, wiki, page = _wiki_and_page()
    child = wiki.children_of(page.uuid)[0]
    xml_bytes = atom.page_entry(wiki, child, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "parent" not in rels
    assert "up" not in rels


def test_navigation_entry_shape():
    _, wiki, page = _wiki_and_page()
    child = wiki.children_of(page.uuid)[0]
    xml_bytes = atom.navigation_entry(wiki, child, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)

    assert root.tag == "{http://www.w3.org/2005/Atom}entry"
    assert root.find("atom:id", NS).text == f"urn:lsid:ibm.com:td:{child.uuid}"
    cat = root.find("atom:category", NS)
    assert cat.get("term") == "navigation"
    assert cat.get("scheme") == "tag:ibm.com,2006:td/type"
    assert root.find("td:uuid", NS).text == child.uuid
    assert root.find("td:label", NS).text == child.label
    assert root.find("td:parentUuid", NS).text == child.parent_uuid
    assert root.find("td:documentUuid", NS).text == child.uuid

    links = root.findall("atom:link", NS)
    rels = {link.get("rel") for link in links}
    assert rels == {"self"}


def test_wikis_feed_is_well_formed():
    wikiset, _, _ = _wiki_and_page()
    xml_bytes = atom.wikis_feed(wikiset, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"
    entries = root.findall("atom:entry", NS)
    assert len(entries) == len(wikiset.wikis)


def test_artifacts_feed_default_category_is_comments():
    _, wiki, page = _wiki_and_page()
    xml_bytes = atom.artifacts_feed(wiki, page, category=None, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    entries = root.findall("atom:entry", NS)
    assert len(entries) == len(page.comments)
    ids = {e.find("atom:id", NS).text for e in entries}
    assert ids == {f"urn:lsid:ibm.com:td:{c.uuid}" for c in page.comments}


def test_artifacts_feed_versions_category():
    _, wiki, page = _wiki_and_page()
    xml_bytes = atom.artifacts_feed(wiki, page, category="version", auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    entries = root.findall("atom:entry", NS)
    assert len(entries) == len(page.versions)
    for entry in entries:
        assert entry.find("td:versionLabel", NS) is not None


def test_artifacts_feed_attachments_and_tags_category():
    _, wiki, page = _wiki_and_page({"attachments_per_page": 2, "tags_per_page": 3})
    page = wiki.top_level_pages()[0]

    xml_bytes = atom.artifacts_feed(
        wiki, page, category="attachment", auth_root=AUTH, base_url=BASE
    )
    root = ET.fromstring(xml_bytes)
    assert len(root.findall("atom:entry", NS)) == len(page.attachments)

    # tags are feed-level, scheme-less
    # <category term=.. label=..> on the <feed> -- not one <entry> per tag.
    xml_bytes = atom.artifacts_feed(wiki, page, category="tag", auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    assert root.findall("atom:entry", NS) == []
    tag_cats = root.findall("atom:category", NS)
    assert [c.get("term") for c in tag_cats] == list(page.tags)
    assert all(c.get("scheme") is None for c in tag_cats)


def test_feed_carries_opensearch_total_results():
    wikiset, _, _ = _wiki_and_page()
    xml_bytes = atom.wikis_feed(wikiset, auth_root=AUTH, base_url=BASE)
    root = ET.fromstring(xml_bytes)
    total = root.find("opensearch:totalResults", NS)
    assert total is not None
    assert total.text == str(len(wikiset.wikis))


def test_pagination_ps_slices_and_next_link_present_when_more_remain():
    seed = SynthSeed(seed=11, wiki_count=1, depth=1, pages_per_level=5)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]

    xml_bytes = atom.wiki_pages_feed(wiki, auth_root=AUTH, base_url=BASE, ps=2, page=1)
    root = ET.fromstring(xml_bytes)
    entries = root.findall("atom:entry", NS)
    assert len(entries) == 2
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" in rels


def test_truncation_signature_full_page_with_no_next_link():
    # 4 pages total, ps=4 exactly exhausts them on page 1: exactly `ps`
    # items, and correctly no next link -- the truncation signature
    # (Producible truncation signature).
    seed = SynthSeed(seed=12, wiki_count=1, depth=1, pages_per_level=4)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]

    xml_bytes = atom.wiki_pages_feed(wiki, auth_root=AUTH, base_url=BASE, ps=4, page=1)
    root = ET.fromstring(xml_bytes)
    entries = root.findall("atom:entry", NS)
    assert len(entries) == 4
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" not in rels
