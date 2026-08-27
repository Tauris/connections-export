"""Crawling Wikis: wikis feed -> per wiki nav feed -> per page
entry/body/comments/versions/attachments.

Unlike every other app, a wiki feed cannot be asked "what changed since"
(`profiles.WIKIS.since_encoding` is `"unsupported"`), which is why this reads
each page's own entry to learn its date -- cheap next to the body, comments,
versions and assets that hang off it, and the whole difference between a
rescan and a re-download.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from urllib.parse import urlparse

from connections_export.adapters import atom
from connections_export.adapters.model import NavNode, WikiRef
from connections_export.adapters.wikis import (
    artifacts_url,
    nav_feed_url,
    page_entry_url,
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_page_entry,
    parse_pages_feed,
    parse_tags_feed,
    parse_versions_feed,
    parse_wikis_feed,
    wiki_feed_url,
    wikis_feed_url,
)
from connections_export.archive.blobs import read_blob
from connections_export.archive.records import ComponentSelection
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import assets, events, report

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).
#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.
from connections_export.crawler.apps._shared import ADAPTER_VERSION, _make_paginate
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
    begin_run,
    default_clock,
    finish_run,
)
from connections_export.crawler.events import Emit
from connections_export.http.client import HttpClient


def _note_archived_page_date(fetcher, entry_url: str) -> None:
    """Tell the fetcher what date the ARCHIVED copy of `entry_url` carries.

    Read out of the archived document itself rather than from the manifest,
    which records a date only for fetches that were given one -- so an entry
    captured before the index existed has none. Nothing here can cause a
    fetch: an entry that is not archived, or does not parse, simply leaves the
    date unknown, and unknown means fetch.
    """
    cached = fetcher.cached_records.get(entry_url)
    if cached is None or not cached.body_hash or cached.item_date:
        return
    try:
        body = read_blob(fetcher.archive.root, cached.body_hash.split(":", 1)[-1])
        page = parse_page_entry(body)
    except Exception:  # noqa: BLE001 - an unreadable archived entry just means "fetch"
        return
    if page.modified:
        fetcher.remember_item_date(entry_url, page.modified)


def _page_date_index(
    fetcher, paginate, *, base_url: str, auth_root: str, wiki_label: str, discovered_from: str
) -> dict[str, str]:
    """`{page label: the date an update gates on}` for one wiki, in one request.

    `/wikis/{auth}/api/wiki/{label}/feed` returns
    page entries carrying `atom:updated` -- the same field `parse_page_entry`
    fills `Page.modified` from, and the one that moves when a page is edited
    OR commented on. So the index answers the only question the per-page entry
    request was being made to answer.

    A page the index cannot speak for is simply absent from the mapping, and
    is then read exactly as it was before. Skipping a page is never the
    consequence of this being incomplete -- only fetching one is.
    """
    url = wiki_feed_url(base_url=base_url, auth_root=auth_root, wiki_label=wiki_label)
    # ALWAYS: an index served out of the archive would report every page
    # unchanged, which is the failure this whole module keeps rediscovering.
    # Tagged "wiki", not "page": it is the wiki's own feed, the same kind a
    # scoped crawl already reads it under. Tagging it "page" would add a page
    # to the discovered count that no page corresponds to, and the counters
    # are supposed to be answerable.
    pages = paginate(url, parse_pages_feed, "wiki", discovered_from, policy=CachePolicy.ALWAYS)
    return {page.label: page.modified for page in pages if page.label and page.modified}


def crawl(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
    wiki_labels: list[str] | None = None,
    community_uuid: str | None = None,
    community_title: str | None = None,
    max_pages: int | None = None,
    author: str | None = None,
    stop_event: threading.Event | None = None,
) -> CrawlResult:
    """Crawl one deployment's wikis into `archive`, composed from
    `config` (what/how), `client` (fetch), `archive` (store), and the
    `adapters` (interpret) -- no globals, every dependency injected.

    `wiki_labels` (crawler single-wiki scoping): `None`
    (the default) is today's behaviour, unchanged -- fetch the wikis
    feed and crawl every wiki it lists. A non-empty list skips the
    wikis-feed enumeration entirely and instead synthesizes one
    `WikiRef` per given label (the label doubles as id/label/title,
    since a scoped crawl was never told anything else about the wiki)
    and crawls each straight from its own nav feed. Everything
    downstream -- pages, body, comments, versions, attachments, the
    report, the event stream -- is identical either way.
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
        adapter_version=ADAPTER_VERSION,
        max_items=max_pages,
        # Recorded so derive can attribute a scoped wiki to its community:
        # the wikis LIST feed carries that marker and a scoped crawl skips it.
        # The labels go in beside it -- "what this run was asked to do, per
        # component" is precisely the mapping derive needs to know WHICH wiki
        # belongs to that community when an archive holds several.
        community_uuid=community_uuid,
        community_title=community_title,
        components=[ComponentSelection(kind="wiki", id=label) for label in (wiki_labels or [])],
        # What this archive was captured under. The field existed and was
        # documented ("an archive made with 'only me' cannot later be updated
        # as if it were an everyone archive") but nothing ever filled it, so
        # an archive could not say why an item's bytes were absent.
        author_filter=author,
    )
    fetcher = session.fetcher
    issues = session.issues
    base_url = session.base_url
    auth_root = session.auth_root
    hcl_hosts = list(config.hcl_hosts)

    pages_crawled = 0
    all_orphans: list[str] = []
    asset_capture = AssetCapture(
        fetcher=fetcher,
        base_url=base_url,
        hcl_hosts=hcl_hosts,
        enabled=config.capture_assets,
    )

    # Author filter:
    # a page's authorship is fully known once its entry + comments are read, so
    # when `author` is set we decide keep IMMEDIATELY -- a non-matching page's
    # images/attachments are never fetched and it never enters the hierarchy
    # (no PageDerived), so the ingest list shows only matches from the start and
    # the filtered model grows live. `author=None` keeps everything.
    # `page_facts` records who authored each page (cheap) so every run, filtered
    # or not, can report the distinct authors seen (the pick-the-name affordance).
    author_target = _norm(author)
    page_facts: list[PageFacts] = []
    kept_pages = 0

    paginate = _make_paginate(fetcher, stop_event)

    def process_page(
        wikiref: WikiRef,
        node: NavNode,
        nav_url: str,
        index: dict[str, str] | None = None,
    ) -> None:
        nonlocal pages_crawled
        if not node.label:
            return
        entry_url = page_entry_url(
            base_url=base_url, auth_root=auth_root, wiki_label=wikiref.label, page_label=node.label
        )
        # The entry is this run's change-detection instrument for wikis: the
        # feed cannot be asked "what changed since", so the only way to learn a
        # page's date is to read its entry. Cheap next to the body, comments,
        # versions and assets that hang off it -- which is the whole difference
        # between a rescan and a re-download.
        #
        # Cheap is not free, though: it is one request per page, every update,
        # for every page, and on a real deployment that was 23 requests to
        # learn that 22 pages had not moved. `index` is the wiki's own feed
        # read once (`_page_date_index`), which carries the same `atom:updated`
        # this would -- so where it has an answer, the entry is asked for only
        # when that answer says the page moved. `IF_CHANGED` compares the
        # index's date against the date recorded beside the archived entry:
        # either side unknown means fetch, so a page the index does not cover
        # costs exactly what it always did.
        indexed = (index or {}).get(node.label)
        if indexed:
            # The archive holds the entry but not, from before this existed, a
            # date beside it -- and `IF_CHANGED` with nothing to compare
            # against means fetch. The date is IN the archived entry, so read
            # it from there: no request, and no run spent re-learning what the
            # archive already knew.
            _note_archived_page_date(fetcher, entry_url)
        archived_date = fetcher.archived_item_date(entry_url) if indexed else None
        entry_content = fetcher.get_content(
            entry_url,
            "page",
            nav_url,
            policy=CachePolicy.IF_CHANGED if indexed else CachePolicy.ALWAYS,
            item_date=indexed,
        )
        if entry_content is None:
            return
        page = fetcher.parse_or_none(parse_page_entry, entry_content, url=entry_url)
        if page is None:
            return
        if (
            indexed
            and archived_date
            and indexed <= archived_date
            and author_target is None
            and not config.recheck_comments
        ):
            pages_crawled += 1
            return
        # Everything below is refetched only if this page's own date moved.
        moved = CachePolicy.IF_CHANGED
        when = page.modified

        # Collect this page's expensive asset ops; either run them now (no
        # filter) or defer them for the keep-set replay.
        ops: list[Callable[[], None]] = []

        if page.content_src:
            body_bytes = fetcher.get_content(
                page.content_src, "body", entry_url, policy=moved, item_date=when
            )
            if body_bytes is not None:
                src = page.content_src
                ops.append(
                    lambda b=body_bytes, s=src: asset_capture.scan_body(
                        b, base=s, discovered_from=s
                    )
                )

        comments_url = page.replies_url or artifacts_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wikiref.label,
            page_label=node.label,
            category="comment",
        )
        # A page that did not move keeps its comments, versions and
        # attachments from the archive.
        #
        # Commenting on a page moves its `atom:updated` to the comment's
        # time while leaving HCL's own `modified` element at its older value. `when` below is
        # `page.modified`, which the wiki adapter fills from `atom:updated`
        # (see `adapters/wikis.py`) -- the field that moves. So a rescan does
        # see a new comment, and `recheck_comments` is belt and braces rather
        # than the only thing standing between an update and a silent gap.
        #
        # Blogs are the app where that is not true, and there the recheck is
        # unconditional rather than optional.
        comments = paginate(
            comments_url,
            parse_comments_feed,
            "comments",
            entry_url,
            policy=CachePolicy.ALWAYS if config.recheck_comments else moved,
            item_date=when,
        )
        comment_count = len(comments)

        version_count = 0
        if config.versions != "none":
            versions_url = artifacts_url(
                base_url=base_url,
                auth_root=auth_root,
                wiki_label=wikiref.label,
                page_label=node.label,
                category="version",
            )
            versions = paginate(
                versions_url,
                parse_versions_feed,
                "versions",
                entry_url,
                policy=moved,
                item_date=when,
            )
            version_count = len(versions)
            if config.versions == "full":
                for version in versions:
                    if version.content_src:
                        vsrc = version.content_src
                        ops.append(
                            lambda s=vsrc, u=versions_url: fetcher.get_content(s, "versions", u)
                        )

        attachments_url = artifacts_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wikiref.label,
            page_label=node.label,
            category="attachment",
        )
        attachments = paginate(
            attachments_url,
            parse_attachments_feed,
            "attachments",
            entry_url,
            policy=moved,
            item_date=when,
        )
        attachment_count = len(attachments)
        for attachment in attachments:
            href = attachment.enclosure_href
            if not href:
                continue
            resolved = assets.resolve(href, base_url=base_url)
            if urlparse(resolved).scheme not in ("http", "https"):
                continue  # not a fetchable URL (e.g. a symbolic urn:)
            ops.append(
                lambda h=href, u=attachments_url: asset_capture.handle(
                    h, base=base_url, discovered_from=u, kind="attachments"
                )
            )

        # Tags: the `?category=tag` list, archived so derive can attach them to
        # the page (and the export/PDF can print them). Cheap text -- never deferred.
        tags_url = artifacts_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wikiref.label,
            page_label=node.label,
            category="tag",
        )
        paginate(tags_url, parse_tags_feed, "tags", entry_url)

        nonlocal kept_pages
        page_involvement = Involvement(
            author=page.author,
            author_userid=page.author_userid,
            contributors=tuple(page.contributors or ()),
        )
        comment_involvements = tuple(
            Involvement(
                author=c.author,
                author_userid=getattr(c, "author_userid", None),
                contributors=tuple(getattr(c, "contributors", ()) or ()),
            )
            for c in comments
        )
        # Always record who authored this page (cheap metadata) so every run
        # can report the distinct authors it saw -- the "pick the exact name"
        # affordance -- even an unfiltered one.
        page_facts.append(
            PageFacts(id=node.id, involvement=page_involvement, comments=comment_involvements)
        )
        pages_crawled += 1

        # Decide keep IMMEDIATELY: a page matches on its own author or any of
        # its comments (all read above), so we never wait for a second pass.
        # A non-matching page is pruned on the spot -- its images/attachments
        # are never fetched and it NEVER enters the hierarchy (no PageDerived).
        involved = (
            author_target is None
            or page_involvement.matches(author_target)
            or any(iv.matches(author_target) for iv in comment_involvements)
        )
        if not involved:
            events.safe_emit(emit, events.Pruned(kind="page", id=node.id))
            return
        kept_pages += 1
        for op in ops:
            op()
        events.safe_emit(
            emit,
            events.PageDerived(
                # The navigation-node id -- the same id the derive layer
                # uses as `DerivedPage.id`, and the space `parent` below is
                # in -- so a consumer correlates this event with the derived
                # model exactly by id (never by title, which can collide).
                # The page-entry uuid lives on in the model's provenance.
                page_id=node.id,
                wiki=wikiref.label,
                wiki_title=wikiref.title,
                title=page.title,
                parent=node.parent or None,
                ordinal=node.ordinal,
                comment_count=comment_count,
                version_count=version_count,
                attachment_count=attachment_count,
                author=page.author,
                modified=page.modified,
            ),
        )

    if wiki_labels:
        # Scoped: no wikis-feed fetch, so there is no "discovered_from"
        # for the nav feeds below -- each was named directly, not found
        # via a feed (skip the wikis-feed enumeration).
        wikis_url = None
        # `title=label` was a placeholder standing in for a name nobody had
        # looked up, and it reached the reader, the TOC and the PDF body as
        # the wiki's name -- "eng-handbook" where "Engineering Handbook"
        # belongs. The wiki's own feed carries the real `<title>`; one request
        # per scoped wiki recovers it, mirroring blogs and forums.
        wikirefs = []
        for label in wiki_labels:
            recovered = None
            content = fetcher.get_content(
                wiki_feed_url(base_url=base_url, auth_root=auth_root, wiki_label=label),
                "wiki",
                None,
            )
            if content is not None:
                try:
                    recovered = atom.feed_title(content)
                except Exception:  # noqa: BLE001 - a name is never fatal
                    recovered = None
            # The community, from the run rather than from a feed. A scoped
            # crawl skips the wikis LIST feed, which is the only place
            # `snx:communityUuid` appears, so the marker was absent from the
            # archive and the reader filed a community's own wiki under "Not
            # in a community". A community capture already knows which
            # community it is crawling: that is an input, not something to
            # rediscover, and recording it invents no deployment behaviour --
            # unlike assuming a wiki's own feed carries the marker, which no
            # capture has ever shown.
            wikirefs.append(
                WikiRef(
                    uuid=label,
                    label=label,
                    title=recovered or label,
                    community_uuid=community_uuid,
                    community_title=community_title,
                )
            )
    else:
        wikis_url = wikis_feed_url(base_url=base_url, auth_root=auth_root)
        # ALWAYS: this feed is what says which wikis EXIST, and what they are
        # called. Read from the archive -- `paginate`'s default -- an update
        # could only ever list the wikis it already had, so one created since
        # the last capture was never seen and a rename never arrived.
        wikirefs = paginate(wikis_url, parse_wikis_feed, "wiki", None, policy=CachePolicy.ALWAYS)

    for wikiref in wikirefs:
        # Stop ends the RUN, not just this container -- hence the check
        # here, in the outer loop. Checking only inside would finish the
        # current container and go straight on to the next, which across a
        # deployment's worth of them is a button that does nothing.
        if stop_event is not None and stop_event.is_set():
            break
        nav_url = (
            f"{nav_feed_url(base_url=base_url, auth_root=auth_root, wiki_label=wikiref.label)}"
            "?tree=true&acls=true"
        )
        # ALWAYS, because this feed is the authority on which pages the wiki
        # HAS. Read from the archive -- which the default `IF_MISSING` did --
        # an update could only ever see the pages that were already captured,
        # so a page written since the last run was never discovered and the
        # run reported success. `CachePolicy` names this case in its own
        # docstring: serving a document that reveals change out of the archive
        # makes every update find nothing.
        #
        # It costs one request per wiki. Everything the feed points at is
        # still judged on its own date, so an unchanged page is not refetched.
        nav_content = fetcher.get_content(nav_url, "nav", wikis_url, policy=CachePolicy.ALWAYS)
        nav_tree = (
            fetcher.parse_or_none(parse_nav_feed, nav_content, url=nav_url)
            if nav_content is not None
            else None
        )
        if nav_tree is None:
            continue

        for orphan_id in report.detect_orphans(nav_tree.nodes):
            issues.mark()
            all_orphans.append(orphan_id)
            events.safe_emit(
                emit,
                events.Warning(
                    kind="orphan",
                    detail="page's parent uuid resolves to no crawled page",
                    ref=orphan_id,
                ),
            )

        # Only in `update`: `resume` serves the entries from the archive
        # regardless of policy, and `refresh` refetches them regardless, so
        # in both the index would be a request that buys nothing.
        index = (
            _page_date_index(
                fetcher,
                paginate,
                base_url=base_url,
                auth_root=auth_root,
                wiki_label=wikiref.label,
                discovered_from=nav_url,
            )
            if getattr(config, "fetch", "resume") == "update"
            else None
        )
        for node in nav_tree.nodes:
            if stop_event is not None and stop_event.is_set():
                break
            if max_pages is not None and pages_crawled >= max_pages:
                break
            process_page(wikiref, node, nav_url, index)

    # Pass 2: with an author filter, fetch assets only for the kept pages and
    # surface the pruned ones so the ingest list matches the reader.
    if page_facts:
        involvements = [pf.involvement for pf in page_facts]
        for pf in page_facts:
            involvements.extend(pf.comments)
        events.safe_emit(
            emit,
            events.AuthorFilterSummary(
                kept=kept_pages,
                total=len(page_facts),
                author=author or "",
                identities=format_identities(involvements),
            ),
        )

    return finish_run(session, orphans=all_orphans, pages_crawled=pages_crawled)


# --------------------------------------------------------------------
# Stage 2 -- Blogs traversal
# --------------------------------------------------------------------
