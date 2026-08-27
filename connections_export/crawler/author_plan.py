"""Pure keep-set planning for the two-pass author-filtered crawl.

Pass 1 of the crawl reads structure + text (no images/attachments) and records,
per entity, who is *involved* (author name / snx:userid / contributor). This
module turns those facts into the **keep-set**: exactly which entities' assets
Pass 2 should fetch, with the context that makes a match readable --

- a matched wiki page keeps its ancestor path (as context);
- a comment by the author keeps the WHOLE page/post;
- a single reply by the author promotes the WHOLE thread (the "fill back
  later" case that is *why* the crawl is two-pass).

Pure: no HTTP, no parsing. Mirrors `derive.author_filter._involves` so the
crawl-time keep and the derive-time filter never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connections_export.identity import norm_identity as _norm


@dataclass(frozen=True)
class Involvement:
    """Who an entity credits: an author name, a stable `snx:userid`, and any
    contributors. A target matches if it equals any of these (normalized)."""

    author: str | None = None
    author_userid: str | None = None
    contributors: tuple[str, ...] = ()

    def matches(self, target: str) -> bool:
        identities = {_norm(self.author), _norm(self.author_userid)}
        identities.update(_norm(c) for c in self.contributors)
        return target in (identities - {None})


@dataclass
class PageFacts:
    """A wiki page (or blog post): its own involvement, its ancestor id path
    (root..parent, for context), and its comments' involvements."""

    id: str
    involvement: Involvement = field(default_factory=Involvement)
    ancestor_ids: tuple[str, ...] = ()
    comments: tuple[Involvement, ...] = ()


@dataclass
class ThreadFacts:
    """A forum thread: the topic's involvement plus each reply's involvement."""

    topic_id: str
    topic: Involvement = field(default_factory=Involvement)
    replies: tuple[Involvement, ...] = ()


@dataclass
class KeepSet:
    """What Pass 2 should fetch assets for. `context_ids` are ancestor pages
    kept only so a kept descendant's tree path survives -- they are in
    `page_ids` too, but flagged so the crawl can note them as context."""

    page_ids: set[str] = field(default_factory=set)
    post_ids: set[str] = field(default_factory=set)
    thread_topic_ids: set[str] = field(default_factory=set)
    context_ids: set[str] = field(default_factory=set)

    def keeps_page(self, page_id: str) -> bool:
        return page_id in self.page_ids

    def keeps_post(self, post_id: str) -> bool:
        return post_id in self.post_ids

    def keeps_thread(self, topic_id: str) -> bool:
        return topic_id in self.thread_topic_ids


def format_identities(involvements: list[Involvement], *, limit: int = 200) -> tuple[str, ...]:
    """Distinct, human-readable author identities present in `involvements`,
    for the "your filter matched nothing -- here's what's actually stored"
    diagnostic. Each is `"name (uid: X)"` (or just one part when the other is
    absent). Order-stable, de-duplicated, capped at `limit`.

    The cap has to clear a whole component comfortably: this is the list a
    user picks their own name from, and each name is a filter they cannot
    construct by hand, so a truncated list silently removes options. Capped
    all the same, because one event should not have to carry every author in
    a deployment; 200 names is a few kilobytes and more than a community has.
    """
    seen: dict[str, None] = {}
    for inv in involvements:
        names = [inv.author, *inv.contributors]
        for name in names:
            label = (name or "").strip()
            uid = (inv.author_userid or "").strip()
            if not label and not uid:
                continue
            if label and uid and name == inv.author:
                text = f"{label} (uid: {uid})"
            elif label:
                text = label
            else:
                text = f"(uid: {uid})"
            seen.setdefault(text, None)
    return tuple(list(seen)[:limit])


def _page_involved(page: PageFacts, target: str) -> bool:
    return page.involvement.matches(target) or any(c.matches(target) for c in page.comments)


def _thread_involved(thread: ThreadFacts, target: str) -> bool:
    return thread.topic.matches(target) or any(r.matches(target) for r in thread.replies)


def plan_author_keep(
    author: str,
    *,
    pages: list[PageFacts] | None = None,
    posts: list[PageFacts] | None = None,
    threads: list[ThreadFacts] | None = None,
) -> KeepSet:
    """The keep-set for `author` from Pass-1 facts. A matched page keeps its
    ancestors as context; a comment match keeps the whole page/post; any matched
    reply keeps the whole thread. An empty/whitespace `author` keeps everything
    (a no-op filter, matching `filter_by_author`)."""
    keep = KeepSet()
    target = _norm(author)

    if target is None:  # no filter -> keep all (Pass 2 fetches everything)
        for p in pages or []:
            keep.page_ids.add(p.id)
        for p in posts or []:
            keep.post_ids.add(p.id)
        for t in threads or []:
            keep.thread_topic_ids.add(t.topic_id)
        return keep

    for page in pages or []:
        if _page_involved(page, target):
            keep.page_ids.add(page.id)
            for ancestor in page.ancestor_ids:
                if ancestor not in keep.page_ids:
                    keep.context_ids.add(ancestor)
                keep.page_ids.add(ancestor)

    for post in posts or []:
        if _page_involved(post, target):
            keep.post_ids.add(post.id)

    for thread in threads or []:
        if _thread_involved(thread, target):  # any reply match -> whole thread
            keep.thread_topic_ids.add(thread.topic_id)

    return keep
