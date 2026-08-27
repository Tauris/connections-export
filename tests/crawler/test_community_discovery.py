"""What a community offers, as the setup picker sees it.

`discover_components` is what turns a pasted community URL into the list of
checkboxes a user chooses from. Anything it does not discover cannot be
selected -- so a component missing here is a component that exists on the
deployment, is fully supported by the crawler, and is unreachable from the
console.

That is exactly what happened to Rich Content: crawler, derive, reader and PDF
all handled it, and the picker never offered it.
"""

from __future__ import annotations

from connections_export.crawler.community import discover_components

COMMUNITY = "c-1"
BASE = "https://fake"

LAYOUT = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <entry>
    <title>Welcome</title>
    <snx:widgetResourceContainerId>conn-rte#{COMMUNITY}</snx:widgetResourceContainerId>
    <snx:widgetResourceId>res-a</snx:widgetResourceId>
  </entry>
  <entry>
    <title>Who to ask</title>
    <snx:widgetResourceContainerId>conn-rte#{COMMUNITY}</snx:widgetResourceContainerId>
    <snx:widgetResourceId>res-b</snx:widgetResourceId>
  </entry>
  <entry>
    <title>Never written in</title>
    <snx:widgetResourceContainerId>conn-rte#{COMMUNITY}</snx:widgetResourceContainerId>
  </entry>
  <entry>
    <title>Forums</title>
    <snx:widgetResourceContainerId>conn-forums#{COMMUNITY}</snx:widgetResourceContainerId>
    <snx:widgetResourceId>forum-res</snx:widgetResourceId>
  </entry>
</feed>
""".encode()

EMPTY_LAYOUT = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


def _fetch_factory(layout: bytes | None):
    """Answer only the widget layout; everything else is absent.

    Deliberately narrow: this isolates Rich Content discovery from the wiki,
    blog, forum and files lookups, which have their own routes and their own
    failure modes.
    """

    def fetch(url: str) -> bytes | None:
        if "/communities/service/atom/community/widgets" in url:
            return layout
        return None

    return fetch


def _discover(layout: bytes | None) -> list[dict]:
    answer = discover_components(
        community_uuid=COMMUNITY, base_url=BASE, fetch=_fetch_factory(layout)
    )
    return [c for c in answer["components"] if c["kind"] == "rich_content"]


def test_rich_content_is_offered_as_a_component():
    found = _discover(LAYOUT)

    assert len(found) == 1
    assert found[0]["id"] == COMMUNITY, "addressed by the community uuid, like files"


def test_the_count_is_what_would_actually_be_captured():
    """Two pages have content; a third area was placed and never written in.
    Offering "3" would promise a page that cannot be fetched."""
    found = _discover(LAYOUT)

    assert found[0]["count"] == 2


def test_the_count_is_a_number_like_every_other_kind():
    """The picker sums counts for its "all N items" total and drops anything
    that is not finite, so a string count goes missing rather than showing up
    wrong."""
    found = _discover(LAYOUT)

    assert isinstance(found[0]["count"], int)


def test_another_applications_widget_is_not_offered_as_rich_content():
    found = _discover(LAYOUT)

    assert found[0]["count"] == 2  # the forum widget is not among them


def test_a_community_with_no_rich_content_offers_none():
    """Every community has a widget layout; most hold no rich content. An
    empty layout must yield no component rather than an empty one."""
    assert _discover(EMPTY_LAYOUT) == []


def test_an_unreachable_layout_is_not_a_crash():
    """Discovery runs against a live deployment where any endpoint may 404 or
    be forbidden. A missing layout means "no rich content offered", never a
    failed lookup for the whole community."""
    assert _discover(None) == []


def test_an_unparseable_layout_is_not_a_crash():
    assert _discover(b"<html><body>login page</body></html>") == []


# --- saying WHY nothing was offered ----------------------------------------
#
# Rich Content went missing on a real community that definitely has it, and the
# console showed nothing at all -- no component, no error. These pin the three
# causes apart, because each needs a different fix and a silent absence points
# at none of them.


def _answer(layout: bytes | None) -> dict:
    return discover_components(
        community_uuid=COMMUNITY, base_url=BASE, fetch=_fetch_factory(layout)
    )


def test_an_unanswered_layout_says_so():
    note = _answer(None).get("rich_content_note", "")

    assert "no response" in note
    assert "/communities/service/atom/community/widgets" in note, "name the URL that failed"


def test_a_layout_with_no_rich_content_says_that_instead():
    note = _answer(EMPTY_LAYOUT).get("rich_content_note", "")

    assert "named no rich content" in note


def test_an_unreadable_layout_is_distinguished_from_an_empty_one():
    """A login page is well-formed HTML. Reading it as "this community has no
    Rich Content" would report an authentication failure as a fact about the
    community."""
    note = _answer(b"<html><body>Please sign in</body></html>").get("rich_content_note", "")

    assert "could not be read" in note


def test_areas_placed_but_never_written_in_are_explained():
    """Not a fault -- but "we found four areas and none of them has content"
    is a very different message from "we found nothing"."""
    layout = LAYOUT.replace(b"<snx:widgetResourceId>res-a</snx:widgetResourceId>", b"").replace(
        b"<snx:widgetResourceId>res-b</snx:widgetResourceId>", b""
    )

    note = _answer(layout).get("rich_content_note", "")

    assert "none of them written in" in note


def test_no_note_when_rich_content_was_offered():
    """The note exists to explain an absence. Emitting one alongside a
    successful discovery would train people to ignore it."""
    assert "rich_content_note" not in _answer(LAYOUT)


def test_the_declared_kind_vocabulary_matches_what_the_code_actually_emits():
    """`EMITTED_COMPONENT_KINDS` is hand-written, which is the same shape of
    thing that caused the bug it guards. So it is checked against the module's
    own literals: a kind introduced in discovery without being declared here
    would otherwise reach the console, be offered to the user, and be rejected
    by the CLI -- exactly `rich_content`'s history.

    A `field_validator` on the returned model would subsume both this test
    and the constant, and is the better home for the rule if the model gains
    one.
    """
    import re
    from pathlib import Path

    from connections_export.crawler import community

    source = Path(community.__file__).read_text(encoding="utf-8")
    literals = set(re.findall(r'"kind":\s*"([a-z_]+)"', source))
    literals |= set(re.findall(r'\bkind = "([a-z_]+)"', source))
    literals |= set(re.findall(r'\bkind = "([a-z_]+)" if ', source))
    literals |= set(re.findall(r'"([a-z_]+)" if "ideation', source))

    undeclared = literals - community.EMITTED_COMPONENT_KINDS
    assert not undeclared, f"discovery emits kinds it does not declare: {sorted(undeclared)}"
