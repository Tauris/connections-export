"""The console's own assets, and the demo URLs it offers."""

from __future__ import annotations

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from connections_export.gui.demo import (
    demo_sample_urls,
)
from connections_export.gui.manual import console_document
from connections_export.gui.support import (
    _VENDOR_FILES,
    CONSOLE_CSS,
    CONSOLE_HTML,
    CONSOLE_JS,
    VENDOR_DIR,
)


def register(
    app,
    *,
    demo: bool,
    demo_seed: int,
    demo_delay: float,
    pdf_renderer,
    author_filter: str | None,
) -> None:
    """Register the static routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    @app.get("/")
    def index() -> HTMLResponse:
        # The manual is rendered from its Markdown source into the page
        # here, before it is sent, so the Manual tab opens with its text
        # already in the document rather than fetching it on first click.
        #
        # no-store: the console's markup and assets change together, and a
        # browser holding a cached copy of one against a fresh copy of the
        # other shows neither reliably.
        return HTMLResponse(console_document(CONSOLE_HTML), headers={"Cache-Control": "no-store"})

    @app.get("/console.css")
    def console_css() -> FileResponse:
        # no-store to match index -- the assets are one document split in
        # three, and they have to be reloaded as one.
        return FileResponse(
            CONSOLE_CSS, media_type="text/css", headers={"Cache-Control": "no-store"}
        )

    @app.get("/console.js")
    def console_js() -> FileResponse:
        return FileResponse(
            CONSOLE_JS, media_type="text/javascript", headers={"Cache-Control": "no-store"}
        )

    @app.get("/vendor/{name}")
    def vendor_asset(name: str) -> Response:
        """Serve a version-pinned vendored asset (pdf.js + its worker) by exact
        name. Only names on the allow-list resolve -- no path traversal, no
        arbitrary static serving. Cached hard since the files are immutable."""
        media_type = _VENDOR_FILES.get(name)
        if media_type is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        return FileResponse(
            VENDOR_DIR / name,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/api/licenses")
    def licenses() -> Response:
        """What ships in this build, and under what terms.

        The same inventory `connections-export licenses` prints. Most people
        never open a terminal, and the obligation does not depend on which
        surface they use.
        """
        from connections_export import sbom  # noqa: PLC0415

        source = sbom.bundled_dir()
        try:
            rows = sbom.read_bundle(source)
        except sbom.SbomError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        return JSONResponse(
            {
                "components": [
                    {
                        "name": row.name,
                        "version": row.version,
                        "license": row.license,
                        "texts": list(row.texts),
                    }
                    for row in rows
                ],
                "notice": (source / sbom.NOTICE_FILENAME).read_text(encoding="utf-8"),
                # What the tool uses and does not distribute. The table above
                # is an account of what ships; taken for an account of what
                # RUNS it would be wrong about the largest piece of all.
                "not_shipped": [
                    {"name": item.name, "why": item.why, "how": item.how}
                    for item in sbom.NOT_SHIPPED
                ],
            }
        )

    @app.get("/api/licenses/sbom")
    def licenses_sbom() -> Response:
        """The machine-readable inventory, for whoever needs to feed it to
        something else."""
        from connections_export import sbom  # noqa: PLC0415

        path = sbom.bundled_dir() / sbom.SBOM_FILENAME
        if not path.is_file():
            return JSONResponse({"detail": "no SBOM in this build"}, status_code=503)
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{sbom.SBOM_FILENAME}"'},
        )

    @app.get("/api/licenses/bundle")
    def licenses_bundle() -> Response:
        """The SBOM, the NOTICE and every licence text, as one zip.

        The same set `connections-export licenses --extract DIR` writes, for
        someone who is in the console rather than a terminal -- and the shape
        an auditor asks for, because it is self-contained.
        """
        import io  # noqa: PLC0415
        import zipfile  # noqa: PLC0415

        from connections_export import sbom  # noqa: PLC0415

        source = sbom.bundled_dir()
        if not (source / sbom.SBOM_FILENAME).is_file():
            return JSONResponse({"detail": "no licence bundle in this build"}, status_code=503)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, str(path.relative_to(source)).replace("\\", "/"))
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="connections-export-licenses.zip"'
            },
        )

    @app.get("/api/licenses/all-texts")
    def licenses_all_texts() -> Response:
        """Every licence in one text file -- the same bytes `licenses --texts`
        prints, from the same renderer."""
        from connections_export import sbom  # noqa: PLC0415

        source = sbom.bundled_dir()
        if not (source / sbom.SBOM_FILENAME).is_file():
            return JSONResponse({"detail": "no licence bundle in this build"}, status_code=503)
        return Response(
            sbom.render_all_texts(source),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="LICENSES.txt"'},
        )

    @app.get("/api/licenses/text")
    def licenses_text(path: str = "") -> Response:
        """One licence text out of the bundle.

        The path comes from the page, but it arrives over HTTP like anything
        else: resolved against the bundle and refused if it lands outside.
        Serving arbitrary files because the caller asked nicely is how a
        read-only viewer becomes a file server.
        """
        from connections_export import sbom  # noqa: PLC0415

        root = sbom.bundled_dir().resolve()
        try:
            target = (root / path).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            return JSONResponse({"status": "not_found"}, status_code=404)
        if not target.is_file():
            return JSONResponse({"status": "not_found"}, status_code=404)
        # Named, so saving from the opened tab does not produce a file called
        # "text" -- the browser takes the name from the URL's last segment
        # otherwise, and this route's last segment is the word "text".
        filename = f"{target.parent.name}-{target.name}" if target.parent != root else target.name
        return Response(
            target.read_text(encoding="utf-8", errors="replace"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @app.get("/api/demo-urls")
    def demo_urls() -> JSONResponse:
        """Sample URLs for the Select & Tailor screen's demo drag-drop
        chips, derived from the fakeserver's own demo
        entities (`connections_export.gui.demo.demo_sample_urls`) -- never
        hardcoded in the client. Only meaningful in demo mode: a
        non-demo server (a real deployment's `hcl-serve`) has no fake
        dataset to draw sample URLs from, so it returns an empty list
        rather than the demo's fake-deployment URLs."""
        # Always offered. They cost nothing to list, they are the only thing
        # to try on a machine with no deployment configured, and gating them
        # on a mode is what made the mode necessary.
        return JSONResponse({"urls": demo_sample_urls()})
