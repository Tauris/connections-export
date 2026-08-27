"""Assembling Forums: list feed -> per forum: topics -> per topic: flat
replies, rebuilt into a tree.

The flat replies feed is turned back into a tree by `build_reply_tree` and
then flattened into `(reply_ids, replies)` -- feed order preserved throughout,
so a thread reconstructs exactly as it was posted.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlsplit

from connections_export.adapters import atom
from connections_export.adapters.forums import (
    build_reply_tree,
    parse_forums_feed,
    parse_replies_feed,
    parse_topics_feed,
    replies_url,
)
from connections_export.adapters.forums import (
    topic_url as _forum_topic_url,
)
from connections_export.adapters.model import (
    ReplyNode,
)
from connections_export.derive.apps._shared import (
    _FORUMS_LIST_TAIL,
    _attachment_filename,
    _base_url_before,
    _community_title,
    _community_uuid,
    _derive_attachments,
    _feed_note,
)
from connections_export.derive.assets import resolve_body_assets, strip_avatars
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.links import resolve_body_links
from connections_export.derive.model import (
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    Provenance,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _flatten_reply_tree(
    nodes: list[ReplyNode],
    *,
    base_url: str,
    hcl_hosts: Iterable[str],
    index: ArchiveIndex,
) -> tuple[list[str], dict[str, DerivedForumReply]]:
    """Flatten `build_reply_tree`'s nested `ReplyNode`s into
    `(reply_ids, replies)`: `reply_ids` are the ordered top-level reply
    ids (the tree roots -- replies whose parent is the topic itself);
    `replies` is every reply, at any depth, keyed by id, each carrying
    the ordered `child_ids` of its own subtree. Feed order is preserved
    throughout (`build_reply_tree` builds both roots and children in flat-
    feed order)."""
    replies: dict[str, DerivedForumReply] = {}

    def visit(node: ReplyNode) -> None:
        reply = node.reply
        body_html = strip_avatars(reply.content_html or "")
        reply_assets = resolve_body_assets(
            body_html, page_url=base_url, base_url=base_url, hcl_hosts=hcl_hosts, index=index
        )
        for asset in reply_assets:
            asset.filename = _attachment_filename(asset, reply.attachments)
        replies[reply.id] = DerivedForumReply(
            id=reply.id,
            author=reply.author,
            author_userid=reply.author_userid,
            contributors=list(reply.contributors),
            content_html=body_html,
            created=reply.published,
            flags=sorted(reply.flags),
            child_ids=[child.reply.id for child in node.children],
            assets=reply_assets,
            links=resolve_body_links(
                body_html,
                page_url=base_url,
                self_page_id=reply.id,
                base_url=base_url,
                hcl_hosts=hcl_hosts,
                page_lookup={},
            ),
            attachments=_derive_attachments(
                reply.attachments, base_url=base_url, hcl_hosts=hcl_hosts, index=index
            ),
            # A reply carries no raw LSID (the flat-replies adapter keeps
            # only the bare uuid), so provenance holds that uuid -- the
            # best HCL id available for a reply.
            provenance=Provenance(hcl_id=reply.id),
        )
        for child in node.children:
            visit(child)

    reply_ids = [node.reply.id for node in nodes]
    for node in nodes:
        visit(node)
    return reply_ids, replies


def _assemble_topic(
    index: ArchiveIndex,
    topic,
    *,
    forums_base_url: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> DerivedForumTopic:
    notes: list[str] = []
    # HCL-ism confined to provenance: the topic's raw `<id>` LSID text.
    provenance = Provenance(hcl_id=topic.provenance)

    body_html = strip_avatars(topic.content_html or "")
    resolved_assets = resolve_body_assets(
        body_html, page_url=base_url, base_url=base_url, hcl_hosts=hcl_hosts, index=index
    )
    for asset in resolved_assets:
        asset.filename = _attachment_filename(asset, topic.attachments)
    resolved_links = resolve_body_links(
        body_html,
        page_url=base_url,
        self_page_id=topic.id,
        base_url=base_url,
        hcl_hosts=hcl_hosts,
        page_lookup={},
    )

    # No replies link on a topic entry (Forums addressing is query-param,
    # not link-following); build the flat replies feed URL from the topic
    # uuid, exactly as `crawl_forums` does.
    topic_replies_url = replies_url(base_url=forums_base_url, topic_uuid=topic.id)
    replies_items = index.paginate(topic_replies_url, parse_replies_feed, page_size=page_size)
    note = _feed_note(index, "replies", topic_replies_url, page_size=page_size)
    if note:
        notes.append(note)
    reply_ids, replies = _flatten_reply_tree(
        build_reply_tree(replies_items), base_url=base_url, hcl_hosts=hcl_hosts, index=index
    )

    if notes:
        provenance.note = "; ".join(notes)

    # Some deployments record a topic's original poster only as a
    # `<contributor>`, never an `<author>` (the reverse of the usual "author =
    # original creator" convention this adapter otherwise relies on) -- an
    # empty author would otherwise make the reader/PDF show the topic's
    # starting post with no byline at all, while every reply underneath it
    # does have one. Fall back to the first contributor's name so the topic
    # is never silently author-less when *some* name is available.
    topic_author = topic.author
    if topic_author is None and topic.contributors:
        topic_author = topic.contributors[0]

    return DerivedForumTopic(
        id=topic.id,
        title=topic.title,
        author=topic_author,
        author_userid=topic.author_userid,
        contributors=list(topic.contributors),
        content_html=body_html,
        created=topic.published,
        flags=sorted(topic.flags),
        tags=list(getattr(topic, "tags", [])),
        reply_ids=reply_ids,
        replies=replies,
        assets=resolved_assets,
        links=resolved_links,
        attachments=_derive_attachments(
            topic.attachments, base_url=base_url, hcl_hosts=hcl_hosts, index=index
        ),
        provenance=provenance,
        alternate_url=getattr(topic, "alternate_url", None),
    )


def _topic_was_crawled(
    index: ArchiveIndex, topic_id: str, forums_base_url: str, page_size: int
) -> bool:
    """Whether this topic's reply thread was actually fetched during the crawl
    -- an `ok` first page, or an attempted-but-failed one. A topic that only
    ever appeared *inline* in the topics-feed listing (never descended into,
    e.g. beyond a Preview limit) returns False and is kept out of the model,
    so "Preview N" means N crawled topics, not every topic the feed page
    happened to enumerate. Mirrors the replies-feed URL `_assemble_topic`
    builds, so the two never disagree."""
    url = replies_url(base_url=forums_base_url, topic_uuid=topic_id)
    if index.has_feed_page(url, page_size=page_size):
        return True
    return index.first_page_failure(url, page_size=page_size) is not None


def _assemble_forum(
    index: ArchiveIndex,
    forumref,
    *,
    forums_base_url: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> DerivedForum | None:
    topics_feed_url = forumref.self_url
    community_uuid: str | None = None
    community_title: str | None = None
    if not topics_feed_url:
        # No topics feed to follow (defensive) -- an empty-but-present
        # forum, mirroring `_assemble_wiki`'s empty-wiki return.
        return DerivedForum(id=forumref.uuid, title=forumref.title)
    # Listed but never crawled (scoped out -- a "one forum"/"single thread"
    # import): drop it rather than emit an empty container. See `_assemble_blog`.
    if not index.has_feed_page(topics_feed_url, page_size=page_size):
        return None

    # The forum's own name: the forums-list feed gives it when crawled, but a
    # scoped ("one forum" / recovered-from-a-thread) import skips that list, so
    # fall back to the topics feed's own <title> (the forum name) -- the same
    # trick blogs use -- rather than showing the bare uuid.
    title = forumref.title
    from connections_export.adapters.atom import feed_title  # noqa: PLC0415
    from connections_export.crawler.engine import add_query  # noqa: PLC0415

    # Always read the topics feed's first page, even when the forum's name is
    # already known: the community markers live there, and gating this whole
    # block on `title is None` meant a forum discovered through the forums-list
    # feed -- the ordinary path -- never learned which community owned it.
    for page_num in (1, 0):
        first = index.get(add_query(topics_feed_url, ps=page_size, page=page_num))
        if first is not None:
            community_uuid = _community_uuid(first.content)
            community_title = _community_title(first.content)
            # The forum's own name: the forums-list feed gives it when crawled,
            # but a scoped ("one forum" / recovered-from-a-thread) import skips
            # that list, so fall back to the topics feed's own <title> rather
            # than showing the bare uuid.
            if title is None:
                title = feed_title(first.content) or title
            break

    topics = index.paginate(topics_feed_url, parse_topics_feed, page_size=page_size)

    derived_topics: dict[str, DerivedForumTopic] = {}
    topic_ids: list[str] = []
    for topic in topics:
        # A single topics-feed *page* lists many topics inline (bodies and
        # all), but a preview-limited crawl only ever descends into the first
        # N of them (fetching their reply threads). The rest appear in the
        # feed for free yet were never really crawled -- including them would
        # make "Preview N" silently yield far more than N topics (the reported
        # TOC/PDF surprise). Gate on whether this topic's replies feed was
        # *attempted* at all: an `ok` page, or a recorded failure (kept with a
        # note, never dropped). Never-touched == scoped out -> skip.
        if not _topic_was_crawled(index, topic.id, forums_base_url, page_size):
            continue
        derived_topics[topic.id] = _assemble_topic(
            index,
            topic,
            forums_base_url=forums_base_url,
            page_size=page_size,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
        )
        topic_ids.append(topic.id)

    return DerivedForum(
        id=forumref.uuid,
        title=title,
        community_uuid=community_uuid,
        community_title=community_title,
        topic_ids=topic_ids,
        topics=derived_topics,
    )


def _discover_direct_forum_topics(index: ArchiveIndex) -> list[str]:
    """Fallback for archives crawled via a single-topic fast path (no forums
    list feed): returns the distinct topic UUIDs that have successfully-archived
    replies feeds (`/forums/atom/replies?topicUuid={uuid}`)."""
    seen: list[str] = []
    for url in index.ok_urls():
        split = urlsplit(url)
        if not split.path.endswith("/forums/atom/replies"):
            continue
        qs = dict(parse_qsl(split.query))
        uuid = qs.get("topicUuid")
        if uuid and uuid not in seen:
            seen.append(uuid)
    return seen


def _discover_archived_topic_feeds(index: ArchiveIndex) -> list[tuple[str, str]]:
    """Find forum UUIDs from archived topics feeds
    (`/forums/atom/topics?forumUuid={uuid}`), returning `(base_url,
    forum_uuid)` pairs. These are present when `crawl_forums` widened
    from a threadTopic URL and fetched the community forum's full topics
    feed -- but the forum may not appear in the general forums list.
    Used to assemble the forum directly from its archived topics feed."""
    seen: dict[str, str] = {}  # forum_uuid -> base_url
    for url in index.ok_urls():
        split = urlsplit(url)
        if not split.path.endswith("/forums/atom/topics"):
            continue
        qs = dict(parse_qsl(split.query))
        forum_uuid = qs.get("forumUuid")
        if not forum_uuid:
            continue
        # Only include when this is a per-forum feed (has forumUuid param).
        if forum_uuid not in seen:
            seen[forum_uuid] = _base_url_before(url, "/forums/")
    return list(seen.items())


def _assemble_direct_topics(
    index: ArchiveIndex,
    topic_uuids: list[str],
    *,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> list[DerivedForum]:
    """Build `DerivedForum`s for topics reached via the direct-topic crawl
    path (each topic's own `/forums/atom/topic?topicUuid=` + its replies
    feed were fetched, but the forum's own topics-list feed never was --
    `direct_topic_only`, e.g. a single-thread import, or several search-
    seeded threads across possibly-different forums).

    Grouped by each topic's own `forum_uuid` (read off its `thr:in-reply-to`,
    same as every other topic) -- NOT one synthetic forum per topic -- so
    multiple threads from the SAME real forum land under one shared
    container instead of each exploding into its own confusingly-labelled
    single-entry "forum" (the reader showed "Forum · <topic title>" directly
    above that same topic's title -- reads as a duplicated entry, when it was
    really just an unnamed one-topic group mislabelled with the topic's own
    title). Falls back to one `topic-{uuid}` standalone group per topic only
    when a topic's own forum_uuid genuinely isn't known.

    For a group with a real forum_uuid, tries to recover the forum's own
    name from its topics feed's `<title>` -- present when a widened crawl
    (or the crawler's lightweight per-forum title prefetch, `crawl.py`'s
    `crawl_forums`) archived that feed's first page -- the same trick
    `_assemble_forum` uses, so a "capture everything I authored" run that
    never crawled the forum's topic list still gets a readable name instead
    of the topic's own title standing in for it."""
    from connections_export.adapters.forums import (  # noqa: PLC0415
        parse_single_topic as _pst,
    )
    from connections_export.adapters.forums import topics_url as _topics_url  # noqa: PLC0415
    from connections_export.adapters.model import ForumTopic as _FT  # noqa: PLC0415
    from connections_export.crawler.engine import add_query  # noqa: PLC0415

    # Pass 1: fetch each topic's own metadata (title, author, forum_uuid) if
    # archived -- needed up front so topics can be grouped by forum_uuid.
    parsed_topics: dict[str, object] = {}
    for topic_uuid in topic_uuids:
        t_url = _forum_topic_url(base_url=base_url, topic_uuid=topic_uuid)
        t_response = index.get(t_url)
        topics_parsed: list = []
        if t_response is not None:
            try:
                topics_parsed = _pst(t_response.content)
            except Exception:  # noqa: BLE001
                pass
        if not topics_parsed:
            # Fall back to constructing a minimal stub from the UUID alone.
            topics_parsed = [_FT(id=topic_uuid, title=None)]
        parsed_topics[topic_uuid] = topics_parsed[0]

    # Pass 2: group by forum_uuid (a standalone "topic-{uuid}" bucket only
    # when the topic's own forum_uuid is unknown).
    groups: dict[str, list[str]] = {}
    for topic_uuid, topic in parsed_topics.items():
        key = topic.forum_uuid or ("topic-" + topic_uuid)
        groups.setdefault(key, []).append(topic_uuid)

    results: list[DerivedForum] = []
    for key, uuids in groups.items():
        derived_topics = {
            topic_uuid: _assemble_topic(
                index,
                parsed_topics[topic_uuid],
                forums_base_url=base_url,
                page_size=page_size,
                base_url=base_url,
                hcl_hosts=hcl_hosts,
            )
            for topic_uuid in uuids
        }
        if key.startswith("topic-"):
            # Genuinely standalone: this topic's own forum_uuid isn't known
            # at all (not merely un-titled) -- never mislabel the group with
            # the topic's own title (looks like a duplicate entry in the
            # reader); leave title unset, the reader/PDF fall back to a
            # neutral placeholder instead.
            results.append(
                DerivedForum(
                    id=key,
                    title=None,
                    topic_ids=uuids,
                    topics=derived_topics,
                    alternate_url=(
                        base_url.rstrip("/") + "/forums/html/threadTopic?id=" + uuids[0]
                        if base_url
                        else None
                    ),
                )
            )
            continue
        # A real forum_uuid -- try to recover its own name from an archived
        # topics-feed first page (see docstring); `None` if it was never
        # fetched at all (the reader/PDF then show the bare uuid, not a
        # topic's title standing in for it).
        title: str | None = None
        forum_topics_feed_url = _topics_url(base_url=base_url, forum_uuid=key)
        for page_num in (1, 0):
            first = index.get(add_query(forum_topics_feed_url, ps=page_size, page=page_num))
            if first is not None:
                title = atom.feed_title(first.content)
                if title:
                    break
        results.append(DerivedForum(id=key, title=title, topic_ids=uuids, topics=derived_topics))
    return results


def _assemble_forums(
    index: ArchiveIndex,
    *,
    forums_feed_url: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> list[DerivedForum]:
    """Every forum in the archived forums-list feed, each with its
    page-walked topics (bodies, flags, resolved assets/links) and each
    topic's arbitrary-depth reply tree, flattened into the id-keyed model."""
    forums_base_url = _base_url_before(forums_feed_url, _FORUMS_LIST_TAIL)
    forumrefs = index.paginate(forums_feed_url, parse_forums_feed, page_size=page_size)
    assembled = [
        _assemble_forum(
            index,
            forumref,
            forums_base_url=forums_base_url,
            page_size=page_size,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
        )
        for forumref in forumrefs
    ]
    return [forum for forum in assembled if forum is not None]
