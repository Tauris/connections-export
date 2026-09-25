"""Reconstruct archives into a developer format from the console.

The CLI offers `connections-export ingest --format obsidian|jekyll|hugo`. This
exposes the same exporters to the console so someone who never touches a
terminal can produce an Obsidian vault, a Jekyll site or Hugo content from the
archive they are already reading -- or from several archives at once, combined
into one export (`derive.combine`). It writes a folder next to the archives
and reports where, rather than streaming a download: each is a directory tree
the person then opens in their own tool.

Deliberately not on the front of the Reader: PDF is the everyday export, and
these formats are for people who want to re-home the content. The console keeps
them behind an "Advanced" disclosure in the Reader and the Archives screen's
selection, for exactly that reason; both open the same guided dialog, which
checks with `dry_run` before it writes.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path

from fastapi.responses import JSONResponse

from connections_export.gui.requests import _IngestRequest

#: Where the exporter's output lands, relative to the archive it came from, and
#: how the console names each format. Keyed by the `--format` name the CLI uses.
_FORMATS = {
    "obsidian": {"suffix": "-obsidian-vault", "label": "Obsidian vault"},
    "jekyll": {"suffix": "-jekyll-site", "label": "Jekyll site"},
    "hugo": {"suffix": "-hugo-content", "label": "Hugo content"},
}

#: Archives one combined export may take. Each is derived in full, so the cap
#: keeps a mis-click on "select all" from deriving hundreds of archives.
_MAX_ARCHIVES = 50

#: Derived archives kept for the export that usually follows a preview, keyed
#: by path, modification times and author filter so a changed archive is
#: derived again.
_CACHE_SIZE = 8


def _summary(fmt: str, stats) -> str:
    """A one-line, human count of what was written, per format -- with how
    much of the page content stayed HTML, when any could."""
    if fmt == "jekyll":
        counted = (
            f"{stats.posts} post(s), {stats.files} file(s) in "
            f"{stats.libraries} library/libraries, {stats.assets_written} asset(s)"
        )
    elif fmt == "hugo":
        counted = (
            f"{stats.pages} page(s) in {stats.wikis} wiki(s), "
            f"{stats.posts} post(s) in {stats.blogs} blog(s), "
            f"{stats.topics} topic(s) in {stats.forums} forum(s), "
            f"{stats.files} file(s), {stats.highlight_pages} Highlights page(s), "
            f"{stats.assets_written} file(s) copied"
        )
    else:
        counted = (
            f"{stats.pages} page(s) in {stats.wikis} wiki(s), "
            f"{stats.posts} post(s) in {stats.blogs} blog(s), "
            f"{stats.topics} topic(s) in {stats.forums} forum(s), "
            f"{stats.assets_written} attachment(s)"
        )
        disambiguated = len(getattr(stats, "disambiguated", None) or [])
        if disambiguated:
            counted += f", {disambiguated} name(s) disambiguated"
    if stats.html_mode == "markdown":
        return counted
    return (
        f"{counted}; page content as {stats.html_mode}: {stats.markdown_blocks} block(s) "
        f"Markdown, {stats.html_blocks} kept as HTML"
    )


def _counts(model) -> dict[str, int]:
    """What an export of `model` will hold, per kind -- the preview's numbers."""
    return {
        "wikis": len(model.wikis),
        "pages": sum(len(wiki.pages) for wiki in model.wikis),
        "blogs": len(model.blogs),
        "posts": sum(len(blog.posts) for blog in model.blogs),
        "forums": len(model.forums),
        "topics": sum(len(forum.topics) for forum in model.forums),
        "libraries": len(model.file_libraries),
        "files": sum(len(library.files) for library in model.file_libraries),
        "highlights": len(model.rich_content),
        "highlight_pages": sum(len(area.pages) for area in model.rich_content),
    }


def _combined_dir(base: Path, names: list[str], suffix: str) -> Path:
    """The folder a combined export of `names` goes to, beside the archives.

    Named for the selection rather than the moment, so the folder a preview
    names is the folder the export then writes -- and exporting the same
    selection again refreshes it, as exporting one archive again does.
    """
    digest = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:8]
    return base / f"combined-{len(names)}-archives-{digest}{suffix}"


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

    derived: OrderedDict = OrderedDict()
    #: Every Hugo export folder written through this console (resolved).
    app.state.hugo_exports = set()

    def archive_source(path: Path):
        """A model source for the archive at `path`, derived once and kept for
        the export that follows its preview."""
        from connections_export.gui.model_source import ModelSource  # noqa: PLC0415

        # The manifest as well as the folder: a run appending to an archive
        # changes the manifest, not the folder's own modification time.
        manifest = path / "manifest.jsonl"
        stamp = manifest.stat().st_mtime_ns if manifest.is_file() else 0
        key = (str(path), path.stat().st_mtime_ns, stamp, author_filter)
        if key in derived:
            derived.move_to_end(key)
            return derived[key]
        source = ModelSource.from_archive(path, author=author_filter)
        derived[key] = source
        while len(derived) > _CACHE_SIZE:
            derived.popitem(last=False)
        return source

    def named_archives(names: list[str]) -> tuple[list[tuple[str, Path]], JSONResponse | None]:
        from connections_export.gui import support  # noqa: PLC0415
        from connections_export.gui.archives import resolve_archive  # noqa: PLC0415

        unique = list(dict.fromkeys(names))
        if not unique:
            return [], JSONResponse({"error": "choose at least one archive"}, status_code=422)
        if len(unique) > _MAX_ARCHIVES:
            return [], JSONResponse(
                {"error": f"at most {_MAX_ARCHIVES} archives can be combined at once"},
                status_code=422,
            )
        found = []
        for name in unique:
            # `resolve_archive` refuses anything but a plain child of the
            # archives folder -- `..`, a separator, an absolute path.
            path = resolve_archive(support.ARCHIVES_BASE, name)
            if path is None:
                return [], JSONResponse(
                    {"error": f"no archive named {name!r} in the archives folder"},
                    status_code=404,
                )
            found.append((name, path))
        return found, None

    @app.post("/api/ingest")
    def ingest(body: _IngestRequest) -> JSONResponse:
        """Write archives out as an Obsidian vault, a Jekyll site or Hugo
        content -- or, with `dry_run`, say what that would write and where.

        Without `archives` this exports the open archive, through the
        console's already-loaded model source, so the author filter in force
        (if any) is the export's, exactly as the CLI's `--author` would be.
        With `archives` it exports those, by name; several are combined into
        one. The output folder is a sibling of the archives, so it never
        disturbs the append-only captures it came from.
        """
        fmt = (body.format or "").strip().lower()
        spec = _FORMATS.get(fmt)
        if spec is None:
            return JSONResponse(
                {"error": "format must be 'obsidian', 'jekyll' or 'hugo'"}, status_code=422
            )
        if body.starter_site and fmt != "hugo":
            return JSONResponse(
                {"error": "the starter site is for Hugo exports only"}, status_code=422
            )
        from connections_export.ingest import (  # noqa: PLC0415
            HTML_MODES,
            STARTER_LAYOUTS,
            html_mode_for,
        )

        # The layout draws the starter site's front page, so it means nothing
        # without one; refused like an unknown value rather than ignored, so a
        # caller never believes it chose something that was not written.
        if body.starter_layout is not None and (
            not body.starter_site or body.starter_layout not in STARTER_LAYOUTS
        ):
            return JSONResponse(
                {
                    "error": "starter_layout must be one of "
                    + ", ".join(STARTER_LAYOUTS)
                    + ", with the starter site"
                },
                status_code=422,
            )
        starter_layout = (body.starter_layout or STARTER_LAYOUTS[0]) if body.starter_site else None

        try:
            html_mode = html_mode_for(fmt, body.html_mode)
        except ValueError:
            return JSONResponse(
                {"error": "html_mode must be one of " + ", ".join(HTML_MODES)}, status_code=422
            )

        from connections_export.archive.source import ArchiveSourceError  # noqa: PLC0415
        from connections_export.derive import DeriveError  # noqa: PLC0415

        report = None
        listed: list[dict] = []
        try:
            if body.archives is not None:
                from connections_export.gui import support  # noqa: PLC0415
                from connections_export.gui.archives import (  # noqa: PLC0415
                    _display_name_for,
                    read_archive_summary,
                )

                found, refusal = named_archives(body.archives)
                if refusal is not None:
                    return refusal
                listed = [
                    {
                        "name": name,
                        "display_name": _display_name_for(path, read_archive_summary(path)),
                    }
                    for name, path in found
                ]
                if len(found) == 1:
                    name, path = found[0]
                    source = archive_source(path)
                    stem = path.stem if path.is_file() else path.name
                    out_dir = path.parent / f"{stem}{spec['suffix']}"
                else:
                    from connections_export.derive.combine import (  # noqa: PLC0415
                        CombinedSource,
                        input_from_source,
                    )

                    source = CombinedSource(
                        [
                            input_from_source(name, archive_source(path), path)
                            for name, path in found
                        ]
                    )
                    report = source.combine_report
                    out_dir = _combined_dir(
                        support.ARCHIVES_BASE, [name for name, _p in found], spec["suffix"]
                    )
            else:
                source = app.state.model_source
                if source.get_model() is None:
                    return JSONResponse(
                        {
                            "error": "open an archive in the Reader first — there is nothing "
                            "to export yet"
                        },
                        status_code=422,
                    )
                root = source.package_root()
                if root is None:
                    return JSONResponse(
                        {"error": "this reading is not backed by an archive on disk to export"},
                        status_code=422,
                    )
                root = Path(root)
                # Write beside the archive (or, for a zipped archive that backs
                # the model as a file, beside the zip), under a name derived
                # from its own.
                stem = root.stem if root.is_file() else root.name
                out_dir = root.parent / f"{stem}{spec['suffix']}"
        except (ValueError, DeriveError, ArchiveSourceError) as error:
            return JSONResponse({"error": str(error)}, status_code=422)

        combine = report.as_dict() if report is not None else None
        if body.dry_run:
            model = source.get_model()
            if model is None:
                return JSONResponse(
                    {"error": "the archive holds no derivable content"}, status_code=422
                )
            return JSONResponse(
                {
                    "ok": True,
                    "dry_run": True,
                    "format": fmt,
                    "label": spec["label"],
                    "path": str(out_dir),
                    "exists": out_dir.exists(),
                    "html_mode": html_mode,
                    "starter_site": body.starter_site,
                    "starter_layout": starter_layout,
                    "archives": listed,
                    "counts": _counts(model),
                    "combine": combine,
                }
            )

        from connections_export.ingest import from_source_for_format  # noqa: PLC0415

        try:
            stats = from_source_for_format(
                source,
                out_dir,
                fmt,
                html_mode=html_mode,
                starter_site=body.starter_site,
                starter_layout=starter_layout,
            )
        except (ValueError, DeriveError) as error:
            return JSONResponse({"error": str(error)}, status_code=422)
        except OSError as error:
            return JSONResponse({"error": f"could not write the export: {error}"}, status_code=500)
        except Exception as error:  # noqa: BLE001 - the reason belongs in the console
            # Any other failure (e.g. a missing optional dependency) must reach
            # the console with its message, not disappear into the terminal as a
            # bare "export failed". The message the exporter raised is the
            # useful part -- surface it verbatim.
            return JSONResponse(
                {"error": str(error) or f"{spec['label']} export failed ({type(error).__name__})"},
                status_code=500,
            )

        if fmt == "hugo":
            # What `POST /api/hugo-preview` may serve: folders this console
            # wrote, wherever the open archive put them (`routes.hugo`).
            app.state.hugo_exports.add(out_dir.resolve())
        summary = _summary(fmt, stats)
        if report is not None:
            summary = f"{report.summary()}; {summary}"
        missing = getattr(stats, "assets_missing", 0) or 0
        return JSONResponse(
            {
                "ok": True,
                "format": fmt,
                "label": spec["label"],
                "path": str(out_dir),
                "summary": summary,
                "assets_missing": missing,
                "html_mode": stats.html_mode,
                "markdown_blocks": stats.markdown_blocks,
                "html_blocks": stats.html_blocks,
                "starter_site": bool(getattr(stats, "starter_site", False)),
                "starter_layout": getattr(stats, "starter_layout", None),
                "archives": listed,
                "combine": combine,
            }
        )
