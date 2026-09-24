"""`GET /api/pdf` carries the external-images choice to the renderer.

On by default: the third-party images in what is being exported are fetched
by the server (never by the browser) and handed over. `external_images=0`
hands over None, which leaves them out with their addresses as text.
"""

from __future__ import annotations

import asyncio

import httpx

import connections_export.pdf as pdf_pkg
import connections_export.pdf.external as external
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _get(app, url):
    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(url)

    return asyncio.run(_do())


def _app(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    seen: dict = {}

    def fake_render(model, blob_bytes, **kwargs):
        seen.update(kwargs)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(pdf_pkg, "CHROMIUM_AVAILABLE", True)
    monkeypatch.setattr(pdf_pkg, "PAGED_AVAILABLE", True)
    monkeypatch.setattr(pdf_pkg, "render_pdf_paged", fake_render)
    monkeypatch.setattr(
        external, "collect_external_image_urls", lambda _model: ["https://cdn.example.org/a.png"]
    )
    monkeypatch.setattr(external, "fetch_external_images", lambda urls: dict.fromkeys(urls))
    return make_app(archive_dir=archive_dir), seen


def test_external_images_are_fetched_by_default(tmp_path, monkeypatch):
    app, seen = _app(tmp_path, monkeypatch)

    response = _get(app, "/api/pdf")

    assert response.status_code == 200
    assert seen["external_images"] == {"https://cdn.example.org/a.png": None}


def test_external_images_can_be_left_out(tmp_path, monkeypatch):
    app, seen = _app(tmp_path, monkeypatch)

    response = _get(app, "/api/pdf?external_images=0")

    assert response.status_code == 200
    assert seen["external_images"] is None


def test_the_small_image_setting_reaches_the_renderer(tmp_path, monkeypatch):
    app, seen = _app(tmp_path, monkeypatch)
    app.state.editable_settings["pdf_small_image_px"] = 24

    assert _get(app, "/api/pdf").status_code == 200
    assert seen["small_image_px"] == 24
