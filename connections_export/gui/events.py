"""Crawler event -> JSON serialization: the crawler's events become SSE
data.

`to_json` is the one seam between the crawler's typed `events.Event`
union and the wire format `static/console.html` reads over SSE: a
flat, JSON-safe dict with a `"type"` field (the event's snake_case
name) plus its own fields verbatim, nothing renamed or reshaped. The
crawler's events are frozen dataclasses; nothing here fabricates a
field that isn't on the source event.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from connections_export.crawler import events


def _report_payload(report: Any) -> Any:
    """`RunComplete.report` is a `CrawlReport` dataclass in production,
    but the event type itself only promises `Any` (events.py's own
    `RunComplete` docstring) -- tests are free to hand it a plain dict.
    Serialize whichever arrives into something JSON-safe."""
    if dataclasses.is_dataclass(report) and not isinstance(report, type):
        return dataclasses.asdict(report)
    return report


def to_json(event: events.Event) -> dict[str, Any]:
    """Serialize one crawler event to a JSON-safe dict, `{"type":...,
    <event's own fields>}`. Raises `TypeError` for anything that isn't
    one of the documented event types -- this is the GUI's one contract
    with the crawler's event union (`crawler/events.py`'s module
    docstring: "field names are load-bearing")."""
    if isinstance(event, events.RunStarted):
        return {
            "type": "run_started",
            "run_id": event.run_id,
            "base_url": event.base_url,
            "source_version": event.source_version,
        }
    if isinstance(event, events.Discovered):
        return {
            "type": "discovered",
            "url": event.url,
            "kind": event.kind,
            "discovered_from": event.discovered_from,
        }
    if isinstance(event, events.Fetching):
        return {"type": "fetching", "url": event.url, "kind": event.kind}
    if isinstance(event, events.Fetched):
        return {
            "type": "fetched",
            "url": event.url,
            "kind": event.kind,
            "status": event.status,
            "num_bytes": event.num_bytes,
            "from_cache": event.from_cache,
        }
    if isinstance(event, events.Retried):
        return {
            "type": "retried",
            "url": event.url,
            "attempt": event.attempt,
            "reason": event.reason,
        }
    if isinstance(event, events.Stopped):
        return {"type": "stopped", "url": event.url, "kind": event.kind}
    if isinstance(event, events.Failed):
        return {
            "type": "failed",
            "url": event.url,
            "kind": event.kind,
            "error": event.error,
        }
    if isinstance(event, events.PageDerived):
        return {
            "type": "page_derived",
            "page_id": event.page_id,
            "wiki": event.wiki,
            "wiki_title": event.wiki_title,
            "title": event.title,
            "parent": event.parent,
            "ordinal": event.ordinal,
            "comment_count": event.comment_count,
            "version_count": event.version_count,
            "attachment_count": event.attachment_count,
            "author": event.author,
            "modified": event.modified,
        }
    if isinstance(event, events.BlogPostDerived):
        return {
            "type": "blog_post_derived",
            "post_id": event.post_id,
            "blog": event.blog,
            "blog_title": event.blog_title,
            "title": event.title,
            "comment_count": event.comment_count,
            "comments_enabled": event.comments_enabled,
            "author": event.author,
            "published": event.published,
        }
    if isinstance(event, events.ForumTopicDerived):
        return {
            "type": "forum_topic_derived",
            "topic_id": event.topic_id,
            "forum": event.forum,
            "forum_title": event.forum_title,
            "title": event.title,
            "author": event.author,
            "published": event.published,
            "reply_count": event.reply_count,
            "flags": list(event.flags),
        }
    if isinstance(event, events.CommunityFileDerived):
        return {
            "type": "community_file_derived",
            "file_id": event.file_id,
            "library": event.library,
            "library_title": event.library_title,
            "name": event.name,
            "size": event.size,
            "version_label": event.version_label,
            # None means the bytes never arrived -- the difference between
            # having a file and merely knowing of one. Kept distinct from 0,
            # which is a real, empty file.
            "bytes_captured": event.bytes_captured,
            "author": event.author,
        }
    if isinstance(event, events.RichContentDerived):
        return {
            "type": "rich_content_derived",
            "resource_id": event.resource_id,
            "community_uuid": event.community_uuid,
            "title": event.title,
            "version_label": event.version_label,
            "author": event.author,
            "body_captured": event.body_captured,
        }
    if isinstance(event, events.RichContentDiscovered):
        return {
            "type": "rich_content_discovered",
            "community_uuid": event.community_uuid,
            "placed": event.placed,
            "initialized": event.initialized,
        }
    if isinstance(event, events.Warning):
        return {
            "type": "warning",
            "kind": event.kind,
            "detail": event.detail,
            "ref": event.ref,
        }
    if isinstance(event, events.Pruned):
        return {"type": "pruned", "kind": event.kind, "id": event.id, "reason": event.reason}
    if isinstance(event, events.AuthorFilterSummary):
        return {
            "type": "author_filter_summary",
            "kept": event.kept,
            "total": event.total,
            "author": event.author,
            "identities": list(event.identities),
        }
    if isinstance(event, events.RunComplete):
        return {"type": "run_complete", "report": _report_payload(event.report)}
    raise TypeError(f"unknown crawler event type: {type(event)!r}")
