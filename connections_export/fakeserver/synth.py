"""Builds a `WikiSet` from a declarative `SynthSeed`.

Determinism is load-bearing (Deterministic synthesis
from a seed): every uuid and every timestamp comes from a
`random.Random(seed.seed)` instance and a monotonic counter, never from
`uuid.uuid4` or the wall clock. The same seed must produce
byte-identical output every time — `connections_export.archive` holds the same
standard for the same reason (its docstrings forbid wall-clock reads).
"""

import html
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from connections_export.fakeserver.content import (
    blog_body,
    page_comment,
    post_comment,
    topic_body,
    topic_reply,
)
from connections_export.fakeserver.model import (
    BlogSet,
    FakeAttachment,
    FakeBlog,
    FakeBlogComment,
    FakeBlogPost,
    FakeComment,
    FakeCommunityFile,
    FakeCommunityFolder,
    FakeFileLibrary,
    FakeForum,
    FakeForumReply,
    FakeForumTopic,
    FakePage,
    FakeRichContentPage,
    FakeRichContentSet,
    FakeVersion,
    FakeWiki,
    FileSet,
    ForumSet,
    RichContentSet,
    WikiSet,
    person_uid,
)

#: Deterministic, non-wall-clock timestamp arithmetic: every "tick" of
#: the seeded clock advances by this many seconds. `base_timestamp`
#: anchors the whole dataset to a fixed instant supplied by the seed.
_TICK_SECONDS = 3600

_RTL_TITLE = "עמוד בדיקה ‏mixed‎ page \U0001f9ea"
_UNICODE_LABEL_SUFFIX = "unicode"


@dataclass
class SynthSeed:
    """A declarative recipe for a synthesized dataset. All output is a
    pure function of these fields plus the RNG they seed — no other
    input (env, clock, filesystem) may influence synthesis.
    """

    seed: int = 0
    wiki_count: int = 1
    depth: int = 2
    pages_per_level: int = 2
    comments_per_page: int = 0
    versions_per_page: int = 1
    attachments_per_page: int = 0
    tags_per_page: int = 0
    #: Draw human-friendly wiki/page titles, authors, and comment text from
    #: curated pools (for demos). Off by default so structural tests keep
    #: their deterministic `p0` / `Wiki 0` / `Commenter 0` names.
    human_names: bool = False
    #: Add one late reply to an otherwise-unchanged topic, so a demo can show
    #: the comments hole and the "also re-check replies" option catching it.
    #: Off by default: it changes reply counts, and the structural tests assert
    #: exact ones. On for the demo dataset, which is what it is for.
    late_reply: bool = False
    base_timestamp: int = 1_600_000_000  # epoch seconds; never wall-clock

    # -- Blogs (Stage 2, all not documented). Read only by synthesize_blogs.
    blog_count: int = 2
    posts_per_blog: int = 3
    comments_per_post: int = 3
    tags_per_post: int = 2
    # -- Forums (Stage 2, all not documented). Read only by synthesize_forums.
    forum_count: int = 2
    topics_per_forum: int = 3
    #: Top-level replies per topic (each `ref`s the topic directly).
    replies_per_topic: int = 2
    #: Extra nested-reply levels hung under the first top-level reply of
    #: each topic, so `build_reply_tree` is exercised on real depth.
    reply_tree_depth: int = 3

    # Nasty-case toggles.
    deep_nesting: bool = False
    deep_nesting_depth: int = 12
    high_comment_count: bool = False
    high_comment_count_n: int = 250
    unicode_rtl: bool = False
    empty_pages: bool = False
    duplicate_ordinals: bool = False


class _Clock:
    """A seeded, monotonically-advancing fake clock. Never reads the
    wall clock; every value is `base_timestamp + n * tick`."""

    def __init__(self, base_timestamp: int):
        self._next = base_timestamp
        self.last_ms: int = base_timestamp * 1000

    def tick(self) -> str:
        ts = self._next
        self._next += _TICK_SECONDS
        self.last_ms = ts * 1000
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def jump(self, days: int) -> None:
        """Advance by whole days without emitting a timestamp.

        The uniform one-hour tick is right within a thread -- a comment an
        hour after the post it answers -- and wrong between them: it made a
        blog's whole history span an afternoon, so every entry in the demo
        carried effectively the same date. Callers jump between top-level
        entries so a container covers a plausible stretch of time.
        """
        self._next += days * 86400


class _IdFactory:
    """Deterministic uuid4-shaped identifiers from a seeded RNG. Never
    `uuid.uuid4` (which reads OS randomness, not the seed)."""

    def __init__(self, rng):
        self._rng = rng

    def next(self) -> str:
        return str(UUID(bytes=self._rng.randbytes(16), version=4))


def _id_rng(seed: "SynthSeed", app: str) -> random.Random:
    """The id stream for one app.

    Every synthesizer seeded its `_IdFactory` from `random.Random(seed.seed)` --
    the same seed for all five, so the wiki's Nth id, the blog's Nth id and the
    file's Nth id were the SAME uuid. One demo community run had six ids claimed
    by two apps at once: a blog post and a community file sharing a uuid, a file
    and a forum topic, a file and a highlights resource. Real ids do not do that,
    and anything keying entities by id alone silently loses one of each pair --
    the live console dropped two of the community's eight files exactly this way.

    Salting per app keeps the fixture perfectly deterministic (`random.Random`
    derives its state from a string via SHA-512, not `hash`, so this does not
    move between runs or interpreters) while making its ids behave like ids.
    """
    return random.Random(f"{seed.seed}:{app}")


def _body_html(
    *,
    wiki_label: str,
    page_label: str,
    auth_root: str,
    other_label: str,
    page_title: str,
    wiki_title: str,
    sibling_blog_handle: str | None = None,
    sibling_blog_slug: str | None = None,
) -> str:
    """A page body that reads like a real migrated wiki page — a 1:1
    match for the design prototype's curated pages (`static/console.html`,
    `genRecord`): a heading, two prose paragraphs naming the wiki, and an
    author-styled callout (`.authors-note`, the prototype's own inline
    CSS) — *and* still exercises every embedded-asset/link/CSS case
    the design requires: a same-host image, a cross-app image, internal
    and external links, an author `<style>` block, inline `style="..."`
    attributes, and HCL platform classes (`lotusWiki`/`wikiPage`) — and
    never a `<script>`. Titles are HTML-escaped (a page like "Backup &
    Restore" carries an `&`).
    """
    same_host_img = (
        f"/wikis/{auth_root}/api/wiki/{wiki_label}/page/{page_label}/media/img/diagram.png"
    )
    cross_app_img = "/files/basic/api/library/lib1/document/doc1/media/logo.png"
    internal_href = f"/wikis/{auth_root}/api/wiki/{wiki_label}/page/{other_label}/entry"
    # A link to a SIBLING APP in the same community. This is what makes phased
    # capture testable and demonstrable: captured on its own, a wiki records
    # this as external, and it only resolves once the blog is in the archive
    # too -- whether that happened in the same run or a later one.
    cross_app_href = (
        f"/blogs/{sibling_blog_handle}/entry/{sibling_blog_slug}"
        if sibling_blog_handle and sibling_blog_slug
        else None
    )
    heading = html.escape(page_title)
    wiki_name = html.escape(wiki_title)
    return (
        "<style>\n"
        "  .authors-note { border-left: 3px solid #c084fc; background: #f3e8ff; "
        "padding: 8px 12px; }\n"
        "  .lotusWiki .author-heading { color: #204060; }\n"
        "</style>\n"
        '<div class="lotusWiki wikiPage" style="padding: 4px;">\n'
        f'  <h1 class="author-heading">{heading}</h1>\n'
        f"  <p>This page is part of the {wiki_name}. It was migrated with its full "
        "revision history and comment thread intact.</p>\n"
        "  <p>The section below was styled by its author using inline CSS — "
        "preserved verbatim on export.</p>\n"
        '  <div class="authors-note" style="font-weight: 500;">Author callout: keep '
        "this in sync with the release checklist before every deploy.</div>\n"
        f'  <img src="{same_host_img}" alt="Architecture diagram" />\n'
        f'  <img src="{cross_app_img}" alt="Company logo" />\n'
        f'  <p>See also <a href="{internal_href}">a related page</a> and the '
        '<a href="https://example.com">public reference</a>.</p>\n'
        + (
            f"  <p>The decision behind this is written up in "
            f'<a href="{cross_app_href}">a post on the team blog</a>.</p>\n'
            if cross_app_href
            else ""
        )
        + "</div>\n"
    )


# --- human-name pools (opt-in via SynthSeed.human_names, for demos) ------
# Placeholder content only (no real hosts/people) -- keeps the hygiene
# guard green. Selection is a stable hash of a key string, so output stays
# deterministic per seed.
_WIKI_TITLES = (
    "Engineering Handbook",
    "Product Wiki",
    "Operations KB",
    "Design System",
    "Security Playbook",
    "Data Platform",
    "Support Runbooks",
    "Onboarding Hub",
)
# Themed page titles, one pool per wiki (indexed by wiki position, aligned
# with _WIKI_TITLES) so each wiki reads as a coherent set — like the design
# prototype's curated data — rather than a scatter of unrelated titles.
_WIKI_PAGE_POOLS = (
    (
        "Onboarding",
        "Dev Environment",
        "Coding Standards",
        "Code Review",
        "Release Process",
        "Hotfix Runbook",
        "On-Call Guide",
        "Incident Retro",
    ),
    (
        "Vision 2026",
        "Roadmap",
        "Q3 Themes",
        "Personas",
        "Competitor Notes",
        "Pricing Rationale",
        "Launch Checklist",
        "Success Metrics",
    ),
    (
        "Runbooks Index",
        "Backup & Restore",
        "DR Playbook",
        "Network Diagram",
        "Access Requests",
        "Vendor Contacts",
        "Escalation Paths",
        "Change Log",
    ),
    (
        "Design Principles",
        "Color & Type",
        "Components",
        "Iconography",
        "Accessibility",
        "Content Style",
        "Motion",
        "Design Tokens",
    ),
    (
        "Threat Model",
        "Access Control",
        "Incident Response",
        "Secrets Management",
        "Audit Checklist",
        "Vulnerability Triage",
        "Data Classification",
        "Hardening Guide",
    ),
    (
        "Pipelines",
        "Warehouse Schema",
        "Ingestion",
        "Data Quality",
        "Lineage",
        "Retention Policy",
        "Cost Controls",
        "Dashboards",
    ),
    (
        "First Response",
        "Common Issues",
        "Refund Policy",
        "Escalation Matrix",
        "Macros",
        "SLA Targets",
        "Known Bugs",
        "Contact Directory",
    ),
    (
        "Welcome",
        "Your First Week",
        "Tools & Access",
        "Team Directory",
        "Benefits",
        "Culture",
        "Glossary",
        "FAQ",
    ),
)

# Blog/forum pools mirror the wiki pools above in shape (one coherent,
# themed pool per blog/forum) but use their own vocabulary so a demo
# never shows a blog or forum masquerading as a wiki (e.g. "Whole blog:
# Engineering Handbook") -- blogs read as blog posts, forums read as
# discussion threads.
_BLOG_NAMES = (
    "Engineering Blog",
    "Product Updates",
    "Team Notes",
    "Release Notes",
    "Field Report",
    "Customer Stories",
    "Design Diary",
    "Platform Notes",
)
# Themed post titles, one pool per blog (indexed by blog position, aligned
# with _BLOG_NAMES) so each blog's posts read as a coherent series.
_BLOG_POST_TITLES = (
    (
        "What we shipped in Q3",
        "Postmortem: the Friday outage",
        "Why we moved off the old exporter",
        "Migrating our test suite to pytest",
        "Three lessons from the migration",
        "How we cut build times in half",
        "Retiring the legacy importer",
        "On-call, six months in",
    ),
    (
        "Welcome to the new starters",
        "Introducing the new dashboard",
        "Faster search, fewer clicks",
        "What's new this release",
        "Feedback we heard and acted on",
        "A cleaner onboarding flow",
        "Dark mode is here",
        "Roadmap check-in",
    ),
    (
        "How we run retros now",
        "New faces on the team",
        "Our hybrid-work experiment",
        "Notes from the offsite",
        "What we're reading this month",
        "A day in the life of support",
        "Hiring: what we look for",
        "Saying goodbye to a teammate",
    ),
    (
        "Version 8.0: what's inside",
        "Patch notes: stability fixes",
        "Deprecating the old API",
        "Breaking change: auth headers",
        "Performance improvements this release",
        "Bug bash results",
        "Upgrade guide for 8.0",
        "Known issues and workarounds",
    ),
    (
        "Notes from a customer visit",
        "What our biggest client taught us",
        "Rolling out to 500 users",
        "Lessons from a failed pilot",
        "Support tickets we learned from",
        "A week shadowing the help desk",
        "What partners are asking for",
        "Field notes: the EU rollout",
    ),
    (
        "How Acme cut onboarding time in half",
        "A migration that just worked",
        "From spreadsheets to a real workflow",
        "Scaling support without scaling headcount",
        "Why they switched from the old tool",
        "Small team, big rollout",
        "The integration that saved us hours",
        "What success looks like a year in",
    ),
    (
        "Rethinking the empty states",
        "A new type scale",
        "Why we simplified the nav",
        "Prototyping in the open",
        "Color contrast, take three",
        "Notes from a usability session",
        "Small tweaks, big difference",
        "Retiring the old icon set",
    ),
    (
        "Incident review: what changed",
        "Capacity planning for next quarter",
        "Why we added rate limiting",
        "Notes on the database migration",
        "Monitoring: what we watch and why",
        "The postmortem process, revisited",
        "Scaling the ingestion pipeline",
        "On call again already",
    ),
)
_FORUM_NAMES = (
    "Help & Support",
    "General Discussion",
    "Dev Q&A",
    "Announcements",
    "Feature Requests",
    "Bug Reports",
    "Off Topic",
    "Admin Corner",
)
# Themed topic titles, one pool per forum (indexed by forum position,
# aligned with _FORUM_NAMES) -- phrased as questions/discussions/proposals
# rather than wiki page titles.
_FORUM_TOPIC_TITLES = (
    (
        "How do I reset my password?",
        "Export is stuck at 90%, any ideas?",
        "Best practice for large wiki exports?",
        "Anyone else seeing slow blog feeds?",
        "Where do I find my API token?",
        "Attachments not downloading, help?",
        "How to migrate off the old importer?",
        "Why did my scheduled crawl fail?",
    ),
    (
        "What's everyone working on this week?",
        "Favorite productivity tips?",
        "Anyone attending the user conference?",
        "Show and tell: what did you build?",
        "Coffee chat: remote work setups",
        "What tools do you swear by?",
        "Weekend reading recommendations",
        "Introduce yourself here",
    ),
    (
        "How do I configure single sign-on?",
        "Best way to paginate the REST API?",
        "Rate limits on the export endpoint?",
        "Anyone hit a race condition in the crawler?",
        "How to mock the fake server in tests?",
        "Recommended retry strategy for 429s?",
        "Why is my UUID not matching the spec?",
        "Best practice for idempotent imports?",
    ),
    (
        "Proposal: standardise our runbooks",
        "New release: 8.0 is out",
        "Scheduled maintenance this weekend",
        "Deprecation notice: old API sunset",
        "Welcome our newest moderators",
        "Survey: help us prioritize the roadmap",
        "Office hours starting next month",
        "Changelog now published weekly",
    ),
    (
        "Please add bulk export for forums",
        "Request: dark mode for the reader",
        "Can we get CSV export too?",
        "Support for nested tags?",
        "Would love a keyboard shortcut for search",
        "Request: webhook on export complete",
        "Please support custom timestamps",
        "Idea: diff view between versions",
    ),
    (
        "Export crashes on unicode titles",
        "Broken link in the exported wiki",
        "Forum replies out of order after export",
        "Timestamps off by one hour?",
        "Duplicate attachments after re-run",
        "Blog comments missing after migration",
        "Reader shows blank page on load",
        "Crawler hangs on empty pages",
    ),
    (
        "What are you reading right now?",
        "Best coffee near the office?",
        "Recommend a podcast for commutes",
        "Anyone else into home automation?",
        "Favorite conference talk this year?",
        "Board game night, anyone in?",
        "What's your desk setup like?",
        "Best team lunch spot?",
    ),
    (
        "Reminder: renew your access badge",
        "Please update your on-call info",
        "New expense policy starting next quarter",
        "Security training due this month",
        "Office move: what you need to know",
        "Parking changes starting Monday",
        "Please RSVP for the town hall",
        "Benefits enrollment closes Friday",
    ),
)
_PEOPLE = (
    "A. Okafor",
    "M. Lindqvist",
    "R. Delgado",
    "J. Weber",
    "S. Nakamura",
    "P. Novak",
    "L. Haddad",
    "T. Bianchi",
)
#: Who the demo signs you in as. A demo where nobody is you cannot answer
#: "only me" -- and that question is the whole reason an author filter exists,
#: so it was the one path the demo could not exercise.
#:
#: This person is chosen for authoring in EVERY component (wiki, blog, forum,
#: files, Highlights), so filtering to them leaves something in each rather
#: than an export that looks broken.
IMPERSONATED_PERSON = "M. Lindqvist"


def impersonated_person(seed: SynthSeed) -> str:
    """Who a capture of `seed`'s data is running as."""
    return IMPERSONATED_PERSON if seed.human_names else "Fake Author"


_FILENAMES = ("spec.pdf", "notes.docx", "metrics.xlsx", "export.zip", "diagram.png")


def _pick(pool: tuple, key: str) -> str:
    """Deterministically choose an item from `pool` by a stable hash of
    `key` -- reproducible across runs (no `hash` randomization)."""
    h = 2166136261
    for ch in key:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return pool[h % len(pool)]


def _others(pool: tuple, exclude: str) -> tuple:
    """`pool` without `exclude` -- so the person commenting on a page or a
    post is never, by coincidence of the hash, its own author. Only replies
    written as the author's (`content`'s `OP| ` mark) carry that name."""
    return tuple(item for item in pool if item != exclude) or pool


def _make_page(
    *,
    ids: _IdFactory,
    clock: _Clock,
    seed: SynthSeed,
    label: str,
    title: str,
    parent_uuid: str | None,
    ordinal: int,
    wiki_label: str,
    wiki_title: str,
    auth_root: str,
    empty: bool = False,
    n_comments: int | None = None,
) -> FakePage:
    created = clock.tick()
    modified = clock.tick()
    n_comments = seed.comments_per_page if n_comments is None else n_comments

    human = seed.human_names
    page_author = _pick(_PEOPLE, label) if human else "Fake Author"
    # Comments come from `content.page_comment`, written per page title, so
    # a page's comments are about THAT page and an answer is left by the
    # page's own author. Picking them by hash from one pool shared with blog
    # posts gives a page with no screenshot a comment about a screenshot, and
    # sends a page with 41 comments round the same ten lines four times.
    comments = []
    for i in range(n_comments):
        if human:
            text, by_author = page_comment(title, i)
            comment_author = (
                page_author if by_author else _pick(_others(_PEOPLE, page_author), f"{label}:c{i}")
            )
        else:
            text, comment_author = f"Comment {i} on {label}", f"Commenter {i}"
        comments.append(
            FakeComment(
                uuid=ids.next(),
                author=comment_author,
                content_html=f"<p>{text}</p>",
                published=clock.tick(),
                updated=clock.tick(),
                title=f"Comment {i}",
            )
        )
    versions = [
        FakeVersion(
            uuid=ids.next(),
            version_label=i + 1,
            author=_pick(_PEOPLE, f"{label}:v{i}") if human else "Fake Author",
            created=clock.tick(),
            content_html=f"<p>Version {i + 1} of {label}</p>",
        )
        for i in range(max(seed.versions_per_page, 1))
    ]
    attachments = [
        FakeAttachment(
            uuid=ids.next(),
            filename=_pick(_FILENAMES, f"{label}:a{i}") if human else f"attachment-{i}.bin",
            content_type="application/octet-stream",
            size=1000 + i,
            author=_pick(_PEOPLE, f"{label}:a{i}") if human else "Fake Author",
            created=clock.tick(),
        )
        for i in range(seed.attachments_per_page)
    ]
    tags = [f"tag{i}" for i in range(seed.tags_per_page)]

    body = (
        ""
        if empty
        else _body_html(
            wiki_label=wiki_label,
            page_label=label,
            auth_root=auth_root,
            other_label=label,
            page_title=title,
            wiki_title=wiki_title,
            # The first blog's first post: wave 0, so it exists at the moment
            # of any first capture. Handles are `blog{i}` and slugs
            # `blog{i}-entry-{n}` by construction in `_synthesize_blog`, which
            # is what lets a page reference one without the two synthesizers
            # having to be run together.
            sibling_blog_handle="blog0" if seed.blog_count else None,
            sibling_blog_slug="blog0-entry-0" if seed.blog_count else None,
        )
    )

    return FakePage(
        uuid=ids.next(),
        label=label,
        title=title,
        parent_uuid=parent_uuid,
        ordinal=ordinal,
        body_html=body,
        created=created,
        modified=modified,
        version_label=len(versions),
        comments=comments,
        versions=versions,
        attachments=attachments,
        tags=tags,
        author=page_author,
        recommendations=n_comments % 7,
        hits=(ordinal + 1) * 10,
        anonymous_hits=(ordinal + 1) * 3,
    )


def _synthesize_wiki(
    *, ids: _IdFactory, clock: _Clock, seed: SynthSeed, wiki_index: int
) -> FakeWiki:
    auth_root = "basic"
    wiki_label = f"wiki{wiki_index}"
    wiki = FakeWiki(
        uuid=ids.next(),
        label=wiki_label,
        title=(
            _WIKI_TITLES[wiki_index % len(_WIKI_TITLES)]
            if seed.human_names
            else f"Wiki {wiki_index}"
        ),
        created=clock.tick(),
        modified=clock.tick(),
        pages=[],
    )

    # Coherent, sequential titles from this wiki's own themed pool (in
    # crawl/discovery order), so the wiki's pages read as a real set.
    page_pool = _WIKI_PAGE_POOLS[wiki_index % len(_WIKI_PAGE_POOLS)]
    page_seq = 0

    def build_level(parent_uuid: str | None, depth_remaining: int, path: str) -> None:
        nonlocal page_seq
        if depth_remaining <= 0:
            return
        for i in range(seed.pages_per_level):
            ordinal = 0 if (seed.duplicate_ordinals and i > 0) else i
            label = f"{path}p{i}"
            empty = seed.empty_pages and depth_remaining == seed.depth and i == 0
            n_comments = None
            if seed.high_comment_count and depth_remaining == seed.depth and i == 0:
                n_comments = seed.high_comment_count_n
            if seed.human_names:
                title = page_pool[page_seq % len(page_pool)]
                page_seq += 1
            else:
                title = label
            if seed.unicode_rtl and depth_remaining == seed.depth and i == 0:
                title = _RTL_TITLE
                label = f"{label}-{_UNICODE_LABEL_SUFFIX}"
            page = _make_page(
                ids=ids,
                clock=clock,
                seed=seed,
                label=label,
                title=title,
                parent_uuid=parent_uuid,
                ordinal=ordinal,
                wiki_label=wiki_label,
                wiki_title=wiki.title,
                auth_root=auth_root,
                empty=empty,
                n_comments=n_comments,
            )
            wiki.pages.append(page)
            build_level(page.uuid, depth_remaining - 1, f"{path}p{i}-")

    build_level(None, seed.depth, "")

    if seed.deep_nesting:
        # A single unbroken chain, deeper than the regular tree, so
        # deep-nesting stress cases don't depend on `depth` alone.
        parent_uuid: str | None = None
        for level in range(seed.deep_nesting_depth):
            label = f"deep{level}"
            page = _make_page(
                ids=ids,
                clock=clock,
                seed=seed,
                label=label,
                title=label,
                parent_uuid=parent_uuid,
                ordinal=0,
                wiki_label=wiki_label,
                wiki_title=wiki.title,
                auth_root=auth_root,
            )
            wiki.pages.append(page)
            parent_uuid = page.uuid

    return wiki


def synthesize(seed: SynthSeed) -> WikiSet:
    """Build a `WikiSet` from `seed`. Pure function of `seed`: same
    seed in, byte-identical dataset out (scenario: "Same seed, same
    bytes")."""
    ids = _IdFactory(_id_rng(seed, "wiki"))
    clock = _Clock(seed.base_timestamp)

    wikis = [
        _synthesize_wiki(ids=ids, clock=clock, seed=seed, wiki_index=i)
        for i in range(seed.wiki_count)
    ]
    return WikiSet(wikis=wikis, base_timestamp_ms=seed.base_timestamp * 1000)


# --------------------------------------------------------------------
# Blogs synthesis (Stage 2, not documented) -- same determinism guarantees
# as wikis: every uuid/timestamp comes from the seeded RNG and _Clock,
# never uuid.uuid4 or the wall clock.
# --------------------------------------------------------------------


def _blog_post_body(*, blog_handle: str, slug: str, blog_title: str, post_title: str) -> str:
    """A little author HTML for a blog post: two prose paragraphs plus a
    same-host `<a>`/`<img>` -- and never a `<script>`. Deliberately
    lighter than the wiki body (which exercises the full asset/CSS
    matrix); the Blogs round-trip only needs `<content>` to survive.

    The prose comes from `content.blog_body`, which is WRITTEN per title
    rather than templated: one opening sentence shared by every post makes
    a demo of six posts read as the same post six times."""
    img = f"/blogs/{blog_handle}/resource/{slug}/cover.png"
    href = f"/blogs/{blog_handle}/entry/{slug}"
    paragraphs = "\n".join(f"<p>{para}</p>" for para in blog_body(post_title))
    return (
        f'<img src="{img}" alt="{html.escape(post_title)}" />\n'
        f"{paragraphs}\n"
        f'<p><a href="{href}">Permalink</a> \u00b7 posted to '
        f"<b>{html.escape(blog_title)}</b></p>\n"
    )


#: How much of a synthesized set arrives in the SECOND wave -- the content a
#: demo update discovers after the first capture. A quarter: enough that an
#: update visibly finds something, small enough that the archive it is added to
#: is still the substantial thing. A dataset that doubled would flatter the
#: feature rather than demonstrate it.
_SECOND_WAVE_TAIL = 4


#: How far past `base_timestamp` the second wave begins, in days. Every wave-1
#: item is stamped from this window and every wave-0 item from before it, so
#: the boundary is GLOBAL -- which is what a `since` cutoff is. Making the
#: boundary per-container instead would leave one container's early items
#: newer than another's late ones, and a cutoff placed between the waves would
#: then catch the wrong things in both directions.
_SECOND_WAVE_AFTER_DAYS = 900


def wave_clock(seed: SynthSeed) -> _Clock:
    """The clock wave-1 content is stamped from."""
    return _Clock(seed.base_timestamp + _SECOND_WAVE_AFTER_DAYS * 86400)


def _wave_of(index: int, total: int) -> int:
    """Which wave the `index`-th item of `total` belongs to.

    A pure function of position, so it inherits the seed's determinism without
    consulting anything else. The LAST items are the later ones, which keeps
    wave 1 newer than wave 0 by construction once the clock ticks in order.
    """
    if total < 2:
        return 0
    first_wave = total - max(1, total // _SECOND_WAVE_TAIL)
    return 1 if index >= first_wave else 0


def _synthesize_blog(
    *, ids: _IdFactory, clock: _Clock, later: _Clock, seed: SynthSeed, blog_index: int
) -> FakeBlog:
    human = seed.human_names
    handle = f"blog{blog_index}"
    title = _BLOG_NAMES[blog_index % len(_BLOG_NAMES)] if human else f"Blog {blog_index}"
    created = clock.tick()
    modified = clock.tick()
    posts: list[FakeBlogPost] = []
    for p in range(seed.posts_per_blog):
        slug = f"{handle}-entry-{p}"
        wave = _wave_of(p, seed.posts_per_blog)
        # Wave-1 items are stamped from a clock that starts long after every
        # wave-0 stamp. A later item that looked older would be invisible to a
        # date-filtered update -- the demo would prove the opposite of the
        # point it exists to make.
        stamp = later if wave == 1 else clock
        published = stamp.tick()
        updated = stamp.tick()
        post_title = (
            _BLOG_POST_TITLES[blog_index % len(_BLOG_POST_TITLES)][p % len(_BLOG_POST_TITLES[0])]
            if human
            else f"Post {p} in {handle}"
        )
        # Same reasoning as forum topics: a blog's posts span months, not an
        # afternoon. Without this every entry in the demo carried effectively
        # one date, and anything that orients by time had nothing to show.
        clock.jump(5 + (p * 11) % 26)
        post_author = _pick(_PEOPLE, slug) if human else f"Blogger {blog_index}"
        # Comments come from `content.post_comment`, written per post title.
        # Picking them by hash from the pool the wiki pages use would give a
        # post about dark mode a comment like "Should this live under
        # Operations instead?", and share six posts' remarks with twenty wiki
        # pages. The written threads put the post author's
        # answer second, which is the comment the threading below hangs off
        # the first -- so the reply reads as a reply, by the person replied to.
        comments: list[FakeBlogComment] = []
        for c in range(seed.comments_per_post):
            if human:
                text, by_author = post_comment(post_title, c)
                comment_author = (
                    post_author
                    if by_author
                    else _pick(_others(_PEOPLE, post_author), f"{slug}:c{c}")
                )
                userid = person_uid(comment_author)
            else:
                comment_author = f"Commenter {c}"
                text, userid = f"Comment {c} on {slug}", person_uid(comment_author)
            comment = FakeBlogComment(
                uuid=ids.next(),
                author=comment_author,
                author_userid=userid,
                content_html=f"<p>{text}</p>",
                published=clock.tick(),
                # One-level threading: the second comment replies to the
                # first; everything else stays top-level (Blogs is one
                # level only). in_reply_to carries the bare parent uuid.
                in_reply_to=(comments[0].uuid if c == 1 and comments else None),
            )
            comments.append(comment)
        posts.append(
            FakeBlogPost(
                uuid=ids.next(),
                slug=slug,
                title=post_title,
                author=post_author,
                author_userid=person_uid(post_author),
                body_html=_blog_post_body(
                    blog_handle=handle, slug=slug, blog_title=title, post_title=post_title
                ),
                published=published,
                updated=updated,
                # Alternate the documented app:control comment toggle so a
                # disabled-comments entry is exercised too.
                comments_enabled=(p % 2 == 0),
                tags=[f"tag{t}" for t in range(seed.tags_per_post)],
                comments=comments,
                recommendations=p % 7,
                hits=(p + 1) * 10,
                wave=wave,
            )
        )
    return FakeBlog(
        uuid=ids.next(),
        handle=handle,
        title=title,
        created=created,
        modified=modified,
        posts=posts,
    )


def synthesize_blogs(seed: SynthSeed) -> BlogSet:
    """Build a `BlogSet` from `seed`. Pure function of `seed`: same seed
    in, byte-identical dataset out (mirrors `synthesize`'s determinism
    guarantee)."""
    ids = _IdFactory(_id_rng(seed, "blog"))
    clock = _Clock(seed.base_timestamp)
    later = wave_clock(seed)
    blogs = [
        _synthesize_blog(ids=ids, clock=clock, later=later, seed=seed, blog_index=i)
        for i in range(seed.blog_count)
    ]
    homepage = blogs[0].handle if blogs else "homepage"
    return BlogSet(homepage=homepage, blogs=blogs, base_timestamp_ms=seed.base_timestamp * 1000)


# --------------------------------------------------------------------
# Forums synthesis (Stage 2, not documented) -- flags on topics/replies and
# an arbitrary-depth reply tree flattened into the (flat) replies list.
# --------------------------------------------------------------------

# Deterministic per-topic flag assignment covering every documented
# topic flag (pinned/locked/question/answered) across the first topics.
_TOPIC_FLAG_CYCLE = (
    ["question", "answered"],
    ["pinned", "locked"],
    ["question"],
)


def _topic_body(*, title: str, author: str) -> str:
    """The topic's own opening post, in the author's voice.

    From `content.topic_body`, written per title. A single closing sentence
    repeated across every topic is exactly what makes a demo read as
    filler."""
    paragraphs = "\n".join(f"<p>{para}</p>" for para in topic_body(title))
    return f"{paragraphs}\n<p>\u2014 {html.escape(author)}</p>\n"


def _synthesize_topic(
    *,
    ids: _IdFactory,
    clock: _Clock,
    later: _Clock,
    seed: SynthSeed,
    forum_uuid: str,
    forum_index: int,
    topic_index: int,
    late_reply: bool = False,
) -> FakeForumTopic:
    human = seed.human_names
    flags = list(_TOPIC_FLAG_CYCLE[topic_index % len(_TOPIC_FLAG_CYCLE)])
    # Coherent, sequential titles from this forum's own themed pool (mirrors
    # _synthesize_wiki's page_pool indexing) so a forum's topics read as a
    # real, related set of questions/discussions rather than a scatter.
    topic_pool = _FORUM_TOPIC_TITLES[forum_index % len(_FORUM_TOPIC_TITLES)]
    title = topic_pool[topic_index % len(topic_pool)] if human else f"Topic {topic_index}"
    author = _pick(_PEOPLE, f"t{topic_index}") if human else f"Poster {topic_index}"
    wave = _wave_of(topic_index, seed.topics_per_forum)
    stamp = later if wave == 1 else clock
    topic = FakeForumTopic(
        uuid=ids.next(),
        forum_uuid=forum_uuid,
        title=title,
        author=author,
        content_html=_topic_body(title=title, author=author),
        published=stamp.tick(),
        flags=flags,
        tags=["howto"],
        wave=wave,
    )

    def _reply(ref: str, *, reply_wave: int = 0) -> FakeForumReply:
        i = len(topic.replies)
        # Ordered per topic, so the thread answers its own question instead of
        # picking unrelated lines out of a shared pool -- and the person
        # reporting "that fixed it" is the one who asked, not a bystander.
        reply_text, by_op = topic_reply(title, i)
        return FakeForumReply(
            uuid=ids.next(),
            author=(
                (author if by_op else _pick(_others(_PEOPLE, author), f"t{topic_index}r{i}"))
                if human
                else f"Replier {i}"
            ),
            content_html=(
                f"<p>{reply_text}</p>" if human else f"<p>Reply {i} in topic {topic_index}.</p>"
            ),
            published=(later if reply_wave == 1 else stamp).tick(),
            ref=ref,
            source_topic=topic.uuid,
            wave=reply_wave,
        )

    # Top-level replies: each refs the topic directly (so build_reply_tree
    # yields each as a root -- the topic uuid never appears among reply ids).
    top_level: list[FakeForumReply] = []
    for _ in range(seed.replies_per_topic):
        reply = _reply(topic.uuid)
        topic.replies.append(reply)
        top_level.append(reply)

    # A nested chain hung under the first top-level reply, arbitrary depth
    # (each reply refs the previous one -- reconstructable only by
    # build_reply_tree, since the feed itself is flat).
    if top_level:
        parent = top_level[0]
        for _ in range(seed.reply_tree_depth):
            child = _reply(parent.uuid)
            topic.replies.append(child)
            parent = child

    # If the topic is marked answered, flag its last reply as the answer
    # (the `answer` sn/flags term applies to replies, not topics).
    if "answered" in flags and topic.replies:
        topic.replies[-1].flags.append("answer")

    # A late reply on a topic that is otherwise untouched: the comments hole,
    # made demonstrable. A `since`-filtered TOPICS feed may never surface this
    # topic, because the topic itself did not change -- so an update that only
    # trusts the topic feed loses the reply. The demo has to contain one, or
    # the "also re-check replies" option has nothing to catch and there is no
    # way to show anyone why it exists.
    if late_reply and wave == 0:
        topic.replies.append(_reply(topic.uuid, reply_wave=1))

    return topic


def _synthesize_forum(
    *, ids: _IdFactory, clock: _Clock, later: _Clock, seed: SynthSeed, forum_index: int
) -> FakeForum:
    human = seed.human_names
    forum = FakeForum(
        uuid=ids.next(),
        title=(_FORUM_NAMES[forum_index % len(_FORUM_NAMES)] if human else f"Forum {forum_index}"),
        created=clock.tick(),
        modified=clock.tick(),
    )
    for t in range(seed.topics_per_forum):
        # Threads in a forum are days or weeks apart, not an hour. Spacing
        # them deterministically (by index, never a random draw) keeps the
        # same seed producing the same dataset.
        clock.jump(3 + (t * 7) % 23)
        forum.topics.append(
            _synthesize_topic(
                ids=ids,
                clock=clock,
                later=later,
                seed=seed,
                forum_uuid=forum.uuid,
                forum_index=forum_index,
                topic_index=t,
                # The first forum's first topic collects a late reply while
                # staying wave 0 itself -- one instance is enough to make the
                # comments hole visible, and more would clutter the demo.
                late_reply=(seed.late_reply and forum_index == 0 and t == 0),
            )
        )
    return forum


#: Believable community files. Names chosen to exercise the naming rules the
#: package writer has to survive -- a duplicate, a case-only twin, forbidden
#: characters, a trailing dot, a reserved device name -- because a demo whose
#: files are all `report1.pdf` proves nothing about a real library.
#: Sizes are what these files ACTUALLY are, not a plausible-looking number
#: printed next to a stub. A real-world size (a 2 MB deck) declared for a
#: 92-byte body makes the console honestly report "Size 144.7 KB / Captured
#: 92 bytes", which reads as catastrophic data loss in the one tool whose
#: promise is fidelity. Each body is generated
#: to exactly this length and `size` is taken from the body, so the two cannot
#: drift apart again. Kept small (~54 KB for the whole library) because every
#: demo run writes them into an archive.
_FILE_SPECS = (
    ("Onboarding Checklist.pdf", "application/pdf", 4812, "3", True),
    ("Architecture Overview.pptx", "application/vnd.ms-powerpoint", 24576, "12", True),
    ("Q1 Budget: draft?.xlsx", "application/vnd.ms-excel", 2048, "2", False),
    ("Onboarding Checklist.pdf", "application/pdf", 5100, "1", False),
    ("onboarding checklist.pdf", "application/pdf", 1024, "1", False),
    ("Runbook.", "text/plain", 3072, "7", True),
    ("CON.txt", "text/plain", 812, "1", False),
    ("Team Photo.png", "image/png", 12288, "1", False),
)


def _file_body(name: str, version: str, size: int) -> bytes:
    """A file body of exactly `size` bytes.

    Distinct per file, not per name: two documents can share a name and be
    different files -- that is the whole reason the package has to
    disambiguate them -- so identical bodies would hide whether the writer
    kept them apart or silently overwrote one. The header carries the name and
    version, which differ between every same-named pair here.
    """
    header = f"{name}\n\nSynthetic content for the demo.\nversion {version}.\n".encode()
    if len(header) >= size:
        return header[:size]
    return header + b"." * (size - len(header) - 1) + b"\n"


_FOLDER_NAMES = ("Onboarding", "Reference", "Archive 2026")


def _synthesize_file_library(
    *, ids: _IdFactory, clock: _Clock, later: _Clock, seed: SynthSeed, community_uuid: str
) -> FakeFileLibrary:
    """One community library. Deterministic, like every other synthesizer.

    The last few files arrive in the SECOND wave, so an update of a community's
    Files has something to find -- and so a test can tell "the update read the
    library feed again" apart from "the update served the feed from the archive
    and reported success".
    """
    library_id = ids.next()
    files: list[FakeCommunityFile] = []
    total = len(_FILE_SPECS)
    for index, (name, content_type, size, version, filed) in enumerate(_FILE_SPECS):
        wave = _wave_of(index, total)
        at = later if wave else clock
        at.jump(3)
        published = at.tick()
        body = _file_body(name, version, size)
        files.append(
            FakeCommunityFile(
                uuid=ids.next(),
                name=name,
                title=name,
                author=(_pick(_PEOPLE, f"file:{name}") if seed.human_names else "Fake Author"),
                published=published,
                updated=at.tick(),
                # Taken from the body, never declared separately: a reported
                # size that is not the number of bytes served is a lie the
                # console will faithfully report.
                size=len(body),
                content_type=content_type,
                version_label=version,
                filed_in_folder=filed,
                wave=wave,
                tags=["community", "files"] if filed else [],
                body=body,
            )
        )
    foldered = [f.uuid for f in files if f.filed_in_folder]
    folders = [
        FakeCommunityFolder(
            uuid=ids.next(),
            name=_FOLDER_NAMES[i % len(_FOLDER_NAMES)],
            file_uuids=foldered[i :: len(_FOLDER_NAMES)],
        )
        for i in range(min(len(_FOLDER_NAMES), max(1, len(foldered))))
    ]
    return FakeFileLibrary(
        community_uuid=community_uuid,
        library_id=library_id,
        title="Community Files",
        files=files,
        folders=folders,
    )


def synthesize_files(seed: SynthSeed, *, community_uuid: str) -> FileSet:
    """Build a `FileSet` for one community. Pure function of `seed`."""
    ids = _IdFactory(_id_rng(seed, "files"))
    clock = _Clock(seed.base_timestamp)
    return FileSet(
        libraries=[
            _synthesize_file_library(
                ids=ids,
                clock=clock,
                later=wave_clock(seed),
                seed=seed,
                community_uuid=community_uuid,
            )
        ]
    )


#: The Highlights pages a real community front page carries: a welcome, a
#: where-to-find-things, and a status note. Bodies are real HTML with a table,
#: a list and a link, because a rich content page whose body is one paragraph
#: proves nothing about a renderer that has to survive what people actually
#: paste into an editor.
_RTE_SPECS = (
    (
        "Welcome to the team space",
        "126",
        """<h2>Welcome</h2>
<p>This space collects everything the platform team maintains. Start with the
<a href="/wikis/home?lang=en#!/wiki/Engineering%20Handbook">Engineering Handbook</a>.</p>
<p>Straight to what people ask for most: <a href="{page_href}">the onboarding page</a>,
and <a href="{post_href}">this week&rsquo;s note</a>.</p>
<ul><li>Handbook &mdash; conventions and runbooks</li>
<li>Blog &mdash; what changed this week</li>
<li>Forum &mdash; ask here before mailing anyone</li></ul>""",
    ),
    (
        "Who to ask",
        "38",
        """<h2>Who to ask</h2>
<table><thead><tr><th>Area</th><th>Ask</th></tr></thead>
<tbody><tr><td>Build &amp; release</td><td>the release rota</td></tr>
<tr><td>Access requests</td><td>the service desk</td></tr>
<tr><td>Anything else</td><td>the forum</td></tr></tbody></table>
<p><em>Please do not send direct messages for anything the forum can answer.</em></p>""",
    ),
    (
        "Current status",
        "4",
        """<h2>Current status</h2>
<p>Migration is in progress. Nothing here is being deleted; the archive is
being taken so that it does not have to be.</p>""",
    ),
)


def _synthesize_rich_content(
    *, ids: _IdFactory, clock: _Clock, later: _Clock, seed: SynthSeed, community_uuid: str
) -> FakeRichContentSet:
    """One community's Highlights pages. Deterministic, like the rest.

    The last few pages arrive in the SECOND wave, for the same reason the file
    library's do: without it, no test can distinguish an update that re-read
    the widget layout from one that served it out of the archive.
    """
    # A Highlights front page LINKS INTO the community it fronts -- that is
    # what it is for. The bodies carry placeholders rather than fixed hrefs so
    # they name content this same seed actually synthesized, whatever it chose
    # to call it: a link to a page that does not exist proves nothing about
    # whether links resolve.
    wikis, blogs = synthesize(seed), synthesize_blogs(seed)
    first_page = wikis.wikis[0].pages[0] if wikis.wikis and wikis.wikis[0].pages else None
    first_blog = blogs.blogs[0] if blogs.blogs else None
    first_post = first_blog.posts[0] if first_blog and first_blog.posts else None
    hrefs = {
        "page_href": (
            f"/wikis/basic/wiki/{wikis.wikis[0].label}/page/{first_page.label}"
            if first_page
            else "/wikis/home"
        ),
        "post_href": (
            f"/blogs/{first_blog.handle}/entry/{first_post.slug}" if first_post else "/blogs/home"
        ),
    }

    pages: list[FakeRichContentPage] = []
    total = len(_RTE_SPECS)
    for index, (title, version, body) in enumerate(_RTE_SPECS):
        body = body.format(**hrefs) if "{" in body else body
        wave = _wave_of(index, total)
        at = later if wave else clock
        at.jump(5)
        published = at.tick()
        pages.append(
            FakeRichContentPage(
                resource_id=ids.next(),
                title=title,
                body_html=body,
                author=(_pick(_PEOPLE, f"rte:{title}") if seed.human_names else "Fake Author"),
                published=published,
                updated=at.tick(),
                version_label=version,
                wave=wave,
            )
        )
    # One widget placed and never written into. A live capture saw eight
    # instances and seven initialized, and an export that quietly reported
    # only the seven would be hiding the difference.
    return FakeRichContentSet(community_uuid=community_uuid, pages=pages, uninitialized=1)


def synthesize_rich_content(seed: SynthSeed, *, community_uuid: str) -> RichContentSet:
    """Build a `RichContentSet` for one community. Pure function of `seed`."""
    ids = _IdFactory(_id_rng(seed, "rich_content"))
    clock = _Clock(seed.base_timestamp)
    return RichContentSet(
        communities=[
            _synthesize_rich_content(
                ids=ids,
                clock=clock,
                later=wave_clock(seed),
                seed=seed,
                community_uuid=community_uuid,
            )
        ]
    )


def synthesize_forums(seed: SynthSeed) -> ForumSet:
    """Build a `ForumSet` from `seed`. Pure function of `seed`: same seed
    in, byte-identical dataset out (mirrors `synthesize`'s determinism
    guarantee)."""
    ids = _IdFactory(_id_rng(seed, "forum"))
    clock = _Clock(seed.base_timestamp)
    later = wave_clock(seed)
    forums = [
        _synthesize_forum(ids=ids, clock=clock, later=later, seed=seed, forum_index=i)
        for i in range(seed.forum_count)
    ]
    return ForumSet(forums=forums, base_timestamp_ms=seed.base_timestamp * 1000)
