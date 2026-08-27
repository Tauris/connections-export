"""The reader's hierarchy stays on screen while you read a long page.

scrolling down a long page carried the page
tree away with it, so getting to another page meant scrolling all the way
back up.

`.r-nav` is `position: sticky` for exactly this reason. A later rule of equal
specificity -- `.r-nav { position: relative }`, added so that the section
headers INSIDE the nav could stick -- silently won the cascade and turned the
whole thing back into a column that scrolls with the document. The inner
headers never needed it: the nav is already its own scroll container.

A cascade bug is only real in a browser, so this uses one. Asserting on the
stylesheet text would have passed against the broken file, because both rules
are in it.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from connections_export.pdf.browser import CHROMIUM_AVAILABLE, launch_browser

pytestmark = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")


@pytest.fixture
def console_url():
    import uvicorn

    from connections_export.gui.app import make_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(
        make_app(demo=True),
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


def test_the_page_tree_sticks_rather_than_scrolling_away(console_url):
    """Asked of a real browser, which is the only thing that resolves a
    cascade. The stylesheet contains both rules either way, so reading the
    file proves nothing about which one wins."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        # Through the product's own launcher, not `chromium.launch`: the
        # skip guard above proves a browser by starting one from that same
        # candidate order (a system Edge or Chrome before Playwright's bundled
        # Chromium), so asking here for the bundled one is asking for a
        # different browser than the guard checked -- green locally, and
        # "Executable doesn't exist" on a runner that has Chrome.
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(console_url, wait_until="load")
        page.wait_for_selector("#r-nav", state="attached", timeout=20_000)

        position = page.evaluate(
            "() => getComputedStyle(document.querySelector('#r-nav')).position"
        )
        assert position == "sticky", position

        # The headers inside it stick too -- that is what the rule which broke
        # this was added for, so both have to hold at once.
        inner = page.evaluate(
            """() => {
                const el = document.createElement('button');
                el.className = 'r-component-section';
                document.querySelector('#r-nav').appendChild(el);
                const value = getComputedStyle(el).position;
                el.remove();
                return value;
            }"""
        )
        assert inner == "sticky", inner

        browser.close()


def test_opening_a_section_at_the_bottom_shows_some_of_it(console_url):
    """Expanding a section whose contents would unfold below the fold looked
    like a click that did nothing: the header stayed put and nothing visible
    changed. It has to scroll far enough to show a few entries."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = launch_browser(play)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(console_url, wait_until="load")
        # The reader panel must actually be SHOWN: inside a hidden panel every
        # rect is zero-sized, and "is this entry inside the nav" is then true
        # of everything -- so the assertion would hold whether or not the
        # navigation actually stays put.
        page.click('[data-section="reader"]')
        page.wait_for_selector("#panel-reader:not([hidden])", timeout=20_000)
        page.wait_for_selector("#r-nav", state="attached", timeout=20_000)

        # A nav with a scroll of its own, and a section sitting at the very
        # bottom of it whose contents are hidden until it is opened.
        revealed = page.evaluate(
            """() => {
                const nav = document.querySelector('#r-nav');
                nav.hidden = false;
                nav.style.height = '200px';
                nav.style.overflow = 'auto';
                nav.innerHTML = '';
                const filler = document.createElement('div');
                filler.style.height = '400px';
                nav.appendChild(filler);
                const head = document.createElement('button');
                head.className = 'r-component-section';
                head.setAttribute('aria-expanded', 'false');
                head.innerHTML = '<span class="r-component-chevron"></span><span>SECTION</span>';
                const content = document.createElement('div');
                content.className = 'r-component-content';
                content.hidden = true;
                for (let i = 0; i < 12; i++) {
                    const a = document.createElement('a');
                    a.textContent = 'entry ' + i;
                    content.appendChild(a);
                }
                nav.appendChild(head);
                nav.appendChild(content);
                nav.scrollTop = nav.scrollHeight;

                content.hidden = false;              // what the click does
                window.__revealExpandedProbe(head, content);

                const navBox = nav.getBoundingClientRect();
                if (!navBox.height) return -1;   // nothing is laid out: prove nothing
                return Array.from(content.querySelectorAll('a')).filter((a) => {
                    const b = a.getBoundingClientRect();
                    return b.height > 0 && b.top >= navBox.top && b.bottom <= navBox.bottom;
                }).length;
            }"""
        )

        assert revealed >= 3, f"only {revealed} entries came into view"
        browser.close()
