"""Listing, opening and deleting archives on disk."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from connections_export.archive.source import ArchiveSourceError
from connections_export.derive import DeriveError
from connections_export.gui import support
from connections_export.gui.archives import (
    SUMMARY_FILENAME,
    list_archives,
    read_archive_summary,
    resolve_archive,
    summarize_archive,
    superseded_names,
    writable_archive,
)
from connections_export.gui.model_source import ModelSource
from connections_export.gui.requests import (
    _ArchiveDeleteRequest,
    _ArchiveLabelRequest,
    _ArchivesDirRequest,
    _ArchiveZipRequest,
    _BulkDeleteRequest,
    _BulkRepairRequest,
    _OpenArchiveRequest,
    _OpenExternalRequest,
    _SelectedDeleteRequest,
)
from connections_export.gui.routes._lookup import (
    _authed_lookup,
    _lookup_base_auth,
)
from connections_export.gui.support import (
    BULK_DELETE_CONFIRMATION,
    _community_uuid_of,
    _demo_community_components,
)


def _remove_archive(target: Path) -> None:
    """Remove an archive, whichever shape it has.

    A zipped archive is read-only, but deleting one is not a write into it --
    it removes a file the user themselves put in the archives folder, and
    refusing would leave them with something they can see, cannot use and
    cannot get rid of.
    """
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _fetch_url(url: str, *, timeout: float = 120.0) -> bytes:
    """Download `url`.

    At module level rather than inside `register` so a test can stand in for
    the network -- a closure is unreachable from outside, and the alternative
    is a test that really goes out to the internet.
    """
    import httpx  # noqa: PLC0415

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _blog_comment_count(archive_dir) -> int | None:
    """How many blog comments an archive holds, or None if it can't be read.

    Used to measure a repair: the count before and after, so the console can
    say how many were recovered rather than only that it ran. Best-effort --
    a repair that succeeds is not undone by an unreadable count.
    """
    try:
        from connections_export.gui.model_source import ModelSource  # noqa: PLC0415

        model = ModelSource.from_archive(archive_dir).get_model()
        if model is None:
            return None
        return sum(len(post.comments) for blog in model.blogs for post in blog.posts.values())
    except Exception:  # noqa: BLE001 - a count is a courtesy, never the operation
        return None


def register(
    app,
    *,
    demo: bool,
    demo_seed: int,
    demo_delay: float,
    pdf_renderer,
    author_filter: str | None,
) -> None:
    """Register the archives routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    #: The FastAPI app under a name no route parameter shadows -- `community_of`
    #: has a query parameter called `app` (the HCL app kind).
    _app = app

    @app.get("/api/archives")
    def get_archives() -> JSONResponse:
        """Recent run archives under ARCHIVES_BASE (newest first) so the
        console can offer 'open an existing archive' without the CLI.
        `archives_dir` rides along so the console's capped recent
        list can name where the rest live without a second round-trip
        to `/api/settings`."""
        infos = list_archives(support.ARCHIVES_BASE)
        # Which runs a later attempt at the same target has replaced -- the
        # pile that repeated attempts produce, and what "select superseded"
        # offers to clear. Computed from persisted summaries only (see
        # `archive_target_key`); an archive of unknown content is never
        # included.
        superseded = superseded_names(infos)
        return JSONResponse(
            {
                "archives": [
                    {
                        "name": a.name,
                        "is_demo": a.is_demo,
                        "is_superseded": a.name in superseded,
                        "mtime": a.mtime,
                        "display_name": a.display_name,
                        "item_count": a.item_count,
                        "repair_needed": a.repair_needed,
                        "repair_reason": a.repair_reason,
                        "summary": read_archive_summary(a.path),
                    }
                    for a in infos
                ],
                "archives_dir": str(support.ARCHIVES_BASE),
            }
        )

    @app.get("/api/repair-status")
    def repair_status() -> JSONResponse:
        """How a running or just-finished repair is going -- so the console
        can show progress and, at the end, how many comments were recovered
        and that every archive is now up to date, rather than a bare
        "started"."""
        job = getattr(_app.state, "repair_job", None)
        if job is None:
            return JSONResponse({"state": "idle"})
        return JSONResponse(job)

    @app.post("/api/repair-stop")
    def repair_stop() -> JSONResponse:
        """Stop a running repair -- for when it goes awry (too slow, too many
        feeds). It finishes the archive it is on, so what that one already
        fetched is kept, and does not start the remaining archives."""
        event = getattr(_app.state, "repair_stop_event", None)
        if event is None or not getattr(_app.state, "repair_in_progress", False):
            return JSONResponse({"stopped": False, "detail": "no repair is running"})
        event.set()
        job = getattr(_app.state, "repair_job", None)
        if job is not None:
            job["stage"] = "stopping…"
        return JSONResponse({"stopped": True})

    @app.post("/api/repair-archives")
    def repair_archives(body: _BulkRepairRequest) -> JSONResponse:
        """Repair every affected archive, measuring what each one recovered.

        Names are taken as the selection, but each is re-checked against the
        server's own list -- so "repair all" is exactly the archives that
        actually need it, whatever the page showed.
        """
        names = list(dict.fromkeys(body.names or []))
        if not names or body.confirmation != str(len(names)):
            return JSONResponse(
                {"error": "the archive selection confirmation is invalid"}, status_code=422
            )
        if getattr(_app.state, "repair_in_progress", False):
            return JSONResponse(
                {"error": "another archive repair is already running"}, status_code=409
            )
        infos = {info.name: info for info in list_archives(support.ARCHIVES_BASE)}
        targets = []
        for name in names:
            info = infos.get(name)
            if info is None or not info.repair_needed:
                return JSONResponse(
                    {"error": f"{name} is not detected as needing repair"}, status_code=422
                )
            if not writable_archive(info.path):
                return JSONResponse({"error": f"{name} is read-only"}, status_code=422)
            targets.append((name, info.path))

        _app.state.repair_job = {
            "state": "running",
            "total": len(targets),
            "done": 0,
            "comments_restored": 0,
            "current": targets[0][0] if targets else None,
            "stage": "starting",
            # Progress dimensions kept SEPARATE (hand-over #3): aggregate index
            # pages and per-post comment feeds are different work and must not be
            # summed into one counter, and posts are counted for THIS run from
            # its own events -- never from the cumulative manifest.
            "posts_total": 0,  # blog posts in the archive (the upper bound)
            "posts_done": 0,  # posts re-derived this run (unique, event-counted)
            "aggregate_pages": 0,  # aggregate comment-index pages read this run
            "post_feeds_done": 0,  # per-post comment feeds fetched this run
            "comments_recovered": 0,  # comments seen on the current archive
            "min_interval": None,  # effective pacing, and where it came from
            "config_source": None,
            "stopped": False,  # set true when the user stops it early
            "results": [],
        }
        # A fresh stop signal for this repair, settable by POST /api/repair-stop.
        # Threaded through crawl_main so a run that goes awry can be halted.
        _app.state.repair_stop_event = threading.Event()

        def run_repairs() -> None:
            job = _app.state.repair_job
            stop_event = _app.state.repair_stop_event
            try:
                import json  # noqa: PLC0415
                from datetime import UTC, datetime  # noqa: PLC0415

                from connections_export.cli import crawl_main  # noqa: PLC0415
                from connections_export.config import (  # noqa: PLC0415
                    discover_config_file,
                    load_config,
                )
                from connections_export.crawler import events as crawler_events  # noqa: PLC0415

                # Pacing visibility (hand-over #3 §4): a repair fetches many
                # feeds, and when the executable is launched away from the repo
                # it finds no `connections-export.toml` and paces at the 1s
                # default. Surface the effective interval and where it came from.
                try:
                    cfg = load_config({}, env=os.environ)
                    src = discover_config_file(env=os.environ)
                    job["min_interval"] = cfg.min_interval
                    job["config_source"] = str(src) if src else "built-in defaults"
                except Exception:  # noqa: BLE001 - pacing info is a courtesy
                    pass

                for name, target in targets:
                    job["current"] = name
                    summary = read_archive_summary(target) or {}
                    job["stage"] = "counting before repair"
                    job["posts_done"] = 0
                    job["posts_total"] = sum(
                        int(group.get("count") or 0)
                        for group in summary.get("groups", [])
                        if group.get("kind") in {"blog", "ideation_blog"}
                    )
                    job["aggregate_pages"] = 0
                    job["post_feeds_done"] = 0
                    job["comments_recovered"] = 0
                    before = _blog_comment_count(target)
                    job["stage"] = "reading aggregate index"

                    def emit(event, job=job):
                        # Two separate dimensions, told apart by URL: the blog's
                        # aggregate comment index vs. a single post's
                        # `entrycomments` feed. Never summed together.
                        if isinstance(event, crawler_events.Fetched) and event.kind == "comments":
                            if "entrycomments" in (event.url or ""):
                                job["post_feeds_done"] += 1
                                if job["stage"] == "reading aggregate index":
                                    job["stage"] = "fetching affected post feeds"
                            else:
                                job["aggregate_pages"] += 1
                        # Unique posts for THIS run, from its own derive events.
                        if isinstance(event, crawler_events.BlogPostDerived):
                            job["posts_done"] += 1
                            job["comments_recovered"] += event.comment_count

                    ok = (
                        crawl_main(
                            ["--repair", "--into", str(target)],
                            env=os.environ,
                            emit=emit,
                            stop_event=stop_event,
                        )
                        == 0
                    )
                    job["stage"] = "counting after repair"
                    after = _blog_comment_count(target)
                    restored = (
                        after - before
                        if before is not None and after is not None and after >= before
                        else None
                    )
                    if restored:
                        job["comments_restored"] += restored
                    result = {
                        "name": name,
                        "ok": ok,
                        "comments_restored": restored,
                        "aggregate_pages": job["aggregate_pages"],
                        "post_feeds_done": job["post_feeds_done"],
                        "posts_done": job["posts_done"],
                        "posts_total": job["posts_total"],
                        "min_interval": job["min_interval"],
                        "config_source": job["config_source"],
                        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    job["results"].append(result)
                    # Durable report (hand-over #3 §5): survives a GUI reload and
                    # makes a restarted repair understandable afterward.
                    try:
                        (target / "repair-report.json").write_text(
                            json.dumps(result, indent=2), encoding="utf-8"
                        )
                    except OSError:
                        pass
                    job["done"] += 1
                    # Stop the whole repair here if the user asked, rather than
                    # rolling on to the next archive. The archive just finished
                    # keeps what it recovered; the rest are simply not started.
                    if stop_event.is_set():
                        job["stopped"] = True
                        break
            except Exception as exc:  # noqa: BLE001 - the job records its own failure
                job["error"] = str(exc)[:300]
            finally:
                job["current"] = None
                job["stage"] = "stopped" if job.get("stopped") else "complete"
                job["state"] = "done"
                _app.state.repair_in_progress = False

        _app.state.repair_in_progress = True
        threading.Thread(target=run_repairs, name="archive-repair", daemon=True).start()
        return JSONResponse({"status": "started", "count": len(targets)})

    @app.put("/api/archives-dir")
    def set_archives_dir(body: _ArchivesDirRequest) -> JSONResponse:
        """Point the console at a different archives directory.

        It was an environment variable and a read-only line of text, which
        meant changing it required knowing what an environment variable is and
        restarting the process, in that order.

        Nothing is created here. A path with a typo in it would otherwise
        become an empty archives directory, and the archives someone was
        looking for would become "none found" -- which reads as data loss even
        though nothing was lost.
        """
        raw = (body.path or "").strip()
        if not raw:
            return JSONResponse({"error": "give a directory"}, status_code=422)
        candidate = Path(raw).expanduser()
        if not candidate.exists():
            return JSONResponse({"error": f"no such directory: {candidate}"}, status_code=422)
        if not candidate.is_dir():
            return JSONResponse({"error": f"not a directory: {candidate}"}, status_code=422)
        support.ARCHIVES_BASE = candidate.resolve()
        return JSONResponse({"archives_dir": str(support.ARCHIVES_BASE)})

    @app.get("/api/archive-match")
    def get_archive_match(community_uuid: str = "", url: str = "") -> JSONResponse:
        """Archives that already hold what is about to be captured.

        Re-capturing something you already have is one of the few ways to
        waste hours here, and Archives only notices afterwards. This is the
        same question asked before the run instead.

        Deliberately just an answer, never an action: someone may genuinely
        want a fresh capture beside the old one, and silently turning that
        into an update would be worse than not noticing at all.
        """
        wanted = (community_uuid or "").strip()
        matches: list[dict] = []
        if wanted:
            for info in list_archives(support.ARCHIVES_BASE):
                summary = read_archive_summary(info.path)
                if not summary or summary.get("status") != "ok":
                    continue
                ids = {c.get("id") for c in summary.get("communities") or []}
                if wanted not in ids:
                    continue
                groups = summary.get("groups") or []
                matches.append(
                    {
                        "archive": info.name,
                        "display_name": info.display_name,
                        "captured_at": next(
                            (g.get("captured_at") for g in groups if g.get("captured_at")), None
                        ),
                        "components": len(groups),
                        "items": sum(int(g.get("count") or 0) for g in groups),
                    }
                )
        return JSONResponse({"matches": matches})

    @app.get("/api/archive-ledger")
    def get_archive_ledger(name: str = "") -> JSONResponse:
        """Everything the setup screen needs to extend or update one archive.

        Holdings and live availability come back together on purpose. Split
        across two requests, the screen could render holdings alone -- "8
        items", with no way to tell whether that is current -- and a
        half-answer here reads exactly like a complete one.
        """
        target = resolve_archive(support.ARCHIVES_BASE, name)
        if target is None:
            # Not an empty ledger: "this archive holds nothing" and "no such
            # archive" are different statements and must not render alike.
            return JSONResponse({"status": "not_found"}, status_code=404)

        from connections_export.gui.archives import archive_ledger  # noqa: PLC0415

        ledger = archive_ledger(target)
        ledger["status"] = "ok"
        # What the live system has that this archive never captured -- the
        # "extend" half. Best-effort: an unreachable deployment must still
        # leave the update half of the screen usable, so this degrades to an
        # empty list with a reason rather than failing the request.
        ledger["available"] = []
        ledger["live_error"] = None
        community_uuid = _community_uuid_of(ledger)
        if community_uuid:
            try:
                answer = None
                # The fake server is not reachable at the demo's placeholder
                # host, so the demo answers from its own synthesized data --
                # and only for its own, which is why no flag is needed.
                answer = _demo_community_components(community_uuid)
                if answer is None:
                    from connections_export.crawler.community import (  # noqa: PLC0415
                        discover_components,
                    )

                    base, auth_mode = _lookup_base_auth(_app, ledger.get("base_url") or "")

                    def _fetch(url: str, base=base, auth_mode=auth_mode) -> bytes | None:
                        status, content, error = _authed_lookup(_app, url, base, auth_mode)
                        return content if status and status < 400 and not error else None

                    answer = discover_components(
                        community_uuid=community_uuid, base_url=base or "", fetch=_fetch
                    )
                # Keyed on the id a component is addressed by, never its
                # title: a renamed blog is the same blog, and title matching
                # would offer it again as something new.
                held = {(row["kind"], row.get("id")) for row in ledger["rows"]}
                ledger["available"] = [
                    component
                    for component in answer.get("components", [])
                    if (component["kind"], component.get("id")) not in held
                ]
            except Exception as exc:  # noqa: BLE001
                ledger["live_error"] = str(exc)
        return JSONResponse(ledger)

    @app.get("/api/archive-summary")
    def get_archive_summary(name: str = "") -> JSONResponse:
        """Characterize one archive on demand so listing directories stays fast."""
        target = resolve_archive(support.ARCHIVES_BASE, name)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        return JSONResponse(summarize_archive(target))

    @app.post("/api/archive-zip")
    def archive_zip(body: _ArchiveZipRequest) -> JSONResponse:
        """Write an archive out as a single `.zip` beside it, and say where.

        Archives are local and can be large, so this does not stream a download
        through the browser: it creates `<name>.zip` in the archives folder,
        next to the archive it came from -- saving the manual zip step -- and
        reports the path and size. The `.zip` then shows up in the archive list
        and opens straight back in the Reader, read-only. An archive that is
        already a `.zip` needs nothing done.
        """
        name = (body.name or "").strip()
        target = resolve_archive(support.ARCHIVES_BASE, name)
        if target is None:
            return JSONResponse({"error": f"no such archive: {name}"}, status_code=404)
        if target.is_file():  # already a zip
            return JSONResponse(
                {
                    "ok": True,
                    "already_zip": True,
                    "path": str(target),
                    "size": target.stat().st_size,
                }
            )

        dest = target.parent / f"{target.name}.zip"
        # A temp file in the SAME directory, so the final rename is atomic (same
        # filesystem) and a failure never leaves a half-written `<name>.zip`.
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w") as zf:
                for path in sorted(target.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = str(path.relative_to(target)).replace("\\", "/")
                    # Nest under the archive's own name so unzipping yields a
                    # clean named folder (and the Reader reads that one prefix).
                    arcname = f"{target.name}/{rel}"
                    # Blobs are already-compressed media; storing them skips a
                    # pointless recompression pass. Text (manifest/feeds) deflates.
                    stored = rel.startswith("blobs/")
                    zf.write(
                        path,
                        arcname,
                        compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED,
                    )
            os.replace(tmp, dest)
        except OSError as error:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            return JSONResponse({"error": f"could not write the zip: {error}"}, status_code=500)
        return JSONResponse({"ok": True, "path": str(dest), "size": dest.stat().st_size})

    @app.put("/api/archives/{name}/label")
    def set_archive_label(name: str, body: _ArchiveLabelRequest) -> JSONResponse:
        """Rename an archive for a human, without moving it for a machine.

        An archive is named for the first community it captured, which is a
        reasonable guess and not always the right one -- a set of communities
        is a thing a person has their own name for.

        The name is written into `archive-summary.json`; the directory keeps
        its name, because that is the archive's id. `resolve_archive`,
        "extend & update" and every `into:` reference address an archive by
        it, and a rename that moved directories would break all of them
        quietly.
        """
        directory = resolve_archive(support.ARCHIVES_BASE, name)
        if directory is None:
            return JSONResponse(
                {"status": "error", "detail": f"no such archive: {name}"}, status_code=404
            )
        if not writable_archive(directory):
            # The name lives in `archive-summary.json`, inside the archive.
            # A zip has no room for one, and accepting the rename would show
            # it in the list until the next reload and then lose it.
            return JSONResponse(
                {
                    "status": "error",
                    "detail": (
                        f"{name} is a zipped archive, which is read-only, so it cannot be renamed."
                    ),
                },
                status_code=400,
            )
        summary = read_archive_summary(directory) or {}
        label = (body.label or "").strip()
        if label:
            summary["display_name"] = label
        else:
            # Not an empty name -- "stop overriding". The name the directory
            # implies comes back.
            summary.pop("display_name", None)
        (directory / SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return JSONResponse({"status": "ok", "display_name": label or None})

    def _attach(target: Path, *, label: str | None = None) -> JSONResponse:
        """Point the reader at `target`, or explain why not.

        Never clobbers what is already open on failure: an archive that will
        not derive should leave you looking at the one that does.
        """
        try:
            source = ModelSource.from_archive(target)
        except (DeriveError, ArchiveSourceError) as exc:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": f"{target.name} did not open as an archive: {exc}",
                },
                status_code=400,
            )
        app.state.model_source = source
        return JSONResponse({"status": "ok", "name": label or target.name})

    @app.post("/api/open-external")
    def open_external(body: _OpenExternalRequest) -> JSONResponse:
        """Open an archive from anywhere on this machine -- a directory or a
        `.zip` -- rather than from the archives folder.

        An archive someone hands you does not arrive already filed: it is a zip
        in Downloads, a folder on a share, a file synced out of OneDrive.
        Requiring it to be moved first is asking for filing before reading.

        This is the same reach `connections-export open <path>` already gives
        the same user at a terminal. It is a state-changing POST, so the Origin
        guard (gui/security.py) keeps a page they happen to have open from
        reaching it, and nothing is copied or written -- the archive is read
        where it lies.
        """
        raw = (body.path or "").strip()
        if not raw:
            return JSONResponse({"status": "error", "detail": "nothing to open"}, status_code=400)

        lowered = raw.lower()
        if lowered.startswith(("http://", "https://")):
            return _open_downloaded(raw)
        # A file manager hands the browser `file:///...`, never a path.
        if lowered.startswith("file://"):
            raw = url2pathname(unquote(urlparse(raw).path))
        target = Path(raw).expanduser()
        if not target.exists():
            return JSONResponse(
                {"status": "error", "detail": f"there is nothing at {target}"}, status_code=400
            )
        return _attach(target)

    def _open_downloaded(url: str) -> JSONResponse:
        """Fetch a zipped archive from a link and open it.

        A link is what SharePoint, OneDrive and a plain web server hand you,
        and the bytes have to come down before anything can be read. Only a
        `.zip`: following an arbitrary URL is the capture flow's job, and
        doing it here would make a reader into a fetcher of whatever it was
        pointed at.

        A share that wants a login answers with a sign-in PAGE and a 200, so
        what came back is checked for actually being a zip -- storing that page
        as an archive is the same class of error as archiving a login page as
        the document it refused.
        """
        if not urlparse(url).path.lower().endswith(".zip"):
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "only a link to a .zip can be opened as an archive",
                },
                status_code=400,
            )
        holding = Path(tempfile.mkdtemp(prefix="hcl-fetched-"))
        name = Path(unquote(urlparse(url).path)).name or "archive.zip"
        target = holding / name
        try:
            target.write_bytes(_fetch_url(url))
        except Exception as exc:  # noqa: BLE001 - any failure is the same answer
            shutil.rmtree(holding, ignore_errors=True)
            return JSONResponse(
                {"status": "error", "detail": f"could not download {url}: {exc}"},
                status_code=400,
            )
        if not zipfile.is_zipfile(target):
            shutil.rmtree(holding, ignore_errors=True)
            return JSONResponse(
                {
                    "status": "error",
                    "detail": (
                        f"what came back from {url} is not a zip -- a share that wants a "
                        "login answers with a sign-in page, not an error"
                    ),
                },
                status_code=400,
            )
        response = _attach(target, label=name)
        if response.status_code != 200:
            shutil.rmtree(holding, ignore_errors=True)
        return response

    @app.post("/api/upload-archive")
    async def upload_archive(request: Request) -> JSONResponse:
        """Take a zipped archive's bytes and open it.

        For the case where the browser has the file but not its path --
        anything dragged out of OneDrive, SharePoint or a download that has not
        been saved. Streamed to a temporary file rather than buffered, because
        an archive is as big as the community it captured.
        """
        name = (request.headers.get("X-Archive-Name") or "uploaded.zip").strip()
        name = Path(name).name or "uploaded.zip"
        holding = Path(tempfile.mkdtemp(prefix="hcl-dropped-"))
        target = holding / name
        size = 0
        with target.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                handle.write(chunk)
        if not zipfile.is_zipfile(target):
            shutil.rmtree(holding, ignore_errors=True)
            return JSONResponse(
                {"status": "error", "detail": f"{name} is not a zip file"}, status_code=400
            )
        response = _attach(target, label=name)
        if response.status_code != 200:
            # It never opened, so the copy is worth nothing; keeping it would
            # leave the user's disk holding failures they cannot see.
            shutil.rmtree(holding, ignore_errors=True)
        return response

    @app.post("/api/open-archive")
    def open_archive(body: _OpenArchiveRequest) -> JSONResponse:
        """Attach the reader/model to a chosen recent archive. The name must
        resolve to a direct child dir of support.ARCHIVES_BASE (path-safe); anything
        else is 404 — indistinguishable from genuinely absent, and never
        touches the filesystem with unvalidated input."""
        target = resolve_archive(support.ARCHIVES_BASE, body.name)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        try:
            source = ModelSource.from_archive(target)
        except (DeriveError, ArchiveSourceError):
            # Nothing derivable in this dir (an empty/partial archive from a run
            # that never produced a page -- e.g. a failed real crawl). Return a
            # clear error INSTEAD of attaching an empty source: the old behaviour
            # attached a pending model, so `/api/model` returned 503 forever and
            # the reader spun endlessly ("opening the reader from an archive does
            # not work"). Do not clobber the currently-open model on failure.
            return JSONResponse(
                {
                    "status": "empty",
                    "detail": (
                        "This archive has no browsable content — the import may not have finished."
                    ),
                },
                status_code=422,
            )
        app.state.model_source = source
        return JSONResponse({"opened": True, "name": body.name})

    @app.post("/api/delete-archive")
    def delete_archive(body: _ArchiveDeleteRequest) -> JSONResponse:
        """Delete one archive only when its exact name is deliberately typed."""
        target = resolve_archive(support.ARCHIVES_BASE, body.name)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        if body.confirmation != body.name:
            return JSONResponse(
                {"error": "type the exact archive name to confirm deletion"}, status_code=422
            )
        _remove_archive(target)
        return JSONResponse({"status": "ok", "name": body.name})

    def _stream_delete(names: list[str]) -> Response:
        """Delete `names` under support.ARCHIVES_BASE, streaming NDJSON progress.

        One `deleted` record per archive, then a `done` summary. A large
        collection takes long enough that a silent blocking response looks
        like a hang and can outlive a proxy or browser timeout. A name that
        does not resolve -- unknown, or an attempted traversal out of the
        base -- is reported in `failed` rather than aborting the rest.
        """
        total = len(names)

        def _generate():
            deleted = 0
            failed: list[dict[str, str]] = []
            for index, name in enumerate(names, start=1):
                target = resolve_archive(support.ARCHIVES_BASE, name)
                if target is None:
                    failed.append({"name": name, "error": "not found"})
                else:
                    try:
                        _remove_archive(target)
                    except OSError as exc:
                        failed.append({"name": name, "error": str(exc)})
                    else:
                        deleted += 1
                yield (
                    json.dumps({"event": "deleted", "name": name, "index": index, "total": total})
                    + "\n"
                )
            yield (
                json.dumps({"event": "done", "status": "ok", "deleted": deleted, "failed": failed})
                + "\n"
            )

        return StreamingResponse(_generate(), media_type="application/x-ndjson")

    @app.post("/api/delete-archives")
    def delete_archives(body: _SelectedDeleteRequest) -> Response:
        """Delete a chosen set of archives once their count is confirmed."""
        names = [name for name in body.names if name]
        if not names:
            return JSONResponse({"error": "select at least one archive"}, status_code=422)
        if body.confirmation.strip() != str(len(names)):
            return JSONResponse(
                {
                    "error": (
                        f"type {len(names)} to confirm deleting {len(names)} archives "
                        f"-- received {body.confirmation.strip()!r}"
                    )
                },
                status_code=422,
            )
        return _stream_delete(names)

    @app.post("/api/delete-demo-archives")
    def delete_demo_archives(body: _BulkDeleteRequest) -> Response:
        """Delete all demo archives after an explicit bulk confirmation.

        Streams NDJSON -- one `deleted` record per archive, then a `done`
        summary -- because a large collection takes long enough that a silent
        blocking response looks like a hang (and can outlive a proxy or
        browser timeout). The confirmation is checked up front so a refusal is
        still an ordinary 422 JSON body, not a stream that fails mid-flight.
        """
        supplied = " ".join(body.confirmation.split())
        if supplied != BULK_DELETE_CONFIRMATION:
            return JSONResponse(
                {
                    "error": (
                        f"type {BULK_DELETE_CONFIRMATION} to confirm deletion "
                        f"-- received {supplied!r}"
                    )
                },
                status_code=422,
            )

        demo_names = [info.name for info in list_archives(support.ARCHIVES_BASE) if info.is_demo]
        return _stream_delete(demo_names)
