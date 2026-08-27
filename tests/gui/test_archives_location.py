"""Choosing where archives are kept, from the console.

It was an environment variable and a read-only line of text, which means
changing it required knowing what an environment variable is, restarting the
process, and doing both in the right order. Meanwhile the console already
knows the archives it can see and shows the path it is using -- everything
except the ability to change it.

An archive directory is a real thing on disk, so the safety rules are about
the disk: point at a directory that exists, refuse a file, and never create
anything just because someone typed a path with a typo in it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path / "archives")
    (tmp_path / "archives").mkdir()
    return TestClient(make_app(demo=True, demo_delay=0), base_url="http://127.0.0.1"), tmp_path


def test_the_current_location_is_reported(client):
    http, tmp_path = client

    assert http.get("/api/settings").json()["archives_dir"] == str(tmp_path / "archives")


def test_a_new_location_can_be_chosen(client):
    http, tmp_path = client
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    response = http.put("/api/archives-dir", json={"path": str(elsewhere)})

    assert response.status_code == 200
    assert http.get("/api/settings").json()["archives_dir"] == str(elsewhere)


def test_the_archives_there_are_the_ones_listed(client):
    """The point of changing it. A setting that moved the label and not the
    listing would be worse than none."""
    from connections_export.gui.demo import run_demo

    http, tmp_path = client
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run_demo(lambda _e: None, delay=0, archive_dir=elsewhere / "export-there", wave=0)

    http.put("/api/archives-dir", json={"path": str(elsewhere)})

    names = [a["name"] for a in http.get("/api/archives").json()["archives"]]
    assert "export-there" in names


def test_a_path_that_does_not_exist_is_refused(client):
    """Silently creating it would turn a typo into an empty archives
    directory, and the archives you were looking for into "none found"."""
    http, tmp_path = client

    response = http.put("/api/archives-dir", json={"path": str(tmp_path / "typo")})

    assert response.status_code == 422
    assert not (tmp_path / "typo").exists()


def test_a_file_is_refused(client):
    http, tmp_path = client
    a_file = tmp_path / "notadir.txt"
    a_file.write_text("x", encoding="utf-8")

    assert http.put("/api/archives-dir", json={"path": str(a_file)}).status_code == 422


def test_an_empty_path_is_refused(client):
    http, _ = client

    assert http.put("/api/archives-dir", json={"path": "  "}).status_code == 422


def test_a_refused_change_leaves_the_old_location_in_place(client):
    """A rejected change must not half-apply: the console goes on listing what
    it was listing."""
    http, tmp_path = client

    http.put("/api/archives-dir", json={"path": str(tmp_path / "typo")})

    assert http.get("/api/settings").json()["archives_dir"] == str(tmp_path / "archives")
