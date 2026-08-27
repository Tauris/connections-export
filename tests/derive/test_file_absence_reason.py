"""A document you filtered out and a document that would not come down are
not the same thing, and an archive must not say they are.

An author-filtered Files capture still lists every document in the library --
the listing is the library's own feed, and trimming it would be inventing a
library that never existed. But the documents outside the filter were never
downloaded, and the reader marked them exactly as it marks one whose bytes
failed: "not captured". So an archive said the same words about a document
nobody tried to fetch and a document the deployment refused.

The reason is knowable: the run records the filter it was captured under, and
the file records its author. What is missing is only that nothing carried the
answer to the page.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_files
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import synthesize, synthesize_files
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.demo import demo_synth_seed
from connections_export.http.client import HttpClient

COMMUNITY = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"


@pytest.fixture
def deployment():
    seed = demo_synth_seed(0)
    return synthesize(seed), synthesize_files(seed, community_uuid=COMMUNITY)


def _run(deployment, root: pathlib.Path, author=None):
    wikis, files = deployment
    transport = httpx.MockTransport(SyncASGIBridge(make_app(wikis, fileset=files)).handle_request)
    crawl_files(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
        community_uuid=COMMUNITY,
        author=author,
    )


def _files(root):
    model = derive(Archive.open(root))
    return [f for lib in model.file_libraries for f in lib.files.values()]


def test_the_archive_records_which_filter_it_was_captured_under(deployment, tmp_path):
    author = deployment[1].libraries[0].files[0].author
    _run(deployment, tmp_path / "a", author=author)
    assert derive(Archive.open(tmp_path / "a")).author_filter == author


def test_a_document_outside_the_filter_says_so_rather_than_not_captured(deployment, tmp_path):
    author = deployment[1].libraries[0].files[0].author
    _run(deployment, tmp_path / "a", author=author)

    files = _files(tmp_path / "a")
    kept = [f for f in files if f.author == author]
    excluded = [f for f in files if f.author != author]
    assert kept and excluded, "the fixture must have both, or this proves nothing"

    assert all(f.asset is not None and f.asset.present for f in kept)
    assert all(f.excluded_by_author_filter for f in excluded)
    assert not any(f.excluded_by_author_filter for f in kept)


def test_an_unfiltered_capture_marks_nothing_excluded(deployment, tmp_path):
    """Absent bytes in an unfiltered archive mean what they always meant."""
    _run(deployment, tmp_path / "a")
    assert not any(f.excluded_by_author_filter for f in _files(tmp_path / "a"))
    assert derive(Archive.open(tmp_path / "a")).author_filter is None
