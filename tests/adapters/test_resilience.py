"""Malformed / wrong-shape input raises a typed `AdapterError`,
never a raw lxml (or json) exception, so a crawl can record the entity
as "unparsed" and continue."""

import pytest

from connections_export.adapters.errors import AdapterError
from connections_export.adapters.wikis import (
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_navigation_entry,
    parse_page_entry,
    parse_versions_feed,
    parse_wikis_feed,
)

_MALFORMED_XML = b"<feed><entry><unterminated-element"


@pytest.mark.parametrize(
    "parser",
    [
        parse_wikis_feed,
        parse_page_entry,
        parse_navigation_entry,
        parse_comments_feed,
        parse_versions_feed,
        parse_attachments_feed,
    ],
)
def test_malformed_xml_raises_adapter_error(parser):
    with pytest.raises(AdapterError):
        parser(_MALFORMED_XML)


def test_no_raw_lxml_exception_escapes():
    from lxml import etree

    try:
        parse_page_entry(_MALFORMED_XML)
    except AdapterError:
        pass
    except etree.XMLSyntaxError:
        pytest.fail("a raw lxml.etree.XMLSyntaxError escaped the adapter boundary")


def test_wrong_root_element_raises_adapter_error():
    wrong_root = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><id>not-an-entry</id></feed>
    """
    with pytest.raises(AdapterError):
        parse_page_entry(wrong_root)


def test_page_entry_missing_required_label_raises_adapter_error():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">
      <id>urn:lsid:ibm.com:td:11111111-2222-3333-4444-555555555555</id>
      <title type="text">No Label</title>
    </entry>
    """
    with pytest.raises(AdapterError):
        parse_page_entry(xml)


def test_page_entry_missing_id_raises_adapter_error():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td">
      <title type="text">No Id</title>
      <td:label>noId</td:label>
    </entry>
    """
    with pytest.raises(AdapterError):
        parse_page_entry(xml)


def test_nav_feed_missing_items_key_raises_adapter_error():
    with pytest.raises(AdapterError):
        parse_nav_feed(b'{"identifier": "id"}')


def test_nav_feed_not_a_json_object_raises_adapter_error():
    with pytest.raises(AdapterError):
        parse_nav_feed(b"[1, 2, 3]")


def test_nav_feed_malformed_json_raises_adapter_error():
    with pytest.raises(AdapterError):
        parse_nav_feed(b"{not json at all")
