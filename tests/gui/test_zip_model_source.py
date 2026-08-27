"""The server serves a zipped archive: model, blobs and all.

Deriving from a zip is `test_zip_parity`'s job. What is left is the reader's
own path -- blobs come out of `ModelSource`, which held a directory and read
`blobs/<hex>` itself.
"""

from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from connections_export.archive.source import ArchiveSourceError
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.model_source import ModelSource
from connections_export.http.client import HttpClient


@pytest.fixture
def zipped_archive(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=1)
    transport = httpx.MockTransport(SyncASGIBridge(make_app(synthesize(seed))).handle_request)
    root = tmp_path / "archive"
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    destination = tmp_path / "archive.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                bundle.write(path, str(path.relative_to(root)).replace("\\", "/"))
    return destination


def _some_blob_hash(root):
    return sorted(p.name for p in (root / "blobs").iterdir())[0]


def test_a_zipped_archive_serves_its_model(zipped_archive):
    source = ModelSource.from_archive(zipped_archive)
    model = source.get_model()
    assert model is not None
    assert model.wikis


def test_blobs_come_out_of_the_zip(zipped_archive, tmp_path):
    source = ModelSource.from_archive(zipped_archive)
    digest = _some_blob_hash(tmp_path / "archive")
    served = source.get_blob(digest)
    assert served is not None
    data, _content_type = served
    assert data == (tmp_path / "archive" / "blobs" / digest).read_bytes()


def test_a_blob_the_archive_does_not_hold_is_not_found_rather_than_an_error(zipped_archive):
    source = ModelSource.from_archive(zipped_archive)
    assert source.get_blob("ab" * 32) is None


def test_a_malformed_hash_never_reaches_the_zip(zipped_archive):
    source = ModelSource.from_archive(zipped_archive)
    assert source.get_blob("../../etc/passwd") is None


def test_concurrent_blob_reads_return_the_right_bytes(zipped_archive, tmp_path):
    """`zipfile.ZipFile` is not thread-safe and an image-heavy page issues many
    parallel requests, so the source keeps a handle per thread."""
    source = ModelSource.from_archive(zipped_archive)
    digests = sorted(p.name for p in (tmp_path / "archive" / "blobs").iterdir())[:8]
    expected = {d: (tmp_path / "archive" / "blobs" / d).read_bytes() for d in digests}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda d: (d, source.get_blob(d)), digests * 4))

    for digest, served in results:
        assert served is not None
        assert served[0] == expected[digest]


def test_a_zip_that_holds_no_archive_says_so(tmp_path):
    empty = tmp_path / "not-an-archive.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("readme.txt", "nothing to see")
    with pytest.raises(ArchiveSourceError):
        ModelSource.from_archive(empty)
