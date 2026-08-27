"""The crawler's traversal entry points, re-exported.

The real code lives one level down, in `crawler/apps/`: one module per
application, mirroring the `adapters/` norm. This module held all six in 1,540
lines, which was not only a readability cost -- a module that large cannot be
imported from cleanly, and it was the single biggest source of the repo's
function-local imports.

This file stays as the address those entry points have always had. Nothing
should be added here: a new app gets a module in `crawler/apps/` and a line in
`crawler/dispatch.py`.

Which crawl runs for a given selection is `crawler.dispatch`'s job. That was
written out by hand in three places, with three different coverages, and one of
them silently omitted an entire app.
"""

from __future__ import annotations

from connections_export import paging
from connections_export.crawler.apps._shared import (
    ADAPTER_VERSION,
    BLOGS_ADAPTER_VERSION,
    COMMUNITY_ADAPTER_VERSION,
    FILES_ADAPTER_VERSION,
    FORUMS_ADAPTER_VERSION,
    RTE_ADAPTER_VERSION,
)
from connections_export.crawler.apps.blogs import crawl_blogs
from connections_export.crawler.apps.community import crawl_community_logo
from connections_export.crawler.apps.files import crawl_files
from connections_export.crawler.apps.forums import crawl_forums
from connections_export.crawler.apps.rich_content import crawl_rich_content
from connections_export.crawler.apps.wikis import crawl
from connections_export.crawler.engine import CrawlError, CrawlResult

#: The page-walk safety cap, re-exported from `connections_export.paging` for
#: callers that still reach for it here. Read THROUGH `paging` at every call
#: site rather than bound by value -- a by-value copy forks the cap between the
#: crawler and derive, which is precisely what happened before.
MAX_PAGES = paging.MAX_PAGES

__all__ = [
    "ADAPTER_VERSION",
    "BLOGS_ADAPTER_VERSION",
    "COMMUNITY_ADAPTER_VERSION",
    "CrawlError",
    "CrawlResult",
    "FILES_ADAPTER_VERSION",
    "FORUMS_ADAPTER_VERSION",
    "MAX_PAGES",
    "RTE_ADAPTER_VERSION",
    "crawl",
    "crawl_blogs",
    "crawl_community_logo",
    "crawl_files",
    "crawl_forums",
    "crawl_rich_content",
]
