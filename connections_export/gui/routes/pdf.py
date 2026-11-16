"""Rendering a PDF -- from the derived model, or live from the deployment."""

from __future__ import annotations

import datetime
import inspect
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Query
from fastapi.responses import FileResponse, JSONResponse, Response

from connections_export.config import load_config
from connections_export.gui import exports
from connections_export.gui.pdf_jobs import (
    PdfExportCancelled,
    PdfJob,
    PdfJobFailed,
    PdfJobs,
)
from connections_export.pdf.progress import PdfOutOfMemoryError


@dataclass
class _Plan:
    """What a PDF request resolved to: the model and how to render it."""

    model: Any
    renderer: Any
    render_kwargs: dict[str, Any]
    part_size_mb: float | None
    fidelity: str
    scope: str
    kind: str | None
    item_id: str | None


def _signature_params(func) -> set[str]:
    try:
        return set(inspect.signature(func).parameters)
    except (TypeError, ValueError):
        return set()


def _write_pdf_failure_report(
    *,
    app,
    model,
    fidelity: str,
    scope: str,
    kind: str | None,
    item_id: str | None,
    timeout: float,
    exc: Exception,
) -> Path | None:
    """Write a durable Markdown report for a failed PDF render."""
    root = app.state.model_source.package_root()
    report_dir = root if root is not None and root.is_dir() else Path.cwd() / "pdf-failure-reports"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        path = report_dir / f"pdf-failure-{stamp}.md"
        failures = list(dict.fromkeys(getattr(exc, "failures", []) or []))
        links: list[str] = []

        def collect(value) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"source_url", "alternate_url", "url"} and isinstance(child, str):
                        links.append(child)
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(model.model_dump(mode="json"))
        links = list(dict.fromkeys(link for link in links if link))
        lines = [
            "# PDF export failure",
            "",
            f"- Time (UTC): `{stamp}`",
            f"- Renderer fidelity: `{fidelity}`",
            f"- Scope: `{scope}`",
            f"- Kind: `{kind or ''}`",
            f"- Item ID: `{item_id or ''}`",
            f"- Archive/package: `{root or ''}`",
            f"- Exception: `{type(exc).__name__}`",
            f"- Maximum wait: `{timeout:g} seconds`",
            "",
            "## Error",
            "",
            "```text",
            str(exc) or "(no exception message)",
            "```",
            "",
            "## Failed browser resources",
            "",
        ]
        if failures:
            lines.extend(f"- {failure}" for failure in failures)
        else:
            lines.append("- No individual browser resource failure was captured.")
        lines.extend(["", "## Source links in the rendered model", ""])
        if links:
            lines.extend(f"- <{link}>" for link in links[:500])
        else:
            lines.append("- No source links were present in the rendered model.")
        lines.extend(["", "## Traceback", "", "```text", traceback.format_exc(), "```", ""])
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except (OSError, TypeError, ValueError):
        return None


#: Measured: 3,263 pages in about 190 s laid out in parts -- 0.06 s a page --
#: plus a fixed cost for each file (starting the browser, joining).
SECONDS_PER_PAGE = 0.06
SECONDS_PER_FILE = 8.0


def preflight(estimated_size: float, folder: Path, *, browser_detail: str | None) -> list[dict]:
    """What could stop or slow this export, said before it starts:
    `[{"level": "error"|"warning", "message"}]`. An error means it would fail."""
    from connections_export.pdf import memory  # noqa: PLC0415

    checks: list[dict] = []
    if browser_detail:
        checks.append({"level": "error", "message": browser_detail})
    # The PDF itself, the parts while they are joined, and the images while
    # the browser reads them -- about three times the result, briefly.
    needed = int(estimated_size * 3) + 50 * 2**20
    free = memory.free_disk(folder)
    if free is not None and free < needed:
        checks.append(
            {
                "level": "error",
                "message": (
                    f"Not enough disk space: the export needs about {_size(needed)} while it"
                    f" runs, and the disk holding {folder} has {_size(free)} free."
                    " Free some space, or move the archive to a larger disk."
                ),
            }
        )
    available = memory.available_memory()
    if available is not None and available < memory.LOW_MEMORY_BYTES:
        checks.append(
            {
                "level": "warning",
                "message": (
                    f"Only {_size(available)} of memory is free. The export will be laid out"
                    " in small parts and take longer; closing other programs helps."
                ),
            }
        )
    return checks


def _size(n: float) -> str:
    gb = n / 2**30
    return f"{gb:.1f} GB" if gb >= 1 else f"{n / 2**20:.0f} MB"


def estimated_seconds(pages: float, files: int = 1) -> int:
    """About how long an export of `pages` takes, in `files` files."""
    return round(pages * SECONDS_PER_PAGE + files * SECONDS_PER_FILE)


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

    def prepare(
        *,
        fidelity: str,
        comments: bool,
        scope: str,
        kind: str | None,
        id: str | None,
        include: list[str] | None,
        chrome: bool,
        open_bookmarks: bool,
    ):
        """The model to render and the renderer to use, or the error response.

        Shared by `GET /api/pdf` and `POST /api/pdf/jobs`, so both answer an
        empty scope, a missing model or a missing browser identically."""
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
        part_size_mb: float | None = None
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
                if renderer is render_pdf_paged:
                    render_kwargs["pdf_timeout"] = float(
                        app.state.editable_settings.get("pdf_timeout", 120.0)
                    )
                    configured = app.state.editable_settings.get("pdf_part_size_mb")
                    # None: the renderer works it out from the memory free then.
                    part_size_mb = None if configured in (None, "") else float(configured)
                    # The bookmarks are always written; whether viewers open
                    # with the panel showing is the reader's choice.
                    if open_bookmarks:
                        render_kwargs["open_bookmarks"] = True
        return _Plan(
            model=model,
            renderer=renderer,
            render_kwargs=render_kwargs,
            part_size_mb=part_size_mb,
            fidelity=fidelity,
            scope=scope,
            kind=kind,
            item_id=id,
        )

    def external_kwargs(plan: _Plan, external_images: bool) -> dict[str, Any]:
        """Third-party images, fetched for just what is exported -- slow, so
        a job does it on its worker thread, not inside the request."""
        if pdf_renderer is not None:
            return {}
        from connections_export.pdf.external import prepare_external_images  # noqa: PLC0415

        # Fetched here, never by the browser: a third-party host that does
        # not answer costs one bounded request, not the whole render timeout.
        return {
            "external_images": prepare_external_images(plan.model, include=external_images),
            "small_image_px": int(app.state.editable_settings.get("pdf_small_image_px", 48)),
        }

    def blob_bytes(digest: str) -> bytes | None:
        result = app.state.model_source.get_blob(digest)
        return result[0] if result is not None else None

    def failure_detail(plan: _Plan, exc: Exception) -> str:
        """The sentence for a failed render, plus the written report's path."""
        error_type = type(exc).__name__
        # The person reads the first part; what support needs follows
        # "Technical details:", which the console moves behind "Copy details".
        technical = f"PDF rendering failed ({error_type}) using {plan.fidelity} fidelity."
        # The paged renderer wraps the browser's timeout in its own error.
        timed_out = "TimeoutError" in (error_type, type(exc.__cause__).__name__)
        if timed_out:
            detail = (
                "The PDF stopped making progress and was given up on. Try again, export it as "
                "several smaller PDFs, or give it longer under Settings, Advanced, Render timeout."
            )
            technical += (
                " The browser did not finish loading or paginating the document within"
                " the render timeout."
            )
            if exc.__cause__ is not None:
                technical += f" {exc}"
        else:
            detail = (
                "The PDF could not be made. Try again; if it keeps failing, export it as "
                "several smaller PDFs."
            )
            technical += f" {exc}" if str(exc) else " See the server log for the full traceback."
        report = _write_pdf_failure_report(
            app=app,
            model=plan.model,
            fidelity=plan.fidelity,
            scope=plan.scope,
            kind=plan.kind,
            item_id=plan.item_id,
            timeout=float(app.state.editable_settings.get("pdf_timeout", 120.0)),
            exc=exc,
        )
        if report is not None:
            detail += " A report for support was saved."
            technical += f" Failure report: {report}"
        print(f"connections-export: {detail} {technical}", flush=True)
        return f"{detail}\n\nTechnical details: {technical}"

    pdf_headers = {"Content-Disposition": 'attachment; filename="wiki-export.pdf"'}

    def _archives_base() -> Path:
        from connections_export.gui import support  # noqa: PLC0415

        return support.ARCHIVES_BASE

    @app.get("/api/pdf")
    def get_pdf(
        fidelity: str = "portable",
        comments: bool = True,
        scope: str = "all",
        kind: str | None = None,
        id: str | None = None,
        include: Annotated[list[str] | None, Query()] = None,
        chrome: bool = True,
        external_images: bool = True,
        open_bookmarks: bool = False,
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

        `external_images=0` leaves third-party images out (each keeps its
        address as text). By default they are fetched now, embedded, marked
        where they sit and listed on an "External content" page -- the
        person exporting decides whether that is theirs to reproduce.

        `chrome=0` drops the cover + TOC, rendering just the body -- used by
        the live preview's per-entity tiles, where each entity is rendered on
        its own and a repeated cover per tile would be noise. It forces the
        portable (paged) renderer, the only path that honours it.

        `pdf_renderer` (a `make_app` kwarg) is injected by tests so no
        real browser is launched; unset in production."""
        plan = prepare(
            fidelity=fidelity,
            comments=comments,
            scope=scope,
            kind=kind,
            id=id,
            include=include,
            chrome=chrome,
            open_bookmarks=open_bookmarks,
        )
        if isinstance(plan, Response):
            return plan
        kwargs = {**plan.render_kwargs, **external_kwargs(plan, external_images)}
        if plan.part_size_mb is not None:
            kwargs["part_size_mb"] = plan.part_size_mb
        try:
            pdf = plan.renderer(plan.model, blob_bytes, **kwargs)
        except Exception as exc:  # noqa: BLE001 - convert renderer failures to a useful API error
            return JSONResponse(
                {"status": "pdf_error", "detail": failure_detail(plan, exc)}, status_code=500
            )
        return Response(content=pdf, media_type="application/pdf", headers=pdf_headers)

    app.state.pdf_jobs = PdfJobs()

    def archive_label() -> str:
        """The open archive's name, for file names."""
        from connections_export.pdf.split import safe_filename  # noqa: PLC0415

        root = app.state.model_source.package_root()
        if root is None:
            return "export"
        return safe_filename(root.stem if root.suffix.lower() == ".zip" else root.name, 60)

    def output_folder() -> Path:
        from connections_export.gui import support  # noqa: PLC0415

        return exports.pdf_folder(app.state.model_source.package_root(), support.ARCHIVES_BASE)

    def split_limits(split: str, max_pages: int | None, max_mb: float | None):
        """The `pdf.split.Limits` a request asks for, or None for one PDF."""
        from connections_export.pdf.split import GROUPINGS, Limits  # noqa: PLC0415

        if split in ("", "none", "0", "false"):
            return None
        if split not in GROUPINGS:
            raise ValueError(f"split must be none or one of {', '.join(GROUPINGS)}")
        return Limits(
            max_pages=max_pages if max_pages and max_pages > 0 else None,
            max_mb=max_mb if max_mb and max_mb > 0 else None,
            grouping=split,
        )

    @app.get("/api/pdf/plan")
    def plan_pdf(
        comments: bool = True,
        scope: str = "all",
        kind: str | None = None,
        id: str | None = None,
        include: Annotated[list[str] | None, Query()] = None,
        split: str = "none",
        max_pages: int | None = None,
        max_mb: float | None = None,
    ) -> Response:
        """What an export would make, before it is made: the whole export's
        estimated pages, PDF size and time, and -- with `split` (auto,
        community, app or container) and optional `max_pages` / `max_mb` --
        the proposed files. Estimated from the model alone; nothing renders.

        `{"estimate": {pages, size, seconds}, "files": [{name, title, pages,
        size, contents}], "folder": where the export will be written}`."""
        from connections_export.pdf.split import build_tree, propose  # noqa: PLC0415

        plan = prepare(
            fidelity="portable",
            comments=comments,
            scope=scope,
            kind=kind,
            id=id,
            include=include,
            chrome=True,
            open_bookmarks=False,
        )
        if isinstance(plan, Response) and plan.status_code != 503:
            return plan
        model = plan.model if not isinstance(plan, Response) else app.state.model_source.get_model()
        if model is None:
            return plan
        browser_detail = None
        if isinstance(plan, Response):
            # No browser: plan anyway, and say so first.
            import json  # noqa: PLC0415

            browser_detail = json.loads(plan.body).get("detail")
        try:
            limits = split_limits(split, max_pages, max_mb)
        except ValueError as exc:
            return JSONResponse({"status": "bad_split", "detail": str(exc)}, status_code=422)
        blob_size = app.state.model_source.blob_size
        root = build_tree(model, blob_size=blob_size)
        files = propose(model, limits, blob_size=blob_size) if limits is not None else []
        folder = exports.pdf_folder(app.state.model_source.package_root(), _archives_base())
        return JSONResponse(
            {
                "estimate": {
                    "pages": max(1, round(root.pages)),
                    "size": round(root.size),
                    "seconds": estimated_seconds(root.pages, max(1, len(files))),
                },
                "files": [
                    {
                        **f.as_dict(),
                        "seconds": estimated_seconds(f.pages, 1),
                    }
                    for f in files
                ],
                "folder": str(folder),
                # What one PDF would be called (several are listed in files).
                "single_name": f"{archive_label()} {exports.stamp()}.pdf",
                "checks": preflight(root.size, folder, browser_detail=browser_detail),
            }
        )

    @app.post("/api/pdf/jobs")
    def start_pdf_job(
        fidelity: str = "portable",
        comments: bool = True,
        scope: str = "all",
        kind: str | None = None,
        id: str | None = None,
        include: Annotated[list[str] | None, Query()] = None,
        chrome: bool = True,
        external_images: bool = True,
        open_bookmarks: bool = False,
        attach_attachments: bool = False,
        split: str = "none",
        max_pages: int | None = None,
        max_mb: float | None = None,
    ) -> Response:
        """Start the same render as `GET /api/pdf` in the background and
        answer `{"job": id}` at once; poll `GET /api/pdf/jobs/{id}`. The same
        4xx/503 answers as the GET come back here, before anything starts.
        `409` while another PDF is still being made.

        The result is written beside the archive (`<archive>-pdf/`); with
        `split` (and `max_pages` / `max_mb`) it is several PDFs in a folder
        of their own plus a ZIP of them for the download. `attach_attachments`
        attaches the pages' attachments to the PDF as files."""
        plan = prepare(
            fidelity=fidelity,
            comments=comments,
            scope=scope,
            kind=kind,
            id=id,
            include=include,
            chrome=chrome,
            open_bookmarks=open_bookmarks,
        )
        if isinstance(plan, Response):
            return plan
        try:
            limits = split_limits(split, max_pages, max_mb)
        except ValueError as exc:
            return JSONResponse({"status": "bad_split", "detail": str(exc)}, status_code=422)
        params = _signature_params(plan.renderer)
        if limits is not None and "member" not in params and pdf_renderer is None:
            return JSONResponse(
                {
                    "status": "bad_split",
                    "detail": "Several PDFs need the portable renderer (paged.js).",
                },
                status_code=422,
            )
        if pdf_renderer is None:
            from connections_export.pdf.split import build_tree  # noqa: PLC0415

            estimate = build_tree(plan.model, blob_size=app.state.model_source.blob_size)
            blocking = [
                c
                for c in preflight(estimate.size, output_folder(), browser_detail=None)
                if c["level"] == "error"
            ]
            if blocking:
                return JSONResponse(
                    {"status": "preflight", "detail": blocking[0]["message"]}, status_code=507
                )
        label = archive_label()
        when = exports.stamp()

        def work(job: PdfJob) -> None:
            try:
                kwargs = {**plan.render_kwargs, **external_kwargs(plan, external_images)}
                if plan.part_size_mb is not None:
                    kwargs["part_size_mb"] = plan.part_size_mb
                if attach_attachments and "attach_attachments" in params:
                    kwargs["attach_attachments"] = True
                # Renderers that do not report progress (and test doubles
                # keeping the (model, blob_bytes) contract) are not given it.
                if "progress" in params:
                    kwargs["progress"] = job.report
                folder = output_folder()
                if limits is None:
                    path = exports.unique(folder / f"{label} {when}.pdf")
                    # Written straight to its file by a renderer that can: never
                    # held in memory. One that returns bytes has them written.
                    if "output" in params:
                        kwargs["output"] = path
                    result = plan.renderer(plan.model, blob_bytes, **kwargs)
                    if result is not None:
                        path.write_bytes(result)
                    job.files, job.download, job.folder = [path], path, folder
                    return
                from connections_export.pdf import sets  # noqa: PLC0415
                from connections_export.pdf import split as pdf_split  # noqa: PLC0415

                files = pdf_split.propose(
                    plan.model, limits, blob_size=app.state.model_source.blob_size
                )
                kwargs.pop("progress", None)
                # Inside the archive's own -pdf folder, the archive's name would
                # only repeat; the ZIP, which travels, keeps it.
                target = exports.unique(folder / f"{when} – {len(files)} PDFs")
                paths = sets.render_pdf_set(
                    plan.model, blob_bytes, files, target, progress=job.report, **kwargs
                )
                job.files, job.folder = paths, target
                job.download = sets.zip_set(paths, exports.unique(folder / f"{label} {when}.zip"))
            except (PdfExportCancelled, PdfOutOfMemoryError):
                raise
            except Exception as exc:  # noqa: BLE001
                raise PdfJobFailed(failure_detail(plan, exc)) from exc

        def guarded(job: PdfJob) -> None:
            try:
                return work(job)
            except PdfOutOfMemoryError as exc:
                print(f"connections-export: {exc}", flush=True)
                raise PdfJobFailed(str(exc), out_of_memory=True) from exc

        what = (
            "Chosen components"
            if include
            else ("The open item" if scope in ("item", "tile") else "Whole archive")
        )
        how = {
            None: "one PDF",
            "auto": "several PDFs",
            "community": "one PDF per community",
            "app": "one PDF per app",
            "container": "one PDF per container",
        }[limits.grouping if limits is not None else None]
        job = app.state.pdf_jobs.start(guarded, title=f"{what} — {how}", archive=label)
        if job is None:
            return JSONResponse(
                {
                    "status": "busy",
                    "detail": (
                        "Another PDF export is still running. Wait for it to finish, or cancel it."
                    ),
                },
                status_code=409,
            )
        return JSONResponse({"job": job.id})

    def find_job(job_id: str):
        return app.state.pdf_jobs.get(job_id)

    def no_job() -> JSONResponse:
        return JSONResponse(
            {"status": "unknown_job", "detail": "No such PDF export (it may have expired)."},
            status_code=404,
        )

    @app.get("/api/pdf/jobs/{job_id}")
    def get_pdf_job(job_id: str) -> Response:
        """`{"state": running|done|failed|cancelled, "progress": the latest
        PdfProgress dict or null, "elapsed_s", "detail", "out_of_memory"}`."""
        job = find_job(job_id)
        return no_job() if job is None else JSONResponse(job.as_dict())

    @app.get("/api/pdf/jobs/{job_id}/file")
    def get_pdf_job_file(job_id: str) -> Response:
        """The finished PDF, as `GET /api/pdf` returns it; `409` until done."""
        job = find_job(job_id)
        if job is None:
            return no_job()
        if job.state != "done" or job.download is None or not Path(job.download).is_file():
            return JSONResponse(
                {"status": "not_ready", "detail": "The PDF is not finished."}, status_code=409
            )
        download = Path(job.download)
        zipped = download.suffix.lower() == ".zip"
        return FileResponse(
            download,
            media_type="application/zip" if zipped else "application/pdf",
            filename=download.name,
        )

    @app.post("/api/pdf/jobs/{job_id}/reveal")
    def reveal_pdf_job(job_id: str) -> Response:
        """Show the finished export in the system's file manager. Only the
        folder this job wrote can be opened -- the request names a job, never
        a path."""
        job = find_job(job_id)
        if job is None:
            return no_job()
        if job.folder is None or not Path(job.folder).is_dir():
            return JSONResponse(
                {"status": "not_ready", "detail": "The export has no folder yet."}, status_code=409
            )
        opened = exports.reveal(Path(job.folder))
        return JSONResponse({"opened": opened, "folder": str(job.folder)})

    @app.delete("/api/pdf/jobs/{job_id}")
    def cancel_pdf_job(job_id: str) -> Response:
        """Cancel: the render stops at its next progress report."""
        job = find_job(job_id)
        if job is None:
            return no_job()
        app.state.pdf_jobs.cancel(job)
        return JSONResponse(job.as_dict())

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
        or `503 {"status":"unavailable"}` if all pages fail.
        `400 {"status":"not_web_url"}` for a `url` that is not http(s): the
        browser carries the session and can read local files, so it only
        ever visits web pages."""
        from connections_export.gui.routes._lookup import (  # noqa: PLC0415
            UntrustedDeployment,
            bound_live_cookies,
            is_trusted_deployment,
            live_cookie_jar,
            untrusted_response,
        )
        from connections_export.pdf.live import navigable  # noqa: PLC0415

        if url and not navigable(url):
            return JSONResponse(
                {
                    "status": "not_web_url",
                    "detail": "The live PDF renders web pages; give it an http(s) address.",
                },
                status_code=400,
            )
        # The dropped URL is fetched with the session and opened in a browser
        # carrying it; a GET's `url` can come from a captured page the Reader
        # shows, so the host must be one the user chose.
        if url and not is_trusted_deployment(app, url):
            return untrusted_response(UntrustedDeployment(url))

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
                        # Domain-bound: a bare {name: value} mapping went
                        # to whatever host the dropped URL named.
                        resp = httpx.get(
                            feed + "?ps=200&page=0",
                            cookies=live_cookie_jar(app),
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

        # Addresses recorded in an archive are data, and the browser visiting
        # them carries the session: only a deployment the user chose.
        untrusted = [u for u in live_urls if not is_trusted_deployment(app, u)]
        live_urls = [u for u in live_urls if is_trusted_deployment(app, u)]
        if untrusted and not live_urls:
            return untrusted_response(UntrustedDeployment(untrusted[0]))

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
        # Each cookie with its domain, so the browser offers it to its own
        # host only (and Playwright accepts it at all).
        result = render_live_pdf(live_urls, cookies=bound_live_cookies(app) or None, proxy=decision)
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
