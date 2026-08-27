"""Crawling Blogs: list feed -> per blog: entries -> per post: comments.

Entry feeds are 0-indexed on a real deployment, and take a `since`
cutoff as RFC 3339 (`profiles.BLOGS`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial

from connections_export import paging
from connections_export.adapters import atom
from connections_export.adapters import blogs as blogs_adapter
from connections_export.adapters.blogs import (
    blogs_list_url,
    entries_feed_url,
    parse_blogs_feed,
    parse_entries_with_comment_urls,
    parse_entry_comments_feed,
)
from connections_export.adapters.model import BlogRef
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).
#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.
from connections_export.crawler.apps._shared import BLOGS_ADAPTER_VERSION, _make_paginate
from connections_export.crawler.author_plan import (
    Involvement,
    PageFacts,
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
from connections_export.http.client import HttpClient


def _posts_from_changed_comments(changed: list, known_posts: set[str]) -> tuple[set[str], set[str]]:
    """`(posts to refresh, references we could not place)`.

    A comment's `ref` is its IMMEDIATE parent, which for a reply is a sibling
    comment rather than the entry -- so a ref is not a post id and must never
    be used as one. The chain always ENDS at a post, though, so it is followed:
    a parent that also changed is in this same feed, and one that did not is
    already in the archive under the post that holds it.

    What comes back unplaced is a reference to something this run has not seen,
    which is a thing to go and look for rather than a reason to guess.
    """
    by_id = {comment.id: comment for comment in changed if comment.id}
    posts: set[str] = set()
    unplaced: set[str] = set()
    for comment in changed:
        seen: set[str] = set()
        # `source` names the entry outright when the server fills it in.
        target = comment.parent_source or comment.parent_ref
        while target and target not in seen:
            seen.add(target)
            if target in known_posts:
                posts.add(target)
                break
            parent = by_id.get(target)
            if parent is None:
                unplaced.add(target)
                break
            target = parent.parent_source or parent.parent_ref
    return posts, unplaced


def _post_holding_comment(
    fetcher, posts_by_id: dict, comment_id: str, discovered_from
) -> str | None:
    """Which post holds `comment_id`, from what the archive already has.

    A reply names its sibling, and that sibling is a comment on some post --
    so the answer exists; it is only a question of where to look. Every post's
    comments feed is already in the archive from the last capture, so this
    reads them back rather than asking the deployment: `IF_MISSING` serves an
    archived feed without a request, and fetches only one the archive lacks,
    which is the case where we genuinely do not have it yet.
    """
    for post_id, (_post, href) in posts_by_id.items():
        if not href:
            continue
        # Through `paginate`, not a bare `get_content`: the capture stored
        # these feeds under their PAGINATED urls (`?ps=…&page=1`), so asking
        # for the bare href misses every cached record and turns a local
        # lookup into a request per post -- measured, before this line.
        comments = fetcher.paginate(
            href,
            parse_entry_comments_feed,
            "comments",
            discovered_from,
            max_pages=paging.MAX_PAGES,
            policy=CachePolicy.IF_MISSING,
        )
        if comments and any(comment.id == comment_id for comment in comments):
            return post_id
    return None


def _changed_comments(fetcher, session, blogref, since: str) -> list | None:
    """The comments this blog reports as changed since `since`, or `None`.

    `None` means the question could not be asked -- the feed did not answer,
    or did not parse -- and the caller re-reads every post, which is what it
    did before this existed.
    """
    if not blogref.uuid:
        return None
    url = blogs_adapter.all_comments_feed_url(
        base_url=session.base_url, blog_uuid=blogref.uuid, since=since
    )
    content = fetcher.get_content(url, "comments", blogref.self_url, policy=CachePolicy.ALWAYS)
    if content is None:
        return None
    changed = fetcher.parse_or_none(blogs_adapter.parse_changed_comments, content, url=url)
    if changed is None:
        return None
    return changed


def _blogs_entries_since(entries_url: str, since: str) -> str:
    """Put the cutoff on a blog's own entries feed URL.

    The URL comes from the list feed's `rel="self"`, so it cannot be rebuilt
    from `entries_feed_url` without losing whatever else the deployment put on
    it. `sortBy=modified` rides along for the reason the adapter documents:
    without it "changed since" and "published since" differ for an edited old
    post.
    """
    from connections_export.adapters import profiles  # noqa: PLC0415
    from connections_export.adapters.since import encode_for  # noqa: PLC0415

    return add_query(entries_url, since=encode_for(profiles.BLOGS, since), sortBy="modified")


def crawl_blogs(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    blogs_homepage: str,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
    max_posts: int | None = None,
    since: str | None = None,
    blog_uuids: list[str] | None = None,
    entry_ids: list[str] | None = None,
    author: str | None = None,
    stop_event: threading.Event | None = None,
) -> CrawlResult:
    """Crawl one deployment's Blogs into `archive`, mirroring `crawl`:
    list feed -> per blog: entries feed (page-walked) -> per post:
    entry-comments feed (page-walked). Every response and every failure
    is archived; a `BlogPostDerived` event is emitted per fully-walked
    post.

    `blogs_homepage` is the handle of the blog configured as the Blogs
    home page -- the only handle that serves the "list all blogs" feed
    (`/blogs/{homepage}/feed/blogs/atom`). Each blog's own entries feed
    and each post's comments feed are then followed straight from the
    `rel="self"`/`rel="replies"` links the feeds carry, never rebuilt
    from bare uuids.

    `max_posts` caps the total number of posts crawled across all blogs
    (for preview runs; `None` means unlimited).
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
        adapter_version=BLOGS_ADAPTER_VERSION,
        max_items=max_posts,
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

    def paginate_blog_entries(feed_base_url, parser, kind, discovered_from):
        """HCL Connections blog entry feeds are 0-indexed. Start at page 0 with deduplication so the
        crawler works regardless of whether a server has been fixed."""
        return fetcher.paginate(
            feed_base_url,
            parser,
            kind,
            discovered_from,
            max_pages=paging.MAX_PAGES,
            start_page=0,
            stop_event=stop_event,
            # ALWAYS: this feed is what says which posts exist. A `since`
            # cutoff usually changes the URL and forces a fetch anyway, but
            # that is protection by accident -- it disappears the moment there
            # is no cutoff, and then an update lists only what it already had.
            policy=CachePolicy.ALWAYS,
        )

    posts_crawled = 0
    # Two-pass author filter (see crawl): defer post/comment body-image scans
    # and keep whole posts the author wrote or commented on.
    author_target = _norm(author)
    post_facts: list[PageFacts] = []
    kept_posts = 0

    blogs_url = blogs_list_url(base_url=session.base_url, homepage=blogs_homepage)
    # ALWAYS: which blogs exist is exactly what an update needs to re-read.
    # The entries inside them are still filtered by `since`.
    blogrefs = paginate(blogs_url, parse_blogs_feed, "blog", None, policy=CachePolicy.ALWAYS)
    # Scope to a specific blog / single entry when the URL identified one, so
    # the archive holds only what was imported (mirrors `crawl`'s wiki_labels).
    if blog_uuids is not None:
        blogrefs = [b for b in blogrefs if b.uuid in blog_uuids]
        # A blog may not appear in the homepage list feed (personal,
        # private, or beyond the paged list). Fall back to building its
        # entries URL directly from the UUID -- this is the verified
        # working path (`entries_feed_url`), so the crawl still succeeds.
        found_uuids = {b.uuid for b in blogrefs}
        for uuid in blog_uuids:
            if uuid not in found_uuids:
                blogrefs.append(
                    BlogRef(
                        uuid=uuid,
                        title=None,
                        self_url=entries_feed_url(base_url=session.base_url, blog_uuid=uuid),
                    )
                )

    # A blog reached by uuid alone has no title, and the live console then had
    # nothing to call it but the uuid -- which is exactly the shape of a
    # community ingest, where every component is selected by id. Recover the
    # name from the blog's own entries feed, the same `<title>` the derive
    # step falls back to, so the run reads as names rather than uuids.
    for index, blogref in enumerate(blogrefs):
        if blogref.title or not blogref.self_url:
            continue
        # Same shape as the forum-title prefetch below: `get_content` returns
        # None on any failure, so a missing name never fails a run.
        title_content = fetcher.get_content(
            add_query(blogref.self_url, ps=session.config.page_size, page=0),
            "blog",
            blogs_url,
        )
        recovered = atom.feed_title(title_content) if title_content is not None else None
        if recovered:
            blogrefs[index] = blogref.model_copy(update={"title": recovered})

    for blogref in blogrefs:
        # Stop ends the RUN, not just this container -- hence the check
        # here, in the outer loop. Checking only inside would finish the
        # current container and go straight on to the next, which across a
        # deployment's worth of them is a button that does nothing.
        if stop_event is not None and stop_event.is_set():
            break
        # The list feed's `rel="self"` is that blog's own entries feed.
        entries_url = blogref.self_url
        if not entries_url:
            continue  # no link to follow (defensive; the fake always emits one)
        # Which posts' discussions grew, in one request.
        #
        # A blog comment moves neither its post nor the dated entries feed,
        # so the dated walk alone cannot find new discussion. The per-blog
        # aggregate comments feed answers that question in one request,
        # instead of re-reading every post's comments on every update.
        #
        # `None` means "could not be sure" and every post is re-read, exactly
        # as before: a saved request is never worth a lost conversation. A set
        # -- possibly empty -- means the feed answered and named these posts.
        changed_comments = _changed_comments(fetcher, session, blogref, since) if since else None
        if since:
            # Ask the server for what changed instead of walking the whole
            # blog. The cutoff goes on in RFC 3339 -- the adapter owns that
            # choice, because Forums wants the same instant as epoch millis.
            entries_url = _blogs_entries_since(entries_url, since)
        if max_posts is not None:
            entry_pages = max(
                1, (max_posts + session.config.page_size - 1) // session.config.page_size
            )
            pairs = fetcher.paginate(
                entries_url,
                parse_entries_with_comment_urls,
                "post",
                blogs_url,
                max_pages=entry_pages,
                start_page=0,
                stop_event=stop_event,
                policy=CachePolicy.ALWAYS,  # as above: it reveals which posts exist
            )
        else:
            pairs = paginate_blog_entries(
                entries_url, parse_entries_with_comment_urls, "post", blogs_url
            )
        # Comments can arrive on a post the dated entries feed does not
        # return -- that feed reports posts that MOVED, and a comment moves
        # nothing. So the post is never reached, and the new discussion is
        # lost with the run reporting success. Reproduced before this existed.
        #
        # The index knows which posts grew, so any it names that the dated
        # walk did not return are fetched from the blog's full entries feed:
        # one extra walk per blog, only when there is new discussion on an old
        # post, and only using a feed shape the capture path already relies on.
        posts_with_new_comments = None
        if changed_comments is not None:
            # Resolved against the posts this walk actually returned. A ref
            # that lands on none of them belongs to a post the dated feed left
            # out -- which is the whole point, since a comment moves nothing --
            # so the blog's full entries feed is read and the chain resolved
            # again against everything it holds. One extra walk, and only when
            # there is discussion this walk could not account for.
            known = {post.id for post, _href in pairs}
            posts_with_new_comments, unplaced = _posts_from_changed_comments(
                changed_comments, known
            )
            if unplaced and max_posts is None:
                everything = paginate_blog_entries(
                    blogref.self_url, parse_entries_with_comment_urls, "post", blogs_url
                )
                all_posts = {post.id: (post, href) for post, href in everything}
                posts_with_new_comments, still_unplaced = _posts_from_changed_comments(
                    changed_comments, set(all_posts)
                )
                for reference in set(still_unplaced):
                    # It is in reply to something, so that something exists --
                    # and if the archive already holds it, we know the rest of
                    # the story without asking anyone.
                    holder = _post_holding_comment(fetcher, all_posts, reference, blogref.self_url)
                    if holder is not None:
                        posts_with_new_comments.add(holder)
                        still_unplaced.discard(reference)
                if still_unplaced:
                    # In reply to something neither this blog's posts nor its
                    # archived comments account for. Re-read the lot rather
                    # than decide which conversation to leave behind.
                    posts_with_new_comments = None
                    pairs = list(all_posts.values())
                else:
                    reached = {post.id for post, _href in pairs}
                    pairs = [
                        *pairs,
                        *[all_posts[pid] for pid in posts_with_new_comments - reached],
                    ]

        comment_max_pages = (
            max(1, (max_posts + session.config.page_size - 1) // session.config.page_size)
            if max_posts is not None
            else paging.MAX_PAGES
        )

        for post, comments_href in pairs:
            if stop_event is not None and stop_event.is_set():
                break
            if entry_ids is not None and post.id not in entry_ids:
                continue  # single-entry scope: skip every post but the named one
            if max_posts is not None and posts_crawled >= max_posts:
                break
            # #27: same-origin `<img>` in the post body is captured
            # exactly like a wiki page body (the design, "Asset
            # boundary") -- external is recorded, never fetched. The
            # post carries no separate content fetch to key off of (its
            # body is inline in the entries feed), so its own
            # `alternate_url` stands in for `page.content_src` as the
            # base for relative hrefs, falling back to `base_url` when
            # the feed omitted it.
            ops: list[Callable[[], None]] = []
            if post.content_html:
                # `partial` rather than a lambda with default arguments: both
                # bind the loop's current values eagerly, but the lambda form
                # cannot be wrapped -- ruff format collapses it back over the
                # line limit that ruff check then rejects.
                ops.append(
                    partial(
                        asset_capture.scan_body,
                        post.content_html,
                        base=post.alternate_url or session.base_url,
                        discovered_from=entries_url,
                    )
                )
            comment_count = 0
            comments = []
            if comments_href:
                comments = fetcher.paginate(
                    comments_href,
                    parse_entry_comments_feed,
                    "comments",
                    entries_url,
                    max_pages=comment_max_pages,
                    stop_event=stop_event,
                    # Always, and not because the option says so.
                    #
                    # A `since`-filtered entries feed reports posts that MOVED.
                    # A comment does not move its post: adding one leaves
                    # the post's `updated` where it was, and a `since` feed
                    # covering that hour returns ZERO posts.
                    # Blog comments do not surface at all.
                    #
                    # So for blogs this is not a trade a user can sensibly be
                    # asked to make: with it off, every update silently loses
                    # new discussion on old posts. It costs one request per
                    # post. Forums and wikis stay on the option, because there
                    # the same measurement showed the discussion DOES surface.
                    #
                    # With the aggregate index available, ALWAYS narrows to the
                    # posts it named: everything else keeps the comments the
                    # archive already holds, because the server has just said
                    # nothing about them changed.
                    policy=(
                        CachePolicy.ALWAYS
                        if posts_with_new_comments is None or post.id in posts_with_new_comments
                        else CachePolicy.IF_MISSING
                    ),
                )
                comment_count = len(comments)
                for comment in comments:
                    if comment.content_html:
                        ops.append(
                            lambda h=comment.content_html, u=comments_href: asset_capture.scan_body(
                                h, base=session.base_url, discovered_from=u
                            )
                        )

            post_inv = Involvement(
                author=post.author,
                author_userid=getattr(post, "author_userid", None),
                contributors=tuple(getattr(post, "contributors", ()) or ()),
            )
            comment_invs = tuple(
                Involvement(
                    author=c.author,
                    author_userid=getattr(c, "author_userid", None),
                    contributors=tuple(getattr(c, "contributors", ()) or ()),
                )
                for c in comments
            )
            post_facts.append(PageFacts(id=post.id, involvement=post_inv, comments=comment_invs))
            posts_crawled += 1

            # Keep a post iff the author wrote it or commented on it (comments
            # read above) -- decided now; a post you're not in is pruned
            # immediately and never enters the hierarchy.
            involved = (
                author_target is None
                or post_inv.matches(author_target)
                or any(iv.matches(author_target) for iv in comment_invs)
            )
            if not involved:
                events.safe_emit(emit, events.Pruned(kind="post", id=post.id))
                continue
            kept_posts += 1
            for op in ops:
                op()
            events.safe_emit(
                emit,
                events.BlogPostDerived(
                    post_id=post.id,
                    blog=blogref.uuid,
                    blog_title=blogref.title,
                    title=post.title,
                    comment_count=comment_count,
                    comments_enabled=post.comments_enabled,
                    author=post.author,
                    published=post.published,
                ),
            )

    if post_facts:
        involvements = [pf.involvement for pf in post_facts]
        for pf in post_facts:
            involvements.extend(pf.comments)
        events.safe_emit(
            emit,
            events.AuthorFilterSummary(
                kept=kept_posts,
                total=len(post_facts),
                author=author or "",
                identities=format_identities(involvements),
            ),
        )

    return finish_run(session, orphans=[], pages_crawled=posts_crawled)


# --------------------------------------------------------------------
# Stage 2 -- Forums traversal
# --------------------------------------------------------------------
