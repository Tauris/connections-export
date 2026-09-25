"""Preview a Hugo export with the person's own Hugo (`gui.hugo_preview`).

`GET /api/hugo` says whether Hugo is installed, and which preview is
running. `POST /api/hugo-preview` starts `hugo server` for an export folder
and answers with its address, which the console opens in a new tab;
`POST /api/hugo-preview/stop` stops it.

The folder is a path the console's own export reported, and only such a
path is served: `hugo server` runs whatever templates the folder holds, so
the endpoint must not become a way to run Hugo over an arbitrary directory.
Accepted are the folders this console wrote (`app.state.hugo_exports`,
wherever the open archive put them) and Hugo export folders that are plain
children of the archives folder, which is where every export by name or
combined export goes -- so an export written before a restart can still be
previewed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse

from connections_export.gui import hugo_preview
from connections_export.gui.requests import _HugoPreviewRequest

#: The ending every Hugo export folder's name has (`routes.ingest._FORMATS`).
_HUGO_SUFFIX = "-hugo-content"


def preview_folder(app, raw: str) -> Path | None:
    """The export folder `raw` names, if it is one the console may preview;
    otherwise `None`.

    Resolved first -- `..`, symlinks and all -- and judged by where it
    really is: a symlink in the archives folder pointing elsewhere is not in
    the archives folder."""
    from connections_export.gui import support  # noqa: PLC0415

    if not raw or "\x00" in raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        base = Path(support.ARCHIVES_BASE).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_dir():
        return None
    if resolved in app.state.hugo_exports:
        return resolved
    if resolved.parent == base and resolved.name.endswith(_HUGO_SUFFIX):
        return resolved
    return None


def register(app, **_kwargs) -> None:
    """Register the Hugo preview routes on `app`."""
    app.state.hugo_preview = hugo_preview.HugoPreview()
    if not hasattr(app.state, "hugo_exports"):
        app.state.hugo_exports = set()

    @app.get("/api/hugo")
    def hugo_status() -> JSONResponse:
        """Whether Hugo is usable (`{available, version, path, error}`, see
        `hugo_preview.hugo_info`), and the preview running now, if any."""
        return JSONResponse(
            {**hugo_preview.hugo_info(), "preview": app.state.hugo_preview.status()}
        )

    @app.post("/api/hugo-preview")
    def start_preview(body: _HugoPreviewRequest) -> JSONResponse:
        """Serve an export folder with `hugo server`, on its own port."""
        folder = preview_folder(app, body.path)
        if folder is None:
            return JSONResponse(
                {"error": "only a Hugo export this console wrote can be previewed"},
                status_code=403,
            )
        if not (folder / "content").is_dir():
            return JSONResponse(
                {"error": "that folder holds no Hugo content to preview"}, status_code=422
            )
        # No starter site, nothing to render the content with: say how to
        # get one rather than writing files into the folder unasked.
        if not (folder / "hugo.toml").is_file() or not (folder / "layouts").is_dir():
            return JSONResponse(
                {
                    "error": "this export has no starter site to view it with — tick "
                    "“Add a starter site” and export again"
                },
                status_code=422,
            )
        info = hugo_preview.hugo_info()
        if not info["available"]:
            reason = (
                f"Hugo was found at {info['path']} but did not answer `hugo version`"
                if info.get("path")
                else "Hugo is not installed (not found on this computer's PATH), so there "
                "is nothing to preview with"
            )
            return JSONResponse({"error": reason}, status_code=422)
        try:
            url = app.state.hugo_preview.start(folder, info["path"])
        except hugo_preview.PreviewError as error:
            return JSONResponse({"error": str(error)}, status_code=422)
        return JSONResponse({"ok": True, "url": url, "path": str(folder)})

    @app.post("/api/hugo-preview/stop")
    def stop_preview() -> JSONResponse:
        """Stop the running preview, if there is one."""
        return JSONResponse({"ok": True, "stopped": app.state.hugo_preview.stop()})
