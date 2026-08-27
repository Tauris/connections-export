""": `GET /api/settings` (a read-only snapshot of the
effective config + environment status) and the `#panel-settings` front
end that renders it, replacing the previous blank "Coming soon." stub.
Editing config is a later feature -- this is read-only, side-effect-free
status (mirrors the existing read-only endpoints).

Server test driven via `httpx.ASGITransport`, same pattern as
`test_app.py`/`test_pdf_endpoint.py`. Static/structural assertions over
the *served* `console.html`/`console.js` mirror `test_setup_screen.py`'s
approach: hold unconditionally, no dependency on a real server.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from tests.gui._served_assets import served_console_html, served_console_js


def _run(coro):
    return asyncio.run(coro)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


# --- GET /api/settings -------------------------------------------------


def test_settings_endpoint_in_demo_mode():
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/settings")

    response = _run(_do())

    assert response.status_code == 200
    body = response.json()
    assert body["demo"] is True
    # A demo server never resolves the host's real config.
    assert body["base_url"] is None
    assert body["auth_mode"] is None
    assert "archives_dir" in body
    assert isinstance(body["archives_dir"], str) and body["archives_dir"]
    assert "archives_count" in body
    assert isinstance(body["archives_count"], int)
    assert "pdf_browser_available" in body
    assert isinstance(body["pdf_browser_available"], bool)
    # detail is only ever populated when the browser is unavailable.
    if body["pdf_browser_available"]:
        assert body["pdf_browser_detail"] is None
    else:
        assert isinstance(body["pdf_browser_detail"], str) and body["pdf_browser_detail"]


def test_settings_endpoint_reflects_archives_count(tmp_path, monkeypatch):
    """`archives_count` reflects `list_archives(ARCHIVES_BASE)` -- point
    `ARCHIVES_BASE` at an empty temp dir and confirm it comes back 0
    rather than whatever the real machine's temp dir happens to hold."""

    empty_base = tmp_path / "archives"
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", empty_base)
    app = make_app(demo=True)

    async def _do():
        async with _client(app) as client:
            return await client.get("/api/settings")

    response = _run(_do())

    assert response.status_code == 200
    body = response.json()
    assert body["archives_dir"] == str(empty_base)
    assert body["archives_count"] == 0


# --- #panel-settings front end ------------------------------------------

HTML = served_console_html()
JS = served_console_js()


def _settings_panel_markup() -> str:
    start = HTML.index('id="panel-settings"')
    end = HTML.index("</section>", start)
    return HTML[start:end]


def test_panel_settings_is_no_longer_the_blank_stub():
    assert 'id="panel-settings"' in HTML
    assert "Coming soon." not in _settings_panel_markup()


def test_panel_settings_has_the_expected_status_fields():
    for element_id in (
        "settings-auth-mode",
        "settings-min-interval",
        "settings-author-filter",
        "settings-save",
        "set-pdf-status",
        "set-pdf-detail",
        "set-archives-dir",
        "set-archives-count",
    ):
        assert f'id="{element_id}"' in HTML


def test_settings_does_not_ask_for_a_deployment_address():
    """The address comes from the URL you drop. Offering it as a setting
    implied you had to configure a server before anything would work, which
    stopped being true once a URL became the unit of specification -- and the
    read-only lookups never read this field anyway; they fall back to
    connections-export.toml / the environment."""
    assert 'id="settings-base-url"' not in HTML


def test_panel_settings_does_not_duplicate_the_sidebar_theme_toggle():
    # There is exactly one #theme-toggle (the sidebar's); Settings may add
    # its own control but it must use a different id.
    assert HTML.count('id="theme-toggle"') == 1
    assert 'id="settings-theme-btn"' in HTML


def test_console_js_references_load_settings_and_its_endpoint():
    assert "loadSettings" in JS
    assert "/api/settings" in JS


def test_show_section_settings_triggers_load_settings():
    assert 'if (id === "settings"' in JS


def test_every_settings_card_commits_with_the_same_word():
    """Three cards, one act, one verb.

    "Save settings", "Save appearance" and "Use this" sat in one panel for
    what is the same thing -- and the first two called literally the same
    function. Three verbs read as three mechanisms. Each card's header already
    names what is being saved, so the button does not repeat it.
    """
    for stale in ("Save settings", "Save appearance", "Use this"):
        assert stale not in HTML, f"{stale!r} is back"
    for button_id in ("settings-save", "style-save", "set-archives-apply"):
        marker = f'id="{button_id}" type="button">Save</button>'
        assert marker in HTML, f"{button_id} does not read Save"
