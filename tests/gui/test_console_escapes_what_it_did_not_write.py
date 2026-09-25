"""Every string the console did not write itself is escaped before it becomes markup.

The console builds most of its UI as HTML strings assigned to `innerHTML`. The
values interpolated into them come from three untrusted places: the derived
model (titles, links, URLs captured from the deployment), the live event stream
(request URLs, error text -- also deployment-controlled), and archives handed
over by someone else. On the console's origin, script can call every local API,
so one missed `escapeHtml` is the whole console.

The functions here are run for real under `node` (as `test_reader_pure_js.py`
does), with the static checks at the end as a backstop for the wiring.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CONSOLE_JS = (
    Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static" / "console.js"
)
JS = CONSOLE_JS.read_text(encoding="utf-8")
NODE = shutil.which("node") or shutil.which("nodejs")

needs_node = pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")


def _function(name: str) -> str:
    match = re.search(rf"\n  function {name}\(.*?\n  \}}\n", JS, re.S)
    assert match, f"could not locate {name} in console.js"
    return match.group(0)


def _const(name: str) -> str:
    match = re.search(rf"\n  const {name} = .*?;\n", JS)
    assert match, f"could not locate const {name} in console.js"
    return match.group(0)


PURE = re.search(
    r"READER-PURE-BEGIN =================\n(.*)// ================= READER-PURE-END", JS, re.S
).group(1)


def _run_node(script: str) -> object:
    result = subprocess.run(
        [NODE, "--input-type=commonjs", "-e", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


# --- the activity log ---------------------------------------------------------

_LOG = (
    _const("escapeHtml")
    + "function deploymentHost() { return 'deployment.example'; }\n"
    + _function("shortLabel")
    + _function("logMarkup")
)


@needs_node
def test_a_percent_encoded_url_path_is_shown_as_text_not_markup():
    """`shortLabel` decodes path segments so a label reads as the page name --
    which also turns `%3Cimg%20onerror...%3E` in a request URL back into a tag.
    The deployment chooses those URLs, so the row escapes what it shows."""
    out = _run_node(
        _LOG
        + """
        const url = "https://evil.example/wikis/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E";
        console.log(JSON.stringify(logMarkup(
          { t: "+1.0s", tag: "OK", tagCls: "ok", url: shortLabel(url), extra: "page" }, 1)));
        """
    )
    assert "<img" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out


@needs_node
def test_error_and_kind_text_in_a_log_row_are_escaped():
    """A failure row shows `kind · error` from the event, and an error message
    can quote whatever the deployment answered."""
    out = _run_node(
        _LOG
        + """
        console.log(JSON.stringify(logMarkup({
          t: "+1.0s", tag: "FAIL", tagCls: "fail",
          url: "<svg onload=alert(1)>", extra: "<b>page</b> · <img src=x onerror=alert(2)>",
        }, 1)));
        """
    )
    assert "<svg" not in out
    assert "<img" not in out
    assert "<b>page" not in out


@needs_node
def test_an_aggregated_log_row_still_escapes_its_key():
    out = _run_node(
        _LOG
        + """
        console.log(JSON.stringify(logMarkup({
          t: "+1.0s", tag: "OK", tagCls: "ok", url: "u", extra: "", key: "<img src=x onerror=1>",
        }, 3)));
        """
    )
    assert "<img" not in out
    assert "× 3" in out


# --- links that leave the console ----------------------------------------------


@needs_node
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "/api/delete-archive",
        "//evil.example/",
        "not a url",
        "",
        None,
    ],
)
def test_only_absolute_web_urls_become_outbound_links(url):
    """ "Open original" and the search preview's "open on HCL" point at the
    deployment. Escaping alone keeps `javascript:` intact -- it has no
    characters to escape -- so the scheme itself is checked."""
    out = _run_node(PURE + f"console.log(JSON.stringify(safeHttpUrl({json.dumps(url)})));")
    assert out is None


@needs_node
def test_real_deployment_urls_still_become_links():
    out = _run_node(
        PURE
        + """console.log(JSON.stringify([
          safeHttpUrl("https://connections.example/wikis/home/wiki/W/page/P"),
          safeHttpUrl("http://intranet.example.com/forums/html/topic?id=1"),
        ]));"""
    )
    assert out == [
        "https://connections.example/wikis/home/wiki/W/page/P",
        "http://intranet.example.com/forums/html/topic?id=1",
    ]


@needs_node
def test_the_open_original_link_drops_a_script_url_entirely():
    out = _run_node(
        PURE
        + _const("escapeHtml")
        + _function("originalPageLink")
        + """console.log(JSON.stringify([
          originalPageLink("javascript:alert(document.domain)"),
          originalPageLink("https://connections.example/p?a=1&b=2"),
        ]));"""
    )
    assert out[0] == ""
    assert 'href="https://connections.example/p?a=1&amp;b=2"' in out[1]


@needs_node
def test_escape_html_also_escapes_single_quotes():
    """So a value is safe in a single-quoted attribute too, not only in the
    double-quoted ones the console happens to use today."""
    out = _run_node(_const("escapeHtml") + 'console.log(JSON.stringify(escapeHtml("a\'b")));')
    assert out == "a&#39;b"


# --- wiring backstop -----------------------------------------------------------


def test_inline_comment_and_reply_bodies_go_through_the_sanitizer():
    """Comments and forum replies are the one place captured HTML is rendered
    outside the sandboxed iframe, so `inlineBody` must sanitize -- and must
    sanitize the image-resolved HTML, which is what actually gets rendered."""
    assert "sanitizeHtml(resolveBodyImages(" in _function("inlineBody")
    # The comment above it used to call archive content trusted, which is how
    # the unsanitized render got past review. It is not.
    match = re.search(r"// Render reply/comment body HTML inline.*?function inlineBody", JS, re.S)
    assert match
    assert "trusted" not in match.group(0).replace("untrusted", "")


def test_search_preview_links_are_scheme_checked():
    match = re.search(r"function showSearchPreview\(.*?\n  \}\n", JS, re.S)
    assert match
    assert "safeHttpUrl(r.url)" in match.group(0)


def test_event_values_in_alerts_and_tree_rows_are_escaped():
    """A warning's `kind` falls back to the raw event value when it is not one
    the console knows, and counts in tree rows and the drawer come from events
    too; none may reach markup unescaped."""
    assert "WARNING_TITLE[evt.kind] || escapeHtml(evt.kind)" in JS
    assert "escapeHtml(it.comments)" in _function("addTreeNode")
    assert 'm("Comments", escapeHtml(it.pageCounts.comments))' in JS
