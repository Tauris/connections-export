"""Open an archive that lives anywhere -- dropped, not filed.

The archives list shows what is in the configured folder. An archive someone
hands you does not start there: it arrives as a zip in Downloads, a folder on
a share, a file synced out of OneDrive, or a link. Requiring people to move it
into the right directory first is asking them to do filing before they can
read.

This is the same capability `connections-export open <path>` already gives the
same user at a terminal. The console is bound to localhost and guards
state-changing requests by Origin, so a page they happen to have open cannot
reach it.
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.fakeserver.app import make_app as make_fake
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.app import make_app
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

HOST = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}


@pytest.fixture
def client():
    return TestClient(make_app(demo=True))


@pytest.fixture
def archive_dir(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=2, comments_per_page=0)
    transport = httpx.MockTransport(SyncASGIBridge(make_fake(synthesize(seed))).handle_request)
    root = tmp_path / "somewhere-else" / "export-handed-to-me"
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    return root


@pytest.fixture
def archive_zip(archive_dir, tmp_path):
    destination = tmp_path / "export-handed-to-me.zip"
    with zipfile.ZipFile(destination, "w") as bundle:
        for path in sorted(archive_dir.rglob("*")):
            if path.is_file():
                bundle.write(path, str(path.relative_to(archive_dir)).replace("\\", "/"))
    return destination


def test_a_directory_outside_the_archives_folder_opens(client, archive_dir):
    response = client.post("/api/open-external", json={"path": str(archive_dir)}, headers=HOST)
    assert response.status_code == 200, response.text
    assert client.get("/api/model", headers=HOST).status_code == 200


def test_a_zip_outside_the_archives_folder_opens(client, archive_zip):
    response = client.post("/api/open-external", json={"path": str(archive_zip)}, headers=HOST)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == archive_zip.name


def test_a_file_uri_is_accepted_because_that_is_what_a_drop_carries(client, archive_dir):
    """A file manager hands the browser `file:///...`, not a path."""
    response = client.post("/api/open-external", json={"path": archive_dir.as_uri()}, headers=HOST)
    assert response.status_code == 200, response.text


def test_a_path_that_is_not_an_archive_says_so(client, tmp_path):
    (tmp_path / "not-an-archive").mkdir()
    response = client.post(
        "/api/open-external", json={"path": str(tmp_path / "not-an-archive")}, headers=HOST
    )
    assert response.status_code == 400
    assert "archive" in response.json()["detail"].lower()


def test_a_path_that_does_not_exist_says_so(client, tmp_path):
    response = client.post(
        "/api/open-external", json={"path": str(tmp_path / "nope.zip")}, headers=HOST
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_a_failed_open_does_not_clobber_what_is_already_loaded(client, archive_dir, tmp_path):
    client.post("/api/open-external", json={"path": str(archive_dir)}, headers=HOST)
    before = client.get("/api/current-archive", headers=HOST).json()["name"]
    client.post("/api/open-external", json={"path": str(tmp_path / "nope")}, headers=HOST)
    assert client.get("/api/current-archive", headers=HOST).json()["name"] == before


def test_an_uploaded_zip_opens(client, archive_zip):
    """OneDrive and SharePoint hand the browser a File with no path at all, so
    the bytes have to travel. Sent as a raw body rather than multipart: a
    multi-gigabyte archive is streamed to disk instead of buffered, and it
    costs no new dependency (`python-multipart` is not installed, and adding
    one to a distribution that now ships an SBOM is not free)."""
    response = client.post(
        "/api/upload-archive",
        content=archive_zip.read_bytes(),
        headers={**HOST, "Content-Type": "application/zip", "X-Archive-Name": "handed-to-me.zip"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/model", headers=HOST).status_code == 200


def test_an_upload_that_is_not_a_zip_is_refused(client):
    response = client.post(
        "/api/upload-archive",
        content=b"hello, not a zip at all",
        headers={**HOST, "Content-Type": "application/zip", "X-Archive-Name": "notes.txt"},
    )
    assert response.status_code == 400
    assert "zip" in response.json()["detail"].lower()


def test_an_upload_of_a_zip_holding_no_archive_is_refused(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("readme.txt", "nothing here")
    response = client.post(
        "/api/upload-archive",
        content=buffer.getvalue(),
        headers={**HOST, "Content-Type": "application/zip", "X-Archive-Name": "empty.zip"},
    )
    assert response.status_code == 400


def test_a_cross_origin_page_cannot_open_anything(client, archive_dir):
    """The whole point of the Origin guard: this is a state-changing POST that
    reaches the user's filesystem."""
    response = client.post(
        "/api/open-external",
        json={"path": str(archive_dir)},
        headers={"Host": "127.0.0.1:8765", "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_a_zip_url_is_fetched_and_opened(client, archive_zip, monkeypatch):
    """A link is what SharePoint, OneDrive and a web server give you. The
    bytes have to come down before anything can be read."""
    import connections_export.gui.routes.archives as routes

    def fake_get(url, **_kwargs):
        assert url == "https://share.example/exports/handed.zip"
        return httpx.Response(200, content=archive_zip.read_bytes())

    monkeypatch.setattr(routes, "_fetch_url", lambda url, **_k: fake_get(url).content)

    response = client.post(
        "/api/open-external",
        json={"path": "https://share.example/exports/handed.zip"},
        headers=HOST,
    )
    assert response.status_code == 200, response.text


def test_a_link_that_needs_a_login_says_so_rather_than_saving_the_login_page(client):
    """A SharePoint link usually answers an unauthenticated fetch with a sign-in
    page, 200 and all. Storing that as an archive would be the same class of
    error as archiving a login page as the document it refused."""
    import connections_export.gui.routes.archives as routes

    monkeypatch_target = "<!doctype html><title>Sign in</title>"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(routes, "_fetch_url", lambda url, **_k: monkeypatch_target.encode())
    try:
        response = client.post(
            "/api/open-external",
            json={"path": "https://sharepoint.example/sites/x/exports/handed.zip"},
            headers=HOST,
        )
    finally:
        monkeypatch.undo()
    assert response.status_code == 400
    assert "zip" in response.json()["detail"].lower()


def test_a_url_that_is_not_a_zip_is_not_fetched_at_all(client):
    """Following an arbitrary URL is not what this is for."""
    response = client.post(
        "/api/open-external",
        json={"path": "https://connections.example/communities/service/html/communitystart"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert "zip" in response.json()["detail"].lower()
