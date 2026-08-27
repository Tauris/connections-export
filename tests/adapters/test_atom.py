"""Namespace-aware Atom-core helpers over a synthetic entry
built with the documented element/attribute names -- not tied to any
particular Wikis shape, since atom.py is the shared core reused later
by Blogs/Forums."""

import pytest

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError

_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:td="urn:ibm.com/td"
       xmlns:snx="http://www.ibm.com/xmlns/prod/sn"
       xmlns:thr="http://purl.org/syndication/thread/1.0">
  <id>urn:lsid:ibm.com:td:aaaaaaaa-1111-2222-3333-444444444444</id>
  <title type="text">Sample</title>
  <td:label>sample</td:label>
  <category term="page" scheme="tag:ibm.com,2006:td/type"/>
  <link rel="self" href="https://example.com/entry"/>
  <link rel="replies" href="https://example.com/feed" thr:count="7"/>
  <snx:rank scheme="http://www.ibm.com/xmlns/prod/sn/comment">7</snx:rank>
  <snx:rank scheme="http://www.ibm.com/xmlns/prod/sn/versions">2</snx:rank>
</entry>
"""

_FEED_WITH_TWO_ENTRIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:feed</id>
  <entry><id>1</id></entry>
  <entry><id>2</id></entry>
</feed>
"""

_FEED_WITH_NEXT_LINK = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:feed</id>
  <entry><id>1</id></entry>
  <link rel="next" href="https://example.com/feed?ps=1&amp;page=2"/>
</feed>
"""

_FEED_WITH_TITLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:feed</id>
  <title type="text">Team Updates</title>
  <entry><id>1</id></entry>
</feed>
"""


def test_parse_xml_returns_root_element():
    root = atom.parse_xml(_ENTRY)
    assert root.tag.endswith("}entry")


def test_parse_xml_checks_expected_root():
    atom.parse_xml(_ENTRY, expected_root="entry")  # does not raise
    with pytest.raises(AdapterError):
        atom.parse_xml(_ENTRY, expected_root="feed")


def test_parse_xml_wraps_malformed_xml_in_adapter_error():
    with pytest.raises(AdapterError):
        atom.parse_xml(b"<feed><entry><unterminated-element")


def test_find_text_reads_namespaced_elements():
    root = atom.parse_xml(_ENTRY)
    assert atom.find_text(root, "atom:title") == "Sample"
    assert atom.find_text(root, "td:label") == "sample"
    assert atom.find_text(root, "td:missing") is None


def test_links_by_rel():
    root = atom.parse_xml(_ENTRY)
    links = atom.links_by_rel(root)
    assert set(links) == {"self", "replies"}
    assert links["self"].get("href") == "https://example.com/entry"


def test_categories_returns_term_scheme_pairs():
    root = atom.parse_xml(_ENTRY)
    cats = atom.categories(root)
    assert cats == [("page", "tag:ibm.com,2006:td/type")]


def test_category_term_accepts_either_documented_scheme():
    root = atom.parse_xml(_ENTRY)
    assert atom.category_term(root) == "page"

    alt = _ENTRY.replace(
        b'scheme="tag:ibm.com,2006:td/type"',
        b'scheme="http://www.ibm.com/xmlns/prod/sn/type"',
    )
    alt_root = atom.parse_xml(alt)
    assert atom.category_term(alt_root) == "page"


def test_ranks_keyed_by_scheme_suffix():
    root = atom.parse_xml(_ENTRY)
    assert atom.ranks(root) == {"comment": 7, "versions": 2}


def test_thr_count_reads_replies_link_attribute():
    root = atom.parse_xml(_ENTRY)
    assert atom.thr_count(root) == 7


def test_thr_count_is_none_when_no_replies_link():
    root = atom.parse_xml(b"<entry xmlns='http://www.w3.org/2005/Atom'><id>x</id></entry>")
    assert atom.thr_count(root) is None


def test_entry_uuid_strips_td_prefix():
    assert (
        atom.entry_uuid("urn:lsid:ibm.com:td:aaaaaaaa-1111-2222-3333-444444444444")
        == "aaaaaaaa-1111-2222-3333-444444444444"
    )


def test_entry_uuid_strips_forum_prefix():
    assert atom.entry_uuid("urn:lsid:ibm.com:forum:bbbb-cccc") == "bbbb-cccc"


def test_entry_uuid_passes_through_unrecognized_form():
    assert atom.entry_uuid("just-a-bare-uuid") == "just-a-bare-uuid"


def test_entry_uuid_none_for_none_input():
    assert atom.entry_uuid(None) is None


def test_entry_uuid_strips_blogs_entry_infix():
    """Blogs ids are `urn:lsid:ibm.com:blogs:entry-<uuid>` -- two
    segments (`blogs:` + `entry-`), unlike Wikis'/Forums' single
    `<infix>:` segment. Note the bare uuid below (`aaaaaaaa-1111-...`)
    starts with an all-letters segment, same as the td: regression
    fixture -- proving the fix doesn't just get lucky on the first
    segment being non-alphabetic."""
    assert (
        atom.entry_uuid("urn:lsid:ibm.com:blogs:entry-aaaaaaaa-1111-2222-3333-444444444444")
        == "aaaaaaaa-1111-2222-3333-444444444444"
    )


def test_entry_uuid_td_prefix_regression_after_blogs_generalization():
    """The blogs `entry-` handling must not make
    `td:` (whose uuids can *also* start with an all-letters hex segment)
    lose part of the uuid."""
    assert (
        atom.entry_uuid("urn:lsid:ibm.com:td:aaaaaaaa-1111-2222-3333-444444444444")
        == "aaaaaaaa-1111-2222-3333-444444444444"
    )


# --- 1.2: app namespace, openSearch by URI, three-attr thr:in-reply-to ---


def test_app_namespace_present():
    """`app` = http://www.w3.org/2007/app added to NS,
    needed to read `<app:control><snx:comments.../></app:control>`."""
    assert atom.NS["app"] == "http://www.w3.org/2007/app"


_OPENSEARCH_CAPITAL_S_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/">
  <id>urn:feed</id>
  <openSearch:totalResults>3</openSearch:totalResults>
</feed>
"""


def test_opensearch_matched_by_uri_not_prefix_case():
    """Blogs feeds use the `openSearch` prefix (capital S) for
    the same http://a9.com/-/spec/opensearch/1.1/ URI Wikis/the fake
    server address as `opensearch` -- our NS dict's own alias is
    lowercase, so this only passes if lxml matches by URI, not by the
    document's chosen prefix spelling (Shared-core
    extension)."""
    root = atom.parse_xml(_OPENSEARCH_CAPITAL_S_FEED, expected_root="feed")
    assert atom.find_text(root, "opensearch:totalResults") == "3"


_ENTRY_WITH_IN_REPLY_TO = b"""<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:thr="http://purl.org/syndication/thread/1.0">
  <id>urn:lsid:ibm.com:forum:reply-uuid</id>
  <thr:in-reply-to ref="urn:lsid:ibm.com:forum:parent-uuid"
                    source="urn:lsid:ibm.com:forum:topic-uuid"
                    href="https://example.com/forums/atom/topic?topicUuid=topic-uuid"/>
</entry>
"""


def test_in_reply_to_reads_all_three_attributes():
    """The three `thr:in-reply-to` attributes -- `ref`
    (immediate parent), `source` (top-level topic), `href` -- are what
    Forums needs to reconstruct an arbitrary-depth tree from a flat
    feed."""
    entry = atom.parse_xml(_ENTRY_WITH_IN_REPLY_TO, expected_root="entry")
    assert atom.in_reply_to(entry) == {
        "ref": "urn:lsid:ibm.com:forum:parent-uuid",
        "source": "urn:lsid:ibm.com:forum:topic-uuid",
        "href": "https://example.com/forums/atom/topic?topicUuid=topic-uuid",
    }


def test_in_reply_to_is_none_when_absent():
    root = atom.parse_xml(b"<entry xmlns='http://www.w3.org/2005/Atom'><id>x</id></entry>")
    assert atom.in_reply_to(root) is None


def test_bare_category_terms_excludes_scheme_categories():
    """A bare `<category term="..."/>` with no `scheme` is a user tag,
    both for Blogs (`<category term="tag"/>`) and Forums ("a bare
    category term with no scheme is a user tag") -- distinct from a
    type/flags category, which always carries a `scheme`."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom">
      <id>x</id>
      <category term="roadmap"/>
      <category term="forum-topic" scheme="http://www.ibm.com/xmlns/prod/sn/type"/>
      <category term="pinned" scheme="http://www.ibm.com/xmlns/prod/sn/flags"/>
      <category term="urgent"/>
    </entry>
    """
    entry = atom.parse_xml(xml, expected_root="entry")
    assert atom.bare_category_terms(entry) == ["roadmap", "urgent"]


def test_author_name_reads_nested_name_element():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom">
      <id>x</id>
      <author><name>Jane Doe</name></author>
    </entry>
    """
    entry = atom.parse_xml(xml, expected_root="entry")
    assert atom.author_name(entry) == "Jane Doe"


def test_author_name_none_when_no_author_element():
    root = atom.parse_xml(b"<entry xmlns='http://www.w3.org/2005/Atom'><id>x</id></entry>")
    assert atom.author_name(root) is None


def test_entries_lists_feed_children():
    root = atom.parse_xml(_FEED_WITH_TWO_ENTRIES, expected_root="feed")
    found = atom.entries(root)
    assert len(found) == 2


# --- 3.1 feed_next_link ---


def test_feed_next_link_present():
    assert atom.feed_next_link(_FEED_WITH_NEXT_LINK) == "https://example.com/feed?ps=1&page=2"


def test_feed_next_link_absent():
    assert atom.feed_next_link(_FEED_WITH_TWO_ENTRIES) is None


# --- Finding #26 feed_title ---


def test_feed_title_reads_the_top_level_title():
    assert atom.feed_title(_FEED_WITH_TITLE) == "Team Updates"


def test_feed_title_is_none_when_absent():
    assert atom.feed_title(_FEED_WITH_TWO_ENTRIES) is None


def test_link_href_survives_an_element_with_no_children():
    """An lxml element with no children is FALSEY, so `link or {}` silently
    discarded every link that had no sub-element. That bug was found once and
    fixed in four copies of this helper by hand."""
    root = atom.parse_xml(
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b'<link rel="replies" href="http://example.invalid/c"/>'
        b"</entry></feed>",
        expected_root="feed",
    )
    entry = atom.entries(root)[0]

    assert atom.link_href(entry, "replies") == "http://example.invalid/c"
    assert atom.link_href(entry, "absent") is None


def test_require_entry_id_names_the_feed_that_was_being_read():
    """The only thing the two former copies differed in."""
    root = atom.parse_xml(
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>',
        expected_root="feed",
    )
    entry = atom.entries(root)[0]

    with pytest.raises(AdapterError, match="blogs entry missing id"):
        atom.require_entry_id(entry, what="blogs entry")


def test_no_adapter_keeps_a_private_copy_of_a_shared_helper():
    """The deletion, asserted. Four `_link_href`s, two `_content_html`s, two
    `_require_id`s and one stray `_author_userid` -- and blogs.py read the
    author userid two different ways within one file."""
    import re
    from pathlib import Path

    from connections_export import adapters

    banned = ("_link_href", "_content_html", "_require_id", "_author_userid")
    root = Path(adapters.__file__).parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for name in banned:
            if re.search(rf"^def {name}\b", source, re.M):
                offenders.append(f"{path.name}:{name}")

    assert not offenders, f"private copies of shared helpers came back: {offenders}"
