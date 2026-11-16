"""What is running, what finished, and the exports already on disk.

The console's activity tray asks here when it loads, so a reload or a second
tab picks up a running capture or PDF export instead of losing sight of it, and
the recent-exports list comes from the `-pdf` folders on disk -- so it
survives a server restart too.

Downloads and "Show in folder" name an export folder and a file by NAME; both
are checked to be an export folder of this tool directly beside the archives
and a plain file name inside it, so no request can reach anything else.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from connections_export.gui import exports

#: Newest first, at most this many.
RECENT_LIMIT = 50


class _RevealRequest(BaseModel):
    folder: str
    name: str | None = None


def _export_folders() -> list[Path]:
    from connections_export.gui import support  # noqa: PLC0415

    base = support.ARCHIVES_BASE
    if not base.is_dir():
        return []
    return [
        child
        for child in base.iterdir()
        if child.is_dir() and child.name.endswith(exports.PDF_FOLDER_SUFFIX)
    ]


def _folder(name: str) -> Path | None:
    """The export folder called `name`, if it is one."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    return next((f for f in _export_folders() if f.name == name), None)


def _entry(folder: Path, path: Path) -> dict:
    is_dir = path.is_dir()
    files = sorted(p.name for p in path.glob("*.pdf")) if is_dir else []
    size = sum(p.stat().st_size for p in path.glob("*.pdf")) if is_dir else path.stat().st_size
    return {
        "folder": folder.name,
        "archive": folder.name[: -len(exports.PDF_FOLDER_SUFFIX)],
        "name": path.name,
        "kind": "set" if is_dir else path.suffix.lower().lstrip("."),
        "files": files,
        "size": size,
        "modified": path.stat().st_mtime,
    }


def register(app, **_kwargs) -> None:
    @app.get("/api/activity")
    def activity() -> JSONResponse:
        """Running and recent work: `{"capture": {"running"}, "pdf": [job]}`
        (jobs as `GET /api/pdf/jobs/{id}` describes them, newest first)."""
        threads = getattr(app.state, "run_threads", []) or []
        jobs = getattr(app.state, "pdf_jobs", None)
        return JSONResponse(
            {
                "capture": {"running": any(t.is_alive() for t in threads)},
                "pdf": [j.as_dict() for j in jobs.recent()] if jobs is not None else [],
            }
        )

    @app.get("/api/contents")
    def contents(largest: int = 10) -> JSONResponse:
        """What the open archive holds, as a reader groups it: communities
        (subcommunities inside), apps, containers -- each with how many items
        and about how many pages and MB a PDF of it would be -- and its
        `largest` items. From the model alone; nothing renders."""
        from connections_export.pdf.split import build_tree  # noqa: PLC0415

        model = app.state.model_source.get_model()
        if model is None:
            return JSONResponse({"status": "pending"}, status_code=503)
        root = build_tree(model, blob_size=app.state.model_source.blob_size)

        def describe(node, depth: int) -> dict:
            out = {
                "level": node.level,
                "title": node.title,
                "items": len(node.item_ids),
                "pages": max(1, round(node.pages)),
                "size": round(node.size),
            }
            if node.level not in ("container", "chunk") and depth < 6:
                out["children"] = [describe(c, depth + 1) for c in node.children]
            return out

        leaves = []

        def collect(node, container: str) -> None:
            if not node.children and node.level == "chunk":
                leaves.append((node, container))
            for child in node.children:
                collect(child, node.title if node.level == "container" else container)

        collect(root, "")
        leaves.sort(key=lambda pair: pair[0].size, reverse=True)
        return JSONResponse(
            {
                "tree": describe(root, 0),
                "largest": [
                    {
                        "title": node.title,
                        "in": container,
                        "pages": max(1, round(node.pages)),
                        "size": round(node.size),
                    }
                    for node, container in leaves[: max(0, largest)]
                ],
            }
        )

    @app.get("/api/exports/recent")
    def recent_exports() -> JSONResponse:
        """The PDF exports on disk beside the archives, newest first: single
        PDFs, ZIPs of sets, and the folders the sets were written to."""
        entries = []
        for folder in _export_folders():
            for path in folder.iterdir():
                if path.is_dir() or path.suffix.lower() in (".pdf", ".zip"):
                    entries.append(_entry(folder, path))
        entries.sort(key=lambda e: e["modified"], reverse=True)
        return JSONResponse({"exports": entries[:RECENT_LIMIT]})

    @app.get("/api/exports/file")
    def export_file(folder: str, name: str) -> Response:
        """Download one export again (a PDF or a set's ZIP)."""
        where = _folder(folder)
        if where is None or "/" in name or "\\" in name or name.startswith("."):
            return JSONResponse({"status": "not_found"}, status_code=404)
        path = where / name
        if not path.is_file() or path.suffix.lower() not in (".pdf", ".zip"):
            return JSONResponse({"status": "not_found"}, status_code=404)
        media = "application/zip" if path.suffix.lower() == ".zip" else "application/pdf"
        return FileResponse(path, media_type=media, filename=path.name)

    @app.post("/api/exports/reveal")
    def reveal_export(body: _RevealRequest) -> JSONResponse:
        """Show an export folder (or a set's folder inside it) in the file
        manager."""
        where = _folder(body.folder)
        if where is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        target = where
        if body.name:
            if "/" in body.name or "\\" in body.name or body.name.startswith("."):
                return JSONResponse({"status": "not_found"}, status_code=404)
            candidate = where / body.name
            if candidate.is_dir():
                target = candidate
        opened = exports.reveal(target)
        return JSONResponse({"opened": opened, "folder": str(target)})
