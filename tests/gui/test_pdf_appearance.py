"""The console's PDF appearance controls, and the preview behind them.

Choosing a type size blind is close to guessing, so the panel renders a real
PDF of a fixed sample rather than an HTML approximation: the question is what
this will look like when printed, and only the printer answers that.

Two things have to hold structurally, and both are the kind that rot quietly:

  * the controls are built from what the server publishes, so a token added to
    the stylesheet appears without anyone editing the JavaScript
  * previewing is not saving -- a preview that changed a stored default would
    be a nasty surprise
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from connections_export.gui.app import make_app
from connections_export.pdf.html import style_token_names
from connections_export.pdf.sample import sample_interchange
from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()


def _client() -> TestClient:
    return TestClient(make_app(demo=True), headers={"host": "127.0.0.1"})


def test_settings_publishes_every_token_with_its_default():
    payload = _client().get("/api/settings").json()

    tokens = payload["pdf_style_tokens"]
    assert {t["name"] for t in tokens} == set(style_token_names())
    # A default is what makes the control usable: an empty box says nothing
    # about what you are changing from.
    assert all(t["default"] for t in tokens)


def test_the_controls_are_built_from_the_published_tokens():
    """Not from a list in the JavaScript -- that is the second place someone
    forgets when adding a token."""
    assert "renderStyleControls(s.pdf_style_tokens" in JS
    assert 'id="style-grid"' in HTML
    # And the console never writes a token's CSS name itself -- that is the
    # tell for a hard-coded list. Checking the `--pdf-` prefix rather than the
    # bare names, because a token called `text` collides with every ordinary
    # use of the word in JavaScript.
    assert "--pdf-" not in JS


def test_preview_renders_a_pdf_of_the_sample():
    response = _client().post("/api/pdf-style-preview", json={"pdf_style": {"body_size": "14pt"}})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


def test_preview_reports_an_unknown_token_rather_than_ignoring_it():
    response = _client().post("/api/pdf-style-preview", json={"pdf_style": {"bodysize": "14pt"}})

    assert response.status_code == 422
    assert "unknown PDF style token" in response.json()["error"]


def test_previewing_does_not_change_the_stored_settings():
    client = _client()
    before = client.get("/api/settings").json()["pdf_style"]

    client.post("/api/pdf-style-preview", json={"pdf_style": {"body_size": "22pt"}})

    assert client.get("/api/settings").json()["pdf_style"] == before


def test_the_sample_exercises_everything_the_tokens_touch():
    """A preview that does not show comments cannot show what the comment rule
    colour does. Each of these is styled by at least one token."""
    from connections_export.pdf.html import render_html

    html = render_html(sample_interchange(), blob_bytes=lambda _d: None)

    for marker in ("toc-title", "toc-group", "toc-d1", "hcl-tag", "comment-meta", "hcl-link-url"):
        assert marker in html, f"the sample does not exercise {marker}"


def test_preview_uses_the_renderer_the_export_will_use():
    """A preview rendered by the other engine showed a footer the export would
    not produce -- different content, not just different styling. That is the
    one thing a preview must not do."""
    import inspect

    # The PDF routes moved out of `make_app` into `gui/routes/pdf.py`;
    # this asserts on the wiring, so it follows the wiring.
    from connections_export.gui.routes import pdf as routes_pdf

    source = inspect.getsource(routes_pdf)
    assert "PAGED_AVAILABLE" in source
    assert "render_pdf_paged if PAGED_AVAILABLE else render_pdf" in source


def test_settings_publishes_the_header_footer_defaults():
    payload = _client().get("/api/settings").json()

    # Compared against the module that owns them rather than a copy: the list
    # grew from six to eleven, and a second hand-kept copy is how a field ends
    # up settable but invisible in the console.
    from connections_export.pdf.marks import DEFAULT_MARKS

    defaults = payload["pdf_mark_defaults"]
    assert set(defaults) == set(DEFAULT_MARKS)
    # The three that make a band shapeable rather than merely fillable.
    assert {"footer_center", "mark_font", "footer_rule"} <= set(defaults)


def test_the_mark_controls_come_from_the_published_defaults():
    assert "renderMarkControls(s.pdf_mark_defaults" in JS
    assert 'id="marks-grid"' in HTML


def test_an_empty_mark_field_means_print_nothing_there():
    """Unlike a style token, an empty value is meaningful here -- it switches
    that mark off -- so the console must send it rather than skipping it. It
    distinguishes 'emptied' from 'never touched' by the edit itself."""
    assert 'input.dataset.touched === "1"' in JS


def test_preview_reports_a_bad_placeholder():
    response = _client().post(
        "/api/pdf-style-preview", json={"pdf_marks": {"footer_left": "{nope}"}}
    )

    assert response.status_code == 422
    assert "unknown placeholder" in response.json()["error"]
