""": `html_to_pdf` prefers a **system** browser
(Microsoft Edge, then Chrome) over Playwright's bundled Chromium, so
`pip install` alone is usually enough — no `playwright install chromium`.
The launch *ordering* is pure and tested here; the actual launch stays
browser-gated (`test_browser.py`).
"""

from __future__ import annotations

from connections_export.pdf.browser import (
    ENV_BROWSER_CHANNEL,
    ENV_BROWSER_PATH,
    _launch_candidates,
    _pdf_kwargs,
)


def test_default_order_prefers_edge_then_chrome_then_bundled():
    order = list(_launch_candidates({}))
    assert order == [{"channel": "msedge"}, {"channel": "chrome"}, {}]


def test_explicit_channel_env_wins_outright():
    order = list(_launch_candidates({ENV_BROWSER_CHANNEL: "chrome"}))
    assert order == [{"channel": "chrome"}]


def test_explicit_executable_path_env_wins_over_everything():
    order = list(
        _launch_candidates({ENV_BROWSER_PATH: "/opt/edge/msedge", ENV_BROWSER_CHANNEL: "chrome"})
    )
    assert order == [{"executable_path": "/opt/edge/msedge"}]


# --- footer/header/margin wiring (fix #31) -------------------------------
#
# The real page-number footer is Chromium's `page.pdf(display_header_footer=
# True, footer_template=...)` mechanism (CSS `@page { @bottom-right {
# content: string(...) } }` named strings are not implemented by Chromium's
# print engine -- verified empty). `_pdf_kwargs` is pulled out of
# `html_to_pdf` precisely so this wiring is checkable without launching a
# browser at all.


def test_pdf_kwargs_enables_header_footer_with_page_number_and_title():
    kwargs = _pdf_kwargs(outline=False)
    assert kwargs["display_header_footer"] is True
    assert "pageNumber" in kwargs["footer_template"]
    assert "totalPages" in kwargs["footer_template"]
    assert 'class="title"' in kwargs["footer_template"]


def test_pdf_kwargs_header_template_is_empty():
    kwargs = _pdf_kwargs(outline=False)
    assert kwargs["header_template"] == "<span></span>"


def test_pdf_kwargs_sets_a_nonzero_margin_on_all_sides():
    kwargs = _pdf_kwargs(outline=False)
    margin = kwargs["margin"]
    for side in ("top", "bottom", "left", "right"):
        assert margin[side] not in (None, "", "0", "0mm")


def test_pdf_kwargs_passes_outline_through():
    assert _pdf_kwargs(outline=True)["outline"] is True
    assert _pdf_kwargs(outline=False)["outline"] is False


def test_pdf_kwargs_keeps_print_background_and_a4_format():
    kwargs = _pdf_kwargs(outline=False)
    assert kwargs["print_background"] is True
    assert kwargs["format"] == "A4"
