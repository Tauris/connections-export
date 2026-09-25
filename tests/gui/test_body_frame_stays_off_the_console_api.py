"""A captured body cannot make requests to the console's own API.

Page, post and topic bodies render in `<iframe sandbox="allow-same-origin"
srcdoc=...>`. Scripts never run there, but the frame is same-origin with the
console, so a RELATIVE URL in the body resolves against the console: an
`<img src="/api/resolve-user?...">`, a CSS `url(/api/...)`, an `@import`, a
`srcset` -- each is a GET to the console's API, chosen by whoever wrote the
page. The reader closes that in two layers:

1. `confineToArchive` removes every URL in the body that resolves to the
   console (or to anything else on this machine), except `/api/blob/<hash>`,
   which is how the reader itself shows captured assets;
2. the frame carries its own CSP, which admits the console only at
   `/api/blob/` -- the backstop for any CSS spelling the first layer misses.

The string-level pieces run under `node`; the parser-level piece and the whole
thing, in a real console with a real frame, run in Chromium.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

CONSOLE_JS = (
    Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static" / "console.js"
)
JS = CONSOLE_JS.read_text(encoding="utf-8")
PURE = re.search(
    r"READER-PURE-BEGIN =================\n(.*)// ================= READER-PURE-END", JS, re.S
).group(1)
NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
needs_chromium = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

ORIGIN = "http://127.0.0.1:8123"
BLOB = "/api/blob/" + "a" * 64


def _node(expression: str) -> object:
    result = subprocess.run(
        [
            NODE,
            "--input-type=commonjs",
            "-e",
            PURE + f"\nconsole.log(JSON.stringify({expression}));",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


# --- which URLs count as "the console" ------------------------------------------


@needs_node
@pytest.mark.parametrize(
    "url",
    [
        "/api/resolve-user?email=x",
        "api/delete-archive",
        "//127.0.0.1:8123/api/x",
        "http://127.0.0.1:8123/api/x",
        "http://localhost:8000/api/x",  # another port on this machine
        "http://127.0.0.1:9/",
        "http://[::1]:9000/",
        "http://LOCALHOST:1/",
        " /api/x",
        "/api/blob/" + "a" * 64 + "?extra=1",
        "/api/blob/not-a-hash",
    ],
)
def test_urls_that_would_reach_the_console_or_this_machine(url):
    assert _node(f"reachesConsole({json.dumps(url)}, {json.dumps(ORIGIN)})") is True


@needs_node
@pytest.mark.parametrize(
    "url",
    [
        BLOB,
        f"{ORIGIN}{BLOB}",
        "https://connections.example/files/img.png",
        "http://intranet.example.com/x.png",
        "data:image/png;base64,AAAA",
        "mailto:a@example.com",
        "#section-2",
        "",
    ],
)
def test_urls_that_leave_the_console_alone(url):
    assert _node(f"reachesConsole({json.dumps(url)}, {json.dumps(ORIGIN)})") is False


# --- CSS and srcset -------------------------------------------------------------


@needs_node
def test_css_urls_and_imports_to_the_console_are_neutralised():
    css = (
        "@import '/api/version'; @import url(\"/api/health\"); "
        "a{background:url(/api/current-user)} b{background:url( '/api/x' )} "
        "c{background:url(\\2f api\\2f model)} "  # CSS escapes, decoded as CSS would
        'd{background-image:image-set("/api/y" 1x)} '
        f"e{{background:url({BLOB})}} f{{background:url(https://cdn.example/bg.png)}}"
    )
    out = _node(f"confineCss({json.dumps(css)}, {json.dumps(ORIGIN)})")
    assert "/api/version" not in out
    assert "/api/health" not in out
    assert "/api/current-user" not in out
    assert "/api/x" not in out
    assert "api\\2f model" not in out
    assert "/api/y" not in out
    assert f"url({BLOB})" in out
    assert "url(https://cdn.example/bg.png)" in out


@needs_node
def test_srcset_keeps_only_the_candidates_that_leave_the_console():
    srcset = (
        f"/api/health 1x, https://cdn.example/a.png 2x, {BLOB} 3x, data:image/png;base64,A,B 4x"
    )
    out = _node(f"confineSrcset({json.dumps(srcset)}, {json.dumps(ORIGIN)})")
    assert out == f"https://cdn.example/a.png 2x, {BLOB} 3x, data:image/png;base64,A,B 4x"


# --- the frame CSP --------------------------------------------------------------


@needs_node
def test_the_frame_csp_admits_the_console_only_at_its_blobs():
    """A host-source with a path matches that path prefix only, so the console
    is reachable at `/api/blob/` and nowhere else. `http:` must NOT appear as a
    scheme-source: the console is http, and it would readmit the whole API."""
    csp = _node(f"bodyFrameCsp({json.dumps(ORIGIN)}, null)")
    directives = {d.split()[0]: d.split()[1:] for d in (p.strip() for p in csp.split(";")) if d}
    assert directives["default-src"] == ["'none'"]
    for name in ("img-src", "media-src", "style-src", "font-src", "frame-src"):
        sources = directives[name]
        assert "http:" not in sources, (name, sources)
        assert "*" not in sources, (name, sources)
        assert ORIGIN not in sources, (name, sources)  # never the bare console origin
    assert f"{ORIGIN}/api/blob/" in directives["img-src"]
    assert "https:" in directives["img-src"]


@needs_node
def test_an_http_deployment_is_admitted_by_origin_not_by_scheme():
    csp = _node(f"bodyFrameCsp({json.dumps(ORIGIN)}, 'http://connections.example.com/base')")
    assert "http://connections.example.com" in csp
    assert "http:" not in csp.split()


@needs_node
def test_a_local_deployment_is_never_admitted():
    csp = _node(f"bodyFrameCsp({json.dumps(ORIGIN)}, 'http://localhost:8123')")
    assert "localhost" not in csp


@needs_node
def test_an_https_console_drops_the_https_scheme_source_too():
    csp = _node("bodyFrameCsp('https://127.0.0.1:8443', null)")
    assert "https:" not in csp.split()
    assert "https://127.0.0.1:8443/api/blob/" in csp


@needs_node
def test_the_csp_is_the_first_thing_in_the_frame_document():
    out = _node(f"sandboxedBodyMarkup('<p>hi</p>', {{ consoleOrigin: {json.dumps(ORIGIN)} }})")
    srcdoc = out.split('srcdoc="', 1)[1]
    assert srcdoc.startswith(
        "&lt;!doctype html&gt;&lt;meta http-equiv=&quot;Content-Security-Policy&quot;"
    )
    assert "allow-scripts" not in out


# --- in a real browser ------------------------------------------------------------

SCRIPT = PURE


@needs_chromium
def test_confine_to_archive_strips_console_urls_from_every_attribute():
    from playwright.sync_api import sync_playwright

    body = (
        '<img src="/api/resolve-user?x=1" srcset="/api/health 1x">'
        f'<img src="{BLOB}"><img src="https://cdn.example/a.png">'
        '<a href="/wikis/home/page/P2">in-export</a><a href="#top">anchor</a>'
        '<video poster="/api/licenses"><source src="//127.0.0.1:8123/api/x"></video>'
        '<table background="/api/version"><tr><td>x</td></tr></table>'
        '<link rel="stylesheet" href="/api/model">'
        '<object data="/api/model"></object>'
        '<svg><image href="/api/x"/><a xlink:href="/api/y"><text>t</text></a>'
        '<image href="x"><set attributeName="href" to="/api/z"/></image></svg>'
        '<meta http-equiv="refresh" content="0;url=/api/delete-archive">'
        '<p style="background:url(/api/current-user)">styled</p>'
        "<style>body{background:url('/api/health')}</style>"
    )
    with sync_playwright() as play:
        browser = launch_browser(play)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><body></body>")
            page.add_script_tag(content=SCRIPT)
            out = page.evaluate(
                "([html, origin]) => confineToArchive(html, origin)", [body, ORIGIN]
            )
        finally:
            browser.close()
    for leaked in (
        "/api/resolve-user", "/api/health", "/api/licenses", "127.0.0.1", "/api/version",
        "/api/current-user", "refresh", "/api/z",
    ):  # fmt: skip
        assert leaked not in out, (leaked, out)
    assert ' href="/api/model"' not in out and ' data="/api/model"' not in out
    assert f'src="{BLOB}"' in out
    assert 'src="https://cdn.example/a.png"' in out
    assert 'href="#top"' in out
    # Kept out of the browser's reach, but still there for the reader to route.
    assert 'data-confined-href="/wikis/home/page/P2"' in out
    assert ' href="/wikis/home/page/P2"' not in out
    assert "styled" in out


@pytest.fixture
def console_with_hostile_body(tmp_path):
    """The real console serving a wiki page whose body tries every way of
    reaching the API. Each attempt carries `frame-probe` in its URL, which
    nothing the console itself requests ever does."""
    import uvicorn

    from connections_export.archive.blobs import write_blob
    from connections_export.archive.store import Archive
    from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange, ResolvedAsset
    from connections_export.gui.app import make_app
    from connections_export.interchange.package import write_package

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    body = (
        '<p>body text</p><img src="img/ok.png">'
        '<img src="/api/health?frame-probe=img">'
        '<img srcset="/api/health?frame-probe=srcset 1x">'
        f'<img src="http://127.0.0.1:{port}/api/health?frame-probe=absolute">'
        '<div style="background:url(/api/health?frame-probe=css)">x</div>'
        "<style>@import '/api/health?frame-probe=import';"
        "p{background:\\75 rl(/api/health?frame-probe=escaped-function)}</style>"
        '<link rel="stylesheet" href="/api/health?frame-probe=link">'
        '<video poster="/api/health?frame-probe=poster"></video>'
    )
    asset = ResolvedAsset(
        original_href="img/ok.png",
        resolved_url="https://fake/img/ok.png",
        blob_hash=f"sha256:{digest}",
        present=True,
        scope="same",
    )
    page = DerivedPage(id="p1", title="Page 1", content_html=body, assets=[asset])
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    dest = tmp_path / "package"
    write_package(
        Interchange(base_url="https://fake", wikis=[wiki]),
        archive,
        dest,
        generated_at="2026-07-20T12:00:00Z",
    )
    config = uvicorn.Config(
        make_app(demo=False, package_dir=dest),
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.skip("the console did not start")
    try:
        yield f"http://127.0.0.1:{port}/", digest
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@needs_chromium
def test_the_real_reader_frame_requests_blobs_and_nothing_else_from_the_console(
    console_with_hostile_body,
):
    from playwright.sync_api import sync_playwright

    url, digest = console_with_hostile_body
    with sync_playwright() as play:
        browser = launch_browser(play)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            # Responses, not requests: Chromium reports a request the CSP then
            # blocks, and only one that got an answer actually reached the API.
            requested: list[str] = []
            page.on("response", lambda response: requested.append(response.url))
            page.goto(url, wait_until="load")
            page.click('[data-section="reader"]')
            page.wait_for_selector("#r-article iframe.body-frame", timeout=20_000)
            page.wait_for_timeout(1500)
            frame_text = (
                page.frame_locator("#r-article iframe.body-frame").locator("body").inner_text()
            )
            print(
                "DEBUGHTML",
                page.frame_locator("#r-article iframe.body-frame").locator("html").inner_html(),
            )
        finally:
            browser.close()
    assert "body text" in frame_text
    probes = [u for u in requested if "frame-probe" in u]
    assert probes == [], probes
    # The captured image still loads -- confinement must not cost the reader its assets.
    assert any(u.endswith(f"/api/blob/{digest}") for u in requested), requested
