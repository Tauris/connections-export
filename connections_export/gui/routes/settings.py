"""Reading and writing the console's editable settings, and previewing what
a PDF style change looks like before it is used."""

from __future__ import annotations

from fastapi.responses import JSONResponse, Response

from connections_export.gui import support
from connections_export.gui.archives import (
    list_archives,
)
from connections_export.gui.requests import (
    _SettingsRequest,
    _StylePreviewRequest,
)
from connections_export.gui.settings_store import save_settings
from connections_export.gui.support import (
    ARCHIVES_DIR_ENV,
    _pdf_style_token_defaults,
)
from connections_export.pdf.marks import DEFAULT_MARKS


def register(
    app,
    *,
    demo: bool,
    demo_seed: int,
    demo_delay: float,
    pdf_renderer,
    author_filter: str | None,
) -> None:
    """Register the settings routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    @app.get("/api/settings")
    def get_settings() -> JSONResponse:
        """Snapshot of the editable run settings and environment status +
        environment status for the Settings section:
        which source system/auth a real crawl would use, where run
        archives land, and whether a PDF-capable browser is available.
        No secrets: `base_url`/`auth_mode` are configuration, not
        credentials. In demo mode `base_url`/`auth_mode` are always
        `None` -- a demo server never resolves the host's real config
        (mirrors `/api/start`, which only loads config for a non-demo
        server with no explicit `base_url`)."""
        settings = app.state.editable_settings
        base_url: str | None = settings["base_url"]
        auth_mode: str | None = None if demo else settings["auth_mode"]
        if not demo:
            from connections_export.config import load_config  # noqa: PLC0415

            try:
                cfg = load_config({})
                base_url = settings["base_url"] or cfg.base_url
                auth_mode = settings["auth_mode"] or cfg.auth_mode
            except Exception:
                base_url = None
                auth_mode = None

        from connections_export.pdf.browser import (  # noqa: PLC0415
            CHROMIUM_AVAILABLE,
            browser_unavailable_detail,
        )

        return JSONResponse(
            {
                "demo": demo,
                "base_url": base_url,
                "auth_mode": auth_mode,
                "archives_dir": str(support.ARCHIVES_BASE),
                "archives_dir_env": ARCHIVES_DIR_ENV,
                "archives_count": len(list_archives(support.ARCHIVES_BASE)),
                "pdf_browser_available": CHROMIUM_AVAILABLE,
                "pdf_browser_detail": (
                    None if CHROMIUM_AVAILABLE else browser_unavailable_detail()
                ),
                "min_interval": settings["min_interval"],
                "default_author_filter": settings["default_author_filter"],
                "pdf_style": settings.get("pdf_style") or {},
                "pdf_marks": settings.get("pdf_marks") or {},
                "archive_only": bool(settings.get("archive_only")),
                "pdf_mark_defaults": DEFAULT_MARKS,
                # The controls are built from this rather than a list in the
                # JavaScript: add a token to the stylesheet and it appears in
                # the console, with no second place to remember.
                "pdf_style_tokens": _pdf_style_token_defaults(),
            }
        )

    @app.put("/api/settings")
    def update_settings(body: _SettingsRequest) -> JSONResponse:
        """Update non-secret defaults used by subsequent browser ingests."""
        if body.auth_mode not in {"sspi", "kerberos", "basic", "paste_token"}:
            return JSONResponse({"error": "unsupported auth mode"}, status_code=422)
        if body.min_interval < 0:
            return JSONResponse({"error": "min_interval must be non-negative"}, status_code=422)
        # An omitted `base_url` leaves the stored one alone. The console no
        # longer offers the field -- the deployment address comes from the URL
        # you drop -- so a save from there must not clear a value someone set
        # in connections-export.toml or an earlier version.
        existing_base = (app.state.editable_settings or {}).get("base_url")
        app.state.editable_settings = {
            "base_url": (
                body.base_url.strip() if body.base_url and body.base_url.strip() else existing_base
            ),
            "auth_mode": body.auth_mode,
            "min_interval": body.min_interval,
            "default_author_filter": (
                body.default_author_filter.strip()
                if body.default_author_filter and body.default_author_filter.strip()
                else None
            ),
            "pdf_style": dict(body.pdf_style),
            "pdf_marks": dict(body.pdf_marks),
            # Omitted means unchanged, exactly as `base_url` above: this is
            # set from a different card than the one the Save buttons sit in.
            "archive_only": (
                bool(body.archive_only)
                if body.archive_only is not None
                else bool((app.state.editable_settings or {}).get("archive_only"))
            ),
        }
        app.state.default_author_filter = app.state.editable_settings["default_author_filter"]
        # To disk, or the next start forgets it.
        save_settings(app.state.editable_settings)
        return get_settings()

    @app.post("/api/pdf-style-preview")
    def pdf_style_preview(body: _StylePreviewRequest) -> Response:
        """A two-page PDF of a fixed sample, styled with the given tokens.

        The point of a preview is to answer "what will 11pt look like" without
        exporting seventy pages to find out. Fixed sample content, so what
        changes between two previews is the setting and nothing else.
        """
        from connections_export.pdf import CHROMIUM_AVAILABLE  # noqa: PLC0415

        if not CHROMIUM_AVAILABLE:
            from connections_export.pdf.browser import browser_unavailable_detail  # noqa: PLC0415

            return JSONResponse({"error": browser_unavailable_detail()}, status_code=503)

        # The SAME renderer the export will use. Previewing with the other one
        # showed a footer the export would not produce -- different content,
        # not just different styling -- which is the one thing a preview must
        # not do.
        from connections_export.pdf import (  # noqa: PLC0415
            PAGED_AVAILABLE,
            render_pdf,
            render_pdf_paged,
        )
        from connections_export.pdf.marks import resolve as resolve_marks  # noqa: PLC0415
        from connections_export.pdf.sample import sample_interchange  # noqa: PLC0415

        renderer = render_pdf_paged if PAGED_AVAILABLE else render_pdf
        try:
            pdf_bytes = renderer(
                sample_interchange(),
                lambda _digest: None,
                marks=resolve_marks(dict(body.pdf_marks) or None),
                # A fixed date: the cover otherwise reads "generation date not
                # provided", which is honest in an export and just noise in a
                # preview of type sizes.
                generated_at="2026-01-01T09:00:00Z",
                style_overrides=dict(body.pdf_style) or None,
            )
        except ValueError as error:  # an unknown token name
            return JSONResponse({"error": str(error)}, status_code=422)
        return Response(content=pdf_bytes, media_type="application/pdf")
