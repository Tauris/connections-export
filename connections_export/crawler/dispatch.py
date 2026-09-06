"""Turning a `{kind: [ids]}` selection into crawl calls, once.

This existed in three places -- `cli._run_selected_crawls` and twice inside
`gui.app._run_real` -- with three different coverages. The CLI had no
rich-content branch at all, so a component the console offered did nothing;
and `crawl --into` computed an update cutoff that one of the three dropped. A
selection is a selection: what runs for it must not depend on which front end
asked.

Lives in `crawler/` rather than beside `apps.py` because it imports the crawl
entry points. `apps.py` stays free of them, which is what lets `derive`,
`interchange` and `cli` read the registry without pulling in the crawler.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from connections_export import apps
from connections_export.crawler.crawl import (
    crawl,
    crawl_blogs,
    crawl_files,
    crawl_forums,
    crawl_rich_content,
)
from connections_export.crawler.engine import CrawlResult

#: Which entry point runs for each component kind. `blog` and `ideation_blog`
#: share one: an ideation blog is a blog with a different `kind`, and asking
#: for them separately would open two runs against the same feed.
_CRAWLERS: dict[str, Callable[..., CrawlResult]] = {
    "wiki": crawl,
    "forum": crawl_forums,
    "blog": crawl_blogs,
    "ideation_blog": crawl_blogs,
    "files": crawl_files,
    "rich_content": crawl_rich_content,
}


def crawl_for(kind: str) -> Callable[..., CrawlResult]:
    """The crawl entry point for one component kind."""
    try:
        return _CRAWLERS[kind]
    except KeyError:  # pragma: no cover - pinned by tests/test_apps_registry
        raise KeyError(f"no crawl registered for component kind {kind!r}") from None


def _groups(
    selection: Mapping[str, Sequence[str]],
) -> list[tuple[apps.AppSpec, list[str]]]:
    """Selected ids, grouped so apps sharing a crawl share a call."""
    grouped: dict[Callable[..., CrawlResult], tuple[apps.AppSpec, list[str]]] = {}
    order: list[Callable[..., CrawlResult]] = []
    # `crawl_order`, not declaration order: forums first because they produce
    # visible results fastest, so a person watching a community capture sees
    # the tree fill early. Declaration order is what the console LISTS, which
    # is a different question.
    for app in sorted(apps.APPS, key=lambda spec: spec.crawl_order):
        # `.strip`, not just truthiness: a checkbox carrying whitespace
        # would otherwise become a crawl target, and an id of " " reaches
        # a URL builder as a real value.
        ids = [s for s in (str(i).strip() for i in selection.get(app.kind, ())) if s]
        if not ids:
            continue
        crawler = crawl_for(app.kind)
        if crawler in grouped:
            grouped[crawler][1].extend(ids)
        else:
            grouped[crawler] = (app, list(ids))
            order.append(crawler)
    return [grouped[crawler] for crawler in order]


def run_selection(
    selection: Mapping[str, Sequence[str]],
    *,
    config: Any,
    client: Any,
    archive: Any,
    emit: Any = None,
    max_entries: int | None = None,
    author: str | None = None,
    since: str | None = None,
    single: Mapping[str, Sequence[str]] | None = None,
    restrict_to_forum_uuid: str | None = None,
    blogs_homepage: str = "homepage",
    stop_event: Any = None,
    community_uuid: str | None = None,
    community_title: str | None = None,
    search_userid: str | None = None,
) -> list[CrawlResult]:
    """Run one crawl per selected app, into one archive.

    One archive for the whole selection is what makes a batch a batch: the
    community, its wiki, its blogs and its forums end up in a single export
    rather than one per container.

    `single` carries the per-app single-item scopes a dropped URL can name
    (`{"entries": [...], "topics": [...]}`) -- unchanged from what the CLI and
    the console already pass.

    `search_userid` is the person the Search API can be asked about -- a user
    id, which is what `author` already is when the filter came from "Only me"
    or from a resolved identity. With it, a community capture's forums are
    selected by the community-scoped person query instead of read whole (see
    `crawl_forums`). Passing it is safe when it is wrong: a value Search does
    not know returns nothing, the selection is unusable, and the crawl falls
    back to the full walk saying why.
    """
    single = single or {}
    results: list[CrawlResult] = []

    for app, ids in _groups(selection):
        crawler = crawl_for(app.kind)
        common: dict[str, Any] = {
            "config": config,
            "client": client,
            "archive": archive,
            "emit": emit,
            "author": author,
            app.cap_kwarg: max_entries,
        }
        if stop_event is not None:
            common["stop_event"] = stop_event
        # Only apps whose feeds carry a date filter are told the cutoff.
        if app.accepts_since and since is not None:
            common["since"] = since
        if app.kind in ("blog", "ideation_blog"):
            common["blogs_homepage"] = blogs_homepage
            common["entry_ids"] = list(single.get("entries") or ()) or None
        if app.kind == "wiki" and community_uuid:
            # Only wikis need telling. Blogs, forums, files and rich content
            # all recover their community from a feed they already fetch; a
            # scoped wiki crawl skips the only feed that carries the marker,
            # so for it the community is an input rather than a discovery.
            common["community_uuid"] = community_uuid
            common["community_title"] = community_title
        if app.kind == "forum":
            common["topic_ids"] = list(single.get("topics") or ()) or None
            if restrict_to_forum_uuid:
                common["restrict_to_forum_uuids"] = [restrict_to_forum_uuid]
            # Forums recover their community from a feed they already fetch,
            # so this is not provenance -- it is the second half of the
            # person query, which is only worth asking inside one community.
            # Without an author filter there is nothing to select FOR, and
            # `crawl_forums` ignores both.
            if search_userid and community_uuid and author:
                common["search_userid"] = search_userid
                common["community_uuid"] = community_uuid

        if app.id_arity == "list":
            results.append(crawler(**common, **{app.id_kwarg: ids}))
        else:
            for ident in ids:
                results.append(crawler(**common, **{app.id_kwarg: ident}))

    return results
