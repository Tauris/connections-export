"""The guided export's Hugo controls, over the served console assets.

The starter site is a Hugo choice, on by default in the dialog; the preview
is offered only when Hugo is installed. Whether it is -- its version, or that
it is not on PATH (with a link to Hugo's own installation guide and the hint
to restart a console started before Hugo was installed), or that it was found
but did not answer -- is said as soon as Hugo is chosen. What the dialog shows from the
server -- the preview's address, its folder, Hugo's version -- is escaped,
and only an address on 127.0.0.1 is ever opened or linked.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
NODE = shutil.which("node") or shutil.which("nodejs")


def _function(name: str) -> str:
    match = re.search(rf"\n  (?:async )?function {name}\(.*?\n  \}}\n", JS, re.S)
    assert match, f"could not locate {name} in console.js"
    return match.group(0)


def _node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def _hugo_script(call: str) -> str:
    return "\n".join(
        [
            re.search(r"\n  const escapeHtml = .*?;\n", JS).group(0),
            re.search(r"\n  const HUGO_INSTALL_URL = .*?;\n", JS).group(0),
            _function("devxLocalUrl"),
            _function("devxHugoMissingHtml"),
            _function("devxHugoStatusHtml"),
            _function("devxHugoHtml"),
            f"process.stdout.write(JSON.stringify({call}));",
        ]
    )


def test_the_starter_site_checkbox_is_on_by_default_and_names_hugo_server():
    dialog = HTML[HTML.index('id="devx-scrim"') :]
    assert '<input type="checkbox" id="devx-starter" checked />' in dialog
    assert "Add a starter site" in dialog and "<code>hugo server</code>" in dialog


def test_the_starter_site_is_sent_for_hugo_only():
    body = _function("devxBody")
    assert 'devxFormat() === "hugo"' in body and "starter_site: starter" in body
    starter = _function("devxRenderStarter")
    assert 'row.hidden = devxFormat() !== "hugo"' in starter
    wire = _function("wireDevExport")
    assert "devxRenderStarter()" in wire
    assert (
        '$("devx-starter")?.addEventListener("change", () => { devxRenderStarter(); '
        "devxInvalidate(); })"
    ) in wire


def test_the_dialog_has_a_preview_and_a_stop_control():
    assert 'id="devx-hugo-preview"' in HTML and ">Preview with Hugo<" in HTML
    assert 'id="devx-hugo-stop"' in HTML and ">Stop preview<" in HTML
    start = _function("devxHugoStart")
    assert '"/api/hugo-preview"' in start and 'method: "POST"' in start
    stop = _function("devxHugoStop")
    assert '"/api/hugo-preview/stop"' in stop


def test_the_preview_tab_gets_no_handle_on_the_console():
    """The preview is other people's pages; `opener` would let them navigate
    the console's own tab."""
    start = _function("devxHugoStart")
    assert "tab.opener = null" in start
    assert start.index("tab.opener = null") < start.index("tab.location.href = url")
    assert "devxLocalUrl(j.url)" in start


def test_hugos_error_is_shown_as_text():
    start = _function("devxHugoStart")
    assert "error.textContent = failure" in start
    assert "innerHTML = failure" not in start


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_without_hugo_the_dialog_links_to_its_installation_guide():
    html = _node(
        _hugo_script('devxHugoHtml({available: false, path: null}, "/x/run-hugo-content", null)')
    )

    assert "was not found on this computer's PATH" in html
    assert 'href="https://gohugo.io/installation/"' in html
    assert "third-party" in html
    assert 'rel="noopener noreferrer"' in html


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_choosing_hugo_says_at_once_whether_it_is_here():
    """The format step names Hugo's version, before anything is exported, so
    a missing Hugo is known before the export rather than after it."""
    line = _node(_hugo_script('devxHugoStatusHtml({available: true, version: "0.146.0"})'))

    assert line.startswith("Hugo 0.146.0 found")
    assert "after exporting with the starter site you can preview it here" in line


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_a_missing_hugo_gets_the_restart_hint_and_the_install_guide():
    """A running console keeps the PATH it started with: a Hugo installed
    since is only seen after a restart."""
    line = _node(_hugo_script("devxHugoStatusHtml({available: false, path: null})"))

    assert "was not found on this computer's PATH" in line
    assert "If you installed it after starting the console, restart the console." in line
    assert (
        '<a href="https://gohugo.io/installation/" target="_blank" '
        'rel="noopener noreferrer">how to install it</a>' in line
    )


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_a_hugo_that_did_not_answer_is_named_where_it_was_found():
    hostile = '<img src=x onerror="alert(1)">'
    lines = _node(
        _hugo_script(
            "[devxHugoStatusHtml({available: false, path: "
            + json.dumps(hostile)
            + ', error: "did not answer"}), devxHugoHtml({available: false, path: '
            + json.dumps(hostile)
            + ', error: "did not answer"}, "/p", null)]'
        )
    )

    for line in lines:
        assert "but did not answer <code>hugo version</code>" in line
        assert "<img" not in line
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in line
        assert "not found on this computer's PATH" not in line


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_the_status_line_says_it_is_looking_while_the_server_is_asked():
    assert _node(_hugo_script("devxHugoStatusHtml(undefined)")).startswith("Looking for Hugo")
    assert _node(_hugo_script("devxHugoStatusHtml(null)")) == ""


def test_hugo_is_asked_about_when_the_dialog_opens_and_when_hugo_is_chosen():
    wire = _function("wireDevExport")
    assert 'if (devxFormat() === "hugo") devxRefreshHugo();' in wire
    go = _function("devxGo")
    assert 'devxStep === 2 && devxFormat() === "hugo") devxRefreshHugo()' in go
    opening = _function("openDevExport")
    assert "devxRefreshHugo()" in opening
    refresh = _function("devxRefreshHugo")
    assert "ask !== devxHugo.asked" in refresh, "an older answer must not overwrite a newer one"


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_nothing_is_said_before_an_export_with_the_starter_site():
    assert _node(_hugo_script("devxHugoHtml({available: true}, null, null)")) == ""


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_what_the_server_says_is_escaped():
    hostile = '<img src=x onerror="alert(1)">'
    html = _node(
        _hugo_script(
            "[devxHugoHtml({available: true, version: "
            + json.dumps(hostile)
            + '}, "/p", null), devxHugoHtml(null, "/p", {url: "http://127.0.0.1:1313/", path: '
            + json.dumps(hostile)
            + "})]"
        )
    )

    for part in html:
        assert "<img" not in part
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in part


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)//",
        "http://evil.example/",
        "http://127.0.0.1.evil.example:80/",
        "http://127.0.0.1:1313/x",
        '"><script>alert(1)</script>',
    ],
)
def test_only_an_address_on_this_machine_is_linked(url):
    html = _node(_hugo_script("devxHugoHtml(null, null, {url: " + json.dumps(url) + "})"))

    assert html == ""
