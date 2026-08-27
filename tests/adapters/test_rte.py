"""The Rich Content adapter: the widget layout, and one page.

Two endpoints:

    /communities/service/atom/community/widgets?communityUuid={uuid}
    /connections/rte/community/{uuid}/page/{widgetResourceId}/entry

Discovery is the layout; retrieval is the page. The XML shape below is
reconstructed from what is known to exist rather than from the
bytes, so the parsers must degrade rather than raise -- a shape that differs
slightly should thin the export, never break the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from connections_export.adapters.errors import AdapterError
from connections_export.adapters.rte import (
    parse_page_entry,
    parse_widget_layout,
    rich_content_page_url,
    widget_layout_url,
)

LAYOUT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry>
    <id>urn:lsid:ibm.com:communities:widget-1</id>
    <title>Welcome</title>
    <category term="RichContent" scheme="http://www.ibm.com/xmlns/prod/sn/type"/>
    <snx:widgetResourceContainerId>conn-rte#COMM</snx:widgetResourceContainerId>
    <snx:widgetResourceId>res-aaa</snx:widgetResourceId>
  </entry>
  <entry>
    <id>urn:lsid:ibm.com:communities:widget-2</id>
    <title>Not written yet</title>
    <category term="RichContent" scheme="http://www.ibm.com/xmlns/prod/sn/type"/>
    <snx:widgetResourceContainerId>conn-rte#COMM</snx:widgetResourceContainerId>
  </entry>
  <entry>
    <id>urn:lsid:ibm.com:communities:widget-3</id>
    <title>Forums</title>
    <category term="Forum" scheme="http://www.ibm.com/xmlns/prod/sn/type"/>
    <snx:widgetResourceContainerId>conn-forums#COMM</snx:widgetResourceContainerId>
    <snx:widgetResourceId>res-forum</snx:widgetResourceId>
  </entry>
</feed>
"""

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:td="urn:ibm.com/td">
  <id>urn:lsid:ibm.com:td:page-aaa</id>
  <title>Welcome</title>
  <published>2026-03-01T09:00:00Z</published>
  <updated>2026-05-14T11:20:00Z</updated>
  <author><name>R. Delgado</name><snx:userid
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">RD001</snx:userid></author>
  <category term="page" scheme="http://www.ibm.com/xmlns/prod/sn/type"/>
  <td:versionLabel>126</td:versionLabel>
  <content type="html">&lt;h2&gt;Welcome&lt;/h2&gt;&lt;p&gt;Read the handbook.&lt;/p&gt;</content>
  <link rel="enclosure" href="https://fake/media/page-aaa"/>
  <link rel="alternate" href="https://fake/communities/service/html/communityview"/>
</entry>
"""


# --- URLs -------------------------------------------------------------------


def test_the_layout_url_takes_the_community_uuid():
    url = widget_layout_url(base_url="https://fake/", community_uuid="COMM")

    assert url == "https://fake/communities/service/atom/community/widgets?communityUuid=COMM"


def test_the_page_url_takes_the_resource_id():
    url = rich_content_page_url(base_url="https://fake", community_uuid="COMM", resource_id="res-a")

    assert url.startswith("https://fake/connections/rte/community/COMM/page/res-a/entry?")


def test_the_page_url_asks_for_the_body_inline():
    """Without `inline=true` a deployment can return an entry whose `<content>`
    carries only a `src`, leaving the body a second fetch away."""
    url = rich_content_page_url(base_url="https://fake", community_uuid="COMM", resource_id="res-a")

    assert "inline=true" in url


def test_crawl_and_derive_cannot_disagree_about_the_page_url():
    """The crawler archives what it fetched and derive looks the SAME url back
    up, so they must match character for character. Built independently on each
    side they agreed only by coincidence of parameter order -- and Files
    already produced this bug once, deriving six of eight documents without
    their bytes because one side percent-encoded a name and the other did not.
    """
    import connections_export

    root = Path(connections_export.__file__).parent
    for relative in ("crawler/crawl.py", "derive/assemble.py"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "includeWorkingDraftInfo" not in text, (
            f"{relative} builds the rich content query itself instead of asking the adapter"
        )


def test_a_uuid_with_url_punctuation_is_encoded():
    """The uuid lands in a query string; a stray `&` would truncate it."""
    url = widget_layout_url(base_url="https://fake", community_uuid="a&b=c")

    assert "communityUuid=a%26b%3Dc" in url


# --- The layout: discovery --------------------------------------------------


def test_only_rich_content_widgets_are_returned():
    """The layout lists every widget the community has. A Forums widget also
    carries a resource id, and fetching it as a page would be nonsense."""
    widgets = parse_widget_layout(LAYOUT, community_uuid="COMM")

    assert [w.resource_id for w in widgets] == ["res-aaa", None]


def test_an_uninitialized_widget_is_kept_but_marked():
    """8 placed / 7 initialized was the live observation. Dropping the
    uninitialized one at parse time would make "7" look like the whole truth."""
    widgets = parse_widget_layout(LAYOUT, community_uuid="COMM")

    assert [w.initialized for w in widgets] == [True, False]
    assert widgets[1].title == "Not written yet"


def test_widgets_of_another_community_are_ignored():
    """The container id is `conn-rte#{communityUuid}`. Matching on the `conn-rte`
    prefix alone would pull in another community's pages if a layout ever
    carried them."""
    widgets = parse_widget_layout(LAYOUT, community_uuid="OTHER-COMMUNITY")

    assert widgets == []


def test_an_empty_layout_is_not_an_error():
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    assert parse_widget_layout(empty, community_uuid="COMM") == []


def test_a_login_page_raises_rather_than_reporting_no_rich_content():
    """The layout root is deliberately unconstrained -- the document is not a
    content feed and demanding an Atom `feed` made an unexpected wrapper look
    like an empty community. HTML is the exception: a request bounced to a
    login page returns well-formed markup, and reading that as "this community
    has no Rich Content" would report an auth failure as a fact."""
    with pytest.raises(AdapterError):
        parse_widget_layout("<html><body>login</body></html>", community_uuid="COMM")


def test_an_unexpected_xml_wrapper_does_not_raise():
    """The opposite side of the same line: a layout in a shape we did not
    predict should yield what can be read from it, not an error."""
    assert parse_widget_layout("<somethingElse/>", community_uuid="COMM") == []


# --- The page: retrieval ----------------------------------------------------


def test_the_page_body_comes_inline():
    """The body is in the entry, so there is no second fetch for content."""
    page = parse_page_entry(PAGE, resource_id="res-aaa")

    assert "<h2>Welcome</h2>" in page.content_html
    assert "Read the handbook." in page.content_html


def test_the_page_can_point_to_a_body_source():
    page = parse_page_entry(
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        '<content type="text/html" src="media/body"/></entry>',
        resource_id="res-aaa",
    )

    assert page.content_html is None
    assert page.content_src == "media/body"


def test_the_page_carries_its_identity_and_history():
    page = parse_page_entry(PAGE, resource_id="res-aaa")

    assert page.resource_id == "res-aaa"
    assert page.title == "Welcome"
    assert page.version_label == "126"
    assert page.author == "R. Delgado"
    assert page.updated == "2026-05-14T11:20:00Z"


def test_the_media_link_is_kept_for_asset_capture():
    page = parse_page_entry(PAGE, resource_id="res-aaa")

    assert page.media_url == "https://fake/media/page-aaa"
    assert page.alternate_url.endswith("communityview")


def test_a_page_missing_every_optional_field_still_parses():
    """The shape is a reconstruction: a field guessed wrong must thin the export, not
    break the run."""
    bare = '<entry xmlns="http://www.w3.org/2005/Atom"><id>urn:x</id></entry>'

    page = parse_page_entry(bare, resource_id="res-aaa")

    assert page.resource_id == "res-aaa"
    assert page.title is None and page.content_html is None


def test_a_page_that_is_not_an_entry_raises():
    with pytest.raises(AdapterError):
        parse_page_entry("<html><body>404</body></html>", resource_id="res-aaa")
