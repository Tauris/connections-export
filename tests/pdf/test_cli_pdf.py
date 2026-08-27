"""`connections-export pdf` (Path A entry point): reads a derived model + blobs
from an archive (or package) and writes a PDF. `render` is injected so
no real Chromium is launched -- the browser step itself is covered by
the browser-gated `test_browser.py`; here we test the CLI plumbing
(source selection, blob wiring, file output, exit codes).
"""

from __future__ import annotations

from pathlib import Path

from connections_export.cli import pdf_main
from connections_export.crawler.events import Event
from connections_export.gui.demo import run_demo


def _demo_archive(tmp_path: Path) -> Path:
    archive_dir = tmp_path / "archive"
    events: list[Event] = []
    run_demo(events.append, archive_dir=archive_dir, delay=0)
    return archive_dir


def test_pdf_from_archive_writes_a_file(tmp_path):
    archive_dir = _demo_archive(tmp_path)
    out = tmp_path / "out.pdf"

    captured = {}

    def fake_render(model, blob_bytes):
        # The CLI hands us a real derived model and a working blob lookup.
        captured["wikis"] = [w.title for w in model.wikis]
        first_page = next(iter(model.wikis[0].pages.values()))
        for asset in first_page.assets:
            if asset.present and asset.blob_hash:
                captured["blob_ok"] = blob_bytes(asset.blob_hash.split(":")[-1]) is not None
        return b"%PDF-1.4 fake bytes"

    code = pdf_main(["--archive", str(archive_dir), "--output", str(out)], render=fake_render)

    assert code == 0
    assert out.read_bytes().startswith(b"%PDF-")
    assert captured["wikis"], "the CLI should derive and pass a real model"
    assert captured.get("blob_ok") is True, "blob lookup should resolve a present asset"


def test_pdf_reports_when_no_model_is_available(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out.pdf"

    # An empty dir derives to no model -> a clear nonzero exit, no file.
    code = pdf_main(
        ["--archive", str(empty), "--output", str(out)],
        render=lambda *_: b"%PDF-should-not-be-called",
    )
    assert code == 1
    assert not out.exists()
