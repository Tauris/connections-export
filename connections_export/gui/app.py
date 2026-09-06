"""`make_app(*, demo=True) -> FastAPI`: the
served app -- the single-page console (setup screen -> live console),
a health check, `/api/model` + `/api/blob/{hash}`, and the run
lifecycle: `POST /api/identify` (parse a dropped/pasted wiki URL),
`POST /api/start` (start a crawl -- demo or real -- in a background
thread publishing to `app.state.event_stream`), and `GET /events`
(stream it).

**A run is started explicitly, not on `/events` connect**. `POST /api/start` resets the
`EventStream`, starts a background thread whose `emit` publishes
JSON-serialized events to it, and returns `{"started": true}`
immediately. `GET /events` waits (`make_app` gains an idle
'no run yet' state so the console waits for a start) for a run to
begin, then sends the client what has happened so far followed by
whatever happens next, as `data: {json}\\n\\n`, ending after the crawl's
own final event -- normally `run_complete`.

The stream is a broadcast with a memory, not a queue to be raced for:
every reader gets the whole run, so a reconnecting console, a second
tab or a re-established EventSource sees all of it rather than
whichever fraction it was in the room for
(`connections_export/gui/event_stream.py`). The crawl runs to
completion in its own thread regardless of what the client does -- a
disconnecting client is simply dropped as a subscriber; `emit` is
fire-and-forget (mirroring `crawler.events.safe_emit`), so an
abandoned reader can never affect the crawl.

`POST /api/start`'s `demo` flag (or an absent/empty `base_url`) picks
the demo pipeline -- the real crawler/derive machinery against the
in-process fakeserver, exactly as `connections_export.gui.demo.run_demo` always
has (Demo mode = the real pipeline). A real `base_url`
instead builds a real `HttpClient` + auth strategy (reusing `cli.py`'s
`_build_default_client`) and crawls that one deployment, scoped to
`wiki_label` if given. `sspi`/`kerberos` are still stubs
(`connections_export/http/auth.py`) that raise once a real handshake is
attempted -- first contact is deferred,
so that failure is caught and turned into an explicit `failed` event
plus a `run_complete` that closes the stream, rather than the run
hanging forever (A missing real-auth path is surfaced).

**Where run archives land**: the current directory by default
(`./connections-export-archives`), or wherever the
`CONNECTIONS_EXPORT_ARCHIVES_DIR` env var points -- a value of `"."`
means "the current directory itself" (no `connections-export-archives`
subdirectory appended). See `_resolve_archives_base`/`ARCHIVES_BASE`.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from connections_export.gui.event_stream import EventStream
from connections_export.gui.model_source import ModelSource
from connections_export.gui.requests import (  # noqa: F401 (public at this address)
    _ArchiveDeleteRequest,
    _ArchivesDirRequest,
    _BulkDeleteRequest,
    _IdentifyRequest,
    _OpenArchiveRequest,
    _SelectedDeleteRequest,
    _SettingsRequest,
    _StartRequest,
    _StylePreviewRequest,
)
from connections_export.gui.routes import archives as routes_archives
from connections_export.gui.routes import lookup as routes_lookup
from connections_export.gui.routes import model as routes_model
from connections_export.gui.routes import pdf as routes_pdf
from connections_export.gui.routes import run as routes_run
from connections_export.gui.routes import settings as routes_settings
from connections_export.gui.routes import static as routes_static
from connections_export.gui.security import DEFAULT_LOCAL_HOSTS, LocalGuardMiddleware

#: Re-exported so these names resolve here as well as in their own module.
#:
#: `ARCHIVES_BASE` is deliberately NOT re-exported: it is reassigned at run
#: time by `PUT /api/archives-dir`, and a by-value re-export would be a
#: snapshot that silently stopped tracking it -- the same trap that forked
#: the page cap between the crawler and derive. Read (and patch)
#: `connections_export.gui.support.ARCHIVES_BASE`.
from connections_export.gui.support import (  # noqa: F401 (public at this address)
    _DONE,
    ARCHIVES_DIR_ENV,
    BULK_DELETE_CONFIRMATION,
    CONSOLE_CSS,
    CONSOLE_HTML,
    CONSOLE_JS,
    STATIC_DIR,
    VENDOR_DIR,
    _community_uuid_of,
    _demo_community_components,
    _demo_feed_info,
    _new_run_archive_dir,
    _pdf_style_token_defaults,
    _resolve_archives_base,
    _resolve_interchange_spec_path,
)


def make_app(
    *,
    demo: bool = True,
    demo_seed: int = 0,
    demo_delay: float = 0.03,
    package_dir: str | Path | None = None,
    archive_dir: str | Path | None = None,
    bound_host: str | None = None,
    pdf_renderer: Any = None,
    author_filter: str | None = None,
) -> FastAPI:
    """Build the console app.

    `demo` is accepted and ignored. There is no demo mode: the synthetic
    deployment lives at an address no real system can occupy, so reading
    that address reads it and reading any other reads that one. A flag
    beside the address could only disagree with it, and when it did the
    flag won -- which is how a real community, correctly identified and
    answering every request, was reported as containing nothing. The
    parameter stays so existing callers keep working.

    `demo_seed`/`demo_delay` tune the
    demo pipeline (`connections_export.gui.demo.run_demo`) when a run is
    started with `demo=true` -- tests pass `demo_delay=0` to run the
    same, still entirely real, pipeline at full speed instead of the
    default watchable pace.

    `package_dir`/`archive_dir` point the reader's `/api/model` +
    `/api/blob/{hash}` at a pre-existing package or archive
    (`model_source.py`'s "Archive/package mode") instead of waiting on
    a run to complete; `package_dir` wins if both are given. A
    completed demo run always (re-)stashes its own model regardless of
    how the app was constructed -- "the most recent completed demo
    run's derived model".

    `bound_host` is the host `hcl-serve` binds to; it is added to the
    localhost allowlist of `LocalGuardMiddleware`
    so that serving on a non-default host still works while requests
    addressed to any *other* Host (a DNS-rebinding attacker's) are
    rejected. The default `127.0.0.1`/`localhost` binds are already in
    the allowlist, so demo usage needs nothing here.
    """

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield
        # On shutdown: wake any open /events SSE stream so it exits its
        # loop cleanly before uvicorn cancels the task. Published through the
        # stream, not put on a queue of its own: a terminator delivered by
        # any other road reaches whoever happens to be reading that road.
        if _app.state.event_stream.started:
            try:
                _app.state.event_stream.publish(_DONE)
            except Exception:  # noqa: BLE001
                pass

    app = FastAPI(lifespan=_lifespan)

    # Localhost hardening: reject requests whose Host
    # is not a permitted localhost name (DNS rebinding) and state-changing
    # requests from a non-local Origin (CSRF). Pure-ASGI, so it never
    # buffers the `/events` SSE stream. Added first so it wraps the whole
    # app and sees every request before any route does.
    allowed_hosts = set(DEFAULT_LOCAL_HOSTS)
    if bound_host:
        allowed_hosts.add(bound_host.strip().lower().strip("[]"))
    app.add_middleware(LocalGuardMiddleware, allowed_hosts=allowed_hosts)

    if package_dir is not None:
        app.state.model_source = ModelSource.from_package(package_dir, author=author_filter)
    elif archive_dir is not None:
        app.state.model_source = ModelSource.from_archive(archive_dir, author=author_filter)
    else:
        app.state.model_source = ModelSource()
    #: A CLI-configured default author filter (`--author`), applied to
    #: browser-started runs that don't set their own author.
    app.state.default_author_filter = author_filter
    app.state.editable_settings = {
        "base_url": None,
        "auth_mode": "sspi",
        "min_interval": 1.0,
        "pdf_style": {},
        "pdf_marks": {},
        "archive_only": False,
        "default_author_filter": author_filter,
    }
    # ...over whatever this user saved last time. These lived only in memory
    # before, so every Save died with the process.
    from connections_export.gui.settings_store import load_settings  # noqa: PLC0415

    app.state.editable_settings.update(load_settings())
    if app.state.editable_settings.get("default_author_filter"):
        app.state.default_author_filter = app.state.editable_settings["default_author_filter"]

    #: Threads started per `/api/start` call, kept around so a caller
    #: (a test, or future run-history view) can confirm a run
    #: completed even after its SSE client disconnected. Harmless
    #: bookkeeping: threads are daemons and self-remove nothing is
    #: required for correctness.
    app.state.run_threads: list[threading.Thread] = []
    #: Every reader of an ingest run gets the whole run, ending included.
    app.state.event_stream = EventStream()
    app.state.event_log_path: Path | None = None
    app.state.event_log_lock = threading.Lock()
    # Cookies + base_url from the last real (non-demo) crawl, reused by /api/live-pdf.
    app.state.live_cookies: list[dict] = []
    app.state.live_base_url: str | None = None
    # Stop event: set by /api/stop to interrupt the running crawl.
    app.state.stop_event: threading.Event = threading.Event()
    #: The HTTP client of the run currently in flight, so its pacing can be
    #: changed without stopping it. `None` between runs.
    app.state.live_client = None
    # Cached authenticated HTTP client for /api/feed-info (avoids a new
    # SSPI handshake on every URL drag). Keyed by base_url.
    app.state.feed_info_client: dict = {}  # base_url -> HttpClient

    # The routes live in `gui/routes/`, one module per group. Each group is
    # handed the same names, so a handler reads the same wherever it is
    # defined and the grouping stays a matter of where code lives.
    _route_kwargs = dict(
        demo=demo,
        demo_seed=demo_seed,
        demo_delay=demo_delay,
        pdf_renderer=pdf_renderer,
        author_filter=author_filter,
    )
    routes_model.register(app, **_route_kwargs)
    routes_lookup.register(app, **_route_kwargs)
    routes_archives.register(app, **_route_kwargs)
    routes_settings.register(app, **_route_kwargs)
    routes_pdf.register(app, **_route_kwargs)
    routes_static.register(app, **_route_kwargs)
    routes_run.register(app, **_route_kwargs)

    return app
