"""The CLI takes a zipped archive wherever it takes an archive directory."""

from __future__ import annotations

import zipfile

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient


@pytest.fixture
def zipped(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=2, comments_per_page=0)
    transport = httpx.MockTransport(SyncASGIBridge(make_app(synthesize(seed))).handle_request)
    root = tmp_path / "archive"
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    destination = tmp_path / "archive.zip"
    with zipfile.ZipFile(destination, "w") as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                bundle.write(path, str(path.relative_to(root)).replace("\\", "/"))
    return destination


def test_open_accepts_a_zip(zipped):
    """`open` refused anything that was not a directory, which would have made
    reading a zip a thing no command could actually do."""
    from connections_export.cli import open_main

    seen = {}

    def fake_run(app, *_args, **_kwargs):  # the console is not started in a test
        seen["app"] = app

    assert open_main([str(zipped)], run=fake_run) == 0
    assert "app" in seen


def test_open_still_refuses_something_that_is_neither(tmp_path, capsys):
    from connections_export.cli import open_main

    stray = tmp_path / "notes.txt"
    stray.write_text("hello", encoding="utf-8")
    assert open_main([str(stray)], run=lambda *a, **k: None) == 2
    assert "archive" in capsys.readouterr().err


def test_pdf_reads_a_zipped_archive(zipped, tmp_path):
    from connections_export.gui.model_source import ModelSource

    source = ModelSource.from_archive(zipped)
    assert source.get_model() is not None
