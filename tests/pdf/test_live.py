"""Regression checks for live-PDF layout normalization."""

from connections_export.pdf.live import (
    _HIDE_CHROME_CSS,
    _LAYOUT_RESET_JS,
    _PAGINATE_LIVE_DOM_JS,
    MAX_UI_PAGES,
)


def test_live_pdf_resets_legacy_width_constraints_and_wraps_content():
    assert "min-width: 0 !important" in _HIDE_CHROME_CSS
    assert "max-width: 100% !important" in _HIDE_CHROME_CSS
    assert "box-sizing: border-box !important" in _HIDE_CHROME_CSS
    assert "white-space: normal !important" in _HIDE_CHROME_CSS
    assert "overflow-wrap: anywhere !important" in _HIDE_CHROME_CSS
    assert "table-layout: fixed !important" in _HIDE_CHROME_CSS
    assert ".lotusMeta.lotusLeft" in _HIDE_CHROME_CSS
    assert ".lotusMeta.lotusLeft .vcard" in _HIDE_CHROME_CSS
    assert "white-space: nowrap !important" in _HIDE_CHROME_CSS
    assert "position: static !important" in _HIDE_CHROME_CSS
    assert "el.style.setProperty('min-width','0','important')" in _LAYOUT_RESET_JS


def test_live_pdf_walks_and_retains_ui_pages():
    assert MAX_UI_PAGES == 100
    assert 'a[rel="next"]' in _PAGINATE_LIVE_DOM_JS
    assert "holder.appendChild(snapshot)" in _PAGINATE_LIVE_DOM_JS
    assert "visited.has(href)" in _PAGINATE_LIVE_DOM_JS
