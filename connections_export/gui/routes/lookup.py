"""Answering "what is this URL / who is this person" against a live
deployment.

Every route here performs an authenticated lookup against the real thing, so
each is a place a wrong answer becomes a wrong capture. They share one cached
client per base URL -- an SSPI handshake per dragged URL is slow enough to
feel broken.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse, StreamingResponse

from connections_export.config import Config
from connections_export.gui.demo import (
    DEMO_SAMPLE_BASE_URL,
)
from connections_export.gui.requests import (
    _IdentifyRequest,
)
from connections_export.gui.routes._lookup import (
    _authed_lookup,
    _lookup_base_auth,
    _lookup_deployment,
    _lookup_session,
)
from connections_export.gui.support import (
    _DONE,
    _demo_community_components,
    _demo_current_user,
    _demo_feed_info,
    _demo_subcommunities,
)
from connections_export.gui.wiki_url import parse_url


def _why_nothing_was_read(transport: list[dict[str, Any]]) -> str:
    """One sentence naming what stopped every request.

    Ordered by what a reader can act on. An exception means the request
    never reached a deployment -- a name that will not resolve, a
    certificate that will not verify -- and the message is the finding. A
    status means it arrived and was refused, and 401/403 is about
    credentials rather than about the community.
    """
    errors = [entry["error"] for entry in transport if entry.get("error")]
    if errors:
        return f"no feed could be read: {errors[0]}"
    statuses = sorted({entry["status"] for entry in transport if entry.get("status")})
    if not statuses:
        return "no feed returned anything for this community"
    listed = "/".join(str(status) for status in statuses)
    if any(status in (401, 403) for status in statuses):
        return (
            f"every feed was refused (HTTP {listed}). The deployment answered, and "
            "declined — this is about who the console is signed in as, not about "
            "the community."
        )
    return f"every feed was refused (HTTP {listed})."


def register(
    app,
    *,
    demo: bool,
    demo_seed: int,
    demo_delay: float,
    pdf_renderer,
    author_filter: str | None,
) -> None:
    """Register the lookup routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    #: The FastAPI app under a name no route parameter shadows -- `community_of`
    #: has a query parameter called `app` (the HCL app kind).
    _app = app

    @app.get("/api/search-preview")
    def search_preview(
        userid: str = "",
        scope: str | None = None,
        base_url: str = "",
        community_uuid: str = "",
    ) -> JSONResponse:
        """Preview what HCL's Search person-query would select for `userid` --
        the "search-based ingest" to try before trusting it. Read-only: runs
        the real Search API (authenticating on demand -- no prior ingest
        needed) and returns the hits (title/component/author) to eyeball, plus
        the exact `url` queried so the syntax is inspectable. Ingests nothing.

        Note the Search person-query is a SUPERSET (author OR contributor OR
        community member), so it's for comparison, not exact authorship."""
        userid = (userid or "").strip()
        if not userid:
            return JSONResponse(
                {"status": "no_userid", "detail": "Enter a user id to preview search."},
                status_code=400,
            )
        base, _auth = _lookup_base_auth(_app, base_url)
        if not base:
            return JSONResponse(
                {
                    "status": "no_base_url",
                    "detail": "No deployment URL — enter one on the Select screen.",
                },
                status_code=503,
            )
        from connections_export.adapters import atom  # noqa: PLC0415
        from connections_export.adapters.search import (  # noqa: PLC0415
            parse_search_results,
            search_results_url,
        )

        url = search_results_url(
            base_url=base,
            userid=userid,
            community_uuid=(community_uuid or "").strip() or None,
            scope=scope,
            page=1,
            page_size=150,
        )
        status, content, error = _authed_lookup(_app, url, base, _auth)
        if error is not None:
            return JSONResponse(
                {"status": "unavailable", "detail": error, "url": url}, status_code=503
            )
        if status is None or status >= 400:
            return JSONResponse(
                {"status": "http_error", "detail": f"Search returned {status}.", "url": url},
                status_code=502,
            )
        try:
            results = parse_search_results(content)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"status": "parse_error", "detail": str(exc), "url": url}, status_code=502
            )
        # The feed's own reported total (`openSearch:totalResults`), so the
        # preview is honest about a truncated sample instead of implying
        # `len(results)` (page 1, capped at 150 -- HCL's documented max) is
        # everything -- the actual ingest (`_search_seeds`) pages through the
        # full result set, but this preview deliberately doesn't (it's a
        # quick eyeball, not the ingest itself).
        total_results = None
        try:
            total_results = atom.feed_total_results(content)
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(
            {
                "count": len(results),
                "total": total_results,
                "url": url,
                "results": [
                    {
                        "title": r.title,
                        "component": r.component,
                        "author": r.author,
                        "author_userid": r.author_userid,
                        "url": r.alternate_url,
                        "via": r.via_url,
                    }
                    for r in results
                ],
            }
        )

    @app.get("/api/resolve-user")
    def resolve_user(email: str = "", userid: str = "", base_url: str = "") -> JSONResponse:
        """Resolve a stable **email** (or userid) to the directory GUID
        (`snx:userid`) via the Profiles API,
        so the author filter matches on the stable uuid instead of the volatile
        "Surname, Firstname (Department)" display name. Authenticates ON DEMAND
        (no prior ingest needed). Returns the resolved name/email/userid AND the
        `url` queried, so the lookup is inspectable."""
        email = (email or "").strip()
        userid = (userid or "").strip()
        if not email and not userid:
            return JSONResponse(
                {"status": "no_input", "detail": "Enter an email (or user id) to resolve."},
                status_code=400,
            )
        base, auth_mode = _lookup_base_auth(_app, base_url)
        if not base:
            return JSONResponse(
                {
                    "status": "no_base_url",
                    "detail": "No deployment URL — enter one on the Select screen.",
                },
                status_code=503,
            )
        from connections_export.adapters.directory import (  # noqa: PLC0415
            parse_profile_identity,
            profile_by_email_url,
            profile_by_userid_url,
        )

        url = (
            profile_by_userid_url(base_url=base, userid=userid)
            if userid
            else profile_by_email_url(base_url=base, email=email)
        )
        status, content, error = _authed_lookup(_app, url, base, auth_mode)
        if error is not None:
            return JSONResponse(
                {"status": "unavailable", "detail": error, "url": url}, status_code=503
            )
        if status == 400 and email:
            # Deployments that hide email addresses reject any ?email= with 400.
            return JSONResponse(
                {
                    "status": "email_hidden",
                    "detail": "This deployment doesn't allow email lookup. Try your login/user id.",
                    "url": url,
                },
                status_code=404,
            )
        if status is None or status >= 400:
            return JSONResponse(
                {"status": "http_error", "detail": f"Profiles returned {status}.", "url": url},
                status_code=502,
            )
        try:
            user = parse_profile_identity(content)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"status": "parse_error", "detail": str(exc), "url": url}, status_code=502
            )
        if user is None:
            return JSONResponse(
                {
                    "status": "not_found",
                    "detail": "No profile matched that email/user id.",
                    "url": url,
                },
                status_code=404,
            )
        return JSONResponse(
            {"userid": user.userid, "name": user.name, "email": user.email, "url": url}
        )

    @app.get("/api/community-of")
    def community_of(
        app: str = "", container: str = "", topic_id: str = "", base_url: str = ""
    ) -> JSONResponse:
        """Best-effort discovery of the community uuid that owns the dragged
        container (forum/blog/wiki), so the search can be scoped to that
        community -- the only documented sub-component reduction. Fetches the
        container's own feed (on-demand auth) and scans for `snx:communityUuid`.
        EXPERIMENTAL: `community_uuid` is null for a standalone container, or if
        that element isn't present on this deployment's feeds. Never fatal."""
        app_kind = (app or "").strip()
        container = (container or "").strip()
        if not app_kind or not container:
            return JSONResponse({"community_uuid": None, "detail": "no container"}, status_code=200)
        base, auth_mode = _lookup_base_auth(_app, base_url)
        if not base:
            return JSONResponse({"community_uuid": None, "detail": "no base url"}, status_code=200)
        from connections_export.adapters.blogs import entries_feed_url  # noqa: PLC0415
        from connections_export.adapters.forums import (  # noqa: PLC0415
            parse_single_topic,
            topic_url,
            topics_url,
        )
        from connections_export.adapters.search import community_uuid_from_feed  # noqa: PLC0415
        from connections_export.adapters.wikis import nav_feed_url  # noqa: PLC0415

        if app_kind == "forum":
            if topic_id:
                topic_feed_url = topic_url(base_url=base, topic_uuid=topic_id)
                status, content, error = _authed_lookup(_app, topic_feed_url, base, auth_mode)
                if error is not None or status is None or status >= 400:
                    return JSONResponse(
                        {"community_uuid": None, "url": topic_feed_url, "detail": error or status}
                    )
                topics = parse_single_topic(content)
                forum_uuid = topics[0].forum_uuid if topics else None
                if not forum_uuid:
                    return JSONResponse({"community_uuid": None, "url": topic_feed_url})
                url = topics_url(base_url=base, forum_uuid=forum_uuid)
            else:
                url = topics_url(base_url=base, forum_uuid=container)
        elif app_kind == "blog":
            url = entries_feed_url(base_url=base, blog_uuid=container)
        elif app_kind == "wiki":
            nav = nav_feed_url(base_url=base, auth_root="basic", wiki_label=container)
            url = nav + "?acls=true"
        else:
            return JSONResponse({"community_uuid": None, "detail": "unknown app"}, status_code=200)

        status, content, error = _authed_lookup(_app, url, base, auth_mode)
        if error is not None or status is None or status >= 400:
            return JSONResponse({"community_uuid": None, "url": url, "detail": error or status})
        return JSONResponse({"community_uuid": community_uuid_from_feed(content), "url": url})

    @app.get("/api/subcommunities")
    def subcommunities(community_uuid: str = "", base_url: str = "") -> JSONResponse:
        """A community's children, offered as candidates to add to this run.

        A convenience rather than the feature: what an archive holds is a SET
        of communities, and a community can always be added by URL -- which is
        the only way to add one that is merely related to another rather than
        its child.

        Never fails the page. The picker asks this while the user is still
        typing, so an unreachable deployment, a community with no children and
        a blank box are all the same answer here: no candidates.
        """
        community_uuid = community_uuid.strip()
        # No mode: `_demo_subcommunities` returns None for any community
        # that is not the demo's, so asking it first costs nothing and
        # answers whenever the demo is what is being read.
        if community_uuid:
            demo_answer = _demo_subcommunities(community_uuid)
            if demo_answer is not None:
                return JSONResponse({"children": demo_answer})
        base, auth_mode = _lookup_base_auth(_app, base_url)
        if not community_uuid or not base:
            return JSONResponse({"children": [], "detail": "missing community or base URL"})

        from connections_export.adapters.communities import (  # noqa: PLC0415
            parse_subcommunities,
            subcommunities_url,
        )

        url = subcommunities_url(base_url=base, community_uuid=community_uuid)
        status, content, error = _authed_lookup(_app, url, base, auth_mode)
        if error is not None or status is None or status >= 400:
            return JSONResponse({"children": [], "url": url, "detail": error or status})
        return JSONResponse(
            {
                "children": [
                    {"kind": "community", "id": child.uuid, "title": child.title or child.uuid}
                    for child in parse_subcommunities(content)
                ],
                "url": url,
            }
        )

    def _discover(
        community_uuid: str,
        base_url: str,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Discover a community's components, telling `on_event` as it goes.

        Shared by the plain JSON route and the streaming one so they cannot
        disagree: the stream is the same discovery with a window into it.
        Events are `{"type": "step", "step": ...}` as each phase begins and
        `{"type": "request", "n": ..., "url": ..., "status": ...}` as each
        request completes.
        """
        community_uuid = community_uuid.strip()

        def emit(event: dict[str, Any]) -> None:
            if on_event is not None:
                on_event(event)

        # A demo chip's community answers from the synth data, since the
        # fakeserver runs in-process and is never reachable over HTTP at the
        # placeholder host. `_demo_community_components` returns None for any
        # community that is not the demo's, so this decides itself: no flag
        # can make it answer for a real one, and none can stop it answering
        # for its own.
        if community_uuid:
            demo_answer = _demo_community_components(community_uuid)
            if demo_answer is not None:
                return demo_answer
        base, auth_mode, auth_root = _lookup_deployment(_app, base_url)
        if not community_uuid or not base:
            return {"components": [], "forums": [], "detail": "missing community or base URL"}

        from connections_export.crawler.community import discover_components  # noqa: PLC0415

        # ONE session for the whole discovery. Ten to fifteen requests each
        # opening its own -- each a full integrated-auth handshake -- is what
        # made this slow enough for the console to give up on; see
        # `LookupSession`. Failing to open it is an answer, not an exception:
        # the same answer every feed would then have given.
        try:
            session = _lookup_session(_app, base, auth_mode)
        except Exception as exc:  # noqa: BLE001
            failure = {"url": base, "status": None, "error": str(exc)[:200]}
            return {
                "components": [],
                "forums": [],
                "transport": [failure],
                "detail": _why_nothing_was_read([failure]),
            }

        # What the deployment said, kept. The session knows the status and the
        # exception; discovery is handed only the bytes, so without this a
        # refusal, an unreachable host and a community that genuinely holds
        # nothing all arrive as the same empty list -- and each points at a
        # different thing to go and fix.
        transport: list[dict[str, Any]] = []
        requests_made = 0
        counter_lock = threading.Lock()

        def fetch(url: str) -> bytes | None:
            nonlocal requests_made
            status, content, error = session.get(url)
            answered = status is not None and status < 400 and not error
            if not answered:
                transport.append(
                    {"url": url, "status": status, "error": (error or "")[:200] or None}
                )
            with counter_lock:
                requests_made += 1
                n = requests_made
            emit({"type": "request", "n": n, "url": url, "status": status, "ok": answered})
            return content if answered else None

        def redirect_location(url: str) -> str | None:
            location = session.redirect_location(url)
            if location is None:
                transport.append({"url": url, "status": None, "error": "did not answer"})
            return location

        answer = discover_components(
            community_uuid=community_uuid,
            base_url=base,
            fetch=fetch,
            redirect_location=redirect_location,
            auth_root=auth_root or "basic",
            progress=lambda what: emit({"type": "step", "step": what}),
        )
        if not answer.get("components") and transport:
            answer["transport"] = transport[:8]
            # ...but only speak for the whole answer when the FEEDS were what
            # could not be read. An auxiliary lookup failing while every feed
            # answered is worth recording and is not the finding: the finding
            # is then that the feeds named no component.
            if not any(attempt.get("fetched") for attempt in answer.get("attempts", [])):
                answer["detail"] = _why_nothing_was_read(transport)
        return answer

    @app.get("/api/community-components")
    def community_components(community_uuid: str = "", base_url: str = "") -> JSONResponse:
        """Discover named community components for the setup picker.

        The discovery itself lives in `crawler.community`, so the command line
        can expand a community URL the same way rather than being handed the
        answer. What stays here is what is genuinely console-specific: the demo
        short-circuit, and how this server authenticates a lookup.

        The whole answer at once. `/api/community-components/stream` is the
        same discovery with progress, and is what the console uses; this one
        stays for anything that wants the JSON in one piece -- including a
        person reading it in a browser to see what each feed said.
        """
        return JSONResponse(_discover(community_uuid, base_url))

    @app.get("/api/community-components/stream")
    async def community_components_stream(
        community_uuid: str = "", base_url: str = ""
    ) -> StreamingResponse:
        """The same discovery, as an event stream that shows its working.

        Discovery is ten to fifteen requests with fallbacks between them, and
        against a slow deployment it can run past a minute. A plain request
        for that is silence until it ends -- and a console that has to decide
        when silence means failure will decide wrong in one direction or the
        other. So each phase and each request is reported as it happens; the
        console shows them and treats only silence as a reason to give up.

        Events are JSON: `step`, `request`, and finally one `result` carrying
        exactly what the plain route would have returned. The stream then
        closes. It never emits `id:`, and the console closes it on `result`:
        a browser that reconnected an event stream on its own would start the
        discovery over.
        """
        import asyncio  # noqa: PLC0415

        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                answer = _discover(community_uuid, base_url, on_event=events.put)
                events.put({"type": "result", **answer})
            except Exception as exc:  # noqa: BLE001 - the stream must still end
                events.put(
                    {
                        "type": "result",
                        "components": [],
                        "forums": [],
                        "detail": f"discovery failed: {type(exc).__name__}: {exc}"[:300],
                    }
                )
            events.put(_DONE)

        threading.Thread(target=work, daemon=True).start()
        loop = asyncio.get_event_loop()

        async def _generate():
            while True:
                try:
                    item = await loop.run_in_executor(None, lambda: events.get(timeout=0.5))
                except queue.Empty:
                    continue
                if item is _DONE:
                    break
                yield f"data: {json.dumps(item)}\n\n"

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @app.get("/api/current-user")
    def current_user(base_url: str = "") -> JSONResponse:
        """Resolve the authenticated principal through Profiles' self service."""
        base, auth_mode = _lookup_base_auth(_app, base_url)
        from connections_export.adapters.directory import (  # noqa: PLC0415
            parse_current_user,
            profile_service_url,
        )

        if not base:
            # `_lookup_base_auth` returns nothing for the demo's address, since
            # its server is in-process rather than reachable over HTTP -- so
            # the address asked about is what says whose identity this is.
            if (base_url or "").strip().rstrip("/") == DEMO_SAMPLE_BASE_URL:
                # The demo IS signed in, as one of the people in its own data
                # -- otherwise "Only me" is the one question a demo cannot
                # answer, and the path that answers it goes untested. This
                # takes the same two steps a deployment does, against the same
                # fake the demo crawls: ask `profileService.do`, parse it.
                resolved = _demo_current_user()
                if resolved is not None:
                    return JSONResponse(resolved)
                return JSONResponse({"status": "unavailable"}, status_code=503)
            # Nothing was named, so there is nobody to be. Answering with the
            # demo's principal here is how a real archive came to be labelled
            # with a synthetic person's name.
            return JSONResponse({"status": "no_base_url"}, status_code=503)

        url = profile_service_url(base_url=base)
        status, content, error = _authed_lookup(_app, url, base, auth_mode)
        if error is not None or status is None or status >= 400:
            return JSONResponse(
                {"status": "unavailable", "url": url, "detail": error or status}, status_code=503
            )
        try:
            user = parse_current_user(content)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"status": "parse_error", "url": url, "detail": str(exc)}, status_code=502
            )
        if user is None:
            return JSONResponse({"status": "not_found", "url": url}, status_code=404)
        return JSONResponse({"userid": user.userid, "name": user.name, "url": url})

    @app.post("/api/identify")
    def identify(body: _IdentifyRequest) -> JSONResponse:
        """Identify the HCL deployment and which app (wiki | blog | forum)
        a dropped/pasted URL points at (`parse_url` as JSON), so parsing
        has a single source of truth server-side and the setup screen just
        renders whatever comes back."""
        target = parse_url(body.url)
        return JSONResponse(
            {
                "ok": target.ok,
                "reason": target.reason,
                "app": target.app,
                "community_uuid": target.community_uuid,
                "base_url": target.base_url,
                "auth_root": target.auth_root,
                "wiki_label": target.wiki_label,
                "blog_handle": target.blog_handle,
                "forum_uuid": target.forum_uuid,
                "scope": target.scope,
                "entry_slug": target.entry_slug,
                "topic_id": target.topic_id,
            }
        )

    @app.get("/api/feed-info")
    def feed_info(url: str, scope: str = "all") -> JSONResponse:
        """Fetch the first page of the feed implied by `url` and return
        `{"total": N, "feed_url": "...", "title": "..."}` so the setup
        screen can show how many items will be imported and the
        blog/forum/wiki's own display name (Finding #26 -- the feed's
        `<atom:title>`, e.g. "Team Updates", not just the handle/uuid
        `parse_url` extracts from the URL). Uses the SSPI cookies from
        the last real crawl session. Returns `{"total": null}` on any
        error (fetch failure, no opensearch:totalResults) -- the UI
        degrades gracefully to not showing a count or a name.

        Demo-aware: a demo chip's URL points at
        the placeholder host `DEMO_SAMPLE_BASE_URL`, which the demo
        fakeserver never actually serves over HTTP (it runs in-process
        behind `run_demo`), so the fetch below would always fail for
        one. When the parsed URL's `base_url` is that placeholder,
        `_demo_feed_info` answers from the same synth data instead -- the
        real name and item count, not the raw handle/uuid and a missing
        count. Any other URL takes the HTTP path below. The address is the
        whole of the decision: no real deployment can occupy that host."""
        try:
            from connections_export.gui.wiki_url import parse_url as _parse  # noqa: PLC0415

            target = _parse(url)
            if not target.ok or not target.base_url:
                return JSONResponse({"total": None})

            if target.base_url == DEMO_SAMPLE_BASE_URL:
                demo_result = _demo_feed_info(target)
                if demo_result is not None:
                    return JSONResponse(demo_result)

            from connections_export.adapters.atom import (  # noqa: PLC0415
                feed_title,
                feed_total_results,
            )
            from connections_export.adapters.blogs import (  # noqa: PLC0415
                blogs_list_url,
                entries_feed_url,
            )
            from connections_export.adapters.forums import (  # noqa: PLC0415
                forums_list_url,
                parse_single_topic,
            )
            from connections_export.adapters.forums import (  # noqa: PLC0415
                replies_url as _replies_url,
            )
            from connections_export.adapters.forums import (  # noqa: PLC0415
                topic_url as _topic_url,
            )
            from connections_export.adapters.forums import (  # noqa: PLC0415
                topics_url as _topics_url,
            )
            from connections_export.adapters.wikis import (  # noqa: PLC0415
                wikis_feed_url,
            )
            from connections_export.cli import _build_default_client  # noqa: PLC0415

            base = target.base_url

            # Build an authenticated client once; reuse it for all requests
            # in this handler (topic lookup + feed probe). Falls back to
            # session-cookie-only if SSPI setup fails.
            _cfg = Config(base_url=base, auth_mode="sspi", output_dir=Path("."))
            _http = app.state.feed_info_client.get(base)
            if _http is None:
                try:
                    _http = _build_default_client(_cfg, env={})
                    app.state.feed_info_client[base] = _http
                except Exception:
                    # SSPI not available / failed — fall back to session cookies.
                    import httpx as _httpx  # noqa: PLC0415

                    cookies = {c["name"]: c["value"] for c in (app.state.live_cookies or [])}

                    class _FallbackClient:  # noqa: N801
                        def get(self, u: str) -> _FallbackClient:
                            self._r = _httpx.get(
                                u, cookies=cookies, follow_redirects=True, timeout=10, verify=True
                            )
                            return self._r

                    _http = _FallbackClient()

            def _get(u: str):
                return _http.get(u)

            # Determine the feed URL for the total count.
            if target.app == "wiki":
                auth = target.auth_root or "basic"
                if target.wiki_label:
                    # The wiki's PAGES feed (Atom) carries opensearch:totalResults
                    # = the page count. The nav feed is JSON and has no
                    # totalResults, so feed_total_results raised on it and the
                    # count silently came back None (the All-count never showed).
                    feed_url = f"{base}/wikis/{auth}/api/wiki/{target.wiki_label}/feed"
                else:
                    feed_url = wikis_feed_url(base_url=base, auth_root=auth)
            elif target.app == "blog":
                if target.blog_handle:
                    feed_url = entries_feed_url(base_url=base, blog_uuid=target.blog_handle)
                else:
                    feed_url = blogs_list_url(base_url=base, homepage="homepage")
            elif target.app == "forum":
                if scope == "single" and target.topic_id:
                    # Single thread: count replies in that specific thread.
                    feed_url = _replies_url(base_url=base, topic_uuid=target.topic_id)
                elif target.forum_uuid:
                    feed_url = _topics_url(base_url=base, forum_uuid=target.forum_uuid)
                elif target.topic_id:
                    # threadTopic URL: resolve forum UUID from the topic entry first.
                    t_resp = _get(_topic_url(base_url=base, topic_uuid=target.topic_id))
                    t_status = getattr(t_resp, "status", None) or getattr(t_resp, "status_code", 0)
                    if t_status == 200:
                        parsed = parse_single_topic(t_resp.content)
                        forum_uuid = parsed[0].forum_uuid if parsed else None
                        feed_url = (
                            _topics_url(base_url=base, forum_uuid=forum_uuid)
                            if forum_uuid
                            else forums_list_url(base_url=base)
                        )
                    else:
                        feed_url = forums_list_url(base_url=base)
                else:
                    feed_url = forums_list_url(base_url=base)
            else:
                return JSONResponse({"total": None})

            # Use the full authenticated client (SSPI/cookies) so the count
            # works even before the first crawl populates live_cookies.
            from connections_export.cli import _build_default_client  # noqa: PLC0415

            _cfg = Config(base_url=base, auth_mode="sspi", output_dir=Path("."))
            try:
                _client = _build_default_client(_cfg, env={})
            except Exception:
                _client = None

            probe_url = feed_url + ("?ps=1&page=1" if "?" not in feed_url else "&ps=1&page=1")
            try:
                probe_resp = _get(probe_url)
                raw = probe_resp.content
                # HttpClient returns a Fetched object (.status); httpx fallback uses.status_code
                status = getattr(probe_resp, "status", None) or getattr(
                    probe_resp, "status_code", 0
                )
            except Exception:
                raw, status = b"", 0
            if status != 200:
                return JSONResponse({"total": None, "feed_url": feed_url})
            total = feed_total_results(raw)
            title = feed_title(raw)
            return JSONResponse({"total": total, "feed_url": feed_url, "title": title})
        except Exception:
            return JSONResponse({"total": None})
