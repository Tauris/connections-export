"""`feeds.jsonl`: one line per feed page a crawl actually fetched.

`manifest.jsonl` records one HTTP response, which is the right shape for "what
did we fetch and did it work" and the wrong shape for "what did this feed walk
consist of". The manifest is keyed by URL, and an update fetches the same
logical feed under a different URL (`?since=...`), so a reader had no way to
know those pages belonged together except by guessing from the string -- which
is what derive did, alongside a second copy of the crawler's stop condition
that drifted from it four separate ways.

This file is the missing fact, written by the only component that knows it at
the time: the paginator, as it walks. A reader replays a record instead of
re-deriving a decision.
"""

from __future__ import annotations

from pydantic import BaseModel

#: Bumped whenever a field here changes meaning. Nothing is released, so
#: readers reject an older archive rather than carrying migration code.
FEEDS_SCHEMA_VERSION = 1

FEEDS_FILENAME = "feeds.jsonl"


class FeedPage(BaseModel):
    """One page of one feed walk."""

    schema_version: int = FEEDS_SCHEMA_VERSION
    #: `paging.feed_identity` of the URL -- the logical feed, ignoring which
    #: page or date window was asked for.
    feed_id: str
    #: The `page` parameter sent. 0 for HCL blog entry feeds, which are
    #: 0-indexed; 1 for everything else.
    page: int
    #: The `since=` cutoff this page was fetched under, or `None` for a
    #: canonical (unfiltered) page. Pages from different windows are separate
    #: runs over the same feed, not continuations of one another.
    window: str | None = None
    #: The exact URL fetched, so a reader can find the body in the manifest.
    url: str
    #: The pagination signature, parsed by the caller -- the archive never
    #: parses bodies itself. `item_count == page_size` with `has_next` false is
    #: how a deployment silently truncates a feed. `ManifestRecord` has carried
    #: these three fields since the archive was designed and `Archive.report`
    #: has read them the whole time, wired out to a CLI warning and a GUI
    #: event; no writer ever populated them, so the detector could not fire.
    item_count: int | None = None
    page_size: int | None = None
    has_next: bool | None = None
    #: The feed's own `totalResults`, when it carries one. This is what tells a
    #: full final page with no `rel="next"` apart from a silently truncated
    #: one: if the server said how many there are and we have them all, an
    #: exact-boundary page is a clean end, not a suspicious one. Without it the
    #: truncation heuristic fires on every feed whose count is an exact
    #: multiple of the page size -- which is why it could only ever have been
    #: evaluated against hand-written records.
    total_results: int | None = None
    run_id: str | None = None
