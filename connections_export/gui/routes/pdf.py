"""Rendering a PDF -- from the derived model, or live from the deployment."""

from __future__ import annotations

import datetime
from typing import Annotated, Any

from fastapi import Query
from fastapi.responses import JSONResponse, Response

from connections_export.config import load_config


def register(
    app,
    *,
    demo: bool,
    demo_seed: int,
    demo_delay: float,
    pdf_renderer,
    author_filter: str | None,
) -> None:
    """Register the pdf routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    @app.get("/api/pdf")
    def get_pdf(
        fidelity: str = "portable",
        comments: bool = True,
        scope: str = "all",
        kind: str | None = None,
        id: str | None = None,
        include: Annotated[list[str] | None, Query()] = None,
        chrome: bool = True,
    ) -> Response:
        """Render the current derived model to a PDF and return it as a
        download (the `pdf` capability). `fidelity=portable` is Path A
        (our print-ready HTML); `fidelity=browser` is Path B (per-page
        browser fidelity). `503 {"status":"pending"}` if no model yet;
        `503 {"status":"no_browser"}` if no usable Chromium is installed
        (the front end shows that hint rather than a broken download).

        `scope=item&kind=<kind>&id=<id>` narrows -- an item kind
        (`page`, `post`, `topic`, `rich_content_page`) or a whole container
        (`wiki`, `blog`, `forum`, `files`, `rich_content`) --
        the export to a single unit (the "one thread vs the whole" choice);
        `scope=all` (default) exports everything. An `id` that matches nothing
        yields `422 {"status":"empty_scope"}` rather than a blank PDF.

        `chrome=0` drops the cover + TOC, rendering just the body -- used by
        the live preview's per-entity tiles, where each entity is rendered on
        its own and a repeated cover per tile would be noise. It forces the
        portable (paged) renderer, the only path that honours it.

        `pdf_renderer` (a `make_app` kwarg) is injected by tests so no
        real browser is launched; unset in production."""
        model = app.state.model_source.get_model()
        if model is None:
            root = app.state.model_source.package_root()
            return JSONResponse(
                {
                    "status": "pending",
                    "detail": (
                        "The server has no ready model"
                        + (f" for archive {root.name!r}." if root is not None else ".")
                    ),
                },
                status_code=503,
            )

        # `include` is the fine-grained form: repeated `kind:id` pairs, unioned.
        # `scope=item` narrows to exactly one unit, so without `include`
        # choosing two of five forums means exporting all five.
        if include:
            from connections_export.derive.scope import select_interchange  # noqa: PLC0415

            selections = [
                (part.split(":", 1)[0], part.split(":", 1)[1])
                for part in include
                if ":" in part and part.split(":", 1)[1]
            ]
            model = select_interchange(model, selections)
            # `file_libraries` counts: a files-only selection is a real
            # export, not an empty scope.
            if not (
                model.wikis
                or model.blogs
                or model.forums
                or model.file_libraries
                or model.rich_content
            ):
                return JSONResponse({"status": "empty_scope"}, status_code=422)
        elif scope in ("item", "tile") and kind and id:
            from connections_export.derive.scope import (  # noqa: PLC0415
                scope_interchange,
                single_node_interchange,
            )

            # `tile` = one bare leaf entity (no wiki ancestors) for the live
            # preview strip; `item` = the fuller single-unit export scope.
            narrow = single_node_interchange if scope == "tile" else scope_interchange
            model = narrow(model, kind=kind, target_id=id)
            # `file_libraries` counts: a files-only selection is a real
            # export, not an empty scope.
            if not (
                model.wikis
                or model.blogs
                or model.forums
                or model.file_libraries
                or model.rich_content
            ):
                return JSONResponse(
                    {"status": "empty_scope", "detail": f"Nothing matched {kind} {id!r}."},
                    status_code=422,
                )

        renderer = pdf_renderer
        # Real renderers get a real generation date stamped on the title page
        # (the pure render layer defaults to a "(generation date not provided)"
        # placeholder for determinism -- the live server is the injection point
        # and may read the wall clock). Injected test doubles are called with
        # their original (model, blob_bytes) contract, no extra kwargs.
        render_kwargs: dict[str, Any] = {}
        if renderer is None:
            from connections_export.pdf import (  # noqa: PLC0415
                CHROMIUM_AVAILABLE,
                browser_unavailable_detail,
            )

            render_kwargs["generated_at"] = datetime.datetime.now(datetime.UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            # Comments are always ingested; the export may omit them (`?comments=0`).
            render_kwargs["include_comments"] = comments
            # Bare tiles for the live preview (no cover/TOC per entity).
            if not chrome:
                render_kwargs["chrome"] = False

            if not CHROMIUM_AVAILABLE:
                detail = browser_unavailable_detail()
                print(f"connections-export: PDF export unavailable — {detail}", flush=True)
                return JSONResponse(
                    {"status": "no_browser", "detail": detail},
                    status_code=503,
                )
            if fidelity == "browser" and chrome:
                from connections_export.pdf import render_pdf_browser as renderer  # noqa: PLC0415
            else:
                # `chrome=0` (bare tiles) is only honoured by the paged renderer,
                # so a caller asking for browser fidelity + bare falls back here.
                # Portable fidelity prefers the paged-media renderer (running
                # wiki/page footer + TOC page numbers via the vendored paged.js
                # polyfill in the same Chromium). Fall back to the basic
                # static-footer renderer only if the polyfill is somehow absent.
                from connections_export.pdf import (  # noqa: PLC0415
                    PAGED_AVAILABLE,
                    render_pdf,
                    render_pdf_paged,
                )

                renderer = render_pdf_paged if PAGED_AVAILABLE else render_pdf

        def blob_bytes(digest: str) -> bytes | None:
            result = app.state.model_source.get_blob(digest)
            return result[0] if result is not None else None

        pdf = renderer(model, blob_bytes, **render_kwargs)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="wiki-export.pdf"'},
        )

    @app.get("/api/live-pdf")
    def get_live_pdf(
        scope: str = "all", page_id: str | None = None, url: str | None = None
    ) -> Response:
        """Render pages from their **live HCL URLs** via Playwright using the
        auth session from the last real crawl.

        Two paths:
        - `url=<live-page-url>` (no archive needed): fetches the entry list
          directly from the live system and renders those pages. Works before
          any import has been run.
        - `scope=page&page_id=ID` / `scope=all`: uses `alternate_url` values
          from the current derived model (requires a previous import).

        Returns `503 {"status":"no_browser"}` if Chromium is not installed,
        `503 {"status":"no_live_urls"}` if no URLs can be found,
        or `503 {"status":"unavailable"}` if all pages fail."""
        from connections_export.pdf.browser import (  # noqa: PLC0415
            CHROMIUM_AVAILABLE,
            browser_unavailable_detail,
        )

        if not CHROMIUM_AVAILABLE:
            detail = browser_unavailable_detail()
            print(f"connections-export: live PDF unavailable — {detail}", flush=True)
            return JSONResponse(
                {"status": "no_browser", "detail": detail},
                status_code=503,
            )

        live_urls: list[str] = []

        if url:
            # No-archive path: parse the dropped URL, fetch entries directly.
            import httpx  # noqa: PLC0415

            from connections_export.adapters.blogs import entries_feed_url  # noqa: PLC0415
            from connections_export.gui.wiki_url import parse_url  # noqa: PLC0415

            target = parse_url(url)
            if not target.ok:
                return JSONResponse(
                    {"status": "no_live_urls", "detail": f"Could not parse URL: {target.reason}"},
                    status_code=503,
                )
            base = target.base_url or app.state.live_base_url
            cookies = app.state.live_cookies or []

            if target.app == "wiki" and target.wiki_label:
                # Single wiki page alternate URL is in the URL itself for wiki
                # (the user dropped the human page URL directly).
                live_urls = (
                    [url.split("?")[0].split("#")[0].replace("#!", "")]
                    if "#!/" not in url
                    else [f"{base}/wikis/home/wiki/{target.wiki_label}"]
                )
            elif target.app == "blog":
                # Fetch entries feed, extract alternate URLs.
                if base:
                    try:
                        blog_id = target.blog_handle or ""
                        feed = entries_feed_url(base_url=base, blog_uuid=blog_id)
                        jar = {c["name"]: c["value"] for c in cookies}
                        resp = httpx.get(
                            feed + "?ps=200&page=0",
                            cookies=jar,
                            follow_redirects=True,
                            timeout=20,
                        )
                        from connections_export.adapters.blogs import (  # noqa: PLC0415
                            parse_entries_feed,
                        )

                        posts = parse_entries_feed(resp.content)
                        live_urls = [p.alternate_url for p in posts if p.alternate_url]
                    except Exception as exc:
                        return JSONResponse(
                            {
                                "status": "unavailable",
                                "detail": f"Could not fetch blog entries: {exc}",
                            },
                            status_code=503,
                        )
            if not live_urls:
                # Fallback: just render the URL the user dropped.
                live_urls = [url]
        else:
            # Model path: use alternate_url from derived interchange.
            model = app.state.model_source.get_model()
            if model is None:
                return JSONResponse(
                    {
                        "status": "no_model",
                        "detail": "Run an import first, or drop a URL into the Live PDF button.",
                    },
                    status_code=503,
                )
            if scope == "page" and page_id:
                for wiki in model.wikis:
                    p = wiki.pages.get(page_id)
                    if p and p.alternate_url:
                        live_urls.append(p.alternate_url)
                for blog in model.blogs:
                    p2 = blog.posts.get(page_id)
                    if p2 and p2.alternate_url:
                        live_urls.append(p2.alternate_url)
                for forum in model.forums:
                    t = forum.topics.get(page_id)
                    if t and t.alternate_url:
                        live_urls.append(t.alternate_url)
            else:
                for wiki in model.wikis:
                    for p in wiki.pages.values():
                        if p.alternate_url:
                            live_urls.append(p.alternate_url)
                for blog in model.blogs:
                    for p2 in blog.posts.values():
                        if p2.alternate_url:
                            live_urls.append(p2.alternate_url)
                for forum in model.forums:
                    for t in forum.topics.values():
                        if t.alternate_url:
                            live_urls.append(t.alternate_url)

        if not live_urls:
            return JSONResponse(
                {
                    "status": "no_live_urls",
                    "detail": (
                        "No live URLs found. Re-run the import to capture them, "
                        "or drop a URL onto the Live PDF button."
                    ),
                },
                status_code=503,
            )

        from connections_export.http.proxy import resolve_proxy  # noqa: PLC0415
        from connections_export.pdf.live import render_live_pdf  # noqa: PLC0415

        # The browser visits the deployment; it goes there the way the
        # tool's own requests do, loopback bypassed, rather than deciding
        # for itself from the system PAC.
        try:
            proxy_setting = load_config({}).proxy
        except Exception:  # noqa: BLE001 - unreadable configuration means no explicit proxy
            proxy_setting = None
        decision = resolve_proxy(live_urls[0], explicit=proxy_setting) if live_urls else None
        result = render_live_pdf(live_urls, cookies=app.state.live_cookies or None, proxy=decision)
        if not result.pdf_bytes:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "detail": "Could not load any page from the source system.",
                    "skipped": result.skipped,
                },
                status_code=503,
            )
        title = "live-export"
        if scope == "page" and page_id:
            title = f"live-page-{page_id[:8]}"
        return Response(
            content=result.pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{title}.pdf"',
                "X-Live-PDF-Rendered": str(len(result.rendered)),
                "X-Live-PDF-Skipped": str(len(result.skipped)),
            },
        )
