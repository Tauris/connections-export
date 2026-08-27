"""`ModelSource` live mode. A run's archive is
derived **on demand** into a growing snapshot so the reader can show real
content mid-import (`_assemble_page` degrades gracefully on a partial
archive; `derive` raises `DeriveError` only until the first feed is
archived). The derive is cached on the manifest's byte size, so repeated
reads don't re-derive until the archive has actually grown.

Uses `run_demo` to produce a real archive (the genuine crawler → archive
pipeline against the in-process fakeserver), then drives `ModelSource`
against it directly — no sockets, no timing.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.crawler.events import Event
from connections_export.gui.demo import run_demo
from connections_export.gui.model_source import ModelSource


def _demo_archive(tmp_path: Path) -> Path:
    """A real, complete archive from a fast demo run."""
    archive_dir = tmp_path / "archive"
    events: list[Event] = []
    run_demo(events.append, seed=3, archive_dir=archive_dir, delay=0)
    return archive_dir


def test_page_derived_ids_are_exactly_the_model_page_ids(tmp_path):
    """The `page_derived` event's `page_id` is the nav-node id -- the same
    id the derive layer uses for `DerivedPage.id`, and the space `parent`
    is in -- so the GUI correlates events with the derived model exactly by
    id (never by a title that could collide). Locks that correspondence."""
    from connections_export.archive.store import Archive
    from connections_export.crawler.events import PageDerived
    from connections_export.derive import derive

    archive_dir = tmp_path / "archive"
    events: list[Event] = []
    run_demo(events.append, seed=4, archive_dir=archive_dir, delay=0)

    model = derive(Archive.open(archive_dir))
    model_page_ids = {pid for wiki in model.wikis for pid in wiki.pages}
    derived = [e for e in events if isinstance(e, PageDerived)]

    assert derived
    for event in derived:
        assert event.page_id in model_page_ids, (
            f"page_derived id {event.page_id!r} is not a derived model page id"
        )
    # `parent` lives in the same id space (every non-root parent is a page id).
    for event in derived:
        if event.parent is not None:
            assert event.parent in model_page_ids


def test_live_empty_archive_is_pending(tmp_path):
    (tmp_path / "empty").mkdir()
    source = ModelSource()
    source.attach_live(tmp_path / "empty")

    assert source.get_model() is None
    assert source.model_state() == "pending"


def test_live_derives_a_real_archive_and_reports_partial(tmp_path):
    source = ModelSource()
    source.attach_live(_demo_archive(tmp_path))

    model = source.get_model()
    assert model is not None
    assert model.wikis, "a live-derived model should carry the crawled wikis"
    assert source.model_state() == "partial"


def test_live_get_model_is_cached_until_the_archive_grows(tmp_path):
    archive_dir = _demo_archive(tmp_path)
    source = ModelSource()
    source.attach_live(archive_dir)

    first = source.get_model()
    second = source.get_model()
    # No growth between calls -> the very same derived object, not a re-derive.
    assert first is second

    # Simulate the crawl appending another manifest record: the cache key is
    # the manifest's byte size, so a grown manifest forces a re-derive.
    manifest = archive_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    third = source.get_model()
    assert third is not first, "a grown archive must re-derive, not serve the stale cache"


def test_stash_supersedes_live_and_reports_complete(tmp_path):
    archive_dir = _demo_archive(tmp_path)
    source = ModelSource()
    source.attach_live(archive_dir)
    live_model = source.get_model()
    assert live_model is not None
    assert source.model_state() == "partial"

    # The run finished: the final derived model is stashed and wins.
    from connections_export.archive.store import Archive
    from connections_export.derive import derive

    final = derive(Archive.open(archive_dir))
    source.stash(final, archive_dir=archive_dir)

    assert source.get_model() is final
    assert source.model_state() == "complete"


def test_live_blobs_resolve_from_the_attached_archive(tmp_path):
    """`attach_live` sets the blob root immediately, so images/attachments
    resolve while the run is still going (not only after `stash`)."""
    archive_dir = _demo_archive(tmp_path)
    source = ModelSource()
    source.attach_live(archive_dir)

    model = source.get_model()
    assert model is not None
    # Find any resolved body asset with a present blob and fetch it.
    a_hash = None
    for wiki in model.wikis:
        for page in wiki.pages.values():
            for asset in page.assets:
                if asset.present and asset.blob_hash:
                    a_hash = asset.blob_hash.split(":", 1)[-1]
                    break
            if a_hash:
                break
        if a_hash:
            break

    assert a_hash, "the demo body references at least one same-host image asset"
    result = source.get_blob(a_hash)
    assert result is not None
    data, _content_type = result
    assert data
