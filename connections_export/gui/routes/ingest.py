"""Reconstruct the open archive into a developer format from the console.

The CLI has long offered `connections-export ingest --format obsidian|jekyll`.
This exposes the same two exporters to the console so someone who never touches
a terminal can produce an Obsidian vault or a Jekyll site from the archive they
are already reading. It writes a folder next to the archive and reports where,
rather than streaming a download: an Obsidian vault and a Jekyll site are both
directory trees the person then opens in their own tool.

Deliberately not on the front of the Reader: PDF is the everyday export, and
these formats are for people who want to re-home the content. The console keeps
them behind an "Advanced" disclosure for exactly that reason.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse

from connections_export.gui.requests import _IngestRequest

#: Where the exporter's output lands, relative to the archive it came from, and
#: how the console names each format. Keyed by the `--format` name the CLI uses.
_FORMATS = {
    "obsidian": {"suffix": "-obsidian-vault", "label": "Obsidian vault"},
    "jekyll": {"suffix": "-jekyll-site", "label": "Jekyll site"},
}


def _summary(fmt: str, stats) -> str:
    """A one-line, human count of what was written, per format."""
    if fmt == "jekyll":
        return (
            f"{stats.posts} post(s), {stats.files} file(s) in "
            f"{stats.libraries} library/libraries, {stats.assets_written} asset(s)"
        )
    return (
        f"{stats.pages} page(s) in {stats.wikis} wiki(s), "
        f"{stats.posts} post(s) in {stats.blogs} blog(s), "
        f"{stats.topics} topic(s) in {stats.forums} forum(s), "
        f"{stats.assets_written} attachment(s)"
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
    """Register the ingest (developer-format export) route on `app`."""

    @app.post("/api/ingest")
    def ingest(body: _IngestRequest) -> JSONResponse:
        """Write the open archive out as an Obsidian vault or a Jekyll site.

        Uses the console's already-loaded model source, so the author filter in
        force (if any) is the export's, exactly as the CLI's `--author` would
        be. The output folder is a sibling of the archive, so it never disturbs
        the append-only capture it came from.
        """
        fmt = (body.format or "").strip().lower()
        spec = _FORMATS.get(fmt)
        if spec is None:
            return JSONResponse({"error": "format must be 'obsidian' or 'jekyll'"}, status_code=422)

        source = app.state.model_source
        if source.get_model() is None:
            return JSONResponse(
                {"error": "open an archive in the Reader first — there is nothing to export yet"},
                status_code=422,
            )
        root = source.package_root()
        if root is None:
            return JSONResponse(
                {"error": "this reading is not backed by an archive on disk to export from"},
                status_code=422,
            )

        root = Path(root)
        # Write beside the archive (or, for a zipped archive that backs the
        # model as a file, beside the zip), under a name derived from its own.
        stem = root.stem if root.is_file() else root.name
        out_dir = root.parent / f"{stem}{spec['suffix']}"

        from connections_export.derive import DeriveError  # noqa: PLC0415
        from connections_export.ingest import from_source_for_format  # noqa: PLC0415

        try:
            stats = from_source_for_format(source, out_dir, fmt)
        except (ValueError, DeriveError) as error:
            return JSONResponse({"error": str(error)}, status_code=422)
        except OSError as error:
            return JSONResponse({"error": f"could not write the export: {error}"}, status_code=500)

        missing = getattr(stats, "assets_missing", 0) or 0
        return JSONResponse(
            {
                "ok": True,
                "format": fmt,
                "label": spec["label"],
                "path": str(out_dir),
                "summary": _summary(fmt, stats),
                "assets_missing": missing,
            }
        )
