"""Feed identity, and the crawler's rule for when a feed is exhausted.

`feed_identity` is used by BOTH sides -- the crawler stamps it onto every page
it records, and derive looks pages up by it -- so the two cannot disagree
about which URLs are the same logical feed. One definition, read by both,
rather than each side deciding for itself.

`should_stop_paging` is the CRAWLER'S rule. Derive is being moved off having a
stop condition at all: it reads the pages the crawler recorded. Two
implementations of one rule is what let them drift, and they drifted four
separate ways before this module existed -- an empty page was authoritative in
one and not the other; `totalResults` was weighed by one and not the other;
tuple items were unwrapped for dedup by one and not the other (so blog-entry
dedup silently never worked in the engine); and the page cap was bound by
value into two namespaces. Each was found and fixed on its own, in a different
week, by a different reader.

This module sits below both, and imports nothing from either.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

#: Query parameters that say WHICH SLICE of a feed is being asked for, rather
#: than which feed. Two URLs differing only in these are the same logical
#: feed: pagination (`ps`, `page`, `pageSize`) and the date window an update
#: adds (`since`, `sortBy`).
#: Safety cap on page-walking a single feed (the design, "crawler --
#: page-walking"): hitting it emits a `pagination_cap` warning and flags the
#: run rather than looping forever or truncating silently.
#:
#: Defined HERE, and read by both the crawler and derive *through this module*
#: (`paging.MAX_PAGES`, never `from...paging import MAX_PAGES`). A by-value
#: import binds the int into the importing namespace, which silently forks the
#: cap: a test lowering one bound the crawler and left derive walking to
#: 10,000. That bug was fixed once by having derive reach `crawl.MAX_PAGES`
#: through `importlib.import_module` -- a STRING module reference that no
#: rename, no grep for the import form and no linter would have caught when
#: `crawl.py` moved. One definition, read late, is what makes both of those
#: problems not exist.
MAX_PAGES = 10_000

_SLICE_PARAMS = frozenset({"ps", "page", "pageSize", "since", "sortBy"})


def feed_identity(url: str) -> str:
    """A stable key for the logical feed `url` addresses.

    Everything identifying WHICH feed is kept -- host, path, and query
    parameters like `?category=version` or `?communityUuid=` that select a
    different feed entirely. Everything identifying which slice of it is
    dropped.
    """
    split = urlsplit(url)
    kept = sorted(
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if key not in _SLICE_PARAMS
    )
    query = "&".join(f"{key}={value}" for key, value in kept)
    base = f"{split.scheme}://{split.netloc}{split.path}"
    return f"{base}?{query}" if query else base


def feed_window(url: str) -> str | None:
    """The `since=` cutoff `url` was fetched under, or `None` for a canonical
    page.

    An update walks the same feed again with a date filter. Its pages are a
    separate run over that feed, not a continuation of the first -- which is
    why a missing page must end one window's walk without ending the others.
    """
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == "since":
            return value
    return None


def feed_item_id(item: object) -> object:
    """A stable identity for a parsed feed item, for deduplication.

    Some parsers hand a paginator a TUPLE rather than a model -- the blog
    entries walk yields `(BlogPost, comments_url)` pairs so traversal keeps the
    per-entry replies href. A tuple has no `.id`, so a naive key falls through
    to `id(item)`: the Python object address, different for every freshly
    parsed object and therefore never matching. Dedup then silently never
    works on that feed, which stays invisible until a stop condition starts
    depending on the count being distinct. That is not hypothetical -- it was
    live in the engine while derive's copy handled it correctly.

    Unwrap one level, then look for a real identifier. Falling back to
    `id(item)` is the last resort: an item we genuinely cannot identify must
    count as new rather than be discarded as a duplicate.
    """
    if isinstance(item, tuple) and item:
        item = item[0]
    return getattr(item, "id", None) or getattr(item, "uuid", None) or id(item)


def should_stop_paging(
    *,
    items_on_page: int,
    new_items: int,
    distinct_so_far: int,
    page_size: int,
    next_link: str | None,
    total_results: int | None,
    duplicate_pages: int,
) -> tuple[bool, int]:
    """`(stop, duplicate_pages)` after the crawler reads one page.

    Deliberately conservative: continue while EITHER a `rel="next"` link is
    present OR the page came back full OR the server's own total says more
    remains. Stop only when all three say "no more" -- `rel="next"` is
    undocumented for Wikis, so it is never trusted alone.

    `distinct_so_far` counts NEW items only, which is the only count that can
    be compared against the server's total without a repeated page making us
    think we are done.
    """
    # An empty page is authoritative even when a deployment reports a stale or
    # inconsistent total. Otherwise a feed returning zero entries with
    # totalResults=1 walks forever -- which is exactly what derive did, because
    # this clause was in the engine and not in its copy of the rule.
    if items_on_page == 0:
        return True, duplicate_pages

    has_full_page = items_on_page == page_size
    has_more_by_total = total_results is not None and distinct_so_far < total_results

    if new_items == 0:
        # Every item on this page was already seen. Two very different causes:
        #
        # * a 0-indexed walk meeting a 1-indexed server -- page 0 and page 1
        # are the same page, and page 2 is genuinely the next one. Exactly
        # ONE overlapping page, and the total still says content remains.
        # * a deployment that ignores `page` and serves the first page
        # forever. Continuing to the cap would archive the same entries
        # thousands of times at request cost.
        #
        # One repeated page is tolerated while the total promises more; a
        # second means the page parameter is being ignored.
        duplicate_pages += 1
        return (duplicate_pages > 1 or not has_more_by_total), duplicate_pages

    duplicate_pages = 0
    # A reliable total settles an exact-full final page. Without this, every
    # feed whose count is an exact multiple of `ps` gets an unnecessary
    # empty-page probe, which can trigger retries on deployments that dislike
    # out-of-range pages.
    if total_results is not None and not has_more_by_total:
        return True, duplicate_pages
    if next_link is None and not has_full_page and not has_more_by_total:
        return True, duplicate_pages
    return False, duplicate_pages
