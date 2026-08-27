"""What every app's crawl needs: the run's bound paginator, and the
adapter-version stamp each traversal writes into run metadata.

Small on purpose. This is the only thing the per-app crawl modules share, and
keeping it here is what lets each of them be read without the other five.
"""

from __future__ import annotations

import threading

from connections_export import paging
from connections_export.crawler.engine import (
    CachePolicy,
    Fetcher,
)

# Re-exported so `crawler/__init__` and existing importers keep finding
# them here (they were defined in this module before the engine split).

#: The page-walk safety cap. Defined in `connections_export.paging` and read
#: THROUGH the module at each call site, never bound by value here -- a
#: by-value copy forks the cap between the crawler and derive, which is
#: precisely what happened before. Tests patch `paging.MAX_PAGES`.


#: Identifies which adapter produced this archive's run metadata
#: (crawler -- write it at run start). A plain module
#: constant per traversal entry point.
ADAPTER_VERSION = "wikis-1"
BLOGS_ADAPTER_VERSION = "blogs-1"
FORUMS_ADAPTER_VERSION = "forums-1"
FILES_ADAPTER_VERSION = "files-1"
RTE_ADAPTER_VERSION = "rte-1"
COMMUNITY_ADAPTER_VERSION = "community-1"


#: Identifies which adapter produced this archive's run metadata (the design,
#: "crawler -- write it at run start"). A plain module constant per traversal
#: entry point.
ADAPTER_VERSION = "wikis-1"
BLOGS_ADAPTER_VERSION = "blogs-1"
FORUMS_ADAPTER_VERSION = "forums-1"
FILES_ADAPTER_VERSION = "files-1"
RTE_ADAPTER_VERSION = "rte-1"
COMMUNITY_ADAPTER_VERSION = "community-1"


def _make_paginate(fetcher: Fetcher, stop_event: threading.Event | None):
    """A `paginate` bound to this run's fetcher, cap and stop signal.

    The identical eleven-line closure was pasted verbatim into `crawl`,
    `crawl_blogs` and `crawl_forums`. `paging.MAX_PAGES` is read HERE, at call
    time and through the module, so a test lowering the cap still reaches every
    walk.
    """

    def paginate(feed_base_url, parser, kind, discovered_from, *, policy=None, item_date=None):
        return fetcher.paginate(
            feed_base_url,
            parser,
            kind,
            discovered_from,
            max_pages=paging.MAX_PAGES,
            stop_event=stop_event,
            policy=policy or CachePolicy.IF_MISSING,
            item_date=item_date,
        )

    return paginate
