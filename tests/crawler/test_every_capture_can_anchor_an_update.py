"""Every finished capture has to be able to anchor the next update.

`finish_run` is the only thing that writes `completed_at`, and a run without
it is skipped by `last_successful_run` -- deliberately, because a run that
died half way through cannot say "everything before me is captured". Files
and Rich Content built their `CrawlResult` inline and never went through it,
so a capture of either finished successfully and still left an archive that
could not be updated by date.

Worse in company: a run record is named for its run id, the run id comes from
the clock to the second, and a community capture runs its components one
after another. Two landing in the same second shared a filename -- so a later
component's start record (no `completed_at` yet) overwrote an earlier
component's finished one, and an archive that HAD an anchor lost it.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl, crawl_files, crawl_rich_content
from connections_export.crawler.provenance import last_successful_run, update_cutoff
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_files,
    synthesize_rich_content,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

COMMUNITY = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=2)


@pytest.fixture
def deployment():
    return (
        synthesize(SEED),
        synthesize_files(SEED, community_uuid=COMMUNITY),
        synthesize_rich_content(SEED, community_uuid=COMMUNITY),
    )


def _client(deployment):
    wikis, files, rich = deployment
    app = make_app(wikis, fileset=files, rteset=rich)
    return HttpClient(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request), sleep=lambda _s: None
    )


def _config(root, fetch="resume"):
    return Config(base_url="https://fake", output_dir=root, fetch=fetch)


def test_a_files_capture_can_anchor_an_update(deployment, tmp_path: pathlib.Path):
    root = tmp_path / "archive"
    crawl_files(
        config=_config(root),
        client=_client(deployment),
        archive=Archive.open(root),
        emit=lambda _e: None,
        community_uuid=COMMUNITY,
    )
    assert last_successful_run(Archive.open(root)) is not None
    assert update_cutoff(Archive.open(root)) is not None


def test_a_rich_content_capture_can_anchor_an_update(deployment, tmp_path: pathlib.Path):
    root = tmp_path / "archive"
    crawl_rich_content(
        config=_config(root),
        client=_client(deployment),
        archive=Archive.open(root),
        emit=lambda _e: None,
        community_uuid=COMMUNITY,
    )
    assert last_successful_run(Archive.open(root)) is not None
    assert update_cutoff(Archive.open(root)) is not None


def test_components_captured_in_the_same_second_keep_their_own_records(
    deployment, tmp_path: pathlib.Path
):
    """One frozen stamp for every component, which is what a fast capture of a
    small community looks like from the clock's point of view."""
    root = tmp_path / "archive"
    frozen = lambda: "2026-08-25T09:10:11Z"  # noqa: E731
    for crawler, kwargs in (
        (crawl, {}),
        (crawl_files, {"community_uuid": COMMUNITY}),
        (crawl_rich_content, {"community_uuid": COMMUNITY}),
    ):
        crawler(
            config=_config(root),
            client=_client(deployment),
            archive=Archive.open(root),
            emit=lambda _e: None,
            clock=frozen,
            **kwargs,
        )

    records = list(root.glob("run-*.json"))
    assert len(records) == 3, [p.name for p in records]
    assert update_cutoff(Archive.open(root)) is not None
