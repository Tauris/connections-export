"""A zipped archive refuses every write, in words.

Read-only is the whole scope of the zip source. The failure mode worth
guarding is not a traceback -- it is a silent no-op that leaves someone
believing their update landed somewhere.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from connections_export.archive.source import ArchiveSourceError, open_source


def _zip_archive(destination):
    with zipfile.ZipFile(destination, "w") as bundle:
        bundle.writestr(
            "manifest.jsonl",
            '{"url": "https://fake/a", "method": "GET", "outcome": "ok",'
            ' "fetched_at": "2026-08-23T00:00:00Z"}\n',
        )
        bundle.writestr("archive-summary.json", json.dumps({"status": "ok"}))
    return destination


@pytest.fixture
def archives(tmp_path):
    folder = tmp_path / "archives"
    folder.mkdir()
    _zip_archive(folder / "export-2026-08-23.zip")
    return folder


def test_a_source_says_plainly_that_a_zip_is_read_only():
    from connections_export.archive.source import ZipSource

    assert not ZipSource("unused.zip").writable
    assert open_source.__doc__


def test_extending_a_zipped_archive_is_refused_rather_than_attempted(archives, monkeypatch):
    from fastapi.testclient import TestClient

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(support, "ARCHIVES_BASE", archives)
    client = TestClient(make_app(demo=True))
    response = client.post(
        "/api/start",
        json={"into": "export-2026-08-23.zip", "demo": True},
        headers={"Host": "127.0.0.1:8765"},
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]


def test_renaming_a_zipped_archive_is_refused_rather_than_silently_lost(archives, monkeypatch):
    from fastapi.testclient import TestClient

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(support, "ARCHIVES_BASE", archives)
    client = TestClient(make_app(demo=True))
    response = client.put(
        "/api/archives/export-2026-08-23.zip/label",
        json={"label": "Programme archive"},
        headers={"Host": "127.0.0.1:8765"},
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]


def test_a_directory_archive_still_takes_a_rename(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    folder = tmp_path / "archives"
    (folder / "export-plain").mkdir(parents=True)
    (folder / "export-plain" / "manifest.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(support, "ARCHIVES_BASE", folder)
    client = TestClient(make_app(demo=True))
    response = client.put(
        "/api/archives/export-plain/label",
        json={"label": "Programme archive"},
        headers={"Host": "127.0.0.1:8765"},
    )
    assert response.status_code == 200


def test_crawling_into_a_zip_is_refused_at_the_archive_itself(tmp_path):
    from connections_export.archive.store import Archive

    zipped = _zip_archive(tmp_path / "export.zip")
    with pytest.raises(ArchiveSourceError) as excinfo:
        Archive.open(zipped)
    assert "read-only" in str(excinfo.value)


def test_deleting_a_zipped_archive_removes_the_file(archives, monkeypatch):
    """Deleting is not a write INTO the archive -- it removes something the
    user put there. Listing zips made them selectable for deletion, and
    `shutil.rmtree` on a file is a 500."""
    from fastapi.testclient import TestClient

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    monkeypatch.setattr(support, "ARCHIVES_BASE", archives)
    client = TestClient(make_app(demo=True))
    response = client.post(
        "/api/delete-archive",
        json={"name": "export-2026-08-23.zip", "confirmation": "export-2026-08-23.zip"},
        headers={"Host": "127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert not (archives / "export-2026-08-23.zip").exists()


def test_deleting_a_set_that_includes_a_zip_reports_it_deleted(archives, monkeypatch):
    from fastapi.testclient import TestClient

    from connections_export.gui import support
    from connections_export.gui.app import make_app

    (archives / "export-plain").mkdir()
    monkeypatch.setattr(support, "ARCHIVES_BASE", archives)
    client = TestClient(make_app(demo=True))
    response = client.post(
        "/api/delete-archives",
        json={"names": ["export-2026-08-23.zip", "export-plain"], "confirmation": "2"},
        headers={"Host": "127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert '"failed": []' in response.text.replace('"failed":[]', '"failed": []')
    assert not (archives / "export-2026-08-23.zip").exists()
    assert not (archives / "export-plain").exists()
