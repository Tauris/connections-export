"""Rich Content adapter: a community's Highlights pages.

Rich Content is the free text and images a community's owners place on the
Highlights area -- the thing people wrote *about* everything else. An export
that captures the wiki, blogs, forums and files but loses the front page has
lost that.

Where it lives is not where anyone would look, and the route was found only by
capture (the published API reference). HCL's administration guide says
Rich Content is *"stored in File AppData"*, which is true and unhelpful: the
read route is neither a Files API path nor an AppData one, but a dedicated
proxy under `/connections/rte/`. The four guessed AppData paths returned
404/`UnsupportedOperation`, and the remote-applications registry -- the
promising lead from HCL's documented Forums sample -- returned 204 and no
entries on a live deployment.

**Discovery is separate from retrieval**, and that is the whole shape of this
module:

    discovery /communities/service/atom/community/widgets?communityUuid=...
    retrieval /connections/rte/community/{uuid}/page/{widgetResourceId}/entry

Both endpoints are, and discovery is readable by an
ordinary member -- not only by an owner or administrator, which decides whether
the feature works for everybody. The AppData library the community-level RTE
entry exposes returned 401 to that member, so it is not a usable discovery
route.

The entry FIELD NAMES are; the literal XML shape carrying them is
undocumented, as with Files: what is known is which fields exist rather
than the bytes. Every field therefore degrades to None rather than raising.
"""

from __future__ import annotations

from urllib.parse import quote

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.model import RichContentPage, RichContentWidget

#: The widget layout's container id for Rich Content, before the `#{uuid}`.
#: -- `widgetResourceContainerId=conn-rte#{communityUuid}`.
RTE_CONTAINER_PREFIX = "conn-rte"


def widget_layout_url(*, base_url: str, community_uuid: str) -> str:
    """A community's persisted widget layout., and readable by an
    ordinary member.

    This is DISCOVERY: it is the only route to the `widgetResourceId`s that
    retrieval needs. The uuid is percent-encoded because it lands in a query
    string, where the other endpoints take it as a path segment.
    """
    root = base_url.rstrip("/")
    uuid = quote(community_uuid, safe="")
    return f"{root}/communities/service/atom/community/widgets?communityUuid={uuid}"


#: Asked for on every page request. `inline=true` is the load-bearing one: a
#: deployment may otherwise return an entry whose `<content>` carries only a
#: `src`, leaving the body a second fetch away. The other two ask for the
#: version and library metadata the entry can carry.
_PAGE_PARAMS = (
    ("includeWorkingDraftInfo", "true"),
    ("includeLibraryInfo", "true"),
    ("inline", "true"),
)


def rich_content_page_url(*, base_url: str, community_uuid: str, resource_id: str) -> str:
    """One Rich Content page..

    The query string is built HERE and nowhere else. The crawler archives what
    it fetched and derive looks the same URL back up, so the two must agree
    character for character -- and when this was assembled independently on
    each side (`add_query(...)` in the crawler, a literal in derive) they
    agreed only by coincidence of parameter order. Files already produced this
    exact bug once, where six of eight documents derived without their bytes
    because one side percent-encoded a name and the other did not.
    """
    root = base_url.rstrip("/")
    query = "&".join(f"{key}={value}" for key, value in _PAGE_PARAMS)
    return f"{root}/connections/rte/community/{community_uuid}/page/{resource_id}/entry?{query}"


def community_rte_url(*, base_url: str, community_uuid: str) -> str:
    """The community-level RTE entry. to exist, categorised
    `communityAppFiles`, exposing a Files AppData library -- which returned 401
    to an ordinary member. Kept because it is the documented-by-capture
    counterpart of the page route, not because it is a discovery route.
    """
    return f"{base_url.rstrip('/')}/connections/rte/community/{community_uuid}/entry"


#: The two values discovery needs out of the layout, by LOCAL name. Matched
#: case-insensitively and without a namespace, because which namespace (if any)
#: carries them is not documented -- only the values themselves are.
_CONTAINER_KEY = "widgetresourcecontainerid"
_RESOURCE_KEY = "widgetresourceid"


def _values_by_key(element) -> dict[str, str]:
    """Every `key -> value` pair an element carries, however it encodes them.

    A widget layout is not a content feed and there is no documented schema for
    it. The capture recorded the two values as
    `widgetResourceContainerId=conn-rte#{uuid}` -- which says WHAT they are and
    not HOW they are written down. The first implementation guessed one
    encoding (child elements in the `snx` namespace) and found nothing on a
    community that definitely has Rich Content.

    So read all four plausible encodings, which cost nothing to support:

      * a child element whose text is the value
      * an attribute on this element or a descendant
      * a `<property name="..." value="..."/>` pair
      * a `<property name="...">value</property>` pair

    Keys are lowercased; namespaces are ignored throughout.
    """
    found: dict[str, str] = {}

    def remember(key: str | None, value: str | None) -> None:
        if not key or value is None:
            return
        value = value.strip()
        if value:
            found.setdefault(key.split("}")[-1].lower(), value)

    for node in element.iter():
        local = str(node.tag).split("}")[-1].lower() if isinstance(node.tag, str) else ""
        # <property name="x" value="y"/>, <param name="x">y</param>,
        # and Connections' <snx:widgetProperty key="x">y</snx:widgetProperty>
        named = node.get("name") or node.get("key")
        if named:
            remember(named, node.get("value") if node.get("value") is not None else node.text)
        # <snx:widgetResourceId>y</snx:widgetResourceId>
        if node.text and node.text.strip():
            remember(local, node.text)
        # widgetResourceId="y" on any element
        for attr, value in node.attrib.items():
            remember(str(attr), value)
    return found


def _is_rte_container(container: str | None, community_uuid: str) -> bool:
    """Whether a container id names THIS community's Rich Content.

    Both halves are required. Matching `conn-rte` alone would accept another
    community's pages if a layout ever carried them, which would be a
    cross-community leak rather than a thin export; matching the uuid alone
    would accept every other application's widget.

    Compared case-insensitively and after stripping, rather than by string
    equality: a uuid that differs only in case is the same community, and an
    exact compare turns that into "this community has no Rich Content" with no
    error to show for it.
    """
    if not container:
        return False
    haystack = container.strip().lower()
    return RTE_CONTAINER_PREFIX in haystack and community_uuid.strip().lower() in haystack


def parse_widget_layout(data: bytes | str, *, community_uuid: str) -> list[RichContentWidget]:
    """The community widget layout -> its Rich Content widgets, in layout order.

    A layout lists EVERY widget the community has -- forums, files, bookmarks.
    Only the Rich Content ones are returned, matched on both halves of the
    container id (see `_is_rte_container`).

    Uninitialized widgets -- placed by an owner, never written into, and so
    carrying no resource id -- are RETURNED, marked. A live capture saw eight
    instances and seven initialized; dropping the eighth here would make seven
    look like the whole truth, and the crawler needs to be able to say which
    it means.

    The root element is not constrained to an Atom `feed`. The endpoint is
    but the document it returns is not a content feed, and demanding
    one made an unexpected wrapper indistinguishable from a community with no
    Rich Content.

    An HTML document still raises. That is the case that must never be read as
    an empty answer: a deployment that has bounced the request to a login page
    returns perfectly well-formed markup, and treating it as "no Rich Content
    here" would report an authentication failure as a fact about the community.
    """
    root = atom.parse_xml(data)
    if str(root.tag).split("}")[-1].lower() == "html":
        raise AdapterError(
            "widget layout came back as an HTML document -- most likely a login "
            "or error page rather than the layout"
        )
    entries = atom.entries(root)
    if not entries:
        # Not an Atom feed. Treat every element that carries a container id as
        # a widget -- a layout served as plain `<widgets><widget.../></widgets>`
        # has no <entry> to iterate.
        #
        # `_values_by_key` reads descendants, so a wrapper matches whenever one
        # of its children does. Keep only the INNERMOST matches, or every
        # widget is counted once more for each ancestor above it.
        candidates = [
            node
            for node in root.iter()
            if _is_rte_container(_values_by_key(node).get(_CONTAINER_KEY), community_uuid)
        ]
        matched = set(map(id, candidates))
        entries = [
            node
            for node in candidates
            if not any(id(child) in matched for child in node.iterdescendants())
        ]

    widgets: list[RichContentWidget] = []
    for entry in entries:
        values = _values_by_key(entry)
        container = values.get(_CONTAINER_KEY)
        if not _is_rte_container(container, community_uuid):
            continue
        widgets.append(
            RichContentWidget(
                resource_id=values.get(_RESOURCE_KEY) or None,
                title=atom.find_text(entry, "atom:title") or values.get("title"),
                container_id=container,
            )
        )
    return widgets


def parse_page_entry(data: bytes | str, *, resource_id: str) -> RichContentPage:
    """One Rich Content page entry -> a `RichContentPage`.

    `resource_id` is passed in rather than read out: the page is ADDRESSED by
    it, and the entry's own `<id>` is a different identifier. Binding the two
    here keeps the page traceable back to the widget it came from.
    """
    entry = atom.parse_xml(data, expected_root="entry")
    link = atom.links_by_rel(entry)

    def href(rel: str) -> str | None:
        element = link.get(rel)
        # `is not None`: an lxml element with no children is falsey, and `or`
        # here silently discarded every link that had no sub-element.
        return element.get("href") if element is not None else None

    return RichContentPage(
        resource_id=resource_id,
        title=atom.find_text(entry, "atom:title"),
        content_html=atom.find_text(entry, "atom:content"),
        content_src=(
            entry.find("atom:content", namespaces=atom.NS).get("src")
            if entry.find("atom:content", namespaces=atom.NS) is not None
            else None
        ),
        author=atom.author_name(entry),
        author_userid=atom.author_userid(entry),
        published=atom.find_text(entry, "atom:published"),
        updated=atom.find_text(entry, "atom:updated"),
        version_label=atom.find_text(entry, "td:versionLabel"),
        media_url=href("enclosure"),
        alternate_url=href("alternate"),
        provenance=atom.find_text(entry, "atom:id"),
    )
