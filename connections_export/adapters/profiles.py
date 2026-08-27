"""Per-app transport divergences as explicit data (Per-app
capability objects). Page-size ceiling, `since`
encoding, `sortBy` vocabulary, and id infix all differ between Blogs
and Forums even though the parameter *names* are shared -- a single
constant would silently clamp or mis-encode one app or the other.
Capturing the divergence as a frozen dataclass rather than scattered
literals makes it inspectable and testable in its own right (Per-app transport divergences are
explicit).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppProfile:
    """One app's transport capabilities.

    `since_encoding` and `sort_by` are descriptive labels/vocabulary,
    not behavior -- nothing in this change encodes a request; that's a
    capture-layer concern (out of scope here).
    """

    name: str
    ps_max: int
    since_encoding: str
    sort_by: frozenset[str]
    id_infix: str


BLOGS = AppProfile(
    name="blogs",
    ps_max=50,
    since_encoding="rfc3339",
    sort_by=frozenset({"commented", "modified", "popularity", "recommended", "title"}),
    id_infix="blogs:entry-",
)

FORUMS = AppProfile(
    name="forums",
    ps_max=100,
    since_encoding="epoch_ms",
    sort_by=frozenset({"created", "modified"}),
    id_infix="forum:",
)


#: Files. `ps_max` is not documented: a live capture asked for ps=500 on a library
#: of 65 files and got all 65 with no `rel="next"`, which proves the request was
#: accepted but not where the ceiling sits. 500 is what the crawler asks for
#: elsewhere; if a real library exceeds it, the paginator follows `next` as it
#: does everywhere else.
FILES = AppProfile(
    name="files",
    ps_max=500,
    since_encoding="rfc3339",
    sort_by=frozenset({"created", "modified", "title"}),
    id_infix="files:document-",
)

#: Wikis. `ps_max` is not documented: wiki feeds have never been observed to reject
#: a page size, and no ceiling is documented. 500 matches what the crawler asks
#: for elsewhere; the paginator follows `next` regardless. `since_encoding` is
#: `"unsupported"` because a wiki feed cannot be asked "what changed since" at
#: all -- which is exactly why `crawl` reads each page's own entry to find
#: out, and why an update of a wiki costs more than an update of a blog.
WIKIS = AppProfile(
    name="wikis",
    ps_max=500,
    since_encoding="unsupported",
    sort_by=frozenset(),
    id_infix="wiki:",
)

#: Rich Content (Highlights). Discovery is a single persisted widget-layout
#: document rather than a paged feed, so `ps_max` never applies; recorded for
#: completeness so every app has an entry rather than five of six.
RTE = AppProfile(
    name="rich_content",
    ps_max=500,
    since_encoding="unsupported",
    sort_by=frozenset(),
    id_infix="rte:",
)
