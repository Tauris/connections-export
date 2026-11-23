"""Repairing an archive in place, reading as little as possible.

An archive whose forums the `forums-1` reader captured can lack documents
uploaded to topics and replies: the feed carries them nested in a
``snx:field type="file"``, which that reader did not look at. Everything
needed to find them is usually in the archive already -- the feeds were
stored -- so the repair works from what is stored and goes to the deployment
only for what is missing:

- every stored topic and replies feed is parsed again, with no request;
- a thread with no attachment missing its bytes costs nothing more;
- a thread whose missing files are named only by the feed's address, which
  does not download, has its page read for the address that does -- from the
  archive when it is stored, else with one request;
- each missing file is downloaded once.

What is written is only added: the new responses and files, and a run record
saying the repair was made (`RunMetadata.repairs`), which is what stops the
archive being offered the repair again. Nothing is selected anew and nothing
already in the archive changes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from connections_export.adapters.forums import (
    merge_page_attachments,
    parse_replies_feed,
    parse_single_topic,
    parse_topics_feed,
)
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.apps._shared import FORUMS_ADAPTER_VERSION
from connections_export.crawler.engine import (
    begin_run,
    default_clock,
    finish_run,
    read_ok_records,
)
from connections_export.crawler.events import Emit
from connections_export.http.client import HttpClient

#: The key a forum attachment repair is recorded under in `RunMetadata.repairs`.
FORUM_ATTACHMENTS = "forum-attachments"

_FEEDS = {
    "/forums/atom/topics": parse_topics_feed,
    "/forums/atom/topic": parse_single_topic,
    "/forums/atom/replies": parse_replies_feed,
}


@dataclass
class ForumRepairResult:
    threads: int = 0
    threads_affected: int = 0
    pages_read: int = 0
    files_fetched: int = 0
    files_missing: int = 0
    stopped: bool = False


def _stored_threads(archive: Archive) -> dict[str, dict]:
    """Every thread the archive's stored forum feeds describe, by topic id:
    ``{"topic": ForumTopic | None, "replies": {id: ForumReply}}``."""
    from connections_export.archive.blobs import read_blob, valid_digest  # noqa: PLC0415

    threads: dict[str, dict] = {}
    for url, record in read_ok_records(archive).items():
        path = urlsplit(url).path.rstrip("/")
        parser = next((p for suffix, p in _FEEDS.items() if path.endswith(suffix)), None)
        if parser is None or not record.body_hash:
            continue
        digest = valid_digest(record.body_hash)
        if digest is None:
            continue
        try:
            items = parser(read_blob(archive.root, digest))
        except Exception:  # noqa: BLE001 - an unreadable feed is not this repair's to judge
            continue
        for item in items:
            if parser is parse_replies_feed:
                thread = threads.setdefault(item.source_topic or "", {"topic": None, "replies": {}})
                thread["replies"][item.id] = item
            else:
                thread = threads.setdefault(item.id, {"topic": None, "replies": {}})
                thread["topic"] = thread["topic"] or item
    threads.pop("", None)
    return threads


def repair_forum_attachments(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    emit: Emit | None = None,
    clock: Callable[[], str] | None = None,
    stop_event: threading.Event | None = None,
) -> ForumRepairResult:
    """Add the forum attachments a `forums-1` capture missed, reading only
    what is not already in the archive (see the module docstring)."""
    emit = emit if emit is not None else events.default_emit
    config = config.model_copy(
        update={"fetch": "resume", "repairs": [*config.repairs, FORUM_ATTACHMENTS]}
    )
    stored = read_ok_records(archive)
    session = begin_run(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock or default_clock,
        run_id=None,
        adapter_version=FORUMS_ADAPTER_VERSION,
        run_kind="update",
    )
    fetcher = session.fetcher
    result = ForumRepairResult()

    def progress(stage: str, item: str = "") -> None:
        events.safe_emit(
            emit,
            events.RepairProgress(
                stage=stage,
                threads_done=result.threads,
                threads_total=total,
                pages_read=result.pages_read,
                files_fetched=result.files_fetched,
                files_missing=result.files_missing,
                item=item,
            ),
        )

    total = 0
    progress("reading the archive's stored forum feeds")
    threads = _stored_threads(archive)
    total = len(threads)
    progress("checking threads for missing attachments")

    def absolute(href: str) -> str:
        return urljoin(session.base_url + "/", href)

    def missing(entries) -> list:
        return [
            attachment
            for entry in entries
            for attachment in entry.attachments
            if attachment.enclosure_href and absolute(attachment.enclosure_href) not in stored
        ]

    def read_page(url: str) -> bytes | None:
        if url not in stored:
            result.pages_read += 1
        return fetcher.get_content(url, "topic", None)

    for topic_id, thread in sorted(threads.items()):
        if stop_event is not None and stop_event.is_set():
            break
        result.threads += 1
        title = (thread["topic"].title if thread["topic"] is not None else None) or topic_id
        if result.threads % 25 == 0:
            progress("checking threads for missing attachments", title)
        entries = [e for e in [thread["topic"], *thread["replies"].values()] if e is not None]
        lacking = missing(entries)
        if not lacking:
            continue
        # The feed's own download address for an uploaded file does not
        # download; the thread's page has the one that does -- from the
        # archive when it is stored, which is what makes a repeated repair
        # ask for nothing.
        if any("/ajax/download" not in (a.enclosure_href or "") for a in lacking):
            progress("reading a thread page for the working download address", title)
            merge_page_attachments(entries, read_page)
            lacking = missing(entries)
        if not lacking:
            continue
        result.threads_affected += 1
        for attachment in lacking:
            if stop_event is not None and stop_event.is_set():
                break
            url = absolute(attachment.enclosure_href)
            # On the deployment's own host by construction: a forum download.
            if fetcher.get_content(url, "attachments", topic_id) is not None:
                stored[url] = True
                result.files_fetched += 1
            else:
                result.files_missing += 1
            progress("downloading missing attachments", title)
    if stop_event is not None and stop_event.is_set():
        # Stopped part-way: what was fetched stays (it is only additions), but
        # the run is left without a completion time, so it is not recorded as
        # the repair having been made and the archive is offered it again --
        # which then fetches only what is still missing.
        result.stopped = True
        progress("stopped -- what was fetched is kept; run Repair again to finish")
        return result
    progress("finished")
    finish_run(session, orphans=[], pages_crawled=result.threads)
    return result
