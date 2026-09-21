"""Live-system browser PDF: renders pages from their original HCL URLs via
Playwright, injecting CSS to strip navigation chrome. Used by `/api/live-pdf`
when the source deployment is still reachable.

Complements Path A/B (which render from the archived interchange model) by
giving exact platform-CSS fidelity — the real system's own styles, fonts, and
images, exactly as a logged-in user would see them.

Graceful failure: if a URL times out or returns an error, the page is skipped
with a warning rather than aborting the whole export. The caller receives both
a PDF (possibly partial) and a report of any skipped pages.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field

# CSS injected into every live page: hides HCL navigation chrome so only
# the content column is printed. Mirrors what bc_pdf.py does.
_HIDE_CHROME_CSS = """
.lotusFrame > div[role=banner], .lotusColLeft, .lotusColRight,
.lotusActionBar, .lotusLikeAction, .lotusActions, .lotusFeeds,
div[role=navigation], .lotusBtnContainer, .lotusFooter,
.lotusInlinelist[role=toolbar], #lotusCollapseBar,
.lotusPaging[dojoattachpoint=topPageNode],
.lotusPaging[dojoattachpoint=bottomPageNode] {
    display: none !important;
}
#lotusContent {
    margin-left: 0 !important;
    margin-right: 0 !important;
}
html, body, #lotusFrame, #lotusMain, #lotusContent {
    width: 100% !important;
    min-width: 0 !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
}
#lotusContent, #lotusContent * {
    max-width: 100% !important;
    box-sizing: border-box !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
    word-break: break-word !important;
}
#lotusContent img, #lotusContent video, #lotusContent iframe,
#lotusContent svg, #lotusContent canvas {
    max-width: 100% !important;
    height: auto !important;
}
#lotusContent table {
    width: 100% !important;
    max-width: 100% !important;
    table-layout: fixed !important;
}
#lotusContent th, #lotusContent td {
    min-width: 0 !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
    word-break: break-word !important;
}
#lotusContent pre, #lotusContent code {
    white-space: pre-wrap !important;
    overflow-wrap: anywhere !important;
    word-break: break-word !important;
}
#lotusContent .lotusMeta.lotusLeft {
    float: none !important;
    width: auto !important;
    max-width: none !important;
    white-space: normal !important;
}
#lotusContent .lotusMeta.lotusLeft .vcard,
#lotusContent .lotusMeta.lotusLeft .lotusPerson {
    display: inline-block !important;
    max-width: none !important;
    white-space: nowrap !important;
    vertical-align: baseline !important;
}
#lotusContent .lotusMeta.lotusLeft .blogs-comment-count {
    float: none !important;
    position: static !important;
    vertical-align: baseline !important;
}
"""

# Reset injected to un-freeze the SPA viewport so Chromium can paginate.
_LAYOUT_RESET_JS = """(function(){
    document.documentElement.style.setProperty('height','auto','important');
    document.documentElement.style.setProperty('overflow','visible','important');
    document.body.style.setProperty('position','static','important');
    document.body.style.setProperty('overflow','visible','important');
    document.body.style.setProperty('height','auto','important');
    ['lotusFrame','lotusMain','lotusContent'].forEach(function(id){
        var el=document.getElementById(id);
        if(el){
            el.style.setProperty('width','100%','important');
            el.style.setProperty('min-width','0','important');
            el.style.setProperty('max-width','100%','important');
            el.style.setProperty('box-sizing','border-box','important');
            el.style.setProperty('height','auto','important');
            el.style.setProperty('overflow','visible','important');
        }
    });
})()"""

#: Maximum time to wait for a single page to load (ms).
PAGE_TIMEOUT_MS = 45_000
#: Maximum time to wait for the initial network-idle signal (ms).
IDLE_TIMEOUT_MS = 60_000
#: Guard against a broken pager repeatedly returning the same page.
MAX_UI_PAGES = 100

# Walk HCL's rendered pager before the print CSS hides it. The UI differs a
# little between Blogs and Forums, so this deliberately uses the stable
# `rel=next`/pagination semantics first and text/class fallbacks second.
_PAGINATE_LIVE_DOM_JS = """async function(maxPages) {
    const rootSelector = '#lotusContent';
    const holderId = 'hcl-pdf-paged-content';
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const visible = el => {
        if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
        const style = getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' &&
            (el.offsetWidth > 0 || el.offsetHeight > 0);
    };
    const signature = root => {
        const pager = root.querySelector(
            '.lotusPaging, [dojoattachpoint="topPageNode"], ' +
            '[dojoattachpoint="bottomPageNode"]'
        );
        const body = root.querySelector(
            '.lotusContent, .lotusMain, .lotusStream, .lotusTable'
        ) || root;
        return (pager ? pager.innerText : '') + '|' + (body.innerText || '').slice(-2000);
    };
    const nextControl = root => {
        const candidates = Array.from(root.querySelectorAll(
            'a[rel="next"], button[rel="next"], [aria-label*="Next" i], [title*="Next" i], ' +
            '.lotusPaging a, .lotusPaging button, [dojoattachpoint*="next" i]'
        )).filter(visible);
        return candidates.find(el => (el.getAttribute('rel') || '').toLowerCase() === 'next') ||
            candidates.find(el => /^(next|more|older)\b/i.test(
                (el.innerText || el.getAttribute('aria-label') || el.title || '').trim()
            ));
    };
    let root = document.querySelector(rootSelector) || document.body;
    let holder = document.getElementById(holderId);
    if (!holder) {
        holder = document.createElement('div');
        holder.id = holderId;
        holder.style.cssText = 'width:100%;min-width:0;max-width:100%;box-sizing:border-box;';
        root.parentNode.insertBefore(holder, root);
        holder.appendChild(root);
    }
    let pages = 1;
    const visited = new Set([location.href]);
    while (pages < maxPages) {
        root = document.querySelector(rootSelector) || root;
        const control = nextControl(root);
        if (!control) break;
        const href = control.href || control.getAttribute('data-href') || '';
        if (href && visited.has(href)) break;
        if (href) visited.add(href);
        const before = signature(root);
        const oldRoot = root;
        control.click();
        let changed = false;
        for (let attempt = 0; attempt < 80; attempt++) {
            await sleep(250);
            root = document.querySelector(rootSelector) || oldRoot;
            if (signature(root) !== before) { changed = true; break; }
        }
        if (!changed) break;
        pages += 1;
        // The application keeps the live root; retain a snapshot before the
        // next click so prior comments/posts remain printable.
        const snapshot = root.cloneNode(true);
        snapshot.removeAttribute('id');
        snapshot.setAttribute('data-hcl-pdf-page', String(pages));
        holder.appendChild(snapshot);
    }
    return pages;
}"""


@dataclass
class LivePdfResult:
    """Result of `render_live_pdf`."""

    pdf_bytes: bytes
    """Merged PDF; empty bytes if no pages rendered."""
    rendered: list[str] = field(default_factory=list)
    """URLs that were successfully rendered."""
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(url, reason) pairs for pages that could not be rendered."""


def merge_pdf_parts(parts: list[bytes]) -> tuple[bytes, str | None]:
    """Merge per-page PDFs into one. Returns `(pdf, reason it is incomplete)`.

    `reason` is `None` when every part made it in. When it is not, the caller
    must surface it. Returning `parts[0]` silently would let an export of two
    hundred pages hand back a one-page PDF while reporting every page as
    rendered -- a gap that reports success, which is the failure this project
    refuses everywhere else.

    A partial document still beats no document, so the first part is returned
    with the reason rather than raising.
    """
    if not parts:
        return b"", None
    if len(parts) == 1:
        return parts[0], None

    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    try:
        writer = PdfWriter()
        for part in parts:
            for page in PdfReader(io.BytesIO(part)).pages:
                writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue(), None
    except Exception as exc:  # noqa: BLE001 - any failure here means an incomplete PDF
        return parts[0], (
            f"merge failed ({type(exc).__name__}: {exc}) -- this PDF holds the first "
            f"page only, of {len(parts)}"
        )


def render_live_pdf(
    urls: Iterable[str],
    *,
    cookies: list[dict] | None = None,
    proxy=None,
    paper_format: str = "A4",
) -> LivePdfResult:
    """Render each URL via Playwright and merge the per-page PDFs.

    `cookies` is a list of dicts with keys `name`, `value`, `domain`,
    `path` (e.g. from `requests.cookies`) injected into the browser
    context so the live system authenticates. Pass `None` or `[]` when
    the system is publicly accessible or auth is handled another way.

    Raises `RuntimeError` if no usable browser is installed.
    """
    from connections_export.pdf.browser import _launch  # noqa: PLC0415

    url_list = list(urls)
    if not url_list:
        return LivePdfResult(pdf_bytes=b"")

    rendered: list[str] = []
    skipped: list[tuple[str, str]] = []
    pdf_parts: list[bytes] = []

    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed; run `playwright install chromium`."
        ) from exc

    import os  # noqa: PLC0415

    with sync_playwright() as pw:
        browser = _launch(pw, os.environ, proxy=proxy)
        context_kwargs: dict = {"accept_downloads": False}
        if cookies:
            # Convert requests-style cookies to Playwright format.
            pw_cookies = []
            for c in cookies:
                entry = {
                    "name": c.get("name", ""),
                    "value": c.get("value", ""),
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                }
                if entry["name"] and entry["value"]:
                    pw_cookies.append(entry)
            if pw_cookies:
                context_kwargs["storage_state"] = {"cookies": pw_cookies, "origins": []}

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        for url in url_list:
            try:
                page.goto(url, wait_until="networkidle", timeout=IDLE_TIMEOUT_MS)
                # Unfreeze the SPA layout.
                try:
                    page.evaluate(_LAYOUT_RESET_JS)
                except Exception:
                    pass
                # Expand UI-level comments/posts before hiding the pager.
                try:
                    page.evaluate(_PAGINATE_LIVE_DOM_JS, MAX_UI_PAGES)
                except Exception:
                    pass
                page.add_style_tag(content=_HIDE_CHROME_CSS)
                pdf_bytes = page.pdf(
                    format=paper_format,
                    print_background=True,
                    margin={"top": "20px", "bottom": "40px", "left": "10px", "right": "10px"},
                )
                pdf_parts.append(pdf_bytes)
                rendered.append(url)
            except Exception as exc:
                reason = str(exc).split("\n")[0][:120]
                skipped.append((url, reason))

        context.close()
        browser.close()

    if not pdf_parts:
        return LivePdfResult(pdf_bytes=b"", rendered=rendered, skipped=skipped)

    # Merge all per-page PDFs. A failure here is recorded as skipped pages,
    # which the console and the CLI already surface -- rather than returning a
    # truncated document that looks complete.
    merged, incomplete = merge_pdf_parts(pdf_parts)
    if incomplete:
        skipped = [*skipped, *((url, incomplete) for url in rendered[1:])]
        rendered = rendered[:1]

    return LivePdfResult(pdf_bytes=merged, rendered=rendered, skipped=skipped)
