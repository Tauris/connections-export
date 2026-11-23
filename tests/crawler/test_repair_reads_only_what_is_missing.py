"""Repairing forum attachments in place reads only what is missing.

An archive the `forums-1` reader made can lack a reply's uploaded document.
The repair works from the feeds the archive stores: it asks the deployment for
nothing it already has -- no forum, topic or replies feed -- reads a thread's
page only for the download address that works, downloads only the missing
file, adds to the archive without changing what is there, and records that it
ran so the archive is not offered the repair again.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl_forums
from connections_export.crawler.repair import repair_forum_attachments
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize_forums
from connections_export.gui.archives import archive_repair_status
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import SyncASGIBridge
from tests.crawler.test_assets_in_crawl import _empty_wikiset


def _forumset():
    return synthesize_forums(
        SynthSeed(
            seed=81,
            forum_count=1,
            topics_per_forum=2,
            replies_per_topic=1,
            reply_tree_depth=0,
            forum_reply_attachment=True,
        )
    )


class _Recording(httpx.BaseTransport):
    """The fake deployment, recording each path asked for and refusing those
    `blocked` names -- how an older capture came to lack a file."""

    def __init__(self, app, blocked=()):
        self.inner = SyncASGIBridge(app)
        self.blocked = blocked
        self.paths: list[str] = []

    def handle_request(self, request):
        self.paths.append(request.url.path)
        if request.url.path.startswith(self.blocked):
            return httpx.Response(404, request=request)
        return self.inner.handle_request(request)


def _client(transport) -> HttpClient:
    return HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )


def _old_archive(tmp_path: Path, forumset) -> Archive:
    root = tmp_path / "archive"
    old = _Recording(
        make_app(_empty_wikiset(), forumset=forumset),
        blocked=("/forums/ajax/download", "/forums/html/"),
    )
    crawl_forums(
        config=Config(base_url="https://fake", output_dir=root),
        client=_client(old),
        archive=Archive.open(root),
    )
    archive = Archive.open(root)
    archive.write_run_metadata(
        RunMetadata(
            run_id="older-release",
            base_url="https://fake",
            principal="someone",
            adapter_version="forums-1",
            source_system_version="8.0",
            started_at="2026-08-25T10:00:00Z",
            completed_at="2026-08-25T10:05:00Z",
        )
    )
    return archive


def _document(archive, forumset):
    topic = forumset.forums[0].topics[0]
    reply = topic.replies[0]
    derived = derive(archive).forums[0].topics[topic.uuid].replies[reply.uuid]
    return [a for a in derived.attachments if a.filename == reply.attachments[0].filename]


def test_the_repair_reads_one_page_and_one_file_and_nothing_else(tmp_path):
    forumset = _forumset()
    archive = _old_archive(tmp_path, forumset)
    assert archive_repair_status(archive.root)["repair_keys"] == ["forum-attachments"]
    assert not any(a.asset.present for a in _document(archive, forumset))
    manifest_before = (archive.root / "manifest.jsonl").read_text(encoding="utf-8")

    live = _Recording(make_app(_empty_wikiset(), forumset=forumset))
    result = repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(live),
        archive=archive,
    )

    # Only the affected thread's page and its one missing file.
    assert [p.split("/forums/")[1].split("/")[0] for p in live.paths] == ["html", "ajax"], (
        live.paths
    )
    assert (result.threads, result.threads_affected) == (2, 1)
    assert (result.pages_read, result.files_fetched, result.files_missing) == (1, 1, 0)

    # Added to, never rewritten.
    archive = Archive.open(archive.root)
    manifest_after = (archive.root / "manifest.jsonl").read_text(encoding="utf-8")
    assert manifest_after.startswith(manifest_before)
    [document] = _document(archive, forumset)
    assert document.asset.present
    assert archive_repair_status(archive.root)["repair_needed"] is False


def test_a_second_repair_asks_for_nothing(tmp_path):
    forumset = _forumset()
    archive = _old_archive(tmp_path, forumset)
    first = _Recording(make_app(_empty_wikiset(), forumset=forumset))
    repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(first),
        archive=archive,
    )
    again = _Recording(make_app(_empty_wikiset(), forumset=forumset))
    result = repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(again),
        archive=Archive.open(archive.root),
    )
    assert again.paths == []
    assert result.threads_affected == 0


def test_the_cli_repair_needs_no_selection(tmp_path):
    from connections_export.cli import crawl_main

    forumset = _forumset()
    archive = _old_archive(tmp_path, forumset)
    live = _Recording(make_app(_empty_wikiset(), forumset=forumset))
    code = crawl_main(["--repair", "--into", str(archive.root)], env={}, client=_client(live))
    assert code == 0
    [document] = _document(Archive.open(archive.root), forumset)
    assert document.asset.present


class _Silent(httpx.BaseTransport):
    """A server that does not answer at all."""

    def handle_request(self, request):
        raise httpx.ConnectError("no route to host", request=request)


class _Status(httpx.BaseTransport):
    def __init__(self, status):
        self.status = status

    def handle_request(self, request):
        return httpx.Response(self.status, request=request)


def test_a_server_that_cannot_be_contacted_is_said_so_and_nothing_changes(tmp_path):
    from connections_export.cli import crawl_main

    archive = _old_archive(tmp_path, _forumset())
    before = sorted((p.name, p.stat().st_size) for p in archive.root.rglob("*") if p.is_file())

    code = crawl_main(["--repair", "--into", str(archive.root)], env={}, client=_client(_Silent()))

    assert code == 4
    after = sorted((p.name, p.stat().st_size) for p in archive.root.rglob("*") if p.is_file())
    assert after == before
    assert archive_repair_status(archive.root)["repair_needed"] is True


def test_what_the_check_says():
    from connections_export.crawler.reach import server_problem

    silent = server_problem(_client(_Silent()), "https://connections.example.corp")
    assert "cannot be contacted" in silent and "connections.example.corp" in silent
    assert "Nothing in the archive was changed" in silent
    refused = server_problem(_client(_Status(401)), "https://connections.example.corp")
    assert "signing in" in refused
    assert server_problem(_client(_Status(404)), "https://connections.example.corp") is None
    assert server_problem(_client(_Status(302)), "https://connections.example.corp") is None


def test_the_explanation_says_what_is_missing_and_that_only_attachments_are():
    from connections_export.gui.archives import FORUM_ATTACHMENTS_ISSUE

    text = FORUM_ATTACHMENTS_ISSUE["explanation"]
    assert "skipped by mistake" in text
    assert "not show up as broken links" in text and "nothing in the archive mentions" in text
    assert "Only these attachments are affected" in text
    assert "server" in text


def test_the_repair_reports_its_progress_as_it_goes(tmp_path):
    """A long repair visibly moves: stages, threads checked out of how many,
    and what it read and added."""
    from connections_export.crawler.events import RepairProgress

    forumset = _forumset()
    archive = _old_archive(tmp_path, forumset)
    seen = []
    repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(_Recording(make_app(_empty_wikiset(), forumset=forumset))),
        archive=archive,
        emit=seen.append,
    )
    progress = [e for e in seen if isinstance(e, RepairProgress)]
    stages = [e.stage for e in progress]
    assert stages[0].startswith("reading the archive's stored forum feeds")
    assert "downloading missing attachments" in stages
    assert stages[-1] == "finished"
    assert (progress[-1].threads_done, progress[-1].threads_total) == (2, 2)
    assert (progress[-1].pages_read, progress[-1].files_fetched) == (1, 1)
    # Each download names the thread it is for.
    downloads = [e for e in progress if e.stage == "downloading missing attachments"]
    assert downloads and downloads[0].item == forumset.forums[0].topics[0].title


def test_a_stopped_repair_stops_at_once_and_is_offered_again(tmp_path):
    """Stop is answered at the next thread, not after the archive; what was
    fetched is kept, and the repair is not recorded as made."""
    import threading

    forumset = _forumset()
    archive = _old_archive(tmp_path, forumset)
    stop = threading.Event()
    stop.set()
    live = _Recording(make_app(_empty_wikiset(), forumset=forumset))
    result = repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(live),
        archive=archive,
        stop_event=stop,
    )
    assert result.stopped and live.paths == []
    assert archive_repair_status(archive.root)["repair_keys"] == ["forum-attachments"]

    finish = repair_forum_attachments(
        config=Config(base_url="https://fake", output_dir=archive.root),
        client=_client(_Recording(make_app(_empty_wikiset(), forumset=forumset))),
        archive=Archive.open(archive.root),
    )
    assert not finish.stopped and finish.files_fetched == 1
    assert archive_repair_status(archive.root)["repair_needed"] is False
