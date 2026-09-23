"""`POST /api/ingest` writes the open archive out as an Obsidian vault or a
Jekyll site, into a folder beside the archive, and reports where.

This is the console's front door to the same two exporters the CLI has always
had (`connections-export ingest --format …`). Driven via `httpx.ASGITransport`
with a localhost Host (the security guard), same as the PDF endpoint test.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _run(coro):
    return asyncio.run(coro)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _post(app, body):
    async def _do():
        async with _client(app) as client:
            return await client.post("/api/ingest", json=body)

    return _run(_do())


def test_ingest_writes_an_obsidian_vault_beside_the_archive(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, {"format": "obsidian"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["format"] == "obsidian"
    vault = archive_dir.parent / "archive-obsidian-vault"
    assert body["path"] == str(vault)
    assert (vault / "README.md").is_file(), "the vault should have its index note"
    assert body["summary"]


def test_ingest_writes_a_jekyll_site_beside_the_archive(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, {"format": "jekyll"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    site = archive_dir.parent / "archive-jekyll-site"
    assert body["path"] == str(site)
    assert site.is_dir()


def test_an_unknown_format_is_rejected(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, {"format": "pdf"})

    assert response.status_code == 422
    assert "obsidian" in response.json()["error"]


def test_ingest_needs_an_open_archive(tmp_path):
    # No archive and not demo: the model source holds nothing.
    app = make_app()

    response = _post(app, {"format": "obsidian"})

    assert response.status_code == 422
    assert "archive" in response.json()["error"].lower()


def test_an_exporter_failure_is_surfaced_to_the_client(tmp_path, monkeypatch):
    """A failure inside the exporter (e.g. a missing dependency) must reach the
    console with its message, not fall through to a bare 500 with the reason
    only in the terminal."""
    import connections_export.ingest as ingest_mod

    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    def boom(*_a, **_k):
        raise ImportError("the Obsidian ingester needs `markdownify`")

    monkeypatch.setattr(ingest_mod, "from_source_for_format", boom)

    response = _post(app, {"format": "obsidian"})

    assert response.status_code == 500
    assert "markdownify" in response.json()["error"]
