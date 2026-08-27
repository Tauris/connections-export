"""Crawling the community's own artefacts -- currently its picture.

Kept beside the per-app crawls rather than inside one of them: a community is
a container for the other five, not one of them.
"""

from __future__ import annotations

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
from connections_export.crawler.apps._shared import COMMUNITY_ADAPTER_VERSION
from connections_export.crawler.engine import (
    CachePolicy,
    CrawlResult,
    begin_run,
    default_clock,
    finish_run,
)
from connections_export.crawler.events import Emit
from connections_export.http.client import HttpClient


def crawl_community_logo(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    community_uuid: str,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    run_id: str | None = None,
) -> CrawlResult:
    """Capture a community's own picture into `archive`.

    Two requests at most: the community document, and whatever image link it
    names. Captured rather than linked, so an archive read years later still
    has the picture -- which is the whole reason `{logo}` renders from the
    archive and not from the deployment.

    Where the picture lives is not something the API describes (see
    `adapters/communities`), so this follows the link the document gives
    rather than a URL shape it would have to guess at. A community that names
    none costs exactly one request and does nothing: no hunting at invented
    addresses, and no failure either -- losing a community's wiki over its
    avatar would be absurd.
    """
    from connections_export.adapters.communities import logo_href  # noqa: PLC0415

    emit = emit if emit is not None else events.default_emit
    clock = clock or default_clock
    session = begin_run(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock,
        run_id=run_id,
        adapter_version=COMMUNITY_ADAPTER_VERSION,
    )
    fetcher = session.fetcher
    base_url = session.base_url

    instance_url = (
        f"{base_url}/communities/service/atom/community/instance?communityUuid={community_uuid}"
    )
    document = fetcher.get_content(instance_url, "community", None, policy=CachePolicy.ALWAYS)
    href = logo_href(document) if document else None
    if href:
        fetcher.get_content(urljoin(base_url + "/", href), "asset", instance_url)

    return finish_run(session, orphans=[], pages_crawled=1 if href else 0)
