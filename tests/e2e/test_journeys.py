"""The console's key journeys, walked like a person would walk them."""

from __future__ import annotations

import re
import threading
import zipfile

import pytest
from playwright.sync_api import expect

from connections_export.pdf.parts import _advice
from connections_export.pdf.progress import PdfOutOfMemoryError, PdfProgress

from .conftest import DEMO_NAME

pytestmark = pytest.mark.e2e

SLOW = 20_000


def _archives(page, url):
    page.goto(url, wait_until="load")
    page.locator('[data-section="archives"]').click()
    expect(page.locator("#panel-archives")).to_be_visible()


def _open_export_dialog(page, url):
    _archives(page, url)
    page.get_by_role("button", name=re.compile(r"^Export ")).first.click()
    expect(page.get_by_role("dialog", name=re.compile("Export"))).to_be_visible()
    expect(page.locator("#xd-estimate")).not_to_have_text("", timeout=SLOW)


def _export(page):
    expect(page.locator("#xd-go")).to_be_enabled(timeout=SLOW)
    page.locator("#xd-go").click()


def _pdf_folder(folder):
    return folder / f"{DEMO_NAME}-pdf"


def test_first_start_welcomes_and_leads_to_the_demo(start_console, new_page):
    url, _ = start_console(with_archive=False)
    page = new_page
    page.goto(url, wait_until="load")
    welcome = page.locator("#ov-welcome")
    expect(welcome).to_be_visible()
    for choice in ("welcome-capture", "welcome-open", "welcome-demo"):
        expect(page.locator(f"#{choice}")).to_be_visible()
    page.locator("#welcome-demo").click()
    expect(page.locator("#panel-select")).to_be_visible()
    expect(page.locator("#demo-chips")).to_be_visible()
    page.shoot("1-first-start")


def test_open_an_archive_from_the_list(start_console, new_page):
    url, _ = start_console()
    page = new_page
    _archives(page, url)
    row = page.locator("#archive-list")
    expect(row).to_contain_text(DEMO_NAME)
    expect(row).to_contain_text("Demo data")
    page.get_by_role("button", name=re.compile("^Details of ")).click()
    expect(page.locator("#panel-summary")).to_be_visible()
    expect(page.locator("#sum-body")).to_contain_text("What is inside", timeout=SLOW)
    expect(page.locator("#sum-body li").first).to_be_visible()
    expect(page.locator("#sum-demo")).to_be_visible()
    expect(page.locator("#wo-line")).to_contain_text("Demo data")
    page.shoot("2a-details")
    page.locator("#sum-read").click()
    expect(page.locator("#panel-reader")).to_be_visible()
    expect(page.locator("#r-article")).not_to_be_empty(timeout=SLOW)
    expect(page.locator("#wo-line")).to_contain_text("Demo data")
    expect(page.locator("#wo-name")).to_be_visible()
    page.shoot("2b-reader")


def test_export_one_pdf(start_console, new_page):
    url, folder = start_console()
    page = new_page
    _open_export_dialog(page, url)
    expect(page.locator("#xd-estimate")).to_contain_text(re.compile(r"\d"))
    expect(page.locator("#xd-where")).to_contain_text(".pdf")
    _export(page)
    panel = page.locator("#pdf-progress")
    expect(panel).to_be_visible()
    expect(page.locator("#pp-done")).to_be_visible(timeout=120_000)
    expect(page.locator("#pp-download")).to_be_visible()
    expect(page.locator("#pp-reveal")).to_be_visible()
    files = list(_pdf_folder(folder).glob("*.pdf"))
    assert len(files) == 1 and files[0].read_bytes().startswith(b"%PDF")
    with page.expect_download() as dl:
        page.locator("#pp-download").click()
    got = dl.value.path()
    with open(got, "rb") as fh:
        assert fh.read(4) == b"%PDF"
    page.shoot("3-one-pdf")


def test_export_several_pdfs_and_download_the_zip(start_console, new_page):
    url, folder = start_console()
    page = new_page
    _open_export_dialog(page, url)
    page.get_by_role("radio", name=re.compile("Several PDFs")).check()
    page.locator("#xd-split-by").select_option("app")
    expect(page.locator("#xd-file-list > li").first).to_be_visible(timeout=SLOW)
    proposed = page.locator("#xd-file-list > li").count()
    assert proposed >= 2
    _export(page)
    expect(page.locator("#pp-done")).to_be_visible(timeout=180_000)
    expect(page.locator("#pp-done-files > li")).to_have_count(proposed)
    sets = [d for d in _pdf_folder(folder).iterdir() if d.is_dir()]
    zips = list(_pdf_folder(folder).glob("*.zip"))
    assert len(sets) == 1 and len(zips) == 1
    assert len(list(sets[0].glob("*.pdf"))) == proposed
    with page.expect_download() as dl:
        page.locator("#pp-download").click()
    with zipfile.ZipFile(dl.value.path()) as z:
        names = z.namelist()
    assert len([n for n in names if n.endswith(".pdf")]) == proposed
    page.shoot("4-several-pdfs")


def _slow_renderer(stop, release=None):
    def renderer(model, blob_bytes, progress=None):
        for done in range(1, 2000):
            progress(
                PdfProgress(
                    stage="laying_out", pages_done=done, pages_total=2000, message="Laying out"
                )
            )
            if release is not None and release.is_set():
                return b"%PDF-1.4 slow"
            if stop.wait(0.05):
                break
        return b"%PDF-1.4 slow"

    return renderer


def test_cancel_an_export(start_console, new_page):
    stop = threading.Event()
    url, folder = start_console(renderer=_slow_renderer(stop))
    page = new_page
    try:
        _open_export_dialog(page, url)
        _export(page)
        expect(page.locator("#pp-running")).to_be_visible()
        expect(page.locator("#pp-pages")).not_to_have_text("", timeout=SLOW)
        page.locator("#pp-cancel").click()
        title = page.locator("#modal-title")
        expect(title).to_contain_text(re.compile("cancel", re.I), timeout=SLOW)
        expect(page.locator("#modal-body")).to_contain_text(re.compile("nothing", re.I))
        expect(page.locator("#modal-body [data-copy-details]")).to_have_count(0)
        expect(page.locator("#pdf-progress")).to_be_hidden()
        assert not list(folder.rglob("*.pdf"))
        page.shoot("5-cancelled")
    finally:
        stop.set()


def test_out_of_memory_says_so_and_offers_details(start_console, new_page):
    def renderer(model, blob_bytes, progress=None):
        raise PdfOutOfMemoryError(
            "The part is too large to lay out with the memory available. " + _advice()
        )

    url, _ = start_console(renderer=renderer)
    page = new_page
    _open_export_dialog(page, url)
    _export(page)
    expect(page.locator("#modal-title")).to_contain_text("Not enough memory", timeout=SLOW)
    body = page.locator("#modal-body")
    expect(body).to_contain_text(re.compile("memory"))
    expect(body).to_contain_text(_advice()[:20])
    expect(body.get_by_role("button", name="Copy details")).to_be_visible()
    page.shoot("6-out-of-memory")


def test_reload_during_a_job_reattaches(start_console, new_page):
    stop, release = threading.Event(), threading.Event()
    url, folder = start_console(renderer=_slow_renderer(stop, release))
    page = new_page
    try:
        _open_export_dialog(page, url)
        _export(page)
        expect(page.locator("#pp-pages")).not_to_have_text("", timeout=SLOW)
        page.reload(wait_until="load")
        expect(page.locator("#pdf-progress")).to_be_visible(timeout=SLOW)
        expect(page.locator("#pp-running")).to_be_visible()
        expect(page.locator("#pp-pages")).not_to_have_text("", timeout=SLOW)
        page.locator("#sb-activity").click()
        expect(page.locator("#activity-pop")).to_be_visible()
        expect(page.locator("#activity-body")).to_contain_text(re.compile("PDF"))
        page.shoot("7-reload")
        release.set()
        expect(page.locator("#pp-done")).to_be_visible(timeout=SLOW)
    finally:
        stop.set()


def test_recent_exports_list_the_export(start_console, new_page):
    def renderer(model, blob_bytes, progress=None):
        return b"%PDF-1.4 quick"

    url, _ = start_console(renderer=renderer)
    page = new_page
    _open_export_dialog(page, url)
    _export(page)
    expect(page.locator("#pp-done")).to_be_visible(timeout=SLOW)
    _archives(page, url)
    recent = page.locator("#recent-exports-list")
    expect(recent).to_contain_text(".pdf", timeout=SLOW)
    link = recent.get_by_role("link", name=re.compile("Download .* again"))
    expect(link).to_be_visible()
    with page.expect_download() as dl:
        link.click()
    with open(dl.value.path(), "rb") as fh:
        assert fh.read(4) == b"%PDF"
    page.shoot("8-recent")


def test_preflight_blocks_export_when_the_disk_is_full(start_console, new_page, monkeypatch):
    from connections_export.pdf import memory

    monkeypatch.setattr(memory, "free_disk", lambda *_a, **_k: 3 * 1024 * 1024)
    url, _ = start_console()
    page = new_page
    _archives(page, url)
    page.get_by_role("button", name=re.compile(r"^Export ")).first.click()
    checks = page.locator("#xd-checks")
    expect(checks).to_be_visible(timeout=SLOW)
    expect(checks).to_contain_text(re.compile("disk|space", re.I))
    expect(page.locator("#xd-go")).to_be_disabled()
    page.shoot("9-preflight")


def test_clearing_the_pdf_part_size_survives_a_reload(start_console, new_page):
    url, _ = start_console()
    page = new_page
    page.goto(url, wait_until="load")
    page.locator('[data-section="settings"]').click()
    page.locator("#settings-advanced summary").click()
    field = page.locator("#settings-pdf-part-size")
    field.fill("256")
    page.locator("#settings-adv-save").click()
    expect(page.locator("#settings-adv-status")).not_to_have_text("", timeout=SLOW)
    page.reload(wait_until="load")
    page.locator('[data-section="settings"]').click()
    page.locator("#settings-advanced summary").click()
    expect(field).to_have_value("256")
    field.fill("")
    page.locator("#settings-adv-save").click()
    expect(page.locator("#settings-adv-status")).not_to_have_text("", timeout=SLOW)
    page.reload(wait_until="load")
    page.locator('[data-section="settings"]').click()
    page.locator("#settings-advanced summary").click()
    expect(field).to_have_value("")
    page.shoot("10-settings")
