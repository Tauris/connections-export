"""The guided export, over the served console assets.

The dialog has to help, not just offer a button: one step at a time, it names
the archives going in, says what each format is for (and that none of them is
ours), explains how page content is written, checks with the server what will
be written and where -- and only then writes. It is the one way to write these
formats: the Reader's developer-format buttons open it too. Every value it
shows that it did not write itself -- archive names, container titles, paths
-- is escaped.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
NODE = shutil.which("node") or shutil.which("nodejs")


def _function(name: str) -> str:
    match = re.search(rf"\n  (?:async )?function {name}\(.*?\n  \}}\n", JS, re.S)
    assert match, f"could not locate {name} in console.js"
    return match.group(0)


def _dialog() -> str:
    dialog = HTML[HTML.index('id="devx-scrim"') :]
    return dialog[: dialog.index('id="archive-open-overlay"')]


def _panes() -> dict[str, str]:
    return {
        match.group(1): match.group(0)
        for match in re.finditer(
            r'<section class="devx-pane" data-step="(\d)".*?</section>', _dialog(), re.S
        )
    }


def test_the_archives_toolbar_offers_an_export_of_the_selection():
    assert 'id="export-selected"' in HTML
    assert '"Export combined (" + count + ")…"' in JS


def test_the_dialog_walks_through_four_steps():
    """Archives, Format, Check, Export: an indicator naming all four, and one
    pane each, only the first showing when the page loads."""
    dialog = _dialog()
    labels = re.findall(r'<span class="devx-steplabel">([^<]+)</span>', dialog)
    assert labels == ["Archives", "Format", "Check", "Export"]
    openings = re.findall(r'<section class="devx-pane" data-step="(\d)"[^>]*?( hidden)?>', dialog)
    assert openings == [("1", ""), ("2", " hidden"), ("3", " hidden"), ("4", " hidden")]


def test_each_step_holds_its_own_controls():
    panes = _panes()
    assert 'id="devx-archives"' in panes["1"] and 'id="devx-add-select"' in panes["1"]
    for part in (
        'name="devx-format" value="hugo"',
        'name="devx-format" value="jekyll"',
        'name="devx-format" value="obsidian"',
        'id="devx-starter"',
        'id="devx-hugo-status"',
        'id="devx-html-mode"',
        'id="devx-mode-note"',
        "independent, third-party applications",
        "not an endorsement",
    ):
        assert part in panes["2"], part
    assert 'id="devx-check"' in panes["3"]
    assert 'id="devx-result"' in panes["4"] and 'id="devx-hugo-preview"' in panes["4"]


def test_the_footer_is_outside_the_scrolling_body():
    """Back / Next / Check / Export and Close come after the step body, not
    inside it, so they never scroll away; only the body scrolls, and the
    dialog is never taller than the window."""
    dialog = _dialog()
    footer = dialog.index('class="modal-actions devx-foot"')
    assert footer > dialog.rindex("</section>")
    for button in ("devx-close", "devx-back", "devx-next", "devx-check-btn", "devx-export-btn"):
        assert dialog.index(f'id="{button}"') > footer, button
    css = served_console_css()
    assert re.search(r"\.devx-body \{[^}]*overflow-y: auto", css)
    assert re.search(r"\.modal\.devx \{[^}]*height: min\(700px, calc\(100vh", css)


def test_the_dry_run_is_called_check_not_preview():
    """The word Preview belongs to Hugo; the dry run is a Check, so the two
    are never confused."""
    dialog = _dialog()
    assert 'id="devx-check-btn" type="button" hidden>Check</button>' in dialog
    assert 'id="devx-preview-btn"' not in dialog and 'id="devx-preview"' not in dialog
    assert ">Preview</button>" not in dialog
    assert ">Preview with Hugo</button>" in dialog
    assert "Check first" in dialog
    assert "function devxPreview(" not in JS


def test_preview_with_hugo_is_the_primary_action_of_the_result():
    assert 'class="btn primary" id="devx-hugo-preview"' in _dialog()


def test_nothing_is_written_before_a_check_of_the_same_choices():
    """Export starts disabled, and any change of archive, format or content
    mode puts it back to disabled until checked again."""
    assert 'id="devx-export-btn" type="button" disabled' in HTML
    invalidate = _function("devxInvalidate")
    assert "devxChecked = null" in invalidate and "devxRenderFooter()" in invalidate
    footer = _function("devxRenderFooter")
    assert 'show("devx-export-btn", devxStep === 3, !devxBusy && !!devxChecked)' in footer
    wire = _function("wireDevExport")
    assert wire.count("devxInvalidate()") >= 3
    check = _function("devxCheck")
    assert "devxBody(true)" in check and '"/api/ingest"' in check
    assert 'method: "POST"' in check
    exporting = _function("devxExport")
    assert "body = devxChecked" in exporting and 'method: "POST"' in exporting


def test_the_export_shows_elapsed_time_and_what_to_do_next():
    exporting = _function("devxExport")
    assert "setInterval(tick, 1000)" in exporting
    assert "devxGo(4)" in exporting
    assert "DEVX_NEXT_STEPS[j.format]" in exporting
    for fmt in ("hugo:", "jekyll:", "obsidian:"):
        assert fmt in JS[JS.index("const DEVX_NEXT_STEPS") :][:800]


def test_the_readers_developer_format_buttons_open_the_dialog():
    """One way to write these formats: the Reader's buttons open the guided
    export with their format chosen, and nothing in the Reader writes an
    export on its own."""
    for fmt in ("obsidian", "jekyll", "hugo"):
        assert f'id="r-export-{fmt}" data-devx-format="{fmt}"' in HTML
    assert 'id="r-dev-html-mode"' not in HTML and 'id="r-dev-export-result"' not in HTML
    assert "exportDevFormat" not in JS
    wire = _function("wireDevExport")
    assert "openDevExportForReader(button.dataset.devxFormat)" in wire
    reader = _function("openDevExportForReader")
    assert '"/api/current-archive"' in reader and '"/api/ingest"' not in reader
    assert "current.archive_name" in reader
    assert "openSource: { label: current.name }" in reader and "step: 2" in reader


def test_the_open_archive_is_exported_without_naming_archives():
    """An archive opened from outside the archives folder has no name there:
    the request leaves `archives` out, which the server takes as the open
    archive."""
    body = _function("devxBody")
    assert "devxOpenSource ? undefined : devxNames.slice()" in body


@pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")
def test_the_check_escapes_archive_names_and_titles():
    hostile = '<img src=x onerror="alert(1)">'
    script = "\n".join(
        [
            re.search(r"\n  const escapeHtml = .*?;\n", JS).group(0),
            "let archivesOnPage = " + json.dumps([{"name": "a", "display_name": hostile}]) + ";",
            _function("devxArchive"),
            _function("devxCombineHtml"),
            "const combine = "
            + json.dumps(
                {
                    "archives": ["a", "b"],
                    "duplicates_total": 1,
                    "links_resolved": 0,
                    "containers": [
                        {
                            "kind": "wiki",
                            "title": hostile,
                            "winner": "a",
                            "archives": ["a", hostile],
                        }
                    ],
                    "collisions": [],
                }
            )
            + ";",
            "process.stdout.write(JSON.stringify(devxCombineHtml(combine)));",
        ]
    )
    result = subprocess.run(
        [NODE, "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    html = json.loads(result.stdout)

    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_the_starter_site_offers_its_front_page_layout_right_under_it():
    """A small choice, list by default, directly beneath the checkbox it
    belongs to -- and shown only while that is ticked."""
    pane = _panes()["2"]
    checkbox = pane.index('id="devx-starter"')
    select = pane.index('<select id="devx-starter-layout">')
    assert checkbox < select < pane.index('id="devx-hugo-status"')
    between = pane[checkbox:select]
    assert "Front page as" in between and "<label" in between
    options = re.findall(r'<option value="(\w+)"( selected)?>([^<]+)</option>', pane[select:])[:2]
    assert options == [("list", " selected", "List (default)"), ("cards", "", "Cards")]
    render = _function("devxRenderStarter")
    assert 'layout.hidden = !($("devx-starter") && $("devx-starter").checked)' in render
    wire = _function("wireDevExport")
    assert '$("devx-starter-layout")?.addEventListener("change", () => devxInvalidate())' in wire
    assert "devxRenderStarter(); devxInvalidate();" in wire


def test_the_layout_is_sent_only_with_the_starter_site():
    """The server refuses a layout without the starter site, so the dialog
    sends none then."""
    body = _function("devxBody")
    assert 'starter ? (($("devx-starter-layout")' in body and ": null" in body
    assert "starter_layout: layout" in body
