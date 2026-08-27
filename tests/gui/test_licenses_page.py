"""A licences page in the console.

Most people never open a terminal. The obligation to show what ships and under
what terms does not depend on which surface they use, so the console carries
the same inventory the CLI prints -- and offers the texts themselves, because
a table of SPDX identifiers is an inventory, not compliance.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export import sbom
from connections_export.gui.app import make_app
from tests.gui._served_assets import served_console_html, served_console_js

HOST = {"Host": "127.0.0.1:8765"}


@pytest.fixture
def client():
    return TestClient(make_app(demo=True))


def test_the_console_has_a_licences_entry(client):
    html = served_console_html()
    assert 'data-section="licenses"' in html
    assert "licenses-body" in html


def test_the_api_lists_every_component_with_its_licence(client):
    payload = client.get("/api/licenses", headers=HOST).json()
    names = {row["name"] for row in payload["components"]}
    assert "httpx" in names
    assert "pdf.js" in names, "the vendored JavaScript is part of what ships"
    assert payload["notice"].strip()


def test_each_row_points_at_its_own_licence_text(client):
    payload = client.get("/api/licenses", headers=HOST).json()
    row = next(r for r in payload["components"] if r["name"] == "pdf.js")
    assert row["texts"]
    text = client.get("/api/licenses/text", params={"path": row["texts"][0]}, headers=HOST)
    assert text.status_code == 200
    assert "Apache" in text.text


def test_the_machine_readable_sbom_is_downloadable(client):
    response = client.get("/api/licenses/sbom", headers=HOST)
    assert response.status_code == 200
    assert response.json()["bomFormat"] == "CycloneDX"


def test_a_path_outside_the_bundle_is_refused(client):
    """The path comes from the page, but it arrives over HTTP like anything
    else. Serving arbitrary files because the caller asked nicely is how a
    read-only viewer becomes a file server."""
    for attempt in ("../../pyproject.toml", "/etc/passwd", "httpx/../../../LICENSE"):
        response = client.get("/api/licenses/text", params={"path": attempt}, headers=HOST)
        assert response.status_code == 404, attempt


def test_the_page_renders_the_rows_and_offers_the_texts():
    js = served_console_js()
    assert "/api/licenses" in js
    assert "loadLicenses" in js


def test_the_api_says_so_when_a_build_has_no_bundle(monkeypatch, client, tmp_path):
    monkeypatch.setattr(sbom, "bundled_dir", lambda: tmp_path / "nothing-here")
    response = client.get("/api/licenses", headers=HOST)
    assert response.status_code == 503
    assert "sbom" in response.json()["detail"].lower()


def test_the_page_says_what_is_not_shipped(client):
    """The table lists what is distributed. Chromium is not, and a reader who
    takes the table for an account of everything they run would be wrong about
    the one component big enough to matter."""
    payload = client.get("/api/licenses", headers=HOST).json()
    assert payload["not_shipped"]
    entry = payload["not_shipped"][0]
    assert "Chromium" in entry["name"]
    assert entry["why"] and entry["how"]


def test_the_console_renders_it(client):
    from tests.gui._served_assets import served_console_js

    assert "not_shipped" in served_console_js()


def test_everything_downloads_as_one_zip(client):
    """The SBOM and every licence text in one file -- what `--extract DIR`
    writes to disk, for someone who is in the console rather than a terminal."""
    import io
    import zipfile

    response = client.get("/api/licenses/bundle", headers=HOST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        names = bundle.namelist()
    assert sbom.SBOM_FILENAME in names
    assert sbom.NOTICE_FILENAME in names
    assert any(name.startswith("pdf.js/") for name in names)
    assert any(name.startswith("connections-export/") for name in names)


def test_every_licence_downloads_as_one_text_file(client):
    """One file with all of them, which is what an auditor usually asks for."""
    response = client.get("/api/licenses/all-texts", headers=HOST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "BSD 3-Clause" in body
    assert "pdf.js" in body
    assert body.count("=" * 78) >= 4  # one separator pair per component


def test_the_page_offers_both(client):
    from tests.gui._served_assets import served_console_html

    html = served_console_html()
    assert "/api/licenses/bundle" in html
    assert "/api/licenses/all-texts" in html


def test_the_cli_and_the_endpoint_render_the_same_text(client, tmp_path):
    """Two renderings of one thing is how they come to disagree -- and an
    auditor comparing the download with the terminal output would find it."""
    from connections_export import sbom as sbom_module

    served = client.get("/api/licenses/all-texts", headers=HOST).text
    rendered = sbom_module.render_all_texts(sbom_module.bundled_dir())
    assert served.strip() == rendered.strip()


def test_the_notice_is_not_offered_as_a_standalone_download(client):
    """On its own it names components and gives relative paths to their texts,
    which resolve inside the bundle and point at nothing outside it -- a file
    that looks like it holds the licences and holds references. It stays in
    the zip, where the paths mean something; the one-text-file download is
    what actually carries the licences."""
    from tests.gui._served_assets import served_console_html

    html = served_console_html()
    assert 'href="/api/licenses/text?path=NOTICE.txt"' not in html
    assert "/api/licenses/all-texts" in html


def test_the_all_texts_download_actually_contains_the_licences(client):
    body = client.get("/api/licenses/all-texts", headers=HOST).text
    # The full BSD text, not a pointer to it.
    assert "Redistribution and use in source and binary forms" in body
    assert "Apache License" in body  # pdf.js
    assert body.count("=" * 78) >= 40


def test_a_text_opened_from_a_row_suggests_a_sensible_filename(client):
    """The row links open in a tab; saving from there should not produce a
    file called "text"."""
    response = client.get("/api/licenses/text", params={"path": "pdf.js/LICENSE.txt"}, headers=HOST)
    disposition = response.headers.get("content-disposition", "")
    assert "filename=" in disposition
    assert "LICENSE" in disposition
