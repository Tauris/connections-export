"""Which forum topics an author-filtered community capture has to read.

Without this, "capture only my forum content in this community" is a full
read: every forum's topics feed page-walked, every topic's replies feed
fetched, each thread then kept or pruned on who turns out to be in it. The
answer is right and the cost is the whole community, which for a busy one is
tens of thousands of entries fetched to keep a few hundred threads.

The community-scoped person query (`social` person AND `social` community,
`scope=forums:topic`) answers a narrower question directly. It returns **root
topics**: a person with forty replies in one thread yields that thread once,
and a contribution buried in a nested reply still surfaces its root.

So Search selects and the crawl fetches. That division is the point, and it
is not a shortcut around reading replies: the query returns topic URLs and no
reply records, so the crawl still fetches each selected topic's full replies
feed. What it no longer does is fetch every other topic to find out it was
not wanted.

This module is only the selecting half. It decides nothing about what to
keep -- `crawl_forums` still applies the author filter to what comes back,
because the person filter is documented as a superset (author OR contributor
OR community member) and a selector that is occasionally too generous must
not become a capture that is occasionally wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

#: HCL's documented maximum for the Search feed's `pageSize`.
SEARCH_PAGE_SIZE = 150
#: How many pages to walk before stopping. 40 x 150 = 6,000 topics, well
#: past what one person accumulates inside a single community. Reaching it is
#: reported, never silently absorbed.
SEARCH_PAGE_LIMIT = 40

#: Query/fragment keys a forum permalink carries its topic uuid under. Real
#: search permalinks vary in shape between deployments and between the HTML
#: `alternate` link and the Atom `via` link, so all three are tried.
_TOPIC_KEYS = ("topicUuid", "topicId", "id")
_TOPIC_SEGMENTS = ("topic", "topicId", "thread", "threadTopic")


def forum_topic_id(url: str | None) -> str | None:
    """The forum topic uuid a URL names, or None.

    Tries the shared `parse_url` first, then a `topicUuid=`/`topicId=`/`id=`
    query param. Community-hosted forums often route through a hash-fragment
    UI URL (`.../communityview?communityUuid=...#...&topicId=...`), where the
    id sits in the fragment rather than the query string, so the fragment is
    read the same way -- both as `key=value` pairs and as `/`-separated path
    segments (`#/forums/topic/{uuid}`).
    """
    if not url:
        return None
    # Lazily, and from `gui`: `parse_wiki_url` is pure and dependency-free
    # (it predates this module and the CLI already borrows it the same way),
    # but importing a `gui` module at crawler import time would put a web
    # framework behind every crawl.
    from connections_export.gui.wiki_url import parse_url  # noqa: PLC0415

    target = parse_url(url)
    if target.app == "forum" and target.topic_id:
        return target.topic_id
    split = urlsplit(url)
    query = parse_qs(split.query)
    for key in _TOPIC_KEYS:
        if query.get(key) and query[key][0].strip():
            return query[key][0].strip()
    if split.fragment:
        fragment = parse_qs(split.fragment.lstrip("/"))
        for key in _TOPIC_KEYS:
            if fragment.get(key) and fragment[key][0].strip():
                return fragment[key][0].strip()
        segments = [s for s in split.fragment.split("/") if s]
        for index, segment in enumerate(segments):
            if segment in _TOPIC_SEGMENTS and index + 1 < len(segments):
                candidate = segments[index + 1].split("?")[0].strip()
                if candidate:
                    return candidate
    return None


@dataclass(frozen=True)
class ForumTopicSelection:
    """The topics Search says are worth reading, and how much to trust that.

    `usable` is the only field the caller has to act on: false means fall
    back to the full forum walk, which is slower and always correct.
    `complete` distinguishes "that was the whole answer" (a short final page)
    from "cut short at our own page limit" (False) and from "the deployment
    did not answer" (None) -- three states that a plain count cannot tell
    apart, and that a caller reading a bare list would mistake for each
    other.
    """

    topic_ids: tuple[str, ...]
    hits: int
    pages_read: int
    complete: bool | None
    url: str | None
    usable: bool
    summary: str
    #: A few `(alternate_url, via_url)` pairs from hits that yielded no topic
    #: id, so an unrecognised permalink shape is diagnosable from the run log
    #: instead of only showing up as "search found nothing".
    samples: tuple[tuple[str | None, str | None], ...] = ()


def select_community_forum_topics(
    *,
    fetch: Callable[[str], bytes | None],
    base_url: str,
    userid: str,
    community_uuid: str,
    page_size: int = SEARCH_PAGE_SIZE,
    page_limit: int = SEARCH_PAGE_LIMIT,
) -> ForumTopicSelection:
    """Walk the community-scoped person query and return the root topics it
    names.

    `fetch` is the crawl's own fetcher, not a bare HTTP client: these pages
    are the reason the run read the threads it read, so they belong in the
    archive with everything else, under the run's rate limit and its resume
    policy. `None` back means the deployment did not answer -- the failure
    itself is already recorded by the fetcher.
    """
    from connections_export.adapters.search import (  # noqa: PLC0415
        parse_search_results,
        search_results_url,
    )

    topic_ids: list[str] = []
    seen: set[str] = set()
    samples: list[tuple[str | None, str | None]] = []
    hits = pages_read = 0
    first_url: str | None = None
    complete: bool | None = True
    note = ""

    for page in range(1, page_limit + 1):
        url = search_results_url(
            base_url=base_url,
            userid=userid,
            community_uuid=community_uuid,
            scope="forums:topic",
            page=page,
            page_size=page_size,
        )
        if first_url is None:
            first_url = url
        content = fetch(url)
        # No answer is not an empty answer. Read as a short final page, a
        # refusal would mean "this person is in no threads" -- and the
        # capture would come out empty and call itself a success.
        if content is None:
            complete = None
            note = f"the deployment did not answer page {page} of the person query"
            break
        try:
            batch = parse_search_results(content)
        except Exception as exc:  # noqa: BLE001 - selection reports, never raises
            complete = None
            note = f"page {page} did not parse as a Search feed: {exc}"
            break
        pages_read = page
        hits += len(batch)
        for hit in batch:
            tid = forum_topic_id(hit.via_url) or forum_topic_id(hit.alternate_url)
            if tid:
                if tid not in seen:
                    seen.add(tid)
                    topic_ids.append(tid)
            elif len(samples) < 5:
                samples.append((hit.alternate_url, hit.via_url))
        if len(batch) < page_size:
            break  # a partial page is the real last page
        if page == page_limit:
            complete = False

    usable = bool(topic_ids)
    if not usable:
        if note:
            summary = f"{note} — no topic selection, reading the forums in full instead"
        elif hits:
            summary = (
                f"{hits} search hit(s) named no forum topic this build recognises — "
                "reading the forums in full instead"
            )
        else:
            summary = (
                "the person query returned nothing for this community — reading the "
                "forums in full instead (an author filter that is a display name "
                "rather than a user id will do this)"
            )
    elif complete is None:
        summary = (
            f"{len(topic_ids)} thread(s) from {hits} search hit(s) over {pages_read} "
            f"page(s), then {note} — reading those threads; more may exist"
        )
    elif complete is False:
        summary = (
            f"{len(topic_ids)} thread(s) from {hits} search hit(s), every one of "
            f"{pages_read} page(s) full — the answer was cut short at the "
            f"{page_limit}-page limit asked for here, not by the deployment. "
            "Reading those threads; more may exist."
        )
    else:
        summary = (
            f"{len(topic_ids)} thread(s) selected from {hits} search hit(s) over "
            f"{pages_read} page(s) — reading those instead of every topic in the "
            "selected forums"
        )

    return ForumTopicSelection(
        topic_ids=tuple(topic_ids),
        hits=hits,
        pages_read=pages_read,
        complete=complete,
        url=first_url,
        usable=usable,
        summary=summary,
        samples=tuple(samples),
    )
