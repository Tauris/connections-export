"""Crawling Forums: list feed -> per forum: topics -> per topic: flat
replies, rebuilt into a tree by `build_reply_tree`.

Forums addressing is query-param rather than link-following: a topic entry
carries no replies link, so the replies feed URL is built from the topic uuid.
The `since` cutoff goes as epoch milliseconds (`profiles.FORUMS`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from urllib.parse import parse_qs, urlsplit

from connections_export import paging
from connections_export.adapters import atom
from connections_export.adapters.forums import (
    ForumRef,
    attachment_filenames_from_html,
    attachments_from_html,
    build_reply_tree,
    forums_list_url,
    parse_forums_feed,
    parse_replies_feed,
    parse_single_topic,
    parse_topics_feed,
    replies_url,
    topic_url,
    topics_url,
)
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).
#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.
from connections_export.crawler.apps._shared import FORUMS_ADAPTER_VERSION, _make_paginate
from connections_export.crawler.author_plan import (
    Involvement,
    ThreadFacts,
    _norm,
    format_identities,
)
from connections_export.crawler.engine import (
    AssetCapture,
    CachePolicy,
    CrawlResult,
    add_query,
    begin_run,
    default_clock,
    finish_run,
)
from connections_export.crawler.events import Emit
from connections_export.crawler.forum_selection import select_community_forum_topics
from connections_export.http.client import HttpClient


def _enrich_forum_attachment_filenames(fetcher, entries, discovered_from: str) -> None:
    """Recover filenames that the Atom feed replaces with ``name=blob``."""
    if not fetcher.config.capture_assets:
        return
    for entry in entries:
        if not entry.alternate_url:
            continue
        missing = [a for a in entry.attachments if not a.filename]
        if not missing and entry.attachments:
            continue
        html = fetcher.get_content(entry.alternate_url, "topic", discovered_from)
        if html is None:
            continue
        rendered_attachments = attachments_from_html(html)
        names = attachment_filenames_from_html(html)
        existing_hrefs = {a.enclosure_href for a in entry.attachments}
        for rendered in rendered_attachments:
            if (
                rendered.enclosure_href
                and rendered.filename
                and rendered.enclosure_href not in existing_hrefs
            ):
                entry.attachments.append(rendered)
        for attachment in missing:
            node_id = (
                parse_qs(urlsplit(attachment.enclosure_href or "").query).get("nodeId") or [None]
            )[0]
            if node_id and node_id in names:
                attachment.filename = names[node_id]


def crawl_forums(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
    max_topics: int | None = None,
    since: str | None = None,
    forum_uuids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    direct_topic_only: bool = False,
    restrict_to_forum_uuids: Sequence[str] | None = None,
    search_userid: str | None = None,
    community_uuid: str | None = None,
    author: str | None = None,
    stop_event: threading.Event | None = None,
) -> CrawlResult:
    """Crawl one deployment's Forums into `archive`, mirroring `crawl`:
    list feed -> per forum: topics feed (page-walked) -> per topic:
    replies feed (page-walked, FLAT). Every response and every failure
    is archived; a `ForumTopicDerived` event is emitted per fully-walked
    topic (its flat replies feed is rebuilt into a tree by
    `build_reply_tree` on the way, so the count reflects a real,
    reconstructable thread -- never a silently dropped one).

    The forums list feed needs no configured handle (unlike Blogs) --
    it lives at a fixed `/forums/atom/forums`. Each forum's topics feed
    is followed from the list feed's `rel="self"` link; a topic entry
    carries no replies link (see forums.py -- Forums addressing is
    query-param), so the replies feed URL is built from the topic's
    uuid via `replies_url`.

    `restrict_to_forum_uuids`: used with `topic_ids` + `forum_uuids=None`
    (the search-driven fast path below) when the caller knows which forums
    the seeds are allowed to come from -- a seeded topic whose OWN
    `forum_uuid` (read off its `thr:in-reply-to`) is not among them is
    dropped entirely, never fetched further or kept. The Search API has no
    server-side per-forum-instance filter (only the community-level one), so
    without this a person's search hits from every forum they've ever posted
    in (or, unpinned, the whole deployment) all land in the hierarchy even
    when the run was scoped to particular forums.

    It is a SET rather than one uuid because a community capture ticks a set
    of forums and the person query is pinned to the community, so it returns
    topics from the forums that were not ticked as well; one uuid could not
    express "these two, not that third one".

    The seeded path applies the same author filter and the same
    `max_topics` limit as the full walk below. Search is documented as a
    superset of authorship (author OR contributor OR community member), so a
    seed is a candidate to read, never a decision to keep.

    `search_userid` + `community_uuid`: with an `author` filter and a list of
    `forum_uuids`, this crawl asks the community-scoped person query which
    root topics to read and turns itself into the seeded path above, rather
    than page-walking every forum's topics feed and fetching every topic's
    replies to find out who was in them. For a busy community that is the
    difference between reading the threads one person is in and reading the
    whole thing to find them.

    It is a selection, never a filter and never the content: Search returns
    topic URLs and no reply records at all, so each selected topic's replies
    feed is still fetched in full, and the author filter still decides what
    is kept. If the query is refused, returns nothing, or names nothing this
    build recognises, the crawl falls back to the full walk and says so --
    the one thing it must not do is quietly capture less.
    """
    emit = emit if emit is not None else events.default_emit
    clock = clock or default_clock

    session = begin_run(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock,
        run_id=run_id,
        adapter_version=FORUMS_ADAPTER_VERSION,
        max_items=max_topics,
        # What this archive was captured under. The field existed and was
        # documented ("an archive made with 'only me' cannot later be updated
        # as if it were an everyone archive") but nothing ever filled it, so
        # an archive could not say why an item's bytes were absent.
        author_filter=author,
    )
    fetcher = session.fetcher
    asset_capture = AssetCapture(
        fetcher=fetcher,
        base_url=session.base_url,
        hcl_hosts=config.hcl_hosts,
        enabled=config.capture_assets,
    )

    paginate = _make_paginate(fetcher, stop_event)

    topics_crawled = 0
    # Two-pass author filter (see crawl): defer topic/reply body-image scans
    # and keep whole threads where the author posted or replied.
    author_target = _norm(author)
    thread_facts: list[ThreadFacts] = []
    kept_topics = 0

    def _emit_author_summary() -> None:
        """What the filter kept, out of what it read. Emitted on EVERY way
        out of this crawl -- the seeded path returns early, and used to
        return without it, so a search-selected capture could prune threads
        and never say that it had."""
        if not thread_facts:
            return
        involvements = [tf.topic for tf in thread_facts]
        for tf in thread_facts:
            involvements.extend(tf.replies)
        events.safe_emit(
            emit,
            events.AuthorFilterSummary(
                kept=kept_topics,
                total=len(thread_facts),
                author=author or "",
                identities=format_identities(involvements),
            ),
        )

    # Fast path: when specific topic_ids are given but no forum_uuid is known
    # (e.g. a threadTopic URL with only `id=`), skip the full forums list crawl
    # and fetch each topic + its replies directly. This avoids hammering every
    # forum's topics feed looking for one entry.
    # When the user wants the WHOLE forum (topic_ids given but scope is not
    # "single"), we recover the forum_uuid from the topic's thr:in-reply-to
    # field and fall through to the regular list-based crawl for that forum.
    # Topic ids fully processed by the fast-path prefetch below (keyed by the
    # topic's OWN normalized `atom:id`, not the caller-given `tid` -- so this
    # still matches correctly even if the caller's uuid differs in case from
    # what the server's feeds report). Consulted by the widen-to-full-forum
    # pass further down so a prefetched topic is never processed (and its
    # `ForumTopicDerived` event emitted) a second time.
    prefetched_topic_ids: set[str] = set()
    # Forum uuids whose own topics-feed title has already been (attempted to
    # be) fetched below -- at most one lightweight request per distinct
    # forum, never per topic.
    forum_titles_fetched: dict[str, str | None] = {}
    # `None` (no restriction) is not the same as an empty set (restricted to
    # nothing), and a caller passing an empty list means it selected no
    # forums -- so `or None` here would turn "keep none" into "keep all".
    restrict_set = set(restrict_to_forum_uuids) if restrict_to_forum_uuids is not None else None

    if (
        forum_uuids
        and topic_ids is None
        and author_target is not None
        and search_userid
        # The community pin is required, not optional. Without it the person
        # query spans every forum on the deployment they have ever posted in,
        # which for a prolific user is far more likely to be cut short at the
        # page limit -- and after restricting the survivors to the selected
        # forums, the capture would quietly be thinner than the full walk.
        # Pinned to one community, the answer stays small enough to page
        # through comfortably.
        and community_uuid
    ):
        # See the docstring. Only ever a narrowing of what to
        # READ: the forums the caller selected stay the boundary
        # (`restrict_set`), and the author filter below still decides what is
        # kept.
        selection = select_community_forum_topics(
            fetch=lambda url: fetcher.get_content(
                url,
                "search",
                None,
                # The answer to "which of this person's threads are in this
                # community" -- an archived answer to that question can only
                # repeat itself, and a resumed run that skipped it would have
                # no seeds at all.
                policy=CachePolicy.ALWAYS,
            ),
            base_url=session.base_url,
            userid=search_userid,
            community_uuid=community_uuid,
        )
        events.safe_emit(
            emit,
            events.TopicSelection(
                selected=len(selection.topic_ids),
                hits=selection.hits,
                pages_read=selection.pages_read,
                complete=selection.complete,
                used=selection.usable,
                detail=selection.summary,
                ref=selection.url,
            ),
        )
        for alternate, via in selection.samples:
            # The shapes the seed parser choked on, in the run log -- so an
            # unrecognised permalink form is diagnosable where it happened
            # rather than only visible as "search selected nothing".
            print(
                "connections-export: search hit named no topic — "
                f"alternate_url={alternate!r} via_url={via!r}",
                flush=True,
            )
        if selection.usable:
            # Become the seeded path: read these topics directly, bounded to
            # the forums this run was asked for.
            restrict_set = set(forum_uuids)
            topic_ids = list(selection.topic_ids)
            forum_uuids = None
            direct_topic_only = True

    if topic_ids is not None and forum_uuids is None:
        recovered_forum_uuids: list[str] = []
        for tid in topic_ids:
            if stop_event is not None and stop_event.is_set():
                break
            # The Preview limit, checked BEFORE the seed is fetched: this
            # path had no `max_topics` check at all, so previewing a
            # search-selected capture read every selected thread and the
            # limit only ever applied to the full walk below.
            if max_topics is not None and topics_crawled >= max_topics:
                break
            t_url = topic_url(base_url=session.base_url, topic_uuid=tid)
            t_content = fetcher.get_content(t_url, "topic", None)
            if t_content is None:
                continue
            topics_parsed = fetcher.parse_or_none(parse_single_topic, t_content, url=t_url)
            if not topics_parsed:
                continue
            topic = topics_parsed[0]
            if (
                restrict_set is not None
                and topic.forum_uuid
                and topic.forum_uuid not in restrict_set
            ):
                # A search hit from a DIFFERENT forum than the one this run
                # is restricted to -- the client-side "just this one forum"
                # filter (see docstring). Dropped entirely: not counted, not
                # fetched further, never a widen candidate.
                events.safe_emit(
                    emit, events.Pruned(kind="topic", id=topic.id, reason="wrong-forum")
                )
                continue
            prefetched_topic_ids.add(topic.id)
            # If the topic tells us its forum UUID, collect it so we can
            # widen to a full-forum crawl below (scope != "single").
            if topic.forum_uuid and topic.forum_uuid not in recovered_forum_uuids:
                recovered_forum_uuids.append(topic.forum_uuid)
            forum_title = None
            if topic.forum_uuid and direct_topic_only:
                # One lightweight fetch (page 1 only) per distinct forum --
                # never one per topic -- purely so the forum's own `<title>`
                # is archived and known here. Skipped when we're about to
                # widen to a full-forum crawl below: that crawl fetches this
                # same page anyway, so fetching it twice would be wasted.
                # Without this, multiple seeded threads from the same real
                # forum had no name at all, and `derive/assemble.py`'s
                # `_assemble_direct_topics` fell back to a topic's own title
                # standing in for the forum's -- which read as a duplicated
                # entry in the reader (a "Forum · <title>" header directly
                # above that same topic).
                if topic.forum_uuid not in forum_titles_fetched:
                    forum_topics_feed_url = add_query(
                        topics_url(base_url=session.base_url, forum_uuid=topic.forum_uuid),
                        ps=session.config.page_size,
                        page=1,
                    )
                    title_content = fetcher.get_content(forum_topics_feed_url, "topic", t_url)
                    forum_titles_fetched[topic.forum_uuid] = (
                        atom.feed_title(title_content) if title_content is not None else None
                    )
                forum_title = forum_titles_fetched[topic.forum_uuid]
            # Deferred exactly as in the full walk below: whether this
            # thread is kept is not known until its replies have been read
            # (the author may appear only in one), so scanning its bodies and
            # queueing its attachments now would pay for threads that are
            # about to be pruned.
            ops: list[Callable[[], None]] = []
            if topic.content_html:
                ops.append(
                    lambda h=topic.content_html, u=t_url: asset_capture.scan_body(
                        h, base=session.base_url, discovered_from=u
                    )
                )
            for attachment in topic.attachments:
                if attachment.enclosure_href:
                    ops.append(
                        lambda h=attachment.enclosure_href, u=t_url: asset_capture.handle(
                            h,
                            base=session.base_url,
                            discovered_from=u,
                            kind="attachments",
                        )
                    )
            topic_replies_url = replies_url(base_url=session.base_url, topic_uuid=tid)
            replies = paginate(topic_replies_url, parse_replies_feed, "reply", t_url)
            _enrich_forum_attachment_filenames(fetcher, [topic, *replies], t_url)
            for reply in replies:
                if reply.content_html:
                    ops.append(
                        lambda h=reply.content_html, u=topic_replies_url: asset_capture.scan_body(
                            h, base=session.base_url, discovered_from=u
                        )
                    )
                for attachment in reply.attachments:
                    if attachment.enclosure_href:
                        ops.append(
                            lambda h=attachment.enclosure_href, u=topic_replies_url: (
                                asset_capture.handle(
                                    h,
                                    base=session.config.base_url,
                                    discovered_from=u,
                                    kind="attachments",
                                )
                            )
                        )
            build_reply_tree(replies)
            topics_crawled += 1

            # The same two-pass author filter the full walk applies, for the
            # same reason and with the same summary at the end. A seed is a
            # candidate: the person query that produced it is documented as a
            # superset (author OR contributor OR community member), so
            # emitting every seeded topic would have made an "only me"
            # capture keep whatever the superset dragged in, under a filter
            # that said otherwise.
            topic_inv = Involvement(
                author=topic.author,
                author_userid=topic.author_userid,
                contributors=tuple(topic.contributors or ()),
            )
            reply_invs = tuple(
                Involvement(
                    author=r.author,
                    author_userid=r.author_userid,
                    contributors=tuple(r.contributors or ()),
                )
                for r in replies
            )
            thread_facts.append(ThreadFacts(topic_id=topic.id, topic=topic_inv, replies=reply_invs))
            involved = (
                author_target is None
                or topic_inv.matches(author_target)
                or any(iv.matches(author_target) for iv in reply_invs)
            )
            if not involved:
                events.safe_emit(emit, events.Pruned(kind="topic", id=topic.id))
                continue
            kept_topics += 1
            for op in ops:
                op()
            events.safe_emit(
                emit,
                events.ForumTopicDerived(
                    topic_id=topic.id,
                    forum=topic.forum_uuid or "",
                    forum_title=forum_title,
                    title=topic.title,
                    author=topic.author,
                    published=topic.published,
                    reply_count=len(replies),
                    flags=tuple(sorted(topic.flags)),
                ),
            )
        # If the forum UUID was recovered AND the caller wants the whole
        # forum (not just this single topic), widen to a full-forum crawl.
        # When direct_topic_only=True (scope="single" in the UI), stay with
        # only what was directly fetched above.
        if not recovered_forum_uuids or direct_topic_only:
            _emit_author_summary()
            return finish_run(session, orphans=[], pages_crawled=topics_crawled)
        # Widen to full-forum crawl using the recovered forum UUIDs.
        # Clear topic_ids so the full crawl isn't limited to the one prefetched
        # topic, and reset the counter so the preview limit counts from zero
        # (the prefetch was just metadata discovery, not user-visible content
        # in the sense the limit is meant to control).
        forum_uuids = recovered_forum_uuids
        topic_ids = None
        topics_crawled = 0

    forums_url = forums_list_url(base_url=session.base_url)
    if forum_uuids is not None:
        # When specific forum UUIDs are known, skip the global forums list
        # entirely (it can have tens of thousands of entries) and build
        # ForumRef objects directly from the UUIDs. The forum's own topics
        # feed URL is constructed via `topics_url` -- the same fallback the
        # old list-based path used for community forums anyway.
        forumrefs = [
            ForumRef(
                uuid=fuuid,
                title=None,
                self_url=topics_url(base_url=session.base_url, forum_uuid=fuuid),
            )
            for fuuid in forum_uuids
        ]
        # Skipping the list feed also skips the only place the forum's NAME
        # came from, so a scoped run -- every community ingest, and every
        # "one forum" chip -- could only ever call its forums by uuid. One
        # request each recovers it from the topics feed's own `<title>`, the
        # same source the direct-topic path and the derive step already use.
        for index, forumref in enumerate(forumrefs):
            title_content = fetcher.get_content(
                add_query(forumref.self_url, ps=session.config.page_size, page=1),
                "forum",
                forums_url,
            )
            recovered = atom.feed_title(title_content) if title_content is not None else None
            if recovered:
                forumrefs[index] = forumref.model_copy(update={"title": recovered})
    else:
        # ALWAYS, for the same reason as the wikis and blogs list feeds: it
        # is the answer to "which forums are there", and an archived answer
        # can only repeat itself.
        forumrefs = paginate(
            forums_url, parse_forums_feed, "forum", None, policy=CachePolicy.ALWAYS
        )

    for forumref in forumrefs:
        if stop_event is not None and stop_event.is_set():
            break
        # The list feed's `rel="self"` is that forum's own topics feed.
        topics_feed_url = forumref.self_url
        if not topics_feed_url:
            continue  # no link to follow (defensive; the fake always emits one)
        if since:
            # Epoch MILLISECONDS here, where Blogs took RFC 3339. The split is
            # the deployment's; `adapters/since` owns it so no traversal has to
            # remember which app wants which.
            from connections_export.adapters import profiles  # noqa: PLC0415
            from connections_export.adapters.since import encode_for  # noqa: PLC0415

            topics_feed_url = add_query(
                topics_feed_url, since=encode_for(profiles.FORUMS, since), sortBy="modified"
            )
        # Cap page-walking when a preview limit is active so we don't fetch
        # hundreds of topic pages just to process the first N topics.
        remaining = (max_topics - topics_crawled) if max_topics is not None else None
        topics_max_pages = (
            max(1, (remaining + session.config.page_size - 1) // session.config.page_size)
            if remaining is not None
            else paging.MAX_PAGES
        )
        topics = fetcher.paginate(
            topics_feed_url,
            parse_topics_feed,
            "topic",
            forums_url,
            # ALWAYS: the answer to "which topics are in this forum". The
            # `since` cutoff usually changes the URL, but an archived answer
            # to that question can only ever repeat itself.
            policy=CachePolicy.ALWAYS,
            max_pages=topics_max_pages,
            stop_event=stop_event,
        )

        for topic in topics:
            if stop_event is not None and stop_event.is_set():
                break
            if topic_ids is not None and topic.id not in topic_ids:
                continue  # single-thread scope: skip every topic but the named one
            if topic.id in prefetched_topic_ids:
                # Already fully fetched (replies + event) during the fast-path
                # prefetch above, before widening to this full-forum pass --
                # reprocessing it here would emit a second, duplicate
                # `ForumTopicDerived` for the exact same thread.
                continue
            if max_topics is not None and topics_crawled >= max_topics:
                break
            # #27: same-origin `<img>` in the topic body, captured the
            # same way as a wiki page body -- a topic entry carries no
            # separate content fetch (or any own URL) to key a base
            # off of, so `base_url` is used directly (mirrors the
            # attachment-enclosure `asset_capture.handle` call in
            # `crawl` above, which does the same for the same reason).
            ops: list[Callable[[], None]] = []
            if topic.content_html:
                ops.append(
                    lambda h=topic.content_html, u=topics_feed_url: asset_capture.scan_body(
                        h, base=session.base_url, discovered_from=u
                    )
                )
            for attachment in topic.attachments:
                if attachment.enclosure_href:
                    ops.append(
                        lambda h=attachment.enclosure_href, u=topics_feed_url: asset_capture.handle(
                            h, base=session.base_url, discovered_from=u, kind="attachments"
                        )
                    )
            # No replies link on the topic entry (query-param addressing);
            # build the flat replies feed URL from the topic uuid.
            topic_replies_url = replies_url(base_url=session.base_url, topic_uuid=topic.id)
            replies = paginate(
                topic_replies_url,
                parse_replies_feed,
                "reply",
                topics_feed_url,
                # a reply DID
                # move its topic's `updated`, and the dated topics feed
                # returned the topic. That makes the TOPICS FEED the change
                # filter -- so a topic reaching this point is one that moved,
                # and its replies have to be read again.
                #
                # This was `IF_MISSING`, which never refetches once present:
                # the topic came back from the feed and its replies came out
                # of the archive anyway, so a reply added to an old thread was
                # lost and the run reported success. `IF_CHANGED` is not
                # available here -- the parsed topic carries `published` and
                # no moved-date to compare against.
                #
                # The cost is bounded by the same feed: with a cutoff it
                # returns only the topics that moved, so only those are
                # re-read.
                policy=CachePolicy.ALWAYS,
            )
            _enrich_forum_attachment_filenames(fetcher, [topic, *replies], topics_feed_url)
            for reply in replies:
                if reply.content_html:
                    ops.append(
                        lambda h=reply.content_html, u=topic_replies_url: asset_capture.scan_body(
                            h, base=session.base_url, discovered_from=u
                        )
                    )
                for attachment in reply.attachments:
                    if attachment.enclosure_href:
                        ops.append(
                            lambda h=attachment.enclosure_href, u=topic_replies_url: (
                                asset_capture.handle(
                                    h,
                                    base=session.base_url,
                                    discovered_from=u,
                                    kind="attachments",
                                )
                            )
                        )
            # Rebuild the tree so the count reflects a genuinely
            # reconstructable thread (flat feed -> nested structure).
            build_reply_tree(replies)

            topic_inv = Involvement(
                author=topic.author,
                author_userid=topic.author_userid,
                contributors=tuple(topic.contributors or ()),
            )
            reply_invs = tuple(
                Involvement(
                    author=r.author,
                    author_userid=r.author_userid,
                    contributors=tuple(r.contributors or ()),
                )
                for r in replies
            )
            thread_facts.append(ThreadFacts(topic_id=topic.id, topic=topic_inv, replies=reply_invs))
            topics_crawled += 1

            # Keep the whole thread iff the author posted it or replied anywhere
            # (all replies read above) -- decided now, so a thread you're not in
            # is pruned immediately and never enters the hierarchy.
            involved = (
                author_target is None
                or topic_inv.matches(author_target)
                or any(iv.matches(author_target) for iv in reply_invs)
            )
            if not involved:
                events.safe_emit(emit, events.Pruned(kind="topic", id=topic.id))
                continue
            kept_topics += 1
            for op in ops:
                op()
            events.safe_emit(
                emit,
                events.ForumTopicDerived(
                    topic_id=topic.id,
                    forum=forumref.uuid,
                    forum_title=forumref.title,
                    title=topic.title,
                    author=topic.author,
                    published=topic.published,
                    reply_count=len(replies),
                    flags=tuple(sorted(topic.flags)),
                ),
            )

    _emit_author_summary()
    return finish_run(session, orphans=[], pages_crawled=topics_crawled)
