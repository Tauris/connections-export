"""Serving the derived model and its blobs: what the reader reads."""

from __future__ import annotations

import markdown
from fastapi.responses import JSONResponse, Response

from connections_export.gui.support import (
    _HEX64,
    _resolve_interchange_spec_path,
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
    """Register the model routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    @app.get("/api/health")
    def health() -> dict:
        # Liveness only. Which deployment a request reads is decided per
        # request, from its URL, so there is no server-wide demo to report.
        return {"status": "ok"}

    @app.get("/api/model")
    def get_model() -> Response:
        """The derived model of the run this server exposes. `503 {"status": "pending"}` until a
        model is derivable at all. Once it is, the model is served with
        an `X-HCL-Model-State` header: `partial` while
        a run is still importing (a growing live snapshot) or `complete`
        once the run has finished -- so the reader knows whether to keep
        refreshing. It is never a partial *within a page* -- only whole
        pages imported so far, each fully derived."""
        source = app.state.model_source
        model = source.get_model()
        if model is None:
            return JSONResponse({"status": "pending"}, status_code=503)
        return Response(
            content=model.model_dump_json(),
            media_type="application/json",
            headers={"X-HCL-Model-State": source.model_state()},
        )

    @app.get("/api/blob/{blob_hash}")
    def get_blob(blob_hash: str) -> Response:
        """A captured blob's bytes, by content hash. `404` for anything not a present blob under a
        validly-shaped hash -- malformed input and genuinely absent
        blobs are indistinguishable to a caller, and neither ever
        touches the filesystem with unvalidated input (an
        absent or malformed hash is rejected without serving arbitrary
        files)."""
        if not _HEX64.fullmatch(blob_hash):
            return Response(status_code=404)
        result = app.state.model_source.get_blob(blob_hash)
        if result is None:
            return Response(status_code=404)
        data, content_type = result
        return Response(content=data, media_type=content_type)

    @app.get("/api/current-archive")
    def current_archive() -> JSONResponse:
        """Which archive the reader/ingest is currently working with: the
        backing directory's name, its state (pending/partial/complete), and
        whether this is the demo. `name` is null before anything is loaded.
        Lets the GUI always show which archive is in view, in both sections."""
        from connections_export.gui.archives import is_demo_archive  # noqa: PLC0415

        source = app.state.model_source
        root = source.package_root()
        name = root.name if root is not None else None
        return JSONResponse(
            {
                "name": name,
                "state": source.model_state(),
                # How the server was started. Kept because callers already
                # read it, but it does not answer "is what I am looking at
                # demo data" -- a real URL dropped into a `serve --demo`
                # console produces a real archive.
                # Whether THIS archive holds demo data, from its own name.
                "is_demo_data": bool(name) and is_demo_archive(name),
            }
        )

    @app.get("/api/manual/interchange")
    def get_manual_interchange() -> JSONResponse:
        """Rendered HTML of the interchange-format spec, for the
        Manual's "Full interchange format specification" section
        -- lazy-loaded by the console so it isn't shipped inline in
        `console.html`. This is our own trusted document (no untrusted
        content ever reaches it), so rendering it straight to HTML and
        injecting it is fine -- unlike a page body, which stays inside
        the sandboxed reader iframe.

        `_resolve_interchange_spec_path` prefers a currently-open
        package's own bundled copy over the repo doc; `404` with a
        clear message if neither is found (e.g. an installed package
        with no docs/ tree and nothing package-shaped open yet)."""
        path = _resolve_interchange_spec_path(app.state.model_source)
        if path is None:
            return JSONResponse(
                {
                    "error": "not_found",
                    "message": "The interchange format spec isn't available on this server.",
                },
                status_code=404,
            )
        text = path.read_text(encoding="utf-8")
        html = markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])
        return JSONResponse({"html": html})
