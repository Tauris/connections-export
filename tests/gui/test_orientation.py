"""Orientation: the first-run welcome, the "what's open" header, the archive
summary, and the "?" links into the manual. Structure checks on the console's
own files (the behaviour was also driven in a real browser)."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static"
JS = (STATIC / "console.js").read_text(encoding="utf-8")
HTML = (STATIC / "console.html").read_text(encoding="utf-8")
CSS = (STATIC / "console.css").read_text(encoding="utf-8")
MANUAL = (Path(__file__).resolve().parents[2] / "docs" / "manual.md").read_text(encoding="utf-8")


def _body(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start : JS.index("\n  }\n", start)]


def test_the_welcome_offers_the_two_real_starts_and_a_labelled_demo():
    assert 'id="ov-welcome"' in HTML
    assert "Capture from Connections" in HTML
    assert "Open an archive someone gave me" in HTML
    assert "Try it with demo data" in HTML
    assert 'id="welcome-dismiss"' in HTML


def test_the_welcome_shows_only_when_nothing_exists_and_remembers_dismissal():
    body = _body("updateWelcome")
    assert "!woState.name" in body and "woArchives.length === 0" in body
    assert "welcomeDismissed()" in body
    dismissal = JS[JS.index("function welcomeDismissed") :][:200]
    assert "localStorage" in dismissal and "catch" in dismissal
    assert 'localStorage.setItem(WELCOME_KEY, "1")' in JS


def test_the_open_archive_is_named_in_a_persistent_header_with_a_switcher():
    assert 'id="whats-open"' in HTML and 'role="region"' in HTML
    assert HTML.index('id="whats-open"') < HTML.index('id="panel-overview"')
    render = _body("renderWhatsOpen")
    assert "No archive open" in render
    assert "Demo data" in render and "woState.demo" in render
    assert "Switch archive" in render and "aria-expanded" in render
    assert "position: sticky" in CSS[CSS.index(".whats-open") :][:120]


def test_the_header_follows_every_way_the_open_archive_changes():
    assert "refreshWhatsOpen()" in _body("refreshCurrentArchive")
    assert "refreshWhatsOpen()" in _body("loadShellIdentity")
    assert "refreshWhatsOpen()" in _body("showSection")
    assert "/api/current-archive" in _body("refreshWhatsOpen")
    assert "/api/archives" in _body("refreshWhatsOpen")


def test_demo_archives_are_flagged_on_the_card_and_in_the_summary():
    cards = JS[JS.index("function loadRecentArchives") :][:9000]
    assert 'badge.textContent = "Demo data' in cards
    assert 'id="sum-demo"' in HTML and "Everything in this archive is made up" in HTML


def test_the_summary_reads_contents_and_acts_on_the_open_archive():
    assert 'id="panel-summary"' in HTML
    assert '"summary"' in JS[JS.index("const SECTIONS") :][:200]
    load = JS[JS.index("async function loadSummary") :][:4500]
    assert '"/api/contents"' in load and "largest" in load
    for button in ("sum-read", "sum-export", "sum-extend"):
        assert f'id="{button}"' in HTML
    wire = _body("wireSummary")
    assert "openExportDialog(" in wire and "openArchiveForUpdate(" in wire
    assert "about " in JS[JS.index("function sumCost") :][:200] and "as PDF" in JS


def test_every_archive_card_has_details_and_the_header_links_to_the_summary():
    cards = JS[JS.index("function loadRecentArchives") :][:12000]
    assert '"Details"' in cards and '"summary"' in cards
    assert 'id="wo-details"' in JS


def test_help_links_open_the_manual_at_an_anchor_that_exists():
    anchors = set(re.findall(r'id="(man-[a-z-]+)"', MANUAL))
    links = set(re.findall(r'data-manual="(man-[a-z-]+)"', HTML))
    assert len(links) >= 4
    assert links <= anchors, links - anchors
    assert 'class="help-link xd-help"' in HTML
    assert "closeExportDialog()" in _body("openManualAt")


def test_the_manual_starts_with_task_first_help_that_links_to_real_anchors():
    anchors = set(re.findall(r'id="(man-[a-z-]+)"', MANUAL))
    howto = MANUAL[MANUAL.index('id="man-howto"') : MANUAL.index('id="man-intro"')]
    targets = set(re.findall(r"\(#(man-[a-z-]+)\)", howto))
    assert {"man-pdf-large", "man-open", "man-update", "man-pdf-memory", "man-pdf-where"} <= targets
    assert targets <= anchors, targets - anchors
    assert MANUAL.index('id="man-howto"') < MANUAL.index('id="man-intro"')


def test_archive_cards_wrap_at_phone_width():
    block = CSS[CSS.index("Archive cards at narrow widths") :]
    assert "max-width: 720px" in block and "flex-wrap: wrap" in block
