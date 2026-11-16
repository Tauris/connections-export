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
from collections.abc import Callable, Sequence
from urllib.parse import parse_qs, unquote, urldefrag, urlsplit

import lxml.etree
import lxml.html
from pydantic import BaseModel, ConfigDict

from connections_export.adapters import atom
from connections_export.adapters.model import Attachment, ForumReply, ForumTopic, ReplyNode

_FLAGS_SCHEME = "http://www.ibm.com/xmlns/prod/sn/flags"
_NODE_ID_RE = re.compile(r"[?&]nodeId=([^&#\"']+)", re.IGNORECASE)
#: A forum download, in either form a deployment writes it:
#: ``/forums/ajax/download?nodeId=...`` or ``/forums/ajax/download/<post>/<file>/<name>``.
_DOWNLOAD_RE = re.compile(r"/(?:ajax/)?download(?:/|\?)", re.IGNORECASE)


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


def _is_download(href: str | None) -> bool:
    return bool(href) and bool(_DOWNLOAD_RE.search(href))


def _node_id(href: str | None) -> str | None:
    match = _NODE_ID_RE.search(href or "")
    return unquote(match.group(1)) if match else None


def attachment_key(href: str | None) -> str:
    """One attachment, however its download URL is spelled: the node id when
    there is one, otherwise the URL without its fragment."""
    node = _node_id(href)
    if node:
        return f"node:{node}"
    # Host-free: a page links relatively (``/forums/ajax/download/...``) to the
    # file its feed names by a full address, and both are one attachment.
    parts = urlsplit(href or "")
    path = unquote(parts.path).rstrip("/")
    return path + (f"?{parts.query}" if parts.query else "")


def _filename_from_href(href: str | None) -> str | None:
    """The name a download URL itself carries: a ``name``/``fileName`` query
    value, or a last path segment that looks like a file (``.../report.docx``)."""
    parts = urlsplit(href or "")
    query = parse_qs(parts.query)
    for key in ("fileName", "filename", "name", "title"):
        value = (query.get(key) or [""])[0].strip()
        if value and value.lower() != "blob":
            return value
    segment = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    if "." in segment and not segment.lower().startswith("download"):
        return segment
    return None


def _usable_name(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if value and value.lower() != "blob" else None


def _attachments(entry) -> list[Attachment]:
    """Read the downloads a topic/reply entry links to.

    A deployment spells these two ways: a link carrying ``fid``/``nodeId``
    attributes (a pasted image is one, named ``blob``), and a plain
    ``rel="enclosure"`` link to ``/forums/ajax/download/...`` whose name is in
    ``title`` -- an uploaded document. Both are attachments; reading only the
    first lost every uploaded document.
    """
    out: list[Attachment] = []
    seen: set[str] = set()
    for link in atom.findall(entry, "atom:link"):
        href = link.get("href")
        if not href:
            continue
        node_id = link.get("fid") or link.get("nodeId")
        rel = link.get("rel")
        # An enclosure is an attachment by definition; any other link only
        # when it is a download and not navigation (alternate/self/edit).
        if rel != "enclosure" and not (
            _is_download(href) and (node_id or rel in (None, "related"))
        ):
            continue
        key = attachment_key(href)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Attachment(
                uuid=node_id or _node_id(href) or key,
                filename=_usable_name(link.get("name"))
                or _usable_name(link.get("title"))
                or _filename_from_href(href),
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


def _label(element) -> str | None:
    """The name a page shows for a download link: what hovering shows
    (``title``), a ``download``/``data-filename`` attribute, the enclosing
    ``dfAttachment`` row's ``fileName``, or the link text."""
    for value in (
        element.get("title"),
        element.get("download"),
        element.get("data-filename"),
    ):
        if _usable_name(value):
            return unquote(value.strip())
    for row in element.iterancestors():
        if _usable_name(row.get("fileName")):
            return unquote(row.get("fileName").strip())
    # Link text is a name only when it looks like one: "Download" or
    # "Herunterladen" is the link's purpose, and the address names the file.
    text = " ".join(" ".join(element.itertext()).split())
    return _usable_name(text) if "." in text else None


def _marks(element, post_id: str) -> bool:
    """Whether an element names a post as its own -- in an identifying
    attribute, never in a link (a reply links to the post it answers)."""
    return any(
        post_id in (value or "")
        for name, value in element.attrib.items()
        if name in ("id", "uuid", "name") or name.startswith("data-")
    )


def _scope(tree, post_id: str, other_ids: Sequence[str]):
    """The part of a page that belongs to one post: the element naming the
    post, widened for as long as that takes in no other post."""
    others = [o for o in other_ids if o and o != post_id]
    elements = [e for e in tree.iter() if isinstance(e.tag, str)]
    taken: set = set()
    for element in elements:
        if any(_marks(element, o) for o in others):
            taken.add(element)
            taken.update(element.iterancestors())
    for element in elements:
        if not _marks(element, post_id):
            continue
        scope = element
        while (parent := scope.getparent()) is not None and parent not in taken:
            scope = parent
        return scope
    return None


def page_attachments(
    data: bytes | str, *, post_id: str | None = None, other_ids: Sequence[str] = ()
) -> list[Attachment]:
    """Forum downloads linked from a post's rendered page.

    Every link to a forum download counts, named as the page names it (see
    `_label`). With `post_id`, only the part of the page belonging to that
    post is read (see `_scope`); when the page does not say which post is
    which, `post_id=None` reads all of it.
    """
    try:
        tree = lxml.html.fromstring(data)
    except (TypeError, ValueError, lxml.etree.ParserError):
        return []
    root = tree
    if post_id is not None:
        root = _scope(tree, post_id, other_ids)
        if root is None:
            return []
    out: list[Attachment] = []
    seen: set[str] = set()
    for element in root.iter("a"):
        href = element.get("href") or ""
        in_list = any(
            " dfAttachment " in f" {(a.get('class') or '')} " for a in element.iterancestors()
        )
        if not _is_download(href) or not (in_list or "/forums/" in href or _node_id(href)):
            continue
        key = attachment_key(href)
        if key in seen:
            continue
        seen.add(key)
        mime = element.xpath("string(.//span[contains(@class, 'dfMimeIcon')]/@mimeType)")
        out.append(
            Attachment(
                uuid=_node_id(href) or key,
                filename=_label(element) or _filename_from_href(href),
                content_type=mime or None,
                enclosure_href=href,
            )
        )
    return out


def merge_page_attachments(entries: Sequence, read_page: Callable[[str], bytes | None]) -> None:
    """Add what each post's rendered page lists to the attachments its feed
    entry names, and fill in names the feed leaves as ``blob``.

    The feed is not the whole story: a reply's uploaded document can be on
    its page and nowhere in the feed. `read_page` returns a page's bytes, or
    None when it is not available; each page is read once however many
    posts point at it. Posts sharing one page each get only their own part
    of it; what the page does not attribute to any of them goes to the first
    (the topic, when it is one of them) rather than being dropped.
    """
    pages: dict[str, list] = {}
    for entry in entries:
        url = urldefrag(getattr(entry, "alternate_url", None) or "")[0]
        if url:
            pages.setdefault(url, []).append(entry)
    held = {attachment_key(a.enclosure_href) for e in entries for a in e.attachments}
    for url, sharing in pages.items():
        html = read_page(url)
        if not html:
            continue
        ids = [e.id for e in sharing]
        scoped: dict[str, str] = {}
        if len(sharing) > 1:
            for entry in sharing:
                for attachment in page_attachments(html, post_id=entry.id, other_ids=ids):
                    scoped.setdefault(attachment_key(attachment.enclosure_href), entry.id)
        listed = page_attachments(html)
        names = {
            f"node:{node}": name for node, name in attachment_filenames_from_html(html).items()
        }
        names |= {attachment_key(a.enclosure_href): a.filename for a in listed if a.filename}
        for entry in sharing:
            for attachment in entry.attachments:
                if not attachment.filename:
                    attachment.filename = names.get(attachment_key(attachment.enclosure_href))
        by_id = {entry.id: entry for entry in sharing}
        for attachment in listed:
            key = attachment_key(attachment.enclosure_href)
            if key in held:
                continue  # the feed already gave it to its post
            held.add(key)
            by_id[_owner(attachment, sharing, scoped.get(key))].attachments.append(attachment)


def _owner(attachment: Attachment, sharing: Sequence, scoped: str | None) -> str:
    """Which of the posts sharing a page an attachment found on it belongs
    to: the post its download address names (``/download/<post>/<file>/...``),
    else the post whose part of the page holds it, else the first -- the
    topic, when it shares the page -- rather than none."""
    path = unquote(urlsplit(attachment.enclosure_href or "").path)
    for entry in sharing:
        if entry.id and f"/{entry.id}/" in path:
            return entry.id
    return scoped or sharing[0].id


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
