"""Wikis parsers, URL construction, and link extraction. The thick, app-specific layer on top of
`atom.py`'s shared
Atom core.

Link-following first: `parse_page_entry` reads `content/@src` and the `rel="replies"`
link straight off the entry and exposes them as `content_src` /
`replies_url`. The `*_url` builders below exist only as a fallback for
when no link is available to follow.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.model import (
    Attachment,
    Comment,
    NavNode,
    NavTree,
    Page,
    Tag,
    Version,
    WikiRef,
)

# --------------------------------------------------------------------
# URL construction (fallback only -- see module docstring)
# --------------------------------------------------------------------


def wikis_feed_url(*, base_url: str, auth_root: str) -> str:
    """`/wikis/{auth}/api/wikis/feed`."""
    return f"{base_url}/wikis/{auth_root}/api/wikis/feed"


def nav_feed_url(*, base_url: str, auth_root: str, wiki_label: str) -> str:
    """`/wikis/{auth}/api/wiki/{wiki-label}/nav/feed`
    (the reference also documents a `navigation/feed` alias; the fake
    server -- and this builder -- use the shorter `nav/feed` form)."""
    return f"{base_url}/wikis/{auth_root}/api/wiki/{wiki_label}/nav/feed"


def wiki_feed_url(*, base_url: str, auth_root: str, wiki_label: str) -> str:
    """One wiki's own feed. Carries the wiki's `<title>`, which a scoped crawl
    otherwise never sees: it skips the wikis-list enumeration, so the only
    name it has for the wiki is the label from the URL."""
    return f"{base_url}/wikis/{auth_root}/api/wiki/{wiki_label}/feed"


def page_entry_url(*, base_url: str, auth_root: str, wiki_label: str, page_label: str) -> str:
    """`/wikis/{auth}/api/wiki/{wiki-label}/page/{page-label}/entry`."""
    return (
        f"{base_url}/wikis/{auth_root}/api/wiki/{wiki_label}/page/"
        f"{quote(page_label, safe='')}/entry"
    )


def artifacts_url(
    *,
    base_url: str,
    auth_root: str,
    wiki_label: str,
    page_label: str,
    category: str | None = None,
) -> str:
    """the single page-feed URL, category-switched:
    `/wikis/{auth}/api/wiki/{wiki-label}/page/{page-label}/feed
    [?category={tag|version|attachment|comment}]`. Omitted category
    defaults to comments per the reference doc."""
    base = (
        f"{base_url}/wikis/{auth_root}/api/wiki/{wiki_label}/page/{quote(page_label, safe='')}/feed"
    )
    if category is None:
        return base
    return f"{base}?category={category}"


# --------------------------------------------------------------------
# Small internal helpers
# --------------------------------------------------------------------


def _require_text(entry, path: str, *, what: str) -> str:
    text = atom.find_text(entry, path)
    if not text:
        raise AdapterError(f"missing required element {what!r}", detail=path)
    return text


def _optional_int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _parent_uuid(entry) -> str | None:
    """`td:parentUuid` -- empty or absent -> None."""
    text = atom.find_text(entry, "td:parentUuid")
    return text or None


def _author_name(entry) -> str | None:
    author_el = entry.find("atom:author", namespaces=atom.NS)
    if author_el is None:
        return None
    return atom.find_text(author_el, "atom:name")


# --------------------------------------------------------------------
# Wikis feed
# --------------------------------------------------------------------


def parse_wikis_feed(data: bytes | str) -> list[WikiRef]:
    """Parse a wikis feed into `WikiRef`s."""
    root = atom.parse_xml(data, expected_root="feed")
    refs: list[WikiRef] = []
    for entry in atom.entries(root):
        uuid = atom.entry_uuid(_require_text(entry, "atom:id", what="id"))
        label = _require_text(entry, "td:label", what="td:label")
        title = _require_text(entry, "atom:title", what="title")
        refs.append(
            WikiRef(
                uuid=uuid,
                label=label,
                title=title,
                created=atom.find_text(entry, "atom:published"),
                modified=atom.find_text(entry, "atom:updated"),
                self_url=atom.link_href(entry, "self"),
                community_uuid=atom.find_text(entry, "snx:communityUuid"),
                community_title=atom.find_text(entry, "snx:communityTitle"),
            )
        )
    return refs


# --------------------------------------------------------------------
# Page entry
# --------------------------------------------------------------------


def parse_page_entry(data: bytes | str) -> Page:
    """Parse a page entry. Required:
    `<id>` and `<td:label>`; everything else degrades to None/empty
    rather than raising, since only those two identify the page."""
    return _page_from_entry(atom.parse_xml(data, expected_root="entry"))


def parse_pages_feed(data: bytes | str) -> list[Page]:
    """The pages of one wiki, from `wiki_feed_url`.

    the entries in this feed carry the same shape
    as the standalone page entry -- `atom:updated`, `atom:published`,
    `td:label`, `td:modified`, `td:versionLabel`, `atom:content/@src`,
    `rel="replies"` and author data -- so one request answers for every page
    what a per-page entry request answers for one.

    An entry the parser cannot make a page of is skipped rather than fatal:
    this feed is used as an INDEX, and a page missing from the index is read
    the way it always was. Losing a page here would mean skipping it.
    """
    feed = atom.parse_xml(data, expected_root="feed")
    pages: list[Page] = []
    for entry in feed.findall("atom:entry", namespaces=atom.NS):
        try:
            pages.append(_page_from_entry(entry))
        except Exception:  # noqa: BLE001 - an unreadable index entry is not a lost page
            continue
    return pages


def _page_from_entry(entry) -> Page:
    uuid = atom.entry_uuid(_require_text(entry, "atom:id", what="id"))
    label = _require_text(entry, "td:label", what="td:label")
    title = atom.find_text(entry, "atom:title") or ""

    content_el = entry.find("atom:content", namespaces=atom.NS)
    content_src = content_el.get("src") if content_el is not None else None

    return Page(
        uuid=uuid,
        label=label,
        title=title,
        parent_uuid=_parent_uuid(entry),
        version_label=_optional_int(atom.find_text(entry, "td:versionLabel")),
        visibility=atom.find_text(entry, "td:visibility"),
        created=atom.find_text(entry, "atom:published"),
        # Filled from `atom:updated`, deliberately, and load-bearing for
        # updates. The deployment distinguishes the two: commenting on a page
        # moves `atom:updated` and leaves HCL's own `modified` element
        # alone. The crawler gates a page's refetch on this
        # field, so it must stay the one that moves -- the name is the older
        # of the two, what it holds is the newer.
        modified=atom.find_text(entry, "atom:updated"),
        author=_author_name(entry),
        author_userid=atom.author_userid(entry),
        contributors=atom.contributor_names(entry),
        content_src=content_src,
        alternate_url=atom.link_href(entry, "alternate"),
        replies_url=atom.link_href(entry, "replies"),
        replies_count=atom.thr_count(entry),
        ranks=atom.ranks(entry),
        category_term=atom.category_term(entry),
    )


# --------------------------------------------------------------------
# Navigation entry and nav feed
# --------------------------------------------------------------------


def parse_navigation_entry(data: bytes | str) -> NavNode:
    """Parse a single navigation entry. A lone
    entry carries no sibling-order or children information -- those
    only exist in the nav *feed* -- so `ordinal` and `child_ids` are
    left at their defaults."""
    entry = atom.parse_xml(data, expected_root="entry")

    uuid = atom.find_text(entry, "td:uuid") or atom.entry_uuid(atom.find_text(entry, "atom:id"))
    if not uuid:
        raise AdapterError("navigation entry missing td:uuid and id", detail="td:uuid")
    label = atom.find_text(entry, "td:label")
    title = atom.find_text(entry, "atom:title")
    parent = _parent_uuid(entry)

    return NavNode(id=uuid, label=label, title=title, parent=parent, is_root=(parent is None))


def _nav_parent(item: dict) -> str | None:
    """The parent pointer for a nav item. Real HCL 8.0 carries it as
    `parElem`; the older reference-doc
    sample used `parent`, with `parElem` holding a `"true"`/`"false"` flag.
    Prefer a real `parElem` id, ignore that boolean sentinel, and fall back
    to `parent`."""
    par_elem = item.get("parElem")
    if par_elem and str(par_elem) not in ("true", "false"):
        return par_elem
    return item.get("parent") or None


def parse_nav_feed(data: bytes | str) -> NavTree:
    """Parse the JSON navigation feed into a `NavTree` (Parse
    the navigation feed with ordering). The synthetic
    `{"id": "tree", "root": "true"}` node is recognized as the tree
    root and recorded via `NavTree.root_id`, not emitted as a page
    node.

    Sibling `ordinal` is the position of a node's id within the
    `_reference` list of whichever item's `children` array names it
    (including the synthetic root's, for top-level pages) -- the only
    order signal the feed carries (reference doc, "Sibling ordering --
    NOT DOCUMENTED"; Q1). A node no `children` array references at all
    (the real 8.0 `tree=true` feed returns a flat `items[]` with
    `children` empty) falls back to
    its position among the feed's non-root items -- i.e. the items-array
    order, which is the nav-feed sequence the platform authored.

    The parent pointer is read from `parElem` on real 8.0 items and from
    `parent` on the older reference-doc sample; see `_nav_parent`. A
    pointer equal to the tree root (the feed's `identifier` or the
    synthetic `root:"true"` item) means a genuine top-level page and maps
    to `None`; any other unresolved pointer is left intact so orphan
    detection still fires.
    """
    text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError("malformed navigation feed JSON", detail=str(exc)) from exc

    if not isinstance(payload, dict):
        raise AdapterError("navigation feed is not a JSON object", detail=str(type(payload)))
    items = payload.get("items")
    if not isinstance(items, list):
        raise AdapterError("navigation feed missing 'items' list")

    ordinal_by_id: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for idx, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            ref = child.get("_reference")
            if ref is not None and ref not in ordinal_by_id:
                ordinal_by_id[ref] = idx

    # A parent pointer equal to the tree root is a top-level page, not an
    # orphan: the root is the synthetic `root:"true"` item (if present) and
    # the feed's own `identifier` (what a real 8.0 top-level `parElem`
    # points at).
    identifier = payload.get("identifier")
    synthetic_root_id = next(
        (
            item.get("id")
            for item in items
            if isinstance(item, dict) and str(item.get("root")) == "true"
        ),
        None,
    )
    tree_root_ids = {v for v in (synthetic_root_id, identifier) if v}

    root_id: str | None = None
    nodes: list[NavNode] = []
    # For nodes the `_reference` lists don't order (the real 8.0 feed, whose
    # `children` are empty), fall back to position among *siblings* in
    # items-array order -- a per-parent counter, so the ordinal is 0-based
    # within each parent exactly like the reference feed's `_reference`
    # positions (a single global counter would break sibling ordinals).
    sibling_seq: dict[str | None, int] = {}
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            raise AdapterError("navigation feed item missing 'id'", detail=str(item)[:200])

        if str(item.get("root")) == "true":
            root_id = item["id"]
            continue

        node_id = item["id"]
        parent = _nav_parent(item)
        if parent in tree_root_ids:
            parent = None
        child_ids = [
            c.get("_reference")
            for c in (item.get("children") or [])
            if isinstance(c, dict) and c.get("_reference") is not None
        ]
        ordinal = ordinal_by_id.get(node_id)
        if ordinal is None:
            ordinal = sibling_seq.get(parent, 0)
        sibling_seq[parent] = sibling_seq.get(parent, 0) + 1

        nodes.append(
            NavNode(
                id=node_id,
                label=item.get("label"),
                title=item.get("title"),
                parent=parent,
                ordinal=ordinal,
                child_ids=child_ids,
                is_root=(parent is None),
            )
        )

    # Real 8.0 has no synthetic `root:"true"` item; the tree root is the
    # feed's own `identifier`.
    return NavTree(nodes=nodes, root_id=root_id or identifier)


# --------------------------------------------------------------------
# Artifacts: comments / versions / attachments / tags
# --------------------------------------------------------------------
# The reference documents exactly one entry type family here -- the
# single page-feed URL, category-switched -- but not what an
# individual comment/version/attachment entry contains
# (Wikis_Atom_entry_types_80 documents only two entry types, neither of
# these). Every parser below is # INFERRED: it parses the
# plausible shape the fake emits, not a transcribed one.


def parse_comments_feed(data: bytes | str) -> list[Comment]:
    """# INFERRED: comment entry shape is undocumented anywhere
    in the reference. Parses id/title/content/author/published/updated
    and an optional thr:in-reply-to (threading is unconfirmed for wiki
    comments --, "INFERRED entries")."""
    root = atom.parse_xml(data, expected_root="feed")
    comments: list[Comment] = []
    for entry in atom.entries(root):
        uuid = atom.entry_uuid(atom.find_text(entry, "atom:id"))
        if not uuid:
            raise AdapterError("comment entry missing id")
        content_el = entry.find("atom:content", namespaces=atom.NS)
        in_reply_to_el = entry.find("thr:in-reply-to", namespaces=atom.NS)
        comments.append(
            Comment(
                id=uuid,
                title=atom.find_text(entry, "atom:title"),
                content_html=content_el.text if content_el is not None else None,
                author=_author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                published=atom.find_text(entry, "atom:published"),
                updated=atom.find_text(entry, "atom:updated"),
                in_reply_to=(in_reply_to_el.get("ref") if in_reply_to_el is not None else None),
            )
        )
    return comments


def parse_versions_feed(data: bytes | str) -> list[Version]:
    """# INFERRED: version entry shape is undocumented; only
    `<td:versionLabel>` on the *page* entry is. `content_src`
    is populated only if the entry's `<content>` carries a `src`
    attribute (Q9: unconfirmed whether real version entries inline
    content or link to it)."""
    root = atom.parse_xml(data, expected_root="feed")
    versions: list[Version] = []
    for entry in atom.entries(root):
        uuid = atom.entry_uuid(atom.find_text(entry, "atom:id"))
        if not uuid:
            raise AdapterError("version entry missing id")
        content_el = entry.find("atom:content", namespaces=atom.NS)
        content_src = content_el.get("src") if content_el is not None else None
        versions.append(
            Version(
                uuid=uuid,
                version_label=_optional_int(atom.find_text(entry, "td:versionLabel")),
                content_src=content_src,
                author=_author_name(entry),
                created=atom.find_text(entry, "atom:published"),
            )
        )
    return versions


def parse_attachments_feed(data: bytes | str) -> list[Attachment]:
    """# INFERRED: attachment entry shape is undocumented; we
    only know attachments are listed via `?category=attachment` and
    counted by `snx:rank scheme=".../attachments"` on the page entry."""
    root = atom.parse_xml(data, expected_root="feed")
    attachments: list[Attachment] = []
    for entry in atom.entries(root):
        uuid = atom.entry_uuid(atom.find_text(entry, "atom:id"))
        if not uuid:
            raise AdapterError("attachment entry missing id")
        enclosure = atom.links_by_rel(entry).get("enclosure")
        attachments.append(
            Attachment(
                uuid=uuid,
                filename=atom.find_text(entry, "atom:title"),
                content_type=(enclosure.get("type") if enclosure is not None else None),
                enclosure_href=(enclosure.get("href") if enclosure is not None else None),
                author=_author_name(entry),
                created=atom.find_text(entry, "atom:published"),
            )
        )
    return attachments


def parse_tags_feed(data: bytes | str) -> list[Tag]:
    """Wiki tags are **feed-level**
    `<category term=.. label=..>` elements on the `<feed>` itself -- with no
    `scheme`, and NOT one `<entry>` per tag. The `term` is the tag name. (An
    entry's own `<category>` carries a `sn/type` scheme instead; the
    scheme-less filter keeps a stray type marker from being read as a tag.)"""
    root = atom.parse_xml(data, expected_root="feed")
    return [Tag(term=term) for term, scheme in atom.categories(root) if term and not scheme]
