"""The activity tray, finish notices, recent exports, Basic/Advanced settings
and plain-worded errors. Structure checks on the console's own files: the
behaviour was also driven in a real browser (reload mid-export re-attaches)."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static"
JS = (STATIC / "console.js").read_text(encoding="utf-8")
HTML = (STATIC / "console.html").read_text(encoding="utf-8")
CSS = (STATIC / "console.css").read_text(encoding="utf-8")


def _body(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start : JS.index("\n  }\n", start)]


def test_the_tray_is_always_in_the_chrome_and_labelled():
    assert 'id="sb-activity"' in HTML and 'aria-expanded="false"' in HTML
    assert 'id="activity-pop"' in HTML and 'id="activity-live"' in HTML
    assert "Nothing running" in HTML
    assert ".sb-activity" in CSS and ".sr-only" in CSS


def test_the_tray_reads_the_activity_endpoint_and_reattaches():
    tick = _body("trayTick")
    assert '"/api/activity"' in tick
    assert "trackPdfJob(running[0].id" in tick and "pdfTrack.active" in tick
    boot = JS[JS.index("function wireTray") :]
    assert "visibilitychange" in boot and "trayTick({ attach: true })" in boot


def test_one_poll_loop_whichever_way_a_job_arrives():
    assert len(re.findall(r"async function trackPdfJob\(", JS)) == 1
    assert JS.count('fetch("/api/pdf/jobs/" + jobId)') == 1
    assert "pdfTrack.active" in _body("startPdfExport")
    assert "trackPdfJob(jobId" in _body("startPdfExport")


def test_time_left_is_only_said_when_it_can_be_worked_out():
    eta = _body("pdfEtaText")
    assert "pages_total" in eta and "min left" in eta and "less than a minute left" in eta
    assert 'return ""' in eta and "span < 8" in eta


def test_progress_is_announced_by_stage_not_every_second():
    track = _body("trackPdfJob")
    assert "announce(" in track and "lastStage" in track
    panel = _body("ensurePdfProgressPanel")
    assert "aria-live" not in panel
    live = HTML[HTML.index('id="activity-live"') - 80 : HTML.index('id="activity-live"') + 120]
    assert 'aria-live="polite"' in live


def test_finish_notice_changes_the_title_and_asks_permission_only_on_click():
    note = _body("finishedNotice")
    assert "document.hidden" in note and "✓ PDF ready" in note and "Notification" in note
    assert "requestPermission" in _body("offerNotifications")
    assert JS.count("Notification.requestPermission") == 1
    assert "localStorage.setItem(NOTIFY_KEY" in JS and "Notify me when long exports finish" in JS


def test_recent_exports_lists_with_download_and_reveal():
    assert 'id="recent-exports"' in HTML
    load = _body("loadRecentExports")
    assert (
        "/api/exports/recent" in load
        and "/api/exports/file" in load
        and "/api/exports/reveal" in load
    )
    assert "Download again" in load and "Show in folder" in load and "Nothing exported yet" in load
    assert "yesterday" in _body("whenRelative")


def test_settings_split_basic_and_advanced_keeping_every_setting():
    adv = HTML.split('id="settings-advanced"', 1)[1].split("</details>", 1)[0]
    for field in (
        "settings-auth-mode",
        "settings-pdf-timeout",
        "settings-pdf-part-size",
        "settings-pdf-small-image",
    ):
        assert f'id="{field}"' in adv
    assert "<details" in HTML.split('id="settings-advanced"')[0][-40:]
    assert 'placeholder="Automatic (from free memory)"' in adv
    assert "0 lays out the whole document at once" in adv
    for field in ("settings-min-interval", "settings-author-filter", "settings-archive-only"):
        assert f'id="{field}"' not in adv
    body = _body("settingsBody")
    assert 'raw === ""' in body and "return null" in body


def test_errors_offer_copy_details_with_the_raw_request():
    assert "data-copy-details" in _body("notify")
    block = _body("supportDetails")
    for part in ("Version", "Request: ", "Status: ", "Server said: ", "When: "):
        assert part in block
    assert "window.fetch = " in JS and "lastProblem" in JS


def test_one_pdf_says_what_file_it_makes():
    assert "This makes one file: " in JS and "plan.single_name" in JS


def test_no_message_blames_hcl_serve_or_the_browser_console():
    assert "hcl-serve is running" not in JS.replace("// hcl-serve", "")
    assert "see the browser console" not in JS


def test_a_failed_pdf_keeps_its_technical_text_apart_from_the_sentence():
    from connections_export.gui.routes import pdf

    source = (Path(pdf.__file__)).read_text(encoding="utf-8")
    assert "Technical details:" in source
