"""Cross-links that span the whole demo export.

A PDF (or any other rendering) of a big export can be built in separately
rendered parts and stitched together, and the thing that proves the stitching
works is a link whose two ends are far apart: early content pointing at late
content, late content pointing back, and both crossing from one container or
app into another. The demo therefore weaves a short "Related elsewhere in this
community" paragraph into every wiki page, blog post, forum topic and Highlights
page, with targets chosen to be FAR from the item in document order.

Document order is the export's: wikis, then blogs, then forums. The rule, for
the item at position `i` of `N` linkable items (all by index arithmetic, never
`hash()` or a random draw, so a seed always yields the same links):

* **mirror** -- the item nearest position `N - 1 - i` in a different container
  and at least a third of the export away;
* **boundary** -- an item in the LAST container if `i` is in the first half of
  the export, in the FIRST container otherwise (so the first container links to
  the last and the last links back);
* **other app** -- among the apps this item is not in, one of the items
  farthest from it.

Only first-wave items are ever linked TO: a second-wave post or topic does not
exist in the first capture, and a link to it would dangle until an update.
Every URL form below is one `derive.crosslink` resolves to `in_export` by
matching the target's `alternate_url`: a wiki page by its
`/wiki/{wiki}/page/{page}` path key, a blog post by `/blogs/{handle}/entry/{slug}`,
a forum topic by `/forums/html/topic?id={uuid}`.

Highlights pages carry the same paragraph but are never linked TO: they all
share one `alternate_url` (the community view), so a link could not name one.
"""

from __future__ import annotations

import html
from dataclasses import astuple, dataclass

_KIND = {"wiki": "wiki page", "blog": "blog post", "forum": "forum topic"}


@dataclass(frozen=True)
class Target:
    app: str  # "wiki" | "blog" | "forum"
    container: int  # ordinal of the container in document order
    key: str  # unique per item: a page uuid-free handle, e.g. "blog:blog0-entry-3"
    title: str
    href: str
    linkable: bool = True  # False for second-wave items: they link out only


_CATALOGUES: dict[tuple, list[Target]] = {}


def catalogue(seed) -> list[Target]:
    """Every item the demo's wikis, blogs and forums hold, in document order.

    The wikis are the demo's curated ones (`prototype`); blogs and forums are
    synthesized from `seed`. Built from the RAW sets -- before any paragraph is
    woven in -- so building it cannot recurse.
    """
    cache_key = astuple(seed)
    cached = _CATALOGUES.get(cache_key)
    if cached is not None:
        return cached
    from connections_export.fakeserver import prototype, synth  # noqa: PLC0415

    items: list[Target] = []
    container = 0
    for wiki in prototype.raw_wikiset().wikis:
        for page in wiki.pages:
            items.append(
                Target(
                    "wiki",
                    container,
                    f"wiki:{wiki.label}/{page.label}",
                    page.title,
                    f"/wikis/basic/wiki/{wiki.label}/page/{page.label}",
                )
            )
        container += 1
    for blog in synth.raw_blogs(seed).blogs:
        for post in blog.posts:
            items.append(
                Target(
                    "blog",
                    container,
                    f"blog:{post.slug}",
                    post.title,
                    f"/blogs/{blog.handle}/entry/{post.slug}",
                    linkable=post.wave == 0,
                )
            )
        container += 1
    for forum in synth.raw_forums(seed).forums:
        for topic in forum.topics:
            items.append(
                Target(
                    "forum",
                    container,
                    f"forum:{topic.uuid}",
                    topic.title,
                    f"/forums/html/topic?id={topic.uuid}",
                    linkable=topic.wave == 0,
                )
            )
        container += 1
    _CATALOGUES[cache_key] = items
    return items


def _nearest(pool: list[tuple[int, Target]], want: int) -> tuple[int, Target] | None:
    """The pool entry whose position is closest to `want` (ties go earlier)."""
    return min(pool, key=lambda e: (abs(e[0] - want), e[0]), default=None)


def related(items: list[Target], self_key: str) -> list[Target]:
    """The distant targets for the item whose `key` is `self_key`."""
    n = len(items)
    me = next((p for p, t in enumerate(items) if t.key == self_key), None)
    if me is None or n < 2:
        return []
    own = items[me]
    linkable = [(p, t) for p, t in enumerate(items) if t.linkable and t.container != own.container]
    chosen: list[Target] = []

    def take(entry: tuple[int, Target] | None) -> None:
        if entry is not None and entry[1] not in chosen:
            chosen.append(entry[1])

    # mirror: the far end of the export
    far_enough = [e for e in linkable if abs(e[0] - me) >= n // 3]
    take(_nearest(far_enough or linkable, n - 1 - me))

    # boundary: the last container from the first half, the first from the second
    edge = items[-1].container if me < n / 2 else items[0].container
    at_edge = [e for e in linkable if e[1].container == edge]
    if at_edge:
        take(at_edge[me % len(at_edge)])

    # another app, one of the farthest items
    others = sorted({t.app for _p, t in linkable if t.app != own.app})
    if others:
        pool = [e for e in linkable if e[1].app == others[me % len(others)]]
        pool.sort(key=lambda e: (-abs(e[0] - me), e[0]))
        take(pool[(me // 2) % min(3, len(pool))])

    # Two links at least, even when the rules above landed on the same item.
    for entry in sorted(linkable, key=lambda e: (-abs(e[0] - me), e[0])):
        if len(chosen) >= 2:
            break
        take(entry)
    return chosen


def highlights_related(items: list[Target], index: int) -> list[Target]:
    """Targets for the `index`-th Highlights page: the first container, the last,
    and an item from the middle of the export."""
    linkable = [(p, t) for p, t in enumerate(items) if t.linkable]
    if not linkable:
        return []
    first = [e for e in linkable if e[1].container == items[0].container]
    last = [e for e in linkable if e[1].container == items[-1].container]
    chosen: list[Target] = []
    for pool in (first, last):
        if pool:
            chosen.append(pool[index % len(pool)][1])
    mid = _nearest(linkable, len(items) // 2 + index)
    if mid is not None and mid[1] not in chosen:
        chosen.append(mid[1])
    return chosen


def paragraph(targets: list[Target], own_title: str) -> str:
    """The "Related elsewhere" paragraph for `targets`; "" when there are none.

    It names the item it is in, so two items that happen to point at the same
    places still do not carry the same sentence."""
    if not targets:
        return ""
    links = [
        f'<a href="{html.escape(t.href, quote=True)}">{html.escape(t.title)}</a> ({_KIND[t.app]})'
        for t in targets
    ]
    if len(links) > 1:
        text = ", ".join(links[:-1]) + f", and {links[-1]}"
    else:
        text = links[0]
    return (
        f"<p>Reading &ldquo;{html.escape(own_title)}&rdquo;? "
        f"Related elsewhere in this community: {text}.</p>\n"
    )
