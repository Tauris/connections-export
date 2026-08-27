"""The same archive, as a directory and as a zip, derives the same model.

If this holds, the source abstraction is faithful and every other test in this
feature is detail. The zip is built from a real archive at test time rather
than committed as a fixture, so the two can never drift apart.
"""

from __future__ import annotations

import zipfile

import httpx

from connections_export.archive.source import ReadableArchive
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient


def _captured(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=1)
    transport = httpx.MockTransport(SyncASGIBridge(make_app(synthesize(seed))).handle_request)
    root = tmp_path / "archive"
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    return root


def _zipped(root, destination, prefix=""):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, prefix + str(path.relative_to(root)).replace("\\", "/"))
    return destination


def test_a_zipped_archive_derives_the_same_interchange(tmp_path):
    root = _captured(tmp_path)
    zipped = _zipped(root, tmp_path / "archive.zip")

    from_directory = derive(Archive.open(root))
    from_zip = derive(ReadableArchive.open(zipped))

    assert from_zip.model_dump_json() == from_directory.model_dump_json()


def test_the_same_holds_when_the_zip_nests_the_archive_in_a_folder(tmp_path):
    root = _captured(tmp_path)
    zipped = _zipped(root, tmp_path / "nested.zip", prefix="export-2026-08-23/")

    from_directory = derive(Archive.open(root))
    from_zip = derive(ReadableArchive.open(zipped))

    assert from_zip.model_dump_json() == from_directory.model_dump_json()
