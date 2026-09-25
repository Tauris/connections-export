"""`POST /api/ingest` writes the open archive out as an Obsidian vault, a
Jekyll site or Hugo content, into a folder beside the archive, and reports
where -- and how much of the page content stayed HTML.

This is the console's front door to the same exporters the CLI has
(`connections-export ingest --format …`). Driven via `httpx.ASGITransport`
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


def test_ingest_writes_hugo_content_beside_the_archive(tmp_path):
    """Hugo sits beside the other two: content only, in a folder named for it,
    with how much of the page content stayed HTML in the summary."""
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, {"format": "hugo"})

    assert response.status_code == 200
    body = response.json()
    content = archive_dir.parent / "archive-hugo-content"
    assert body["path"] == str(content)
    # The demo holds two communities, so the export is community first.
    assert (content / "content" / "platform-engineering" / "wikis" / "_index.md").is_file()
    assert body["html_mode"] == "mixed"
    assert "kept as HTML" in body["summary"]
    assert body["markdown_blocks"] > 0


def test_ingest_takes_the_html_mode_or_the_formats_default(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    assert _post(app, {"format": "obsidian"}).json()["html_mode"] == "markdown"
    assert _post(app, {"format": "jekyll", "html_mode": None}).json()["html_mode"] == "mixed"
    chosen = _post(app, {"format": "jekyll", "html_mode": "html"}).json()
    assert chosen["html_mode"] == "html"
    assert chosen["markdown_blocks"] == 0 and chosen["html_blocks"] > 0


def test_the_open_archive_is_checked_then_exported_with_every_option(tmp_path):
    """What the Reader's buttons do for an archive opened from outside the
    archives folder: no `archives` in the request, a check first, then the
    export -- with the starter site and the page-content choice, exactly as
    for an archive picked by name."""
    archive_dir = tmp_path / "dropped" / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)
    body = {"format": "hugo", "html_mode": "html", "starter_site": True}

    folder = archive_dir.parent / "archive-hugo-content"

    checked = _post(app, {**body, "dry_run": True}).json()

    assert checked["ok"] and checked["dry_run"] and checked["path"] == str(folder)
    assert checked["counts"]["pages"] > 0 and checked["starter_site"] is True
    assert not folder.exists(), "the check writes nothing"

    written = _post(app, body).json()

    assert written["ok"] and written["path"] == str(folder)
    assert written["html_mode"] == "html" and written["starter_site"] is True
    assert (folder / "hugo.toml").is_file() and (folder / "layouts").is_dir()


def test_an_unknown_html_mode_is_rejected(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    response = _post(app, {"format": "hugo", "html_mode": "<script>"})

    assert response.status_code == 422
    assert "mixed" in response.json()["error"]
    assert not (archive_dir.parent / "archive-hugo-content").exists()


def test_the_console_offers_hugo_and_the_page_content_choice():
    """The developer-formats section has the Hugo button, which opens the
    guided export; the dialog has the four content choices plus the format's
    default, and sends the choice with the export."""
    from pathlib import Path

    static = Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static"
    html = (static / "console.html").read_text(encoding="utf-8")
    script = (static / "console.js").read_text(encoding="utf-8")

    assert 'id="r-export-hugo"' in html and "Export Hugo content" in html
    assert 'id="devx-html-mode"' in html
    for value, label in (
        ("", "Default for the format"),
        ("markdown", "Markdown"),
        ("mixed", "Markdown with HTML where needed"),
        ("html", "HTML"),
        ("raw", "HTML as captured (not cleaned)"),
    ):
        assert f'<option value="{value}"' in html and f">{label}</option>" in html
    assert "Obsidian, Jekyll and Hugo are independent, third-party applications" in html
    assert "html_mode: mode || null" in script
    assert 'data-devx-format="hugo"' in html and "openDevExportForReader" in script


def test_the_starter_sites_front_page_layout_is_written_and_reported(tmp_path):
    """List unless asked otherwise, in the check and the export alike."""
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0, app_filter="wiki")
    app = make_app(demo=True, archive_dir=archive_dir)
    folder = archive_dir.parent / "archive-hugo-content"

    body = {"format": "hugo", "starter_site": True}
    assert _post(app, {**body, "dry_run": True}).json()["starter_layout"] == "list"
    written = _post(app, {**body, "starter_layout": "cards"}).json()

    assert written["ok"] and written["starter_layout"] == "cards"
    assert 'homeLayout = "cards"' in (folder / "hugo.toml").read_text(encoding="utf-8")


def test_an_unknown_layout_or_one_without_the_starter_site_is_rejected(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0, app_filter="wiki")
    app = make_app(demo=True, archive_dir=archive_dir)

    for body in (
        {"format": "hugo", "starter_site": True, "starter_layout": "grid"},
        {"format": "hugo", "starter_site": True, "starter_layout": "<script>"},
        {"format": "hugo", "starter_layout": "cards"},
        {"format": "jekyll", "starter_layout": "list"},
    ):
        response = _post(app, body)
        assert response.status_code == 422, body
        assert "list, cards" in response.json()["error"]
    assert not (archive_dir.parent / "archive-hugo-content").exists()
    assert not (archive_dir.parent / "archive-jekyll-site").exists()
