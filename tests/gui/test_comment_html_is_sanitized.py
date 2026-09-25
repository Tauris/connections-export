"""Comment and reply bodies are rendered inline -- so they are sanitized first.

A page, post or topic body renders inside a sandboxed iframe with no
`allow-scripts`. Comments and forum replies do not: one iframe per reply in a
long thread is impractical, so their HTML goes into the console's own document.
That document is the console's origin, and script there can call every local
API -- start a crawl, delete archives. Comment HTML was written by any user of
the deployment, and a whole archive may have been handed over by someone else,
so it is untrusted, and an allowlist sanitizer (`sanitizeHtml`) sits between
it and `innerHTML`.

The sanitizer parses with the browser's own HTML parser, so it is tested in a
real browser: string matching cannot tell you what Chromium's parser makes of
`<svg><script>` or an unquoted `onerror`. The last test goes further and opens
a hostile archive in the real console, because a sanitizer that is correct but
not called protects nothing.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

CONSOLE_JS = (
    Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static" / "console.js"
)


def _block(name: str) -> str:
    text = CONSOLE_JS.read_text(encoding="utf-8")
    match = re.search(
        rf"{name}-BEGIN =================\n(.*)// ================= {name}-END", text, re.S
    )
    assert match, f"{name}-BEGIN/END markers not found in console.js"
    return match.group(1)


# The sanitizer reuses the pure block's URL helpers, so both are loaded.
SCRIPT = _block("READER-PURE") + "\n" + _block("HTML-SANITIZER")
CONSOLE_ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def browser():
    # One Playwright for the module: the sync API refuses to start a second
    # while the first is still running.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = launch_browser(play)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def browser_page(browser):
    page = browser.new_page()
    # Served from an http origin, as the console is, so "resolves to the
    # console" means what it means in production. Nothing listens there.
    page.route(
        CONSOLE_ORIGIN + "/",
        lambda route: route.fulfill(
            content_type="text/html", body="<!doctype html><body><div id='sink'></div></body>"
        ),
    )
    page.goto(CONSOLE_ORIGIN + "/")
    page.add_script_tag(content=SCRIPT)
    yield page
    page.close()


def _sanitize(page, html: str) -> str:
    return page.evaluate("(html) => sanitizeHtml(html)", html)


def _render(page, html: str) -> dict:
    """Sanitize, then do exactly what the console does with the result --
    assign it to `innerHTML` in a live document -- and report what happened."""
    return page.evaluate(
        """async (html) => {
          window.__pwned = 0;
          const sink = document.getElementById('sink');
          sink.innerHTML = sanitizeHtml(html);
          // Give an onerror / iframe load / meta refresh a moment to fire.
          await new Promise((r) => setTimeout(r, 150));
          const attrs = [];
          sink.querySelectorAll('*').forEach((el) => {
            for (const a of el.attributes) attrs.push(el.localName + '@' + a.name + '=' + a.value);
          });
          return {
            pwned: window.__pwned,
            tags: Array.from(sink.querySelectorAll('*')).map((el) => el.localName),
            attrs,
            text: sink.textContent,
          };
        }""",
        html,
    )


HOSTILE = [
    '<img src=x onerror="window.__pwned=1">',
    "<img src=x onerror=window.__pwned=1>",
    "<svg><script>window.__pwned=1</script></svg>",
    "<svg onload=window.__pwned=1></svg>",
    '<iframe srcdoc="<script>parent.__pwned=1</script>"></iframe>',
    '<object data="javascript:window.__pwned=1"></object>',
    '<embed src="javascript:window.__pwned=1">',
    '<math><mtext><table><mglyph><style><img src=x onerror="window.__pwned=1">',
    '<form><button formaction="javascript:window.__pwned=1">x</button></form>',
    '<details open ontoggle="window.__pwned=1">x</details>',
    '<p style="background:url(javascript:window.__pwned=1)" onclick="window.__pwned=1">x</p>',
    '<noscript><p title="</noscript><img src=x onerror=window.__pwned=1>">',
    '<a href="x" title="&quot; onmouseover=&quot;window.__pwned=1">x</a>',
    '<img src="x\\" onerror=\\"window.__pwned=1">',
    "<template><img src=x onerror=window.__pwned=1></template>",
    '<meta http-equiv="refresh" content="0;url=javascript:window.__pwned=1">',
    '<base href="javascript:window.__pwned=1//">',
    '<link rel="stylesheet" href="https://evil.example/x.css">',
    '<video><source onerror="window.__pwned=1"></video>',
    '<input autofocus onfocus="window.__pwned=1">',
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_a_hostile_comment_neither_runs_nor_leaves_a_handler_behind(browser_page, payload):
    result = _render(browser_page, payload)
    assert result["pwned"] == 0, (payload, result)
    for attr in result["attrs"]:
        name = attr.split("@", 1)[1].split("=", 1)[0]
        assert not name.startswith("on"), (payload, attr)
        assert name not in {"style", "srcdoc", "formaction", "action", "data"}, (payload, attr)
    forbidden = {
        "script", "style", "iframe", "object", "embed", "svg", "math", "template", "form",
        "meta", "link", "base", "noscript", "button", "input", "video", "source", "details",
    }  # fmt: skip
    assert not (set(result["tags"]) & forbidden), (payload, result["tags"])


def test_the_contents_of_script_and_style_are_dropped_not_shown_as_text(browser_page):
    """Unwrapping a `<script>` would print its source into the thread -- not
    dangerous, but a comment full of JavaScript is not what anyone wrote."""
    out = _sanitize(browser_page, "<p>kept</p><script>var secret=1</script><style>p{}</style>")
    assert out == "<p>kept</p>"


@pytest.mark.parametrize(
    "href",
    [
        "javascript:window.__pwned=1",
        "JaVaScRiPt:window.__pwned=1",
        " javascript:window.__pwned=1",
        "java\tscript:window.__pwned=1",
        "java\nscript:window.__pwned=1",
        "&#106;avascript:window.__pwned=1",
        "data:text/html,<script>window.__pwned=1</script>",
        "vbscript:msgbox(1)",
        "\x01javascript:window.__pwned=1",
    ],
)
def test_a_link_with_a_script_scheme_loses_its_href(browser_page, href):
    """Browsers strip tabs, newlines and leading control characters from a URL
    before reading its scheme, and the HTML parser decodes entities first, so
    the check has to see the URL the browser would -- a naive
    `startsWith("javascript:")` is beaten by a tab or `&#106;`."""
    result = _render(browser_page, f'<a href="{href}">x</a>')
    assert not any(a.startswith("a@href=") for a in result["attrs"]), (href, result["attrs"])
    assert result["text"] == "x"


def test_ordinary_formatting_survives(browser_page):
    """Allowlisting is only acceptable if what people actually write in a
    comment -- emphasis, lists, quotes, code, tables -- still reads as written."""
    html = (
        "<p>Hello <b>bold</b> <strong>s</strong> <i>i</i> <em>e</em> <u>u</u> <s>s</s>"
        " H<sub>2</sub>O x<sup>2</sup><br></p>"
        "<blockquote>quoted</blockquote><pre><code>code()</code></pre>"
        "<ul><li>one</li></ul><ol><li>two</li></ol><h3>Heading</h3><hr>"
        '<table><thead><tr><th colspan="2">h</th></tr></thead>'
        '<tbody><tr><td rowspan="1">c</td></tr></tbody></table>'
        "<div><span>span</span></div>"
    )
    out = _sanitize(browser_page, html)
    for tag in (
        "<b>", "<strong>", "<i>", "<em>", "<u>", "<s>", "<sub>", "<sup>", "<br>",
        "<blockquote>", "<pre>", "<code>", "<ul>", "<ol>", "<li>", "<h3>", "<hr>",
        "<table>", "<thead>", "<tbody>", "<tr>", '<th colspan="2">', '<td rowspan="1">',
        "<div>", "<span>",
    ):  # fmt: skip
        assert tag in out, (tag, out)


def test_unknown_elements_are_unwrapped_so_their_text_is_kept(browser_page):
    """A `<font>` or a Connections-specific wrapper is harmless text around
    content someone wrote; dropping it would silently lose words."""
    out = _sanitize(
        browser_page, '<font color="red">red words</font> <x-widget>and more</x-widget>'
    )
    assert out == "red words and more"


def test_links_keep_safe_targets_and_always_open_away_from_the_console(browser_page):
    """A link opens in a new tab with no opener: a comment link must never be
    able to navigate, or script, the console tab that opened it."""
    out = _sanitize(
        browser_page,
        '<a href="https://example.com/x" title="t" target="_self" onclick="x()">a</a>'
        '<a href="mailto:someone@example.com">m</a>',
    )
    assert (
        '<a href="https://example.com/x" title="t" rel="noopener noreferrer" target="_blank">a</a>'
        in out
    )
    assert 'href="mailto:someone@example.com"' in out
    assert "onclick" not in out
    assert "_self" not in out


def test_images_keep_archive_blobs_and_web_images_but_nothing_else(browser_page):
    blob = "/api/blob/" + "a" * 64
    out = _sanitize(
        browser_page,
        f'<img src="{blob}" alt="diagram" width="40" height="20">'
        '<img src="https://example.com/i.png">'
        '<img src="data:image/png;base64,iVBORw0KGgo=">'
        '<img src="javascript:alert(1)" alt="js">'
        '<img src="data:text/html,<script>alert(1)</script>" alt="html">'
        '<img src="/api/delete-archive" alt="api">'
        '<img src="x" width="100%;background:red" alt="w">',
    )
    assert f'<img src="{blob}" alt="diagram" width="40" height="20">' in out
    assert '<img src="https://example.com/i.png">' in out
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in out
    assert "javascript:" not in out
    assert "data:text/html" not in out
    assert "/api/delete-archive" not in out
    assert "background" not in out
    # The image stays (its alt text is content); only the unsafe src goes.
    assert '<img alt="js">' in out


def test_empty_or_missing_input_is_empty_output(browser_page):
    assert _sanitize(browser_page, "") == ""
    assert browser_page.evaluate("() => sanitizeHtml(null)") == ""


# --- the real console, with a hostile archive ---------------------------------

PAYLOAD = (
    "<p>Nice page!</p>"
    '<img src=x onerror="window.__pwned=(window.__pwned||0)+1">'
    '<iframe srcdoc="<script>parent.__pwned=(parent.__pwned||0)+1</script>"></iframe>'
    '<a href="javascript:window.__pwned=1">click me</a>'
)


@pytest.fixture
def hostile_console(tmp_path):
    """The console serving a package whose wiki comment AND forum reply carry
    script -- the two inline render paths."""
    import uvicorn

    from connections_export.archive.store import Archive
    from connections_export.derive.model import (
        DerivedComment,
        DerivedForum,
        DerivedForumReply,
        DerivedForumTopic,
        DerivedPage,
        DerivedWiki,
        Interchange,
    )
    from connections_export.gui.app import make_app
    from connections_export.interchange.package import write_package

    page = DerivedPage(
        id="p1",
        title="Page 1",
        content_html="<p>body</p>",
        comments=[DerivedComment(id="c1", author="Mallory", content_html=PAYLOAD)],
    )
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    topic = DerivedForumTopic(
        id="t1",
        title="Topic 1",
        content_html="<p>topic</p>",
        reply_ids=["r1"],
        replies={"r1": DerivedForumReply(id="r1", author="Mallory", content_html=PAYLOAD)},
    )
    forum = DerivedForum(id="f1", title="Forum 1", topic_ids=["t1"], topics={"t1": topic})
    archive = Archive.open(tmp_path / "archive")
    dest = tmp_path / "package"
    write_package(
        Interchange(base_url="https://fake", wikis=[wiki], forums=[forum]),
        archive,
        dest,
        generated_at="2026-07-20T12:00:00Z",
    )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
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
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_hostile_archive_opened_in_the_reader_runs_nothing(browser, hostile_console):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(hostile_console, wait_until="load")
        page.click('[data-section="reader"]')

        # The wiki page's comment thread.
        page.wait_for_selector("#r-article .cmt-item .inline-body", timeout=20_000)
        page.wait_for_timeout(400)
        comment = page.eval_on_selector("#r-article .cmt-item .inline-body", "el => el.innerHTML")
        assert "Nice page!" in comment
        assert "onerror" not in comment
        assert "<iframe" not in comment
        assert "javascript:" not in comment

        # The forum topic's reply thread, the other inline path.
        # Its nav entry sits in a collapsed section; the click is what matters.
        page.eval_on_selector('[data-reader-key="forum:f1:t1"]', "el => el.click()")
        page.wait_for_selector("#r-article h1:has-text('Topic 1')", timeout=20_000)
        page.wait_for_timeout(400)
        reply = page.eval_on_selector("#r-article .cmt-item .inline-body", "el => el.innerHTML")
        assert "Nice page!" in reply
        assert "onerror" not in reply

        assert page.evaluate("() => window.__pwned || 0") == 0
        assert dialogs == []
    finally:
        page.close()


def test_nothing_in_a_comment_may_address_the_console_or_this_machine(browser_page):
    """A relative URL in a comment resolves against the console itself, so an
    image or a link there is a request to the console's API chosen by the
    comment's author. Only a captured blob -- which the reader itself points
    comment images at -- may name the console."""
    blob = "/api/blob/" + "b" * 64
    out = _sanitize(
        browser_page,
        '<img src="/api/resolve-user?email=x" alt="rel">'
        f'<img src="{CONSOLE_ORIGIN}/api/health" alt="abs">'
        '<img src="http://localhost:9999/x" alt="other-port">'
        f'<img src="{CONSOLE_ORIGIN}{blob}" alt="blob-abs">'
        f'<img src="{blob}" alt="blob">'
        '<a href="/api/delete-archive">rel</a><a href="//127.0.0.1:1/x">proto</a>'
        f'<a href="{blob}">blob-link</a>',
    )
    assert "/api/resolve-user" not in out
    assert "/api/health" not in out
    assert "localhost" not in out
    assert "/api/delete-archive" not in out
    assert "127.0.0.1:1" not in out
    assert f'<img src="{CONSOLE_ORIGIN}{blob}" alt="blob-abs">' in out
    assert f'<img src="{blob}" alt="blob">' in out
    assert f'href="{blob}"' in out
