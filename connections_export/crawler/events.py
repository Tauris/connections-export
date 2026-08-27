"""The crawler's progress event stream.

Typed events, emitted through an injected `emit: Callable[[Event], None]`.
This is the GUI contract `` is blocked on -- field names
are load-bearing, do not rename without updating that change too.

Emitting is fire-and-forget: `safe_emit` wraps every call so a slow or
raising consumer can never affect crawl control flow (A misbehaving consumer cannot break the
crawl).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ResourceKind = Literal[
    "wiki",
    "nav",
    "page",
    "body",
    "comments",
    "versions",
    "attachments",
    "asset",
    # Stage 2 -- Blogs & Forums traversal (additive; Wikis kinds above
    # are unchanged). A list feed is tagged by the item kind it yields:
    # the blogs-list feed -> "blog", a blog's entries feed -> "post"
    # (its comments feed reuses "comments"); the forums-list feed ->
    # "forum", a forum's topics feed -> "topic", a topic's replies feed
    # -> "reply".
    "blog",
    "post",
    "forum",
    "topic",
    "reply",
    # Rich Content: the community widget layout (discovery) and one page
    # (retrieval). Two kinds because they are two applications -- a failure in
    # the layout means "we found no pages", a failure in a page means "we lost
    # one we knew about", and those must not read the same in a report.
    "widgets",
    "rich_content",
    # A community's own document, and the picture it names.
    "community",
]
FailedKind = Literal["transport", "timeout", "http_error", "unparsed"]
#: `placed_not_written` is the odd one out and deliberately so: the other four
#: name a defect in the capture, while it names a difference in the SOURCE that
#: the run must still record -- an owner placed a rich content area and never
#: typed in it. Before it existed the only vocabulary available was
#: `truncation`, so a healthy run reported "Possible silent truncation", and an
#: alarm that fires on healthy runs is one people learn to ignore.
WarningKind = Literal[
    "truncation", "orphan", "external_asset_skipped", "pagination_cap", "placed_not_written"
]


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    base_url: str
    source_version: str


@dataclass(frozen=True)
class Discovered:
    url: str
    kind: ResourceKind
    discovered_from: str | None = None


@dataclass(frozen=True)
class Fetching:
    url: str
    kind: ResourceKind


@dataclass(frozen=True)
class Fetched:
    url: str
    kind: ResourceKind
    status: int | None
    num_bytes: int
    from_cache: bool = False


@dataclass(frozen=True)
class Retried:
    url: str
    attempt: int
    reason: str


@dataclass(frozen=True)
class Stopped:
    """A request abandoned because the run was asked to stop.

    Not a failure: nothing was lost that the user did not choose to give up,
    and counting it as one would make the failure count -- which exists to
    report data loss -- cry wolf every time someone pressed Stop.

    It exists so that everything discovered reaches a conclusion. The stopped
    branch was the one exit from the fetch path with no terminal event, which
    is why a live ingest reported 2,958 discovered against 2,957 concluded,
    and why the counters could not be used to notice a real loss.
    """

    url: str
    kind: ResourceKind


@dataclass(frozen=True)
class Failed:
    url: str
    kind: FailedKind
    error: str


@dataclass(frozen=True)
class PageDerived:
    #: The navigation-node id -- identical to the derived model's
    #: `DerivedPage.id`, and the id space `parent` is in -- so a consumer
    #: (the GUI) correlates this event with the derived model exactly by
    #: id, never by a title that could collide. (Not the page-entry uuid,
    #: which the model keeps in per-page provenance.)
    page_id: str
    wiki: str
    wiki_title: str
    title: str
    parent: str | None
    ordinal: int
    comment_count: int
    version_count: int
    attachment_count: int
    author: str | None = None
    modified: str | None = None


@dataclass(frozen=True)
class BlogPostDerived:
    """Emitted once per fully-traversed blog post (the Blogs analogue of
    `PageDerived`): its comments feed has been page-walked and counted.
    `post_id` is the `entry_uuid`-stripped bare uuid (the same id a
    downstream derived model would key on, never a colliding title);
    `blog` is the parent blog's bare uuid, `blog_title` its title -- so
    a progress UI can group posts under their blog. `comment_count` is
    the number of comments recovered from the (page-walked) comments
    feed; `comments_enabled` is the entry's `app:control` toggle (a post
    with comments disabled but a nonzero legacy count is a UI-worthy
    signal)."""

    post_id: str
    blog: str
    blog_title: str | None
    title: str | None
    comment_count: int
    comments_enabled: bool | None = None
    author: str | None = None
    published: str | None = None


@dataclass(frozen=True)
class ForumTopicDerived:
    """Emitted once per fully-traversed forum topic (the Forums analogue
    of `PageDerived`): its FLAT replies feed has been page-walked,
    counted, and (by the caller) rebuilt into a tree. `topic_id` is the
    bare uuid; `forum` is the parent forum's bare uuid, `forum_title`
    its title. `reply_count` is the number of replies recovered across
    all feed pages; `flags` are the topic's `sn/flags` terms
    (`pinned`/`locked`/`question`/`answered`) as a sorted tuple (kept a
    tuple, not a set, so the event stays a hashable frozen dataclass)."""

    topic_id: str
    forum: str
    forum_title: str | None
    title: str | None
    reply_count: int
    flags: tuple[str, ...] = ()
    author: str | None = None
    published: str | None = None


@dataclass(frozen=True)
class RichContentDerived:
    """Emitted once per Rich Content page captured from a community.

    `resource_id` is the `widgetResourceId` the page was addressed by, which is
    how it ties back to the widget layout entry it was discovered from.
    `body_captured` is False when the entry arrived without content -- the
    difference between "we have this page" and "we know a page is there".
    """

    resource_id: str
    community_uuid: str
    title: str | None
    version_label: str | None = None
    author: str | None = None
    body_captured: bool = False


@dataclass(frozen=True)
class RichContentDiscovered:
    """Emitted once, after the widget layout is read.

    Carries what the layout SAID before anything was fetched, so a run can
    report "8 widgets placed, 7 with content" rather than only the number it
    managed to retrieve. A capture that reports its own yield as the whole
    truth is the failure mode this exists to prevent.
    """

    community_uuid: str
    placed: int
    initialized: int


@dataclass(frozen=True)
class CommunityFileDerived:
    """Emitted once per file captured from a community library.

    The Files analogue of `PageDerived`. `name` is what Connections holds,
    verbatim -- the filesystem-safe name is decided when a package is written,
    not here. `bytes_captured` is None when the download failed or assets were
    disabled, which is the difference between "we have this file" and "we know
    of this file".
    """

    file_id: str
    library: str
    library_title: str | None
    name: str | None
    size: int | None = None
    version_label: str | None = None
    bytes_captured: int | None = None
    author: str | None = None


@dataclass(frozen=True)
class Warning:
    kind: WarningKind
    detail: str
    ref: str | None = None


@dataclass(frozen=True)
class AuthorFilterSummary:
    """Emitted once at the end of an author-filtered crawl: how many entities
    were kept vs seen, and a sample of the DISTINCT author identities actually
    present (name + userid) -- so if the filter matched little/nothing, the
    user can see the real stored form and copy the exact value to filter by.
    `kept == 0` is the "your filter matched nothing" signal."""

    kept: int
    total: int
    author: str
    identities: tuple[str, ...] = ()  # e.g. "Jane Doe (uid: jdoe)"


@dataclass(frozen=True)
class Pruned:
    """An entity crawled for structure/text but pruned by the author filter:
    its images/attachments were NOT fetched and it is not in the export. Seen,
    not kept -- surfaced so the ingest list matches the reader, never a silent
    drop (two-pass author crawl)."""

    kind: str  # "page" | "post" | "topic"
    id: str
    reason: str = "author-filter"


@dataclass(frozen=True)
class RunComplete:
    report: Any


Event = (
    RunStarted
    | Discovered
    | Fetching
    | Fetched
    | Retried
    | Failed
    | Stopped
    | PageDerived
    | BlogPostDerived
    | ForumTopicDerived
    | CommunityFileDerived
    | RichContentDerived
    | RichContentDiscovered
    | Warning
    | Pruned
    | AuthorFilterSummary
    | RunComplete
)

Emit = Callable[[Event], None]


def default_emit(event: Event) -> None:
    """The default `emit`: does nothing. A crawl with no injected
    consumer still runs to completion."""


def safe_emit(emit: Emit, event: Event) -> None:
    """Call `emit(event)`, swallowing any exception the consumer raises.
    The single seam that makes emission fire-and-forget -- every call
    site in the crawler goes through this, never `emit` directly."""
    try:
        emit(event)
    except Exception:
        pass
