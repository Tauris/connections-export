"""Discovery must survive the shape the deployment actually sends.

The widget layout's ENDPOINT is; the XML carrying its two values is
not. The capture recorded them as `widgetResourceContainerId=conn-rte#{uuid}`
and a `widgetResourceId` per page -- key=value notation that says what the
values are and not how they are encoded.

The first implementation guessed one encoding (child elements in the `snx`
namespace, an exact string match, an Atom `feed` root) and found nothing on a
community that definitely has Rich Content. Every one of those assumptions is
a silent zero when wrong: no error, no component, no way for a user to tell
"this community has none" from "we could not read it".

So each plausible encoding is pinned here. A parser that handles only one of
them is a parser that will be wrong again on the next deployment.
"""

from __future__ import annotations

import pytest

from connections_export.adapters.rte import parse_widget_layout

UUID = "b7f1c2a4-5d3e"

CHILD_ELEMENTS = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry><title>Welcome</title>
    <snx:widgetResourceContainerId>conn-rte#{UUID}</snx:widgetResourceContainerId>
    <snx:widgetResourceId>res-a</snx:widgetResourceId>
  </entry>
</feed>"""

ATTRIBUTES = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry><title>Welcome</title>
    <snx:widget widgetResourceContainerId="conn-rte#{UUID}" widgetResourceId="res-a"/>
  </entry>
</feed>"""

NAME_VALUE_PROPERTIES = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry><title>Welcome</title>
    <snx:property name="widgetResourceContainerId" value="conn-rte#{UUID}"/>
    <snx:property name="widgetResourceId" value="res-a"/>
  </entry>
</feed>"""

PROPERTY_TEXT = f"""<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Welcome</title>
    <property name="widgetResourceContainerId">conn-rte#{UUID}</property>
    <property name="widgetResourceId">res-a</property>
  </entry>
</feed>"""

WIDGET_PROPERTY_TEXT = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry><title>Welcome</title>
    <snx:widgetProperty key="widgetResourceContainerId">conn-rte#{UUID}</snx:widgetProperty>
    <snx:widgetProperty key="widgetResourceId">res-a</snx:widgetProperty>
  </entry>
</feed>"""

NO_NAMESPACE = f"""<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Welcome</title>
    <widgetResourceContainerId>conn-rte#{UUID}</widgetResourceContainerId>
    <widgetResourceId>res-a</widgetResourceId>
  </entry>
</feed>"""

WIDGETS_ROOT = f"""<widgets>
  <widget title="Welcome"
          widgetResourceContainerId="conn-rte#{UUID}" widgetResourceId="res-a"/>
</widgets>"""


@pytest.mark.parametrize(
    "layout",
    [
        CHILD_ELEMENTS,
        ATTRIBUTES,
        NAME_VALUE_PROPERTIES,
        PROPERTY_TEXT,
        WIDGET_PROPERTY_TEXT,
        NO_NAMESPACE,
        WIDGETS_ROOT,
    ],
    ids=[
        "child-elements",
        "attributes",
        "name-value-props",
        "property-text",
        "widget-property-key",
        "no-namespace",
        "widgets-root",
    ],
)
def test_the_resource_id_is_found_however_it_is_encoded(layout):
    widgets = parse_widget_layout(layout, community_uuid=UUID)

    assert [w.resource_id for w in widgets] == ["res-a"]


def test_the_container_match_tolerates_case():
    """A uuid that differs only in case is the same community. An exact
    string compare turns that into "this community has no Rich Content"."""
    layout = CHILD_ELEMENTS.replace(UUID, UUID.upper())

    assert [w.resource_id for w in parse_widget_layout(layout, community_uuid=UUID)] == ["res-a"]


def test_surrounding_whitespace_does_not_break_the_match():
    layout = CHILD_ELEMENTS.replace(f">conn-rte#{UUID}<", f">\n      conn-rte#{UUID}\n    <")

    assert [w.resource_id for w in parse_widget_layout(layout, community_uuid=UUID)] == ["res-a"]


def test_another_communitys_widgets_are_still_excluded():
    """Tolerance must not become "match anything with conn-rte in it": that
    would fetch another community's pages into this export."""
    layout = CHILD_ELEMENTS.replace(UUID, "some-other-community")

    assert parse_widget_layout(layout, community_uuid=UUID) == []


def test_another_applications_widget_is_still_excluded():
    layout = CHILD_ELEMENTS.replace("conn-rte#", "conn-forums#")

    assert parse_widget_layout(layout, community_uuid=UUID) == []


def test_an_uninitialized_widget_is_still_reported_without_an_id():
    layout = CHILD_ELEMENTS.replace("<snx:widgetResourceId>res-a</snx:widgetResourceId>", "")

    widgets = parse_widget_layout(layout, community_uuid=UUID)

    assert len(widgets) == 1
    assert widgets[0].resource_id is None
