"""Forums parsers, type discrimination, flat-feed reply-tree
reconstruction, and URL construction.
The thick, app-specific layer on top of `atom.py`'s shared Atom core --
same pattern as `wikis.py`/`blogs.py`.

Lower confidence than wikis.py: the reference doc gives field/attribute lists for
Forums entries but no literal XML sample for either a topic or a reply
entry, so every fixture this module's tests use is not documented, not a
byte-verbatim anchor. There is no fakeserver/round-trip for Forums yet
-- these fixtures are the validation.

**The TRAP**: a forum *topic* entry also carries `<thr:in-reply-to>`
(its `ref` names the parent forum, not a parent reply/topic). Entity
type is read exclusively from `entity_type` below (the `sn/type`
category term) -- nothing in this module ever branches on whether
`thr:in-reply-to` is present to decide topic vs. reply.

**Resource addressing is query-param, not path-segment** (contrast
`wikis.py`'s handle-in-path builders): `/forums/atom/...?forumUuid=|
topicUuid=|replyUuid=`. The reference doc explicitly warns against
hardcoding these -- "do not construct URLs to Forums resources;
instead write code to follow the links from the user's service
document" -- so, as in wikis.py, these builders are a fallback for
when no link is available to follow, not the primary path.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import lxml.etree
import lxml.html
from pydantic import BaseModel, ConfigDict

from connections_export.adapters import atom
from connections_export.adapters.model import Attachment, ForumReply, ForumTopic, ReplyNode

_FLAGS_SCHEME = "http://www.ibm.com/xmlns/prod/sn/flags"
_NODE_ID_RE = re.compile(r"[?&]nodeId=([^&#\"']+)", re.IGNORECASE)


class ForumRef(BaseModel):
    """One entry from the "all forums" list feed (`/forums/atom/forums`).

    # INFERRED [Stage 2]: like `BlogRef` (blogs' list-container), the
    reference documents the forums-list *path* but gives no
    field list for the forum *container* resource -- only for topic and
    reply entries. Modeled here on the cross-app "Genuinely shared"
    fields (id, title, self link) the reference confirms hold for all
    three apps. Defined in this adapter module (not `adapters.model`,
    where `BlogRef`/`ForumTopic`/... live) deliberately: this is the
    single additive Forums-adapter gap `parse_forums_feed` fills, kept
    self-contained to that one function's contract. The `self_url` is
    the forum's own topics feed (`/forums/atom/topics?forumUuid=<uuid>`),
    exactly the URL a crawler follows next -- so traversal never has to
    reconstruct it from the bare uuid.
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str
    title: str | None = None
    self_url: str | None = None


# --------------------------------------------------------------------
# URL construction (fallback only -- see module docstring)
# --------------------------------------------------------------------
# Forums addresses a forum by query parameter rather than in the path.


def forums_list_url(*, base_url: str) -> str:
    """`GET, POST /forums/atom/forums`."""
    return f"{base_url}/forums/atom/forums"


def topics_url(*, base_url: str, forum_uuid: str, since: str | None = None) -> str:
    """`GET /forums/atom/topics?forumUuid={uuid}`
    -- the forum overview page links this feed (topics only).

    `since` (epoch MILLISECONDS) asks only for topics
    changed after that moment. Milliseconds, not seconds -- the different
    format from Blogs is the deployment's, not ours, and getting it wrong
    returns the whole forum while looking like it worked."""
    url = f"{base_url}/forums/atom/topics?forumUuid={forum_uuid}"
    if since:
        from connections_export.adapters import profiles  # noqa: PLC0415
        from connections_export.adapters.since import encode_for  # noqa: PLC0415

        return f"{url}&since={encode_for(profiles.FORUMS, since)}&sortBy=modified"
    return url


def entries_url(*, base_url: str, forum_uuid: str) -> str:
    """`GET /forums/atom/entries?forumUuid={uuid}`
    -- the forum overview page also links this feed, which unlike `topics`
    carries the entries *with their comments*."""
    return f"{base_url}/forums/atom/entries?forumUuid={forum_uuid}"


def topic_url(*, base_url: str, topic_uuid: str) -> str:
    """`GET|PUT|DELETE /forums/atom/topic?topicUuid={uuid}`."""
    return f"{base_url}/forums/atom/topic?topicUuid={topic_uuid}"


def replies_url(*, base_url: str, topic_uuid: str) -> str:
    """`GET /forums/atom/replies?topicUuid={uuid}`."""
    return f"{base_url}/forums/atom/replies?topicUuid={topic_uuid}"


def reply_url(*, base_url: str, reply_uuid: str) -> str:
    """`GET|PUT|DELETE /forums/atom/reply?replyUuid={uuid}`."""
    return f"{base_url}/forums/atom/reply?replyUuid={reply_uuid}"


# --------------------------------------------------------------------
# Type discrimination -- the anti-TRAP mechanism
# --------------------------------------------------------------------


def entity_type(entry) -> str | None:
    """The `sn/type` category term (`forum-topic` / `forum-reply` /
    `recommendation`) -- the ONLY source of entity type. Never infer
    type from the presence of `<thr:in-reply-to>`: a *topic* entry also
    carries one (its `ref` names the parent forum) -- see the module
    docstring's TRAP note and Forums "Threading". `atom.category_term` already matches this
    scheme by URI (the same helper Wikis uses for its own type
    category), so no Forums-specific matching logic is needed here."""
    return atom.category_term(entry)


# --------------------------------------------------------------------
# Small internal helpers
# --------------------------------------------------------------------


#: `sn/flags` terms that are per-viewer UI state or metrics, not export-worthy
#: status -- a real deployment carries e.g. `NotRecommendedByCurrentUser` (does
#: *this viewer* recommend it) and `ThreadRecommendationCount` (a count) in the
#: flags scheme; neither belongs in an exported thread. Dropped by substring so
#: variants are caught. The meaningful flags (pinned/locked/question/answered/
#: answer) are kept.
_FLAG_NOISE = ("recommend", "count")


def _flags(entry) -> set[str]:
    """`sn/flags` category terms (docs/reference/
    hcl-blogs-forums-api.md, Forums "Identifiers and markers"):
    pinned/locked/question/answered on topics, answer on replies -- minus the
    per-viewer/metric noise (`_FLAG_NOISE`)."""
    return {
        term
        for term, scheme in atom.categories(entry)
        if scheme == _FLAGS_SCHEME and term and not any(n in term.lower() for n in _FLAG_NOISE)
    }


def _normalized_ref(reply_to: dict[str, str | None] | None, key: str) -> str | None:
    """`reply_to[key]` (an `in_reply_to` attribute), run through
    `entry_uuid` so it's directly comparable to sibling entries'
    `.id` -- also `entry_uuid`-stripped -- which is what makes it
    usable as a tree pointer at all."""
    if reply_to is None:
        return None
    value = reply_to.get(key)
    if not value:
        return None
    return atom.entry_uuid(value)


def _attachments(entry) -> list[Attachment]:
    """Read HCL forum download links embedded in topic/reply entries."""
    out: list[Attachment] = []
    for link in atom.findall(entry, "atom:link"):
        href = link.get("href")
        node_id = link.get("fid") or link.get("nodeId")
        if not href or not node_id or "/download/" not in href and "/ajax/download" not in href:
            continue
        out.append(
            Attachment(
                uuid=node_id,
                filename=None if (link.get("name") or "").lower() == "blob" else link.get("name"),
                content_type=link.get("type"),
                enclosure_href=href,
            )
        )
    return out


def attachment_filenames_from_html(data: bytes | str) -> dict[str, str]:
    """Find displayed filenames for forum download nodes in rendered HTML."""
    try:
        tree = lxml.html.fromstring(data)
    except (TypeError, ValueError, lxml.etree.ParserError):
        return {}
    found: dict[str, str] = {}
    for element in tree.xpath("//*[@href or @src]"):
        href = element.get("href") or element.get("src") or ""
        match = _NODE_ID_RE.search(href)
        if not match:
            continue
        label = (
            element.get("download")
            or element.get("data-filename")
            or element.get("title")
            or element.get("alt")
            or ""
        ).strip()
        if not label and element.tag.lower() == "a":
            label = " ".join(element.itertext()).strip()
        if not label:
            parent = element.getparent()
            if parent is not None and parent.tag.lower() == "a":
                label = " ".join(parent.itertext()).strip()
        if label and label.lower() != "blob":
            found[match.group(1)] = label
    return found


def attachments_from_html(data: bytes | str) -> list[Attachment]:
    """Parse the deployment's rendered ``dfAttachments`` list, including filenames."""
    try:
        tree = lxml.html.fromstring(data)
    except (TypeError, ValueError, lxml.etree.ParserError):
        return []
    out: list[Attachment] = []
    for row in tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' dfAttachments ')]"
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' dfAttachment ')]"
    ):
        link = row.xpath(".//a[@href][1]")
        if not link:
            continue
        anchor = link[0]
        href = anchor.get("href") or ""
        filename = (row.get("fileName") or "").strip() or " ".join(anchor.itertext()).strip()
        if not filename:
            continue
        owner = row.xpath("ancestor::*[@uuid][1]")
        uuid = (owner[0].get("uuid") if owner else None) or filename
        out.append(
            Attachment(
                uuid=uuid,
                filename=unquote(filename),
                content_type=(
                    row.xpath("string(.//span[contains(@class, 'dfMimeIcon')]/@mimeType)") or None
                ),
                enclosure_href=href,
            )
        )
    return out


# --------------------------------------------------------------------
# Forums list -- mirrors `blogs.parse_blogs_feed`
# --------------------------------------------------------------------


def parse_forums_feed(data: bytes | str) -> list[ForumRef]:
    """Parse the "all forums" list feed (`/forums/atom/forums`) into
    `ForumRef`s -- the Forums counterpart to `blogs.parse_blogs_feed`.
    # INFERRED: no field list is documented for the forum *container*;
    only the generic cross-app Atom fields are read (id required; title
    and the `rel="self"` link -- that forum's topics feed -- degrade to
    None). Required: `<id>`."""
    root = atom.parse_xml(data, expected_root="feed")
    refs: list[ForumRef] = []
    for entry in atom.entries(root):
        uuid, _raw = atom.require_entry_id(entry, what="forum entry")
        refs.append(
            ForumRef(
                uuid=uuid,
                title=atom.find_text(entry, "atom:title"),
                self_url=atom.link_href(entry, "self"),
            )
        )
    return refs


# --------------------------------------------------------------------
# Topics feed
# --------------------------------------------------------------------


def parse_topics_feed(data: bytes | str) -> list[ForumTopic]:
    """Parse a topics feed into `ForumTopic`s (Topic is
    typed by category, not by reply reference). Every topic entry's
    `thr:in-reply-to` (present per the TRAP) is read here only for its
    *data* -- the parent forum's uuid, recorded as `forum_uuid` -- never
    for typing. Required: `<id>`; everything else degrades to
    None/empty rather than raising."""
    root = atom.parse_xml(data, expected_root="feed")
    topics: list[ForumTopic] = []
    for entry in atom.entries(root):
        if entity_type(entry) == "recommendation":
            continue  # a "like", not a topic -- see parse_replies_feed
        uuid, raw_id = atom.require_entry_id(entry, what="forum entry")
        reply_to = atom.in_reply_to(entry)
        topics.append(
            ForumTopic(
                id=uuid,
                forum_uuid=_normalized_ref(reply_to, "ref"),
                title=atom.find_text(entry, "atom:title"),
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                content_html=atom.content_html(entry),
                published=atom.find_text(entry, "atom:published"),
                flags=_flags(entry),
                tags=atom.bare_category_terms(entry),
                attachments=_attachments(entry),
                provenance=raw_id,
                alternate_url=atom.link_href(entry, "alternate"),
            )
        )
    return topics


def parse_single_topic(data: bytes | str) -> list[ForumTopic]:
    """Parse a single-topic document (`/forums/atom/topic?topicUuid=`)
    into a one-element list. The endpoint returns a bare `<entry>`
    rather than a `<feed>`, so `parse_topics_feed` cannot be used
    directly. Falls back gracefully: if the document IS a feed (e.g.
    the server wraps it), delegates to `parse_topics_feed`."""
    root = atom.parse_xml(data)
    if root.tag.endswith("}feed") or root.tag == "feed":
        return parse_topics_feed(data)
    # Bare <entry> document.
    try:
        uuid, raw_id = atom.require_entry_id(root, what="forum entry")
    except Exception:  # noqa: BLE001
        return []
    reply_to = atom.in_reply_to(root)
    return [
        ForumTopic(
            id=uuid,
            forum_uuid=_normalized_ref(reply_to, "ref"),
            title=atom.find_text(root, "atom:title"),
            author=atom.author_name(root),
            author_userid=atom.author_userid(root),
            contributors=atom.contributor_names(root),
            content_html=atom.content_html(root),
            published=atom.find_text(root, "atom:published"),
            flags=_flags(root),
            tags=atom.bare_category_terms(root),
            attachments=_attachments(root),
            provenance=raw_id,
            alternate_url=atom.link_href(root, "alternate"),
        )
    ]


# --------------------------------------------------------------------
# Replies feed -- FLAT
# --------------------------------------------------------------------


def parse_replies_feed(data: bytes | str) -> list[ForumReply]:
    """Parse the FLAT replies feed into `ForumReply`s (Deep
    reply tree from a flat feed). Each reply's `ref` (immediate
    parent -- a topic or another reply) and `source_topic` (top-level
    topic) are recorded exactly as read; nesting is not reconstructed
    here -- see `build_reply_tree`. Required: `<id>`."""
    root = atom.parse_xml(data, expected_root="feed")
    replies: list[ForumReply] = []
    for entry in atom.entries(root):
        if entity_type(entry) == "recommendation":
            # A "like", not a reply. It carries the liking user as its author,
            # so parsing it as a reply would (a) pollute the thread and (b) make
            # the author filter count a like as authorship. Skip it (typed by
            # sn/type, per the module's anti-TRAP rule).
            continue
        uuid, _raw_id = atom.require_entry_id(entry, what="forum entry")
        reply_to = atom.in_reply_to(entry)
        replies.append(
            ForumReply(
                id=uuid,
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                content_html=atom.content_html(entry),
                published=atom.find_text(entry, "atom:published"),
                ref=_normalized_ref(reply_to, "ref"),
                source_topic=_normalized_ref(reply_to, "source"),
                flags=_flags(entry),
                attachments=_attachments(entry),
                alternate_url=atom.link_href(entry, "alternate"),
            )
        )
    return replies


# --------------------------------------------------------------------
# Flat -> tree reconstruction (the forums-specific
# "reconstruct the tree" the shared abstraction deliberately does not
# provide -- "Threading
# models": "a shared 'get children' abstraction works; a shared
# 'rebuild the tree' abstraction does not.")
# --------------------------------------------------------------------


def build_reply_tree(replies: list[ForumReply]) -> list[ReplyNode]:
    """Reconstruct an arbitrary-depth reply tree from one flat pass
    over `replies`: each reply's `ref` names its immediate parent --
    another reply, or (for a top-level reply) the topic itself, whose
    uuid never appears among the replies' own ids and so naturally
    yields a root.

    Cycle-safe: the documented shape never produces a `ref` cycle, but
    malformed/adversarial input could. A per-branch `ancestors` set
    stops recursion the moment a `ref` chain would revisit a node
    already on its own path; a second pass then promotes any reply
    never reached from a natural root (e.g. every reply in a closed
    loop, where no `ref` points outside the reply set at all) to its
    own root, so a cycle breaks the tree apart rather than silently
    dropping data or hanging.
    """
    by_id = {r.id: r for r in replies}
    children_by_ref: dict[str, list[str]] = {}
    for r in replies:
        if r.ref is not None:
            children_by_ref.setdefault(r.ref, []).append(r.id)

    visited: set[str] = set()

    def build_node(reply_id: str, ancestors: frozenset[str]) -> ReplyNode:
        visited.add(reply_id)
        reply = by_id[reply_id]
        next_ancestors = ancestors | {reply_id}
        children = [
            build_node(child_id, next_ancestors)
            for child_id in children_by_ref.get(reply_id, [])
            if child_id not in ancestors
        ]
        return ReplyNode(reply=reply, children=children)

    roots = [
        build_node(r.id, frozenset())
        for r in replies
        if r.ref not in by_id  # ref points at the topic (or is absent/unknown) -> top-level
    ]

    # Cycle fallback: anything a natural root never reached.
    for r in replies:
        if r.id not in visited:
            roots.append(build_node(r.id, frozenset()))

    return roots
