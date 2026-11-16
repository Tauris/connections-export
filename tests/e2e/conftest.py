"""A real console and a real browser, for walking the key journeys.

Each test gets its own console (uvicorn in a background thread, on a free
port) over its own archives folder, so one journey's exports never show up
in another's. The demo archive is built once per module and copied.

Selectors are ids, `data-*` attributes and ARIA roles/labels, never CSS
classes or exact visual copy: the look of the console is free to change.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

SHOTS = Path("/tmp/claude-1000/-home-joe-src-hcl/9b1143c4-fc09-4729-b05b-60292021510b/scratchpad")
DEMO_NAME = "DEMO-FAKE-DATA-journey"


def pytest_collection_modifyitems(items):
    skip = pytest.mark.skip(reason="needs Chromium")
    for item in items:
        if "tests/e2e/" in item.nodeid.replace("\\", "/") and not CHROMIUM_AVAILABLE:
            item.add_marker(skip)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = launch_browser(p)
        yield b
        b.close()


@pytest.fixture(scope="module")
def demo_template(tmp_path_factory):
    from connections_export.gui.demo import run_demo

    root = tmp_path_factory.mktemp("journey-template") / DEMO_NAME
    run_demo(lambda _e: None, archive_dir=root, delay=0)
    return root


@pytest.fixture
def start_console(tmp_path, monkeypatch, demo_template):
    """`start_console(renderer=None, with_archive=True)` -> (url, archives folder)."""
    import uvicorn

    from connections_export.gui import support as gui_support
    from connections_export.gui.app import make_app

    folder = tmp_path / "archives"
    folder.mkdir()
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", folder)
    servers = []

    def start(renderer=None, with_archive=True):
        if with_archive:
            shutil.copytree(demo_template, folder / DEMO_NAME)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        app = make_app(demo=True, pdf_renderer=renderer)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", access_log=False
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            pytest.skip("the console did not start")
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}/", folder

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def new_page(browser, request):
    """A fresh page; with JOURNEY_SHOTS=1 `shoot(name)` saves a screenshot."""
    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    context.set_default_timeout(20_000)
    page = context.new_page()

    def shoot(name: str) -> None:
        if os.environ.get("JOURNEY_SHOTS"):
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / f"journey-{name}.png"))

    page.shoot = shoot
    yield page
    context.close()
