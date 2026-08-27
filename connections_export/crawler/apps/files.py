"""Crawling a community's file library: one flat library feed, one folder
feed, one download per file.

Content comes from the flat feed alone -- a live capture settled that it is
complete -- and folders are fetched for association only, filtered against the
library so someone's personal documents never land in a community archive.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from connections_export import paging
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).
#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.
from connections_export.crawler.apps._shared import FILES_ADAPTER_VERSION
from connections_export.crawler.author_plan import (
    Involvement,
    _norm,
    format_identities,
)
from connections_export.crawler.engine import (
    CachePolicy,
    CrawlResult,
    begin_run,
    default_clock,
    finish_run,
)
from connections_export.crawler.events import Emit
from connections_export.http.client import HttpClient


def crawl_files(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    community_uuid: str,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
    max_files: int | None = None,
    author: str | None = None,
    stop_event: threading.Event | None = None,
) -> CrawlResult:
    """Crawl one community's file library into `archive`.

    Simpler than the other three, because the shape is simpler: one page-walked
    feed of files, one feed of folders, and one download per file.

    Content comes from the FLAT library feed alone. A live capture settled that
    it is complete -- every id reachable through folders was also in the flat
    feed -- so walking folders for content would fetch the same bytes twice.
    Folders are fetched separately, for association only, and filtered against
    the library: the collection feed also names files the community does not
    own, and merging the two would put someone's personal documents into a
    community archive.
    """
    from connections_export.adapters import files as files_adapter  # noqa: PLC0415

    emit = emit if emit is not None else events.default_emit
    clock = clock or default_clock

    session = begin_run(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock,
        run_id=run_id,
        adapter_version=FILES_ADAPTER_VERSION,
        max_items=max_files,
        # What this archive was captured under. The field existed and was
        # documented ("an archive made with 'only me' cannot later be updated
        # as if it were an everyone archive") but nothing ever filled it, so
        # an archive could not say why an item's bytes were absent.
        author_filter=author,
    )
    fetcher = session.fetcher
    base_url = session.base_url

    library_url = files_adapter.community_library_url(
        base_url=base_url, community_uuid=community_uuid
    )
    entries = fetcher.paginate(
        library_url,
        files_adapter.parse_library_feed,
        "community_files",
        None,
        max_pages=paging.MAX_PAGES,
        stop_event=stop_event,
        # ALWAYS, because this feed is what REVEALS change. Files sends no
        # `since` cutoff (see `crawl_files`'s docstring), so unlike Blogs and
        # Forums there is no novel URL to make an update fetch it -- served
        # from the archive, an update found nothing at all and said so with a
        # success exit code.
        policy=CachePolicy.ALWAYS,
    )
    if max_files is not None:
        entries = entries[:max_files]

    # Folders: association only, and filtered. Never a content source.
    collection_url = files_adapter.community_collection_url(
        base_url=base_url, community_uuid=community_uuid
    )
    # One page: folders are few, and this feed is never a content source.
    folders_raw = fetcher.paginate(
        collection_url,
        files_adapter.parse_collection_feed,
        "community_folders",
        None,
        max_pages=1,
        stop_event=stop_event,
        # Folder membership changes without any file changing, and this is one
        # cheap request.
        policy=CachePolicy.ALWAYS,
    )
    folders = files_adapter.folders_limited_to(folders_raw or [], entries)
    folder_names = {file_id: folder.name for folder in folders for file_id in folder.file_ids}

    author_target = _norm(author)
    captured = 0
    #: Everyone this library credits, so the console can offer them. Files
    #: emitted no summary at all, so its people were absent from the author
    #: picker -- unofferable, and therefore unfilterable, because the stored
    #: form of a name is not guessable.
    involvements: list[Involvement] = []
    for item in entries:
        if stop_event is not None and stop_event.is_set():
            break
        involvement = Involvement(
            author=item.author,
            author_userid=getattr(item, "author_userid", None),
            contributors=tuple(getattr(item, "contributors", ()) or ()),
        )
        involvements.append(involvement)
        # Name OR user id OR contributor credit, as wikis, blogs and forums
        # all match. Comparing the display name alone meant that filtering by
        # a user id -- which the author picker hands you whenever an identity
        # has no name -- kept every wiki page and no files at all, silently.
        if author_target and not involvement.matches(author_target):
            continue
        body_len: int | None = None
        if config.capture_assets and item.download_url:
            # The bytes ARE the file. Unlike a wiki image, skipping this leaves
            # an archive that lists documents it does not contain.
            # IF_CHANGED, not the default: the bytes are the expensive part,
            # and a download URL can be stable across versions, so presence in
            # the archive is not evidence the CONTENT is current. Gating on the
            # file's own recorded date re-downloads exactly the files that
            # moved.
            fetched = fetcher.get_content(
                item.download_url,
                "file_media",
                library_url,
                policy=CachePolicy.IF_CHANGED,
                item_date=item.updated or item.published,
            )
            body_len = len(fetched) if fetched is not None else None
        captured += 1
        events.safe_emit(
            emit,
            events.CommunityFileDerived(
                file_id=item.id,
                library=item.library_id or community_uuid,
                library_title=folder_names.get(item.id),
                name=item.name,
                size=item.size,
                version_label=item.version_label,
                bytes_captured=body_len,
                author=item.author,
            ),
        )

    # Who this library credits, so the console can offer them like any other
    # component's people.
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
