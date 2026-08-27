"""Tasks 6.1-6.2: wikis feed and artifacts-by-category feeds. These
drive the fake server's serializers directly (no ASGI/HTTP needed --
`connections_export.fakeserver.atom` produces the same bytes the app would
serve) to get well-formed sample bytes; the independent verbatim
anchor lives in test_page_entry.py / test_navigation.py, not here.

Comment/version/attachment/tag entry parsing is `# INFERRED`:
no schema for these entry types is published anywhere in the
reference. See connections_export/adapters/wikis.py for where that's tagged in
code.
"""

from connections_export.adapters.wikis import (
    parse_attachments_feed,
    parse_comments_feed,
    parse_tags_feed,
    parse_versions_feed,
    parse_wikis_feed,
)
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver.synth import SynthSeed, synthesize

_AUTH_ROOT = "basic"
_BASE_URL = "https://fake"


def _wikiset():
    seed = SynthSeed(
        seed=11,
        wiki_count=2,
        depth=1,
        pages_per_level=1,
        comments_per_page=3,
        versions_per_page=2,
        attachments_per_page=2,
        tags_per_page=2,
    )
    return synthesize(seed)


def test_parse_wikis_feed_extracts_refs():
    wikiset = _wikiset()
    xml = fake_atom.wikis_feed(wikiset, auth_root=_AUTH_ROOT, base_url=_BASE_URL)
    refs = parse_wikis_feed(xml)

    assert len(refs) == len(wikiset.wikis)
    by_uuid = {r.uuid: r for r in refs}
    for wiki in wikiset.wikis:
        ref = by_uuid[wiki.uuid]
        assert ref.label == wiki.label
        assert ref.title == wiki.title
        assert ref.self_url is not None


def test_parse_comments_feed():
    wikiset = _wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]
    xml = fake_atom.artifacts_feed(
        wiki, page, category="comment", auth_root=_AUTH_ROOT, base_url=_BASE_URL
    )
    comments = parse_comments_feed(xml)

    assert len(comments) == len(page.comments)
    parsed_ids = {c.id for c in comments}
    assert parsed_ids == {c.uuid for c in page.comments}
    first = next(c for c in comments if c.id == page.comments[0].uuid)
    assert first.author == page.comments[0].author
    assert first.content_html == page.comments[0].content_html


def test_parse_versions_feed():
    wikiset = _wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]
    xml = fake_atom.artifacts_feed(
        wiki, page, category="version", auth_root=_AUTH_ROOT, base_url=_BASE_URL
    )
    versions = parse_versions_feed(xml)

    assert len(versions) == len(page.versions)
    labels = sorted(v.version_label for v in versions)
    assert labels == sorted(v.version_label for v in page.versions)


def test_parse_attachments_feed():
    wikiset = _wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]
    xml = fake_atom.artifacts_feed(
        wiki, page, category="attachment", auth_root=_AUTH_ROOT, base_url=_BASE_URL
    )
    attachments = parse_attachments_feed(xml)

    assert len(attachments) == len(page.attachments)
    filenames = {a.filename for a in attachments}
    assert filenames == {a.filename for a in page.attachments}
    for attachment in attachments:
        assert attachment.enclosure_href is not None


def test_parse_tags_feed():
    wikiset = _wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]
    xml = fake_atom.artifacts_feed(
        wiki, page, category="tag", auth_root=_AUTH_ROOT, base_url=_BASE_URL
    )
    tags = parse_tags_feed(xml)

    assert {t.term for t in tags} == set(page.tags)


def test_parse_tags_feed_reads_feed_level_categories_verbatim():
    """real wiki tags are feed-level,
    scheme-less <category term=.. label=..> on the <feed>, with no per-tag
    <entry>. A category that DOES carry a scheme (a type marker) is not a tag."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>A page - tag</title>
      <category term="onboarding" label="onboarding"/>
      <category term="engineering" label="engineering"/>
      <category term="page" scheme="tag:ibm.com,2006:td/type"/>
    </feed>"""
    tags = parse_tags_feed(xml)
    assert [t.term for t in tags] == ["onboarding", "engineering"]  # the scheme'd one dropped


def test_comments_feed_defaults_when_category_omitted():
    """Reference: "If this parameter is not provided then all page
    comments are returned" -- comments are the default category."""
    wikiset = _wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]
    xml = fake_atom.artifacts_feed(
        wiki, page, category=None, auth_root=_AUTH_ROOT, base_url=_BASE_URL
    )
    comments = parse_comments_feed(xml)
    assert len(comments) == len(page.comments)
