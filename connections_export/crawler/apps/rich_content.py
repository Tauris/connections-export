"""Crawling a community's Rich Content (Highlights) pages.

Two steps in two different applications, because that is how the deployment
serves them: the persisted widget layout lists every widget, then one GET per
initialized widget returns its body inline. A widget an owner placed but never
wrote into has no resource id and no page; those are counted and reported,
never silently dropped.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from urllib.parse import urljoin

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).
#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.
from connections_export.crawler.apps._shared import RTE_ADAPTER_VERSION
from connections_export.crawler.author_plan import (
    Involvement,
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


def crawl_rich_content(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    community_uuid: str,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
    max_pages_captured: int | None = None,
    author: str | None = None,
    stop_event: threading.Event | None = None,
) -> CrawlResult:
    """Crawl one community's Rich Content pages into `archive`.

    Two steps, in two different applications, because that is how the
    deployment serves them (the published API reference):

      1. **Discovery** -- the community's persisted widget layout, which lists
         every widget the community has. Verified readable by an ordinary
         member, which is what makes this feature usable by anyone rather than
         only by owners.
      2. **Retrieval** -- one GET per initialized widget. The body is inline in
         the Atom entry, so there is no second fetch for content.

    A widget an owner placed but never wrote into has no resource id and no
    page. Those are counted and reported, never silently dropped: the run says
    how many widgets were placed and how many had content, so "we captured
    three" cannot quietly mean "there were four".
    """
    from connections_export.adapters import rte as rte_adapter  # noqa: PLC0415

    emit = emit if emit is not None else events.default_emit
    clock = clock or default_clock

    session = begin_run(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock,
        run_id=run_id,
        adapter_version=RTE_ADAPTER_VERSION,
        max_items=max_pages_captured,
        # What this archive was captured under. The field existed and was
        # documented ("an archive made with 'only me' cannot later be updated
        # as if it were an everyone archive") but nothing ever filled it, so
        # an archive could not say why an item's bytes were absent.
        author_filter=author,
    )
    fetcher = session.fetcher
    base_url = session.base_url
    asset_capture = AssetCapture(
        fetcher=fetcher,
        base_url=base_url,
        hcl_hosts=list(config.hcl_hosts),
        enabled=config.capture_assets,
    )

    layout_url = rte_adapter.widget_layout_url(base_url=base_url, community_uuid=community_uuid)
    # ALWAYS: the widget layout is the document that REVEALS change here --
    # `CachePolicy`'s own docstring names it. Rich Content sends no `since`
    # cutoff, so there is no novel URL to make an update fetch it; served
    # from the archive, an update found no new pages at all and exited 0.
    layout = fetcher.get_content(layout_url, "widgets", None, policy=CachePolicy.ALWAYS)
    widgets = []
    if layout is not None:
        parsed = fetcher.parse_or_none(
            lambda data: rte_adapter.parse_widget_layout(data, community_uuid=community_uuid),
            layout,
            url=layout_url,
        )
        widgets = parsed or []

    initialized = [w for w in widgets if w.initialized]
    events.safe_emit(
        emit,
        events.RichContentDiscovered(
            community_uuid=community_uuid,
            placed=len(widgets),
            initialized=len(initialized),
        ),
    )
    if len(widgets) != len(initialized):
        # Not a failure -- an owner placed a widget and never wrote in it --
        # but the difference has to be visible, or the run's own count reads
        # as the whole truth.
        events.safe_emit(
            emit,
            events.Warning(
                kind="placed_not_written",
                detail=(
                    f"{len(widgets)} rich content widgets are placed on this community, "
                    f"{len(initialized)} have content. The rest were never written into "
                    "and have nothing to capture."
                ),
                ref=layout_url,
            ),
        )

    if max_pages_captured is not None:
        initialized = initialized[:max_pages_captured]

    author_target = _norm(author)
    captured = 0
    #: Everyone Highlights credits. It emitted no summary, so the people who
    #: wrote a community's front page were missing from the author picker --
    #: unofferable, and so unfilterable.
    involvements: list[Involvement] = []
    for widget in initialized:
        if stop_event is not None and stop_event.is_set():
            break
        page_url = rte_adapter.rich_content_page_url(
            base_url=base_url, community_uuid=community_uuid, resource_id=widget.resource_id
        )
        # ALWAYS as well, and deliberately: a page entry is one small request
        # and it is the only place a page's own date appears, so there is
        # nothing cheaper to gate on. A community's Highlights is a handful
        # of pages -- "small enough to re-read whole" is the design.
        body = fetcher.get_content(page_url, "rich_content", layout_url, policy=CachePolicy.ALWAYS)
        if body is None:
            continue
        page = fetcher.parse_or_none(
            lambda data, rid=widget.resource_id: rte_adapter.parse_page_entry(
                data, resource_id=rid
            ),
            body,
            url=page_url,
        )
        if page is None:
            continue
        if not page.content_html and (page.media_url or page.content_src):
            content_href = page.media_url or page.content_src
            content_url = urljoin(base_url.rstrip("/") + "/", content_href)
            # The body behind a media link IS the expensive part, so this one
            # is gated on the page's own date rather than refetched blindly.
            content_body = fetcher.get_content(
                content_url,
                "rich_content",
                page_url,
                policy=CachePolicy.IF_CHANGED,
                item_date=page.updated or page.published,
            )
            if content_body is not None:
                page = page.model_copy(
                    update={
                        "content_html": content_body.decode("utf-8", "replace"),
                        "content_src": content_url,
                    }
                )
        involvement = Involvement(
            author=page.author,
            author_userid=getattr(page, "author_userid", None),
            contributors=tuple(getattr(page, "contributors", ()) or ()),
        )
        involvements.append(involvement)
        # Name OR user id, as every other app matches.
        if author_target and not involvement.matches(author_target):
            continue
        if page.content_html:
            # Images pasted into a rich content page are the same asset problem
            # as anywhere else, and the same deployment boundary applies.
            asset_capture.scan_body(
                page.content_html.encode("utf-8"), base=page_url, discovered_from=page_url
            )
        captured += 1
        events.safe_emit(
            emit,
            events.RichContentDerived(
                resource_id=page.resource_id,
                community_uuid=community_uuid,
                title=page.title,
                version_label=page.version_label,
                author=page.author,
                body_captured=bool(page.content_html),
            ),
        )

    if involvements:
        events.safe_emit(
            emit,
            events.AuthorFilterSummary(
                kept=captured,
                total=len(involvements),
                author=author or "",
                identities=format_identities(involvements),
            ),
        )
    # Through the shared wind-down, like every other app: it is what writes
    # `completed_at`, and a run without that cannot anchor an update -- so a
    # successful capture of this component left an archive that could not be
    # updated by date. It also surfaces the archive's truncation warnings,
    # which the inline version here never did.
    return finish_run(session, orphans=[], pages_crawled=captured)
