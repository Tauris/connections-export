"""Tasks 4.1-4.3: `parse_page_entry`, anchored first against the
verbatim reference field list (not the fake server -- see
tests/adapters/fixtures/page_entry_reference_fields.xml for exactly
what was copied from the published API reference and what was
added on the design/the authority)."""

from pathlib import Path

from connections_export.adapters.wikis import parse_page_entry

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_verbatim_reference_page_entry_fields():
    data = (FIXTURES / "page_entry_reference_fields.xml").read_bytes()
    page = parse_page_entry(data)

    assert page.uuid == "1f9c7f1e-2222-4dab-9a6b-c9a8e7c8d001"
    assert page.label == "referenceSamplePage"
    assert page.title == "Reference Sample Page"
    assert page.version_label == 3
    assert page.parent_uuid == "7c6a4d2e-1111-4c8a-9e33-1a2b3c4d5e6f"
    assert (
        page.content_src
        == "https://example.com/wikis/basic/api/wiki/wiki0321/page/referenceSamplePage/media"
    )
    assert (
        page.replies_url
        == "https://example.com/wikis/basic/api/wiki/wiki0321/page/referenceSamplePage/feed"
    )
    assert page.replies_count == 4
    assert page.visibility == "public"
    assert page.created == "2018-10-09T12:00:00Z"
    assert page.modified == "2018-10-12T08:30:00Z"
    assert page.author == "Reference Author"
    assert page.category_term == "page"

    # All six documented snx:rank counters, keyed by scheme suffix.
    assert page.ranks == {
        "recommendations": 2,
        "comment": 4,
        "hit": 57,
        "anonymous_hit": 12,
        "attachments": 1,
        "versions": 3,
    }


def test_empty_parent_uuid_element_is_none():
    data = (FIXTURES / "page_entry_scheme_sn_type.xml").read_bytes()
    page = parse_page_entry(data)
    assert page.parent_uuid is None


def test_absent_parent_uuid_element_is_none():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">
      <id>urn:lsid:ibm.com:td:cccccccc-0000-1111-2222-333333333333</id>
      <title type="text">No Parent Element</title>
      <td:label>noParentElement</td:label>
    </entry>
    """
    page = parse_page_entry(xml)
    assert page.parent_uuid is None


def test_category_leniency_tag_scheme():
    data = (FIXTURES / "page_entry_reference_fields.xml").read_bytes()
    page = parse_page_entry(data)
    assert page.category_term == "page"


def test_category_leniency_sn_type_scheme():
    data = (FIXTURES / "page_entry_scheme_sn_type.xml").read_bytes()
    page = parse_page_entry(data)
    assert page.category_term == "page"


def test_pages_feed_is_the_same_entry_shape_repeated():
    """The wiki's own feed answers for every page what an entry answers for
    one -- which is the whole reason it can serve as a change index."""
    from connections_export.adapters.wikis import parse_pages_feed

    entry = (FIXTURES / "page_entry_reference_fields.xml").read_bytes().decode()
    entry = entry[entry.index("<entry") :]
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:td="urn:ibm.com/td" xmlns:thr="http://purl.org/syndication/thread/1.0" '
        'xmlns:snx="http://www.ibm.com/xmlns/prod/sn" '
        'xmlns:app="http://www.w3.org/2007/app">' + entry + "</feed>"
    )
    pages = parse_pages_feed(feed.encode())

    assert [page.label for page in pages] == ["referenceSamplePage"]
    assert pages[0].modified  # the field an update gates on
    assert pages[0].uuid == "1f9c7f1e-2222-4dab-9a6b-c9a8e7c8d001"


def test_an_unreadable_index_entry_is_skipped_not_fatal():
    """A page missing from the index is read the way it always was. Raising
    here would lose every page in the wiki over one malformed entry."""
    from connections_export.adapters.wikis import parse_pages_feed

    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">'
        "<entry><title>no id, no label</title></entry>"
        "<entry><id>urn:lsid:ibm.com:td:abc</id><td:label>good</td:label></entry>"
        "</feed>"
    )
    assert [page.label for page in parse_pages_feed(feed.encode())] == ["good"]
