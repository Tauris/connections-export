"""The dataset model carries the fields the round-trip needs.

`FakePage` must carry uuid/label/title/parent_uuid/ordinal/body/comments/
versions/attachments/timestamps so later synth and serializer code has
something concrete to build from.
"""

from connections_export.fakeserver.model import (
    FakeAttachment,
    FakeComment,
    FakePage,
    FakeVersion,
    FakeWiki,
    WikiSet,
)


def test_fake_page_carries_required_fields():
    page = FakePage(
        uuid="11111111-1111-1111-1111-111111111111",
        label="my-page",
        title="My Page",
        parent_uuid=None,
        ordinal=0,
        body_html="<p>hello</p>",
        created="2018-01-01T00:00:00Z",
        modified="2018-01-02T00:00:00Z",
        version_label=1,
    )
    assert page.uuid == "11111111-1111-1111-1111-111111111111"
    assert page.label == "my-page"
    assert page.title == "My Page"
    assert page.parent_uuid is None
    assert page.ordinal == 0
    assert page.body_html == "<p>hello</p>"
    assert page.created == "2018-01-01T00:00:00Z"
    assert page.modified == "2018-01-02T00:00:00Z"
    assert page.version_label == 1
    # Collections default to empty, but are the required attributes.
    assert page.comments == []
    assert page.versions == []
    assert page.attachments == []
    assert page.tags == []


def test_fake_page_holds_comments_versions_attachments():
    comment = FakeComment(
        uuid="c1",
        author="Alice",
        content_html="<p>nice page</p>",
        published="2018-01-01T00:00:00Z",
        updated="2018-01-01T00:00:00Z",
    )
    version = FakeVersion(
        uuid="v1",
        version_label=1,
        author="Alice",
        created="2018-01-01T00:00:00Z",
        content_html="<p>v1 body</p>",
    )
    attachment = FakeAttachment(
        uuid="a1",
        filename="diagram.png",
        content_type="image/png",
        size=1234,
        author="Alice",
        created="2018-01-01T00:00:00Z",
    )
    page = FakePage(
        uuid="p1",
        label="p1",
        title="Page One",
        parent_uuid=None,
        ordinal=0,
        body_html="<p>x</p>",
        created="2018-01-01T00:00:00Z",
        modified="2018-01-01T00:00:00Z",
        version_label=1,
        comments=[comment],
        versions=[version],
        attachments=[attachment],
    )
    assert page.comments == [comment]
    assert page.versions == [version]
    assert page.attachments == [attachment]


def test_wiki_set_resolves_wikis_and_pages_by_label_and_uuid():
    child = FakePage(
        uuid="child-uuid",
        label="child",
        title="Child",
        parent_uuid="parent-uuid",
        ordinal=0,
        body_html="<p>child</p>",
        created="2018-01-01T00:00:00Z",
        modified="2018-01-01T00:00:00Z",
        version_label=1,
    )
    parent = FakePage(
        uuid="parent-uuid",
        label="parent",
        title="Parent",
        parent_uuid=None,
        ordinal=0,
        body_html="<p>parent</p>",
        created="2018-01-01T00:00:00Z",
        modified="2018-01-01T00:00:00Z",
        version_label=1,
    )
    wiki = FakeWiki(
        uuid="wiki-uuid",
        label="wiki0",
        title="Wiki Zero",
        created="2018-01-01T00:00:00Z",
        modified="2018-01-01T00:00:00Z",
        pages=[parent, child],
    )
    wikiset = WikiSet(wikis=[wiki])

    assert wikiset.wiki_by_label("wiki0") is wiki
    assert wikiset.wiki_by_label("nope") is None
    assert wiki.page_by_label("child") is child
    assert wiki.page_by_uuid("parent-uuid") is parent
    assert wiki.top_level_pages() == [parent]
    assert wiki.children_of("parent-uuid") == [child]
    assert wiki.children_of("child-uuid") == []


def test_wiki_children_of_are_ordered_by_ordinal():
    a = FakePage(
        uuid="a",
        label="a",
        title="A",
        parent_uuid="root",
        ordinal=2,
        body_html="",
        created="",
        modified="",
        version_label=1,
    )
    b = FakePage(
        uuid="b",
        label="b",
        title="B",
        parent_uuid="root",
        ordinal=0,
        body_html="",
        created="",
        modified="",
        version_label=1,
    )
    c = FakePage(
        uuid="c",
        label="c",
        title="C",
        parent_uuid="root",
        ordinal=1,
        body_html="",
        created="",
        modified="",
        version_label=1,
    )
    wiki = FakeWiki(
        uuid="root",
        label="wiki0",
        title="Wiki",
        created="",
        modified="",
        pages=[a, b, c],
    )
    assert [p.uuid for p in wiki.children_of("root")] == ["b", "c", "a"]
