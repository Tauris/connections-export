""": `GET /api/pdf` renders the served model to a PDF
download. `pdf_renderer` is injected so no real Chromium is launched
(the browser step is covered by tests/pdf/test_browser.py). Driven via
`httpx.ASGITransport` with a localhost Host (the security guard).
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _run(coro):
    return asyncio.run(coro)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def test_pdf_endpoint_returns_a_pdf_download(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)

    seen = {}

    def fake_renderer(model, blob_bytes):
        seen["wikis"] = [w.title for w in model.wikis]
        return b"%PDF-1.4 fake"

    app = make_app(demo=True, archive_dir=archive_dir, pdf_renderer=fake_renderer)

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/pdf")

    response = _run(_do())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    assert seen["wikis"], "the endpoint should pass the real derived model to the renderer"


def test_pdf_endpoint_reports_renderer_failure(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)

    def failing_renderer(model, blob_bytes, **kwargs):
        raise TimeoutError("renderer timed out")

    app = make_app(demo=True, archive_dir=archive_dir, pdf_renderer=failing_renderer)

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/pdf")

    response = _run(_do())
    assert response.status_code == 500
    assert response.json()["status"] == "pdf_error"
    assert "did not finish loading or paginating" in response.json()["detail"]
    reports = list(archive_dir.glob("pdf-failure-*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "# PDF export failure" in report
    assert "## Traceback" in report


def test_pdf_endpoint_scopes_to_a_single_unit(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)

    seen = {}

    def fake_renderer(model, blob_bytes):
        seen["wiki_ids"] = [w.id for w in model.wikis]
        seen["counts"] = (len(model.wikis), len(model.blogs), len(model.forums))
        return b"%PDF-1.4 fake"

    app = make_app(demo=True, archive_dir=archive_dir, pdf_renderer=fake_renderer)

    async def _do():
        async with _client(app) as client:
            unscoped = await client.get("/api/pdf")
            first_wiki = seen["wiki_ids"][0]
            scoped = await client.get(f"/api/pdf?scope=item&kind=wiki&id={first_wiki}")
            scoped_ids = seen["wiki_ids"]
            missing = await client.get("/api/pdf?scope=item&kind=topic&id=does-not-exist")
            return unscoped, scoped, first_wiki, scoped_ids, missing

    unscoped, scoped, first_wiki, scoped_ids, missing = _run(_do())
    assert unscoped.status_code == 200
    # Scoping to one wiki hands the renderer exactly that wiki, nothing else.
    assert scoped.status_code == 200
    assert scoped_ids == [first_wiki]
    assert seen["counts"] == (1, 0, 0)
    # An id that matches nothing is an honest 422, not a blank PDF.
    assert missing.status_code == 422
    assert missing.json()["status"] == "empty_scope"


def test_pdf_endpoint_tile_scope_is_a_bare_single_page(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)

    seen = {}

    def fake_renderer(model, blob_bytes):
        seen["model"] = model
        return b"%PDF-1.4 fake"

    app = make_app(demo=True, archive_dir=archive_dir, pdf_renderer=fake_renderer)

    async def _do():
        async with _client(app) as client:
            await client.get("/api/pdf")  # unscoped, to discover a page id
            wiki = seen["model"].wikis[0]
            page_id = wiki.root_page_ids[0]
            child_ids = list(wiki.pages[page_id].child_ids)
            tile = await client.get(f"/api/pdf?scope=tile&kind=page&id={page_id}&chrome=0")
            return tile, page_id, child_ids, seen["model"]

    tile, page_id, child_ids, model = _run(_do())
    assert tile.status_code == 200
    # The tile is exactly that one page: no ancestors, no children carried over.
    (wiki,) = model.wikis
    assert set(wiki.pages) == {page_id}
    assert wiki.pages[page_id].child_ids == []


def test_pdf_endpoint_tile_scope_is_available_from_a_full_archive(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)

    seen = {}

    def fake_renderer(model, blob_bytes, **kwargs):
        seen["model"] = model
        seen["kwargs"] = kwargs
        return b"%PDF-1.4 fake"

    app = make_app(demo=True, archive_dir=archive_dir, pdf_renderer=fake_renderer)

    async def _do():
        async with _client(app) as client:
            await client.get("/api/pdf")
            page_id = seen["model"].wikis[0].root_page_ids[0]
            return await client.get(f"/api/pdf?scope=tile&kind=page&id={page_id}&chrome=0")

    response = _run(_do())
    assert response.status_code == 200
    assert set(seen["model"].wikis[0].pages) == {seen["model"].wikis[0].root_page_ids[0]}


def test_vendor_pdfjs_is_served_and_locked_down(tmp_path):
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            lib = await client.get("/vendor/pdf.min.mjs")
            worker = await client.get("/vendor/pdf.worker.min.mjs")
            bad = await client.get("/vendor/anything-else.js")
            return lib, worker, bad

    lib, worker, bad = _run(_do())
    assert lib.status_code == 200 and "javascript" in lib.headers["content-type"]
    assert worker.status_code == 200
    assert "immutable" in lib.headers.get("cache-control", "")
    # Only the allow-listed names resolve -- no arbitrary static serving.
    assert bad.status_code == 404


def test_pdf_endpoint_is_pending_without_a_model():
    app = make_app(demo=True, pdf_renderer=lambda *_: b"%PDF-nope")

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/pdf")

    response = _run(_do())
    assert response.status_code == 503
    assert response.json()["status"] == "pending"


def test_pdf_endpoint_pending_explains_missing_server_model(tmp_path):
    app = make_app(demo=False, pdf_renderer=lambda *_: b"%PDF-nope")

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/pdf")

    response = _run(_do())
    assert response.status_code == 503
    assert "no ready model" in response.json()["detail"]


def test_reader_has_an_export_pdf_button():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            html_response = await client.get("/")
            js_response = await client.get("/console.js")
            return html_response, js_response

    html_response, js_response = _run(_do())
    html = html_response.text
    # The console's script lives in console.js, not in console.html --
    # the button's markup (DOM) stays on the served HTML, but its wiring
    # to /api/pdf (JS) now lives in console.js.
    js = js_response.text
    assert 'id="r-pdf"' in html
    assert 'const pdfUrl = "/api/pdf"' in js


def test_pdf_include_exports_exactly_the_chosen_components(tmp_path, monkeypatch):
    """Export offered the whole archive or the one item open in the reader
    and nothing between."""
    import asyncio

    import httpx

    from connections_export.gui import make_app

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    async def go(params):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get("/api/pdf", params=params)

    # No model yet: the endpoint must say so rather than render an empty PDF.
    pending = asyncio.run(go({"include": ["forum:nope"]}))
    assert pending.status_code in (422, 503)


def test_pdf_include_with_nothing_matching_refuses_rather_than_exporting_all(tmp_path, monkeypatch):
    """Failing open here would export the entire archive when the user asked
    for one thing that no longer exists."""
    import asyncio

    import httpx

    from connections_export.derive.scope import select_interchange
    from connections_export.gui import make_app
    from connections_export.gui.demo import run_demo

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    model = run_demo((lambda _e: None), seed=0, delay=0, archive_dir=tmp_path / "a").interchange

    narrowed = select_interchange(model, [("forum", "does-not-exist")])
    assert not (narrowed.wikis or narrowed.blogs or narrowed.forums)

    app = make_app(demo=True, demo_delay=0)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get("/api/pdf", params={"include": ["forum:does-not-exist"]})

    assert asyncio.run(go()).status_code in (422, 503)
