"""One Export dialog, opened from the Archives page and from the Reader.

Nobody should have to open the Reader just to get a PDF of an archive, and
every export -- one PDF, several, or a developer format -- starts in the same
place. A PDF goes through the one job runner (`startPdfExport`); a developer
format hands over to the guided export."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static"
JS = (STATIC / "console.js").read_text(encoding="utf-8")
CSS = (STATIC / "console.css").read_text(encoding="utf-8")
HTML = (STATIC / "console.html").read_text(encoding="utf-8")


def _body(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start : JS.index("\n  }\n", start)]


def test_the_dialog_markup_is_an_accessible_modal():
    dialog = HTML.split('id="export-dialog"', 1)[1].split('id="modal-scrim"', 1)[0]
    assert 'role="dialog"' in HTML.split('id="export-dialog"', 1)[1][:200]
    assert 'aria-modal="true"' in HTML.split('id="export-dialog"', 1)[1][:200]
    assert 'aria-labelledby="xd-title"' in HTML
    for part in (
        'name="xd-scope"',
        'value="choose"',
        'name="xd-format"',
        'value="obsidian"',
        'value="jekyll"',
        'value="hugo"',
        'name="xd-count"',
        'id="xd-split-by"',
        'id="xd-max-pages"',
        'id="xd-max-mb"',
        'id="xd-comments"',
        'id="xd-external"',
        'id="xd-attach"',
        'id="xd-bookmarks"',
        'id="xd-estimate"',
        'id="xd-where"',
        'id="xd-checks"',
        'id="xd-go"',
    ):
        assert part in dialog, part
    for label in (
        "Automatic (keep together what fits)",
        "One per community",
        "One per app",
        "One per container",
    ):
        assert label in dialog
    assert "Attach the pages' attachment files" in dialog
    assert ".xdlg" in CSS


def test_both_entry_points_open_the_one_dialog():
    assert "actions.appendChild(buildArchiveExportButton(a))" in JS
    card = _body("buildArchiveExportButton")
    assert "openExportDialog(" in card and "Export…" in card
    assert 'id="r-export-open"' in HTML
    assert "openExportDialog({ fromReader: true" in JS
    # No second set of export controls in the Reader's menu.
    menu = HTML.split('id="r-export-menu"', 1)[1].split("</details>", 1)[0]
    assert "r-pdf-scope" not in menu and "r-pdf-comments" not in menu


def test_the_archive_is_attached_before_the_dialog_plans():
    opener = _body("openExportDialog")
    assert "/api/open-archive" in opener and "name" in opener
    assert opener.index("/api/open-archive") < opener.index("xd.ready = true")


def test_the_dialog_plans_live_and_starts_the_shared_runner_with_split_params():
    assert "/api/pdf/plan" in _body("xdPlan")
    assert "setTimeout(xdPlan, delay)" in _body("xdSchedulePlan")  # debounced
    split = _body("xdSplitParams")
    for token in ('"split="', '"max_pages="', '"max_mb="'):
        assert token in split
    go = _body("xdGo")
    assert "startPdfExport(" in go and "xdSplitParams()" in go
    assert "attach_attachments=1" in go
    assert "/api/pdf/jobs" not in go
    assert "openDevExportForReader(format)" in go
    assert "plan.checks" in _body("xdRenderPlan")  # pre-flight checks block Export on errors


def test_choices_are_remembered_per_archive_as_a_convenience():
    assert "localStorage" in _body("xdLoadChoices") and "try" in _body("xdLoadChoices")
    assert "localStorage" in _body("xdSaveChoices") and "try" in _body("xdSaveChoices")


def test_the_dialog_is_keyboard_driven_and_returns_focus():
    assert '"Escape"' in _body("xdOnKey") and '"Tab"' in _body("xdOnKey")
    assert "opener.focus()" in _body("closeExportDialog")


def test_the_finished_state_offers_download_and_show_in_folder():
    done = _body("showPdfPanelDone")
    assert "/file" in done and "/reveal" in done
    assert "opened" in done and "Copy path" in done
    assert "files.length" in done
    assert "File " in _body("trackPdfJob") and "pr.files" in _body("trackPdfJob")


def test_reader_and_archives_page_share_one_job_runner():
    assert len(re.findall(r"async function startPdfExport\(", JS)) == 1
    assert JS.count('"/api/pdf/jobs"') == 1
    assert JS.count("/api/pdf/jobs/") >= 3  # poll, cancel, file/reveal
    assert "/api/pdf/jobs" not in _body("xdGo")


def test_a_busy_server_is_reported_from_the_server_detail():
    runner = _body("startPdfExport")
    # The 409 busy / 507 preflight message: shown as the server wrote it.
    assert "serverMessage(j" in runner
    assert "j.detail || j.error" in _body("serverMessage")
