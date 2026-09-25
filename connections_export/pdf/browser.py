"""`html_to_pdf(html) -> bytes` (html_to_pdf (thin)): a thin Playwright wrapper around
`render_html`'s output.
Deliberately minimal -- all the real logic lives in `html.py`/
`browser_fidelity.py` and is tested without a browser; this module is
only exercised by a browser-gated smoke test (`tests/pdf/test_browser.py`),
skipped when no usable browser is present.

A Chromium-based *browser binary* is a runtime prerequisite, not a Python
dependency. To keep `pip install` self-sufficient on most machines, we prefer a **system browser**
-- Microsoft
Edge, then Google Chrome (both Chromium-based, and Edge ships on every
modern Windows) -- and only fall back to Playwright's bundled Chromium
(the one `playwright install chromium` downloads). An operator can force a
choice with `CONNECTIONS_EXPORT_BROWSER_CHANNEL` (e.g. `msedge`, `chrome`) or
`CONNECTIONS_EXPORT_BROWSER_PATH` (an explicit executable). `CHROMIUM_AVAILABLE`
is decided once, at import time, by actually trying that sequence; the
probe never raises -- any failure just means "unavailable".
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from connections_export.derive.model import Interchange
from connections_export.pdf.html import DEFAULT_GENERATED_AT, BlobBytes, render_html

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from connections_export.pdf.marks import Marks

#: Force a specific browser instead of the Edge -> Chrome -> bundled probe.
ENV_BROWSER_CHANNEL = "CONNECTIONS_EXPORT_BROWSER_CHANNEL"
ENV_BROWSER_PATH = "CONNECTIONS_EXPORT_BROWSER_PATH"
#: Set to 1/true/yes to launch without Chromium's sandbox (see `_launch_args`).
ENV_BROWSER_NO_SANDBOX = "CONNECTIONS_EXPORT_BROWSER_NO_SANDBOX"

_UNSET = object()


def _current_euid() -> int | None:
    """The effective uid, or None where there is no such thing (Windows)."""
    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else None


def _launch_args(env: Mapping[str, str] | None = None, euid: object = _UNSET) -> list[str]:
    """Flags every launch gets.

    `--no-sandbox` only when the browser could not start otherwise. The
    documents printed here are untrusted -- captured pages, possibly from an
    archive someone else made -- and Chromium's sandbox is what contains a
    renderer that such content has compromised, so it stays on by default.
    It has to go in two cases: running as root (Chromium refuses to start its
    sandbox as root, and containers usually run as root -- the case this flag
    was originally added for), and a container that lacks the kernel support
    the sandbox needs even as an ordinary user, which only the operator can
    know: they set `CONNECTIONS_EXPORT_BROWSER_NO_SANDBOX=1`.

    The other two are about output. Edge prints component-loader errors as it
    starts -- "Edge LLM: Error getting component directory" and a matching
    pre-sandbox line -- which are about an on-device model feature that has
    nothing to do with rendering a page. The export works with them present,
    but they are printed by a program we launched, not by us, and a user
    reading their terminal sees ERROR twice and reasonably concludes the export
    failed.

    So: switch the feature off, which removes the cause, and set the log level
    to fatal-only, which stops the rest of Chromium's chatter arriving as our
    output. Anything that actually kills the browser still reaches us.
    """
    env = os.environ if env is None else env
    euid = _current_euid() if euid is _UNSET else euid
    override = env.get(ENV_BROWSER_NO_SANDBOX, "").strip().lower() in ("1", "true", "yes")
    sandbox_off = ["--no-sandbox"] if override or euid == 0 else []
    return [
        *sandbox_off,
        "--disable-features=OptimizationGuideOnDeviceModel",
        "--log-level=3",
    ]


# --- the render's network ------------------------------------------------
#
# The document being printed is complete in itself: images are data URIs,
# the polyfill is injected from the vendored file, and the CSS is inline. So
# the print page has no reason to make a request, and every reason not to --
# the content is untrusted, and a request it makes can reach an intranet host,
# a cloud metadata address or this tool's own console, or carry the export
# away. Sanitization removes what it recognises; this blocks what it misses.

#: The only URLs the page may load: its own inline content.
_RENDER_ALLOWED_PREFIXES = ("data:", "about:", "blob:")


def render_request_allowed(url: str) -> bool:
    """May the print page load `url`? Only content that is already in it."""
    return url.lower().startswith(_RENDER_ALLOWED_PREFIXES)


def block_network(page, blocked: list[str]) -> None:
    """Answer every request the page makes for anything outside itself with
    an empty response from here, recording each in `blocked`. Nothing is
    forwarded. Installed BEFORE `set_content`, so not even the first parse
    can fetch anything.

    Answered empty rather than aborted: paged.js fetches a `<link>`ed
    stylesheet itself and, when that fetch FAILS, never finishes paginating --
    the export would sit until the render timeout. An empty stylesheet (or
    image, or frame) is simply nothing, and the render carries on.

    Blocked requests are recorded separately from real failures: they are the
    guard working, not the export failing -- a captured page that references
    an intranet stylesheet should still print."""

    def handle(route) -> None:
        request = route.request
        url = request.url
        if render_request_allowed(url):
            route.continue_()
            return
        blocked.append(url)
        if request.is_navigation_request() and request.frame == page.main_frame:
            # Answering this one would REPLACE the document being printed
            # with an empty one; refused, the document stays.
            route.abort("blockedbyclient")
            return
        route.fulfill(status=200, content_type="text/plain", body="")

    def handle_web_socket(web_socket) -> None:
        # `page.route` never sees WebSockets. Not connecting to the server
        # is what keeps this one inside the browser; closing it is courtesy.
        blocked.append(web_socket.url)
        web_socket.close()

    page.route("**/*", handle)
    page.route_web_socket(re.compile(".*"), handle_web_socket)


def _launch_candidates(env: Mapping[str, str]) -> Iterator[dict]:
    """Ordered `chromium.launch(**kwargs)` argument sets to try. An explicit
    executable path or channel (env) wins outright; otherwise prefer a
    system Microsoft Edge, then Google Chrome, then Playwright's own bundled
    Chromium (`{}` = no channel)."""
    executable = env.get(ENV_BROWSER_PATH)
    if executable:
        yield {"executable_path": executable}
        return
    channel = env.get(ENV_BROWSER_CHANNEL)
    if channel:
        yield {"channel": channel}
        return
    yield {"channel": "msedge"}
    yield {"channel": "chrome"}
    yield {}


def launch_browser(playwright, env: Mapping[str, str] | None = None, *, proxy=None):
    """Launch the first browser in the `_launch_candidates` order that
    starts; re-raise the last error if none do.

    Public because the browser-driven TESTS need it too, and launching any
    other way makes `CHROMIUM_AVAILABLE` a lie: the probe proves a browser by
    starting one from this same order -- a system Edge or Chrome before
    Playwright's bundled Chromium -- so a test that calls
    `playwright.chromium.launch` directly is asking for a DIFFERENT browser
    than the one the guard checked. On a machine with Chrome installed and no
    bundled Chromium (a GitHub runner, for one) the guard says yes and the
    launch fails.
    """
    env = os.environ if env is None else env
    # `proxy` is a decision for the deployment the browser will visit
    # (`http.proxy.ProxyDecision`), carried in so the browser agrees with the
    # tool's own requests. Without one, Chromium decides for itself from the
    # system PAC -- the same script, so ordinarily the same answer -- and
    # bypasses loopback on its own.
    extra = {}
    if proxy is not None:
        from connections_export.http.proxy import playwright_proxy_kwargs  # noqa: PLC0415

        extra = playwright_proxy_kwargs(proxy)
    launch_args = _launch_args() + extra.pop("args", [])
    last_error: Exception | None = None
    for kwargs in _launch_candidates(env):
        try:
            return playwright.chromium.launch(args=launch_args, **kwargs, **extra)
        except Exception as exc:  # this channel's browser isn't installed -- try the next
            last_error = exc
    raise last_error if last_error is not None else RuntimeError("no browser candidates")


#: The name this had while it was private.
_launch = launch_browser

#: Populated by the import-time probe when no usable browser is found: one
#: short line per candidate that was tried and why it failed. Surfaced through
#: `browser_unavailable_detail` in the `no_browser` API responses, so the
#: failure explains *why* rather than just "no browser" -- e.g. Playwright
#: looking for a Linux Edge at `/opt/microsoft/msedge/msedge` that a Windows
#: install doesn't provide (a Windows browser can't be driven from WSL/Linux).
BROWSER_PROBE_ERRORS: list[str] = []


def _describe_candidate(kwargs: Mapping[str, str]) -> str:
    if "executable_path" in kwargs:
        return f"executable {kwargs['executable_path']}"
    if "channel" in kwargs:
        return f"channel {kwargs['channel']!r}"
    return "Playwright's bundled Chromium"


def _probe_chromium_available() -> bool:
    """Try the `_launch_candidates` sequence once, recording why each attempt
    failed in `BROWSER_PROBE_ERRORS` (the probe never raises). Mirrors
    `_launch`'s order, but keeps every candidate's error instead of only the
    last, so the `no_browser` message can name what was tried."""
    BROWSER_PROBE_ERRORS.clear()
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        BROWSER_PROBE_ERRORS.append(f"playwright is not installed ({exc})")
        return False
    try:
        with sync_playwright() as playwright:
            for kwargs in _launch_candidates(dict(os.environ)):
                try:
                    browser = playwright.chromium.launch(args=_launch_args(), **kwargs)
                    browser.close()
                    return True
                except Exception as exc:
                    first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                    BROWSER_PROBE_ERRORS.append(f"{_describe_candidate(kwargs)}: {first}")
    except Exception as exc:  # Playwright itself could not start at all
        BROWSER_PROBE_ERRORS.append(f"playwright could not start ({exc})")
    return False


def browser_unavailable_detail() -> str:
    """A human, actionable explanation of why PDF rendering found no usable
    browser, assembled from the import-time probe (`BROWSER_PROBE_ERRORS`), so
    the `no_browser` response is not a dead end. Key point operators miss: the
    browser must live in the SAME OS as this process -- a Windows Edge/Chrome
    cannot be driven from a Linux/WSL run."""
    detail = (
        "No usable Chromium-based browser was found for PDF rendering. Install "
        "Microsoft Edge or Google Chrome in the same OS this tool runs in, or run "
        "`playwright install chromium`. A browser installed in another OS is not "
        "usable -- a Windows Edge/Chrome cannot be driven from a Linux/WSL "
        f"process, so install a Linux browser there. You can also set {ENV_BROWSER_PATH} "
        f"to an explicit executable, or {ENV_BROWSER_CHANNEL} to msedge/chrome."
    )
    if BROWSER_PROBE_ERRORS:
        detail += " Tried: " + "; ".join(BROWSER_PROBE_ERRORS) + "."
    return detail


#: Decided once at import time (module docstring): is any usable
#: Chromium-based browser present (system Edge/Chrome or bundled Chromium)?
#: Tests use this to `pytest.skip` the PDF smoke test rather than fail the
#: suite on an environment with no browser at all.
CHROMIUM_AVAILABLE = _probe_chromium_available()


# --- footer / header / margins ------------------------------------------
#
# A CSS-only footer is not available here. `@page { @bottom-right { content:
# string(sectitle) " · " counter(page); } }`, keyed off a `string-set` on
# `.pdf-sectitle`, renders completely empty -- no page number, no name --
# because Chromium's print-to-PDF engine does not implement CSS named strings
# (`string`/`string-set`) in `@page` margin boxes at all.
#
# The mechanism that *does* work is Chromium's own header/footer templates,
# passed to `page.pdf(display_header_footer=True, header_template=...,
# footer_template=...)`. Inside those templates, `class="pageNumber"` /
# `class="totalPages"` / `class="title"` (the document `<title>`) /
# `class="date"` / `class="url"` are special classes Chromium fills in
# itself -- and they DO render. `class="title"` is the one "name" achievable
# this way: a per-*section* running name (which wiki page/post/topic a given
# printed page came from) would need `target-counter`/paged-media support
# that Chromium's print engine doesn't have -- see `html.py`'s `_STYLE`
# comment. Same story for the TOC's "p. N" page numbers (`pdf/html.py`'s
# `_render_toc`): unreachable without a real paged-media engine (WeasyPrint,
# Prince) or a two-pass render, so the TOC stays link-only.
#
# Chromium's print templates default every element to `font-size: 0`
# (invisible) unless a size is set explicitly -- hence the inline style.
_HEADER_TEMPLATE = "<span></span>"
_FOOTER_TEMPLATE = (
    '<div style="font-size:9px; width:100%; padding:0 14mm; color:#555; '
    'display:flex; justify-content:space-between;">'
    '<span class="title"></span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span>'
    "</div>"
)
#: A non-empty margin is required for the footer template to have room to
#: render at all (a zero/absent margin clips it away).
_PDF_MARGIN = {"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"}


def _pdf_kwargs(*, outline: bool, marks: Marks | None = None) -> dict:
    """The `page.pdf(...)` keyword arguments, pulled out so the footer/
    header/margin wiring is inspectable and unit-testable without launching
    a browser (`tests/pdf/test_browser_launch.py`)."""
    from connections_export.pdf.marks import (  # noqa: PLC0415
        Marks,
        chromium_raw,
        chromium_template,
    )

    marks = marks or Marks()
    return {
        "print_background": True,
        "format": "A4",
        "outline": outline,
        "display_header_footer": True,
        "header_template": chromium_raw(marks.header_html, marks)
        if marks.header_html
        else chromium_template(
            marks.header_left,
            marks.header_right,
            marks,
            center=marks.header_center,
            rule=marks.header_rule,
            at_top=True,
        ),
        "footer_template": chromium_raw(marks.footer_html, marks)
        if marks.footer_html
        else chromium_template(
            marks.footer_left,
            marks.footer_right,
            marks,
            center=marks.footer_center,
            rule=marks.footer_rule,
        ),
        "margin": dict(_PDF_MARGIN),
    }


def html_to_pdf(html: str, *, outline: bool = False, marks: Marks | None = None) -> bytes:
    """`page.set_content(html); page.pdf(...)` -- print `html`
    with a headless Chromium-based browser (system Edge/Chrome preferred,
    see the module docstring) and return the PDF bytes. Callers are expected
    to check `CHROMIUM_AVAILABLE` first; this raises whatever Playwright
    raises if no usable browser is present.

    The footer (page number + document title, via `_FOOTER_TEMPLATE`) is
    Chromium's real `display_header_footer` mechanism, not CSS -- see the
    module comment above for why.

    `outline=True` asks the browser to generate a PDF outline (bookmarks)
    from the document's heading structure -- used by Path B's single
    combined document so the reader gets a navigable sidebar; Path A leaves
    it off (default)."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            # Nothing here needs script -- the header and footer are Chromium's
            # own templates -- so the untrusted document gets none. A service
            # worker would see requests before the route does.
            page = browser.new_page(java_script_enabled=False, service_workers="block")
            blocked: list[str] = []
            block_network(page, blocked)
            page.set_content(html, wait_until="load")
            return page.pdf(**_pdf_kwargs(outline=outline, marks=marks))
        finally:
            browser.close()


def render_pdf(
    interchange: Interchange,
    blob_bytes: BlobBytes,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
    chrome: bool = True,
    style_overrides: dict[str, str] | None = None,
    extra_css: str | None = None,
    marks: Marks | None = None,
    external_images: Mapping[str, bytes | None] | None = None,
    small_image_px: int | None = None,
) -> bytes:
    """`html_to_pdf(render_html(...))` -- the end-to-end
    entry point: interchange model -> PDF bytes. `include_comments=False`
    omits comments from the export (they remain in the archive).
    `chrome=False` drops the cover + TOC (the live per-entity tiles)."""
    html = render_html(
        interchange,
        blob_bytes=blob_bytes,
        generated_at=generated_at,
        include_comments=include_comments,
        chrome=chrome,
        style_overrides=style_overrides,
        extra_css=extra_css,
        external_images=external_images,
        small_image_px=small_image_px,
    )
    return html_to_pdf(html, marks=marks)
