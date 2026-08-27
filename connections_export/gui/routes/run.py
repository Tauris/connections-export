"""Starting, stopping and streaming a capture.

The largest group, and the one that most needed lifting out of `make_app`:
`start` and `_run_real` alone were 627 lines inside a 2,287-line closure, none
of it importable or testable except through the whole app.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import queue
import threading
from pathlib import Path

from fastapi.responses import JSONResponse, StreamingResponse

from connections_export import apps
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events as crawler_events
from connections_export.crawler.crawl import crawl as run_crawl
from connections_export.crawler.crawl import crawl_blogs as run_crawl_blogs
from connections_export.crawler.crawl import crawl_forums as run_crawl_forums
from connections_export.crawler.dispatch import run_selection
from connections_export.crawler.events import Emit, safe_emit
from connections_export.crawler.report import CrawlReport
from connections_export.crawler.session import resolve_update_plan
from connections_export.derive import derive as run_derive
from connections_export.gui import support
from connections_export.gui.archives import (
    resolve_archive,
    writable_archive,
    write_archive_summary,
)
from connections_export.gui.demo import (
    DEMO_SAMPLE_BASE_URL,
    run_demo,
)
from connections_export.gui.events import to_json
from connections_export.gui.requests import (
    CommunitySelection,
    _PaceRequest,
    _StartRequest,
)
from connections_export.gui.routes._lookup import (
    _authed_lookup,
    _lookup_base_auth,
)
from connections_export.gui.support import (
    _DONE,
    _IDLE_POLL_SECONDS,
    _new_run_archive_dir,
)
from connections_export.gui.wiki_url import parse_url


def selection_map(components: list[str]) -> dict[str, list[str]]:
    """One community's `"kind:id"` picks, as the dispatcher's mapping.

    Split on the FIRST colon only: an id may contain one, a kind never does.
    A value with no id is dropped rather than passed on -- a bare `"wiki:"`
    would reach the dispatcher as "every wiki in the deployment", which is not
    what an empty tick box means.
    """
    mapping: dict[str, list[str]] = {}
    for value in components:
        if not isinstance(value, str):
            continue
        kind, _, ident = value.partition(":")
        if kind and ident:
            mapping.setdefault(kind, []).append(ident)
    return mapping


def selected_communities(body) -> list[CommunitySelection]:
    """What this run captures, as a list, whichever shape the client sent.

    The legacy single-community fields fold into a list of one -- the console
    posts those today, and a saved CLI invocation may too, so they keep
    working without a second code path underneath them.

    When both shapes arrive the list wins: a client that knows the new shape is
    not also guessing with the old one, and honouring both would capture one
    community twice into the same archive.

    A community with nothing ticked is not captured. Unticking everything is
    how a community is removed, so there is no second control that can
    disagree with the checkboxes.
    """
    if body.communities:
        return [selection for selection in body.communities if selection.components]
    if body.community_uuid and body.community_components:
        return [
            CommunitySelection(
                uuid=body.community_uuid,
                title=body.target_label,
                components=list(body.community_components),
            )
        ]
    return []


def archive_scope(archive_dir, requested: list | None = None) -> dict[str, list[str]]:
    """Which components an update is allowed to touch, by kind.

    An update of an archive is an update OF WHAT IS IN IT. Nothing said so:
    `wiki_labels` was set only from a URL the user had identified, and an
    extend identifies no URL -- so it was `None`, and `crawl` enumerated
    every wiki on the deployment, which is an update pulling in content from
    wikis the archive never held.

    `requested` is the console's per-component selection (what to update, plus
    anything being added). The archive's own summary is the fallback, so a
    request that carries no selection is still scoped to the archive rather
    than to the whole deployment -- "nothing chosen" must not mean
    "everything there is".
    """
    scope: dict[str, list[str]] = {}
    for component in requested or []:
        kind = (component.get("kind") or "").strip() if isinstance(component, dict) else ""
        ident = (component.get("id") or "").strip() if isinstance(component, dict) else ""
        if kind and ident:
            scope.setdefault(kind, []).append(ident)
    if scope:
        return scope

    from connections_export.gui.archives import read_archive_summary  # noqa: PLC0415

    summary = read_archive_summary(archive_dir) or {}
    for group in summary.get("groups", []):
        kind = (group.get("kind") or "").strip()
        ident = (group.get("id") or "").strip() if group.get("id") else ""
        if kind and ident:
            scope.setdefault(kind, []).append(ident)
    return scope


def _merged_report(reports: list[dict]) -> dict:
    """One report for a run made of several components' reports.

    Summed rather than "the last one", which is what a per-component report
    would be: a run that captured a wiki and a blog did all of it, and a
    verdict quoting only the blog understates what happened.
    """
    counts: dict[str, int] = {}
    failures: dict[str, int] = {}
    truncated: list = []
    orphans: list = []
    pages = 0
    for report in reports:
        for key, value in (report.get("counts") or {}).items():
            counts[key] = counts.get(key, 0) + int(value or 0)
        for key, value in (report.get("failures_by_category") or {}).items():
            failures[key] = failures.get(key, 0) + int(value or 0)
        truncated.extend(report.get("possibly_truncated") or [])
        orphans.extend(report.get("orphans") or [])
        pages += int(report.get("pages_crawled") or 0)
    return {
        "counts": counts,
        "failures_by_category": failures,
        "possibly_truncated": truncated,
        "orphans": orphans,
        "pages_crawled": pages,
    }


def _archive_base_url(archive_dir) -> str | None:
    """The deployment an existing archive was captured from, or `None`."""
    try:
        from connections_export.archive.store import Archive  # noqa: PLC0415
        from connections_export.crawler.provenance import archive_base_url  # noqa: PLC0415

        return archive_base_url(Archive.open(archive_dir))
    except Exception:  # noqa: BLE001 - an unreadable archive simply says nothing
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
    """Register the run routes on `app`.

    Takes the names the handlers below close over, so each one reads the
    same here as it does at its call site.
    """

    #: The FastAPI app under a name no route parameter shadows -- `community_of`
    #: has a query parameter called `app` (the HCL app kind).
    _app = app

    def _forum_topic_id(url: str | None) -> str | None:
        """The forum topic uuid from a URL. Tries the shared `parse_url`
        first, then falls back to a `topicUuid=`/`topicId=`/`id=` query param
        (real search permalinks vary in shape). Works equally on a hit's HTML
        `alternate_url` or its Atom API `via_url` -- both are plain URLs, and
        the query-param fallback is shape-agnostic.

        Community-hosted forums often route through a hash-fragment UI URL
        (`.../communityview?communityUuid=...#fullpageWidgetId=...&topicId=...`,
        mirroring the wiki hash-route `#/wiki/{label}/...`), where the id sits
        in the fragment, not the query string -- so the fragment is checked
        the same way, both as a `key=value&...` string and as `/`-separated
        path segments (`#/forums/topic/{uuid}`)."""
        if not url:
            return None
        target = parse_url(url)
        if target.app == "forum" and target.topic_id:
            return target.topic_id
        from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

        split = urlsplit(url)
        keys = ("topicUuid", "topicId", "id")
        qs = parse_qs(split.query)
        for key in keys:
            if qs.get(key) and qs[key][0].strip():
                return qs[key][0].strip()
        if split.fragment:
            frag_qs = parse_qs(split.fragment.lstrip("/"))
            for key in keys:
                if frag_qs.get(key) and frag_qs[key][0].strip():
                    return frag_qs[key][0].strip()
            frag_segments = [s for s in split.fragment.split("/") if s]
            for index, segment in enumerate(frag_segments):
                if segment in ("topic", "topicId", "thread", "threadTopic") and index + 1 < len(
                    frag_segments
                ):
                    candidate = frag_segments[index + 1].split("?")[0].strip()
                    if candidate:
                        return candidate
        return None

    def _search_seeds(hcl_app: str, userid: str, community_uuid: str, base: str, auth_mode):
        """Run the person (+community) Search server-side and turn the hits into
        crawl seeds: forum topic ids (each expanded to a whole thread) or blog
        handles (whole blog, author-filtered). Prefers each hit's `via_url` --
        the Atom API "api" link `ibm-connections-search` itself reads -- over parsing the HTML
        `alternate_url`, whose UI shape varies by deployment; falls back to
        `alternate_url` if `via_url` is absent or unrecognised.

        Pages through the FULL result set (`pageSize=150`, HCL's documented
        max -- "pageSize max 150") instead
        of stopping after page 1: a person with more than 150 relevant items
        would otherwise have the rest silently missing from every run, with
        no indication anything was cut off. Stops at a genuinely last (partial)
        page, or a safety cap (never an infinite loop against a server that
        always claims a full page).

        Returns `(topic_ids, blog_handles, url, error, hit_count, samples)`
        -- `url` is the FIRST page's url (still useful to inspect the query
        shape); `hit_count` is the TOTAL across every page fetched; `error`
        is only a hard failure (page 1 didn't return usable results) UNLESS
        the safety cap was hit, in which case it's a non-fatal note appended
        to the caller's summary so a truncated crawl is never silent.
        `samples` are a few raw `(alternate_url, via_url)` pairs from hits
        that yielded no seed, for on-screen diagnosis when seeding comes up
        empty."""
        from connections_export.adapters.search import (  # noqa: PLC0415
            parse_search_results,
            search_results_url,
        )

        scope = {"wiki": "wikis:page", "blog": "blogs:entry", "forum": "forums:topic"}.get(hcl_app)
        page_size = 150
        max_pages = 40  # safety cap -- 6,000 hits is far beyond any real person's footprint
        first_url: str | None = None
        all_hits = []
        capped = False
        for page in range(1, max_pages + 1):
            url = search_results_url(
                base_url=base,
                userid=userid,
                community_uuid=community_uuid or None,
                scope=scope,
                page=page,
                page_size=page_size,
            )
            if first_url is None:
                first_url = url
            status, content, error = _authed_lookup(_app, url, base, auth_mode)
            if error is not None or status is None or status >= 400:
                if page == 1:
                    return [], [], url, (error or f"search returned {status}"), 0, []
                break  # a later page failing keeps what earlier pages already found
            page_hits = parse_search_results(content)
            all_hits.extend(page_hits)
            if len(page_hits) < page_size:
                break  # a partial page is the real last page
            if page == max_pages:
                capped = True
        hits = all_hits
        topic_ids: list[str] = []
        blog_handles: list[str] = []
        unmatched_samples: list[tuple[str | None, str | None]] = []
        for hit in hits:
            if not hit.alternate_url and not hit.via_url:
                continue
            if hcl_app == "forum":
                tid = _forum_topic_id(hit.via_url) or _forum_topic_id(hit.alternate_url)
                if tid and tid not in topic_ids:
                    topic_ids.append(tid)
                elif not tid and len(unmatched_samples) < 5:
                    unmatched_samples.append((hit.alternate_url, hit.via_url))
            elif hcl_app == "blog":
                via_target = parse_url(hit.via_url) if hit.via_url else None
                handle = via_target.blog_handle if via_target else None
                if not handle and hit.alternate_url:
                    handle = parse_url(hit.alternate_url).blog_handle
                if handle and handle not in blog_handles:
                    blog_handles.append(handle)
                elif not handle and len(unmatched_samples) < 5:
                    unmatched_samples.append((hit.alternate_url, hit.via_url))
        cap_note = (
            f"reached the {max_pages}-page search safety cap ({len(hits)} hits fetched) -- "
            f"more may exist"
            if capped
            else None
        )
        return topic_ids, blog_handles, first_url, cap_note, len(hits), unmatched_samples

    def _run_real(
        *,
        base_url: str,
        auth_mode: str | None,
        hcl_app: str,
        wiki_label: str | None,
        blog_handle: str | None,
        forum_uuid: str | None,
        topic_id: str | None,
        scope: str | None,
        max_entries: int | None,
        emit: Emit,
        output_dir: Path,
        author: str | None = None,
        topic_ids: list[str] | None = None,
        blog_handles: list[str] | None = None,
        community_selections: list[CommunitySelection] | None = None,
        min_interval: float = 1.0,
        into: Path | None = None,
        recheck_comments: bool = False,
        scope_components: dict[str, list[str]] | None = None,
    ) -> None:
        """A real (non-demo) start: build a
        real HttpClient + auth strategy and crawl the identified app --
        `crawl(..., wiki_labels=[wiki_label])` for a wiki, `crawl_blogs`
        for a blog, `crawl_forums` for a forum. Imports `cli.py`
        lazily -- `cli.py` imports `connections_export.gui` for `make_app`, so
        a module-level import here would be a circular import; by the
        time this function actually runs (a background thread, well
        after both modules have finished loading), the cycle is moot.

        `sspi`/`kerberos` (`http/auth.py`) are undialled stubs that
        raise once a handshake is actually attempted -- caught here and
        turned into an explicit `failed` event, then `run_complete`, so
        the stream always terminates instead of hanging forever
        waiting for events that a broken auth strategy will never
        produce (A missing real-auth path is surfaced).
        """
        from connections_export.cli import _build_default_client  # noqa: PLC0415

        # Pointing a run at an existing archive makes it an UPDATE, and the
        # cutoff comes from that archive's own provenance. Writing into an
        # archive is not the same as updating it: under the default `resume`
        # policy every URL already captured is answered from disk, so the run
        # would finish having asked the deployment nothing and report success.
        plan = resolve_update_plan(Config(base_url=base_url, into=into, output_dir=output_dir))
        config = Config(
            base_url=base_url,
            auth_mode=auth_mode or "sspi",
            output_dir=output_dir,
            fetch=plan.fetch,
            recheck_comments=recheck_comments,
            # Delay between requests (seconds), from the setup screen. Config
            # raises anything below its floor, so the clamp lives in one place
            # rather than here as well.
            min_interval=min_interval,
        )
        try:
            client = _build_default_client(config, env={})
            # Published so `/api/pace` can slow this run down while it runs:
            # the reason to do that only appears once it is under way.
            app.state.live_client = client
        except Exception as exc:
            safe_emit(
                emit,
                crawler_events.RunStarted(
                    run_id="n/a", base_url=base_url, source_version=config.source_version
                ),
            )
            safe_emit(
                emit,
                crawler_events.Failed(
                    url=base_url,
                    kind="transport",
                    error=(
                        f"auth mode {config.auth_mode!r} could not prepare a session "
                        f"(a real handshake isn't wired up yet): {exc}"
                    ),
                ),
            )
            safe_emit(emit, crawler_events.RunComplete(report=CrawlReport()))
            return

        # Store cookies + base_url so /api/live-pdf can reuse them.
        app.state.live_base_url = base_url
        try:
            app.state.live_cookies = [
                {"name": c.name, "value": c.value, "domain": c.domain or "", "path": c.path or "/"}
                for c in client.cookies.jar
            ]
        except Exception:
            app.state.live_cookies = []

        archive = Archive.open(config.output_dir)
        if into is not None:
            # Archive updates are scoped by the ledger, not by the stale app
            # left over from the URL that was identified before the archive
            # was opened. Dispatch every selected archived component together.
            run_selection(
                scope_components or {},
                config=config,
                client=client,
                archive=archive,
                emit=emit,
                max_entries=max_entries,
                author=author,
                since=plan.since,
                stop_event=app.state.stop_event,
            )
            archive_report = archive.report()
            safe_emit(
                emit,
                crawler_events.RunComplete(
                    report=CrawlReport(
                        counts=dict(archive_report.counts),
                        failures_by_category=dict(archive_report.failures_by_category),
                        possibly_truncated=list(archive_report.possibly_truncated),
                        pages_crawled=0,
                    )
                ),
            )
        elif hcl_app == "community":
            # Each component crawler uses the common engine, which emits a
            # completion event when it returns. A community run is one UI run,
            # so keep those component completions internal and emit one after
            # all selected components have finished.
            def component_emit(event: crawler_events.Event) -> None:
                if not isinstance(event, crawler_events.RunComplete):
                    emit(event)

            selections = [s for s in (community_selections or []) if s.components]
            if not selections:
                raise ValueError("select at least one community component")

            # Every selected community into ONE archive. Capturing the second
            # into a second archive would leave each knowing the other's
            # content only as a dead URL: `derive/crosslink.py` upgrades a
            # deployment link to an in-export one only when its target is
            # present in the SAME export, which is the whole reason a run
            # takes a set rather than one.
            for community in selections:
                if app.state.stop_event is not None and app.state.stop_event.is_set():
                    # Stopped between communities. The archive holds whatever
                    # completed; the run ends visibly below either way.
                    break
                print(
                    "connections-export: community "
                    f"{community.title or community.uuid}: "
                    + ", ".join(sorted(community.components)),
                    flush=True,
                )
                # One dispatcher, shared with the CLI. This was five per-kind
                # blocks here, four more in `cli._run_selected_crawls`, and a
                # separate single-app chain below -- three hand-written
                # coverages of one decision, one of which silently omitted an
                # entire app.
                run_selection(
                    selection_map(sorted(community.components)),
                    config=config,
                    client=client,
                    archive=archive,
                    emit=component_emit,
                    max_entries=max_entries,
                    author=author,
                    # So a scoped wiki capture records which community it was
                    # for: the marker lives only on the wikis list feed, which
                    # a scoped crawl skips.
                    community_uuid=community.uuid,
                    community_title=community.title,
                    # The cutoff, when this run is adding to an existing
                    # archive. None for a fresh capture, which is every
                    # crawl's default.
                    since=plan.since,
                    stop_event=app.state.stop_event,
                )
            archive_report = archive.report()
            safe_emit(
                emit,
                crawler_events.RunComplete(
                    report=CrawlReport(
                        counts=dict(archive_report.counts),
                        failures_by_category=dict(archive_report.failures_by_category),
                        possibly_truncated=list(archive_report.possibly_truncated),
                        pages_crawled=0,
                    )
                ),
            )
        elif hcl_app == "blog":
            # `blog_handle` is the UUID/handle of a specific blog extracted
            # from the pasted URL (e.g. `/blogs/{uuid}/entry/...`). It is
            # NOT the "homepage" handle that serves the full blog-list feed --
            # only the deployment's designated homepage blog does that. Always
            # fetch the full list via "homepage", then narrow to the one blog
            # the user identified via `blog_uuids`.
            # Search-driven: the blogs found by search (usually one per
            # community); else the single identified blog. The author filter
            # keeps whole matching entries + their comments.
            blog_uuids = blog_handles or ([blog_handle] if blog_handle else None)
            run_crawl_blogs(
                config=config,
                client=client,
                archive=archive,
                blogs_homepage="homepage",
                blog_uuids=blog_uuids,
                emit=emit,
                max_posts=max_entries,
                author=author,
                # The cutoff, when this run adds to an existing archive. The
                # community branch above got this and this chain did not, so an
                # "Extend & update" of a single blog re-read everything and
                # reported success -- the same value-computed-then-dropped bug
                # the CLI had, still live on the console's other path.
                since=plan.since,
                stop_event=app.state.stop_event,
            )
        elif hcl_app == "forum":
            # Search-driven: a list of seed topic ids -> each crawled as a WHOLE
            # thread (topic + all replies), never widening to the full forum.
            forum_topic_ids = topic_ids or ([topic_id] if topic_id else None)
            # `topic_ids` (this function's own plural param) being non-empty
            # means search-seeded -- always use the fast direct-fetch path
            # for those (never the forum_uuids list-crawl path below, which
            # would page-walk that ENTIRE forum's topics feed just to filter
            # down to the seeded ids by string match -- "hundreds fetched,
            # nothing new" for a forum with many other people's topics).
            # `forum_uuid` (singular -- the one container the user separately
            # identified/dragged) then becomes a CLIENT-SIDE restriction on
            # which forum a seeded topic must belong to, not the crawl
            # strategy -- see `crawl_forums`'s `restrict_to_forum_uuid`.
            is_search_driven = bool(topic_ids)
            run_crawl_forums(
                config=config,
                client=client,
                archive=archive,
                emit=emit,
                forum_uuids=(None if is_search_driven else ([forum_uuid] if forum_uuid else None)),
                topic_ids=forum_topic_ids,
                restrict_to_forum_uuid=forum_uuid if is_search_driven else None,
                # Single-thread / seeded scope: only fetch these topics (each a
                # whole thread), don't widen to the whole forum.
                direct_topic_only=bool(topic_ids)
                or (topic_id is not None and forum_uuid is None and scope == "single"),
                max_topics=max_entries,
                author=author,
                since=plan.since,
                stop_event=app.state.stop_event,
            )
        else:
            # An update is scoped to the archive it is updating. Without
            # this `wiki_labels` was None for an extend -- no URL was
            # identified, because you pick an archive rather than typing one
            # -- and `crawl` walked every wiki on the deployment.
            wiki_labels = (scope_components or {}).get("wiki") or (
                [wiki_label] if wiki_label else None
            )
            run_crawl(
                config=config,
                client=client,
                archive=archive,
                emit=emit,
                wiki_labels=wiki_labels,
                max_pages=max_entries,
                # No `since` here, deliberately: a wiki feed cannot be asked
                # "what changed since" (`profiles.WIKIS.since_encoding` is
                # "unsupported"), which is why `crawl` reads each page's own
                # entry instead. Passing one would be a TypeError.
                # Two-pass author filter: skip fetching images/attachments for
                # pages the user didn't author (blogs/forums still filter at
                # derive; their crawl-time pass is a follow-up).
                author=author,
                stop_event=app.state.stop_event,
            )

    @app.post("/api/start")
    def start(body: _StartRequest) -> JSONResponse:
        """Start a crawl in a background thread whose events feed a
        fresh shared queue that `/events` drains. `demo=true`, or simply no real `base_url` given,
        runs the demo pipeline (as `hcl-serve --demo` always has);
        otherwise a real crawl against `base_url` (see `_run_real`).
        """
        event_queue: queue.Queue = queue.Queue()
        app.state.event_queue = event_queue
        # Reset the stop event so a new run starts clean.
        app.state.stop_event.clear()

        #: Every component's crawl ends with its own `RunComplete`, so a run
        #: capturing five of them emits five. The console took the first as
        #: the end of the RUN: it announced "Complete", re-enabled Start, and
        #: the ingest carried on behind it -- including after Stop, which is
        #: how this was noticed. They are marked as what they are, and the one
        #: authoritative end is emitted below when the thread is actually done.
        component_reports: list[dict] = []

        def publish(payload: dict) -> None:
            """One way out: the stream and the run's own log, together.

            Events the run synthesizes -- the end of the run, an internal
            failure -- go out this way too, rather than onto the queue alone.
            The log is meant to BE the record of the run; a record missing the
            moment the run ended is missing the part you go looking for.
            """
            try:
                event_queue.put(payload)
                log_path = app.state.event_log_path
                if log_path is not None:
                    record = {
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                        "event": payload,
                    }
                    with app.state.event_log_lock:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(json.dumps(record, ensure_ascii=True) + "\n")
                            log_file.flush()
            except Exception:
                pass

        def emit(event: crawler_events.Event) -> None:
            # Fire-and-forget, mirroring `crawler.events.safe_emit`: a
            # consumer-side problem must never affect the crawl.
            try:
                payload = to_json(event)
                if isinstance(payload, dict) and payload.get("type") == "run_complete":
                    payload["final"] = False
                    report = payload.get("report")
                    if isinstance(report, dict):
                        component_reports.append(report)
                publish(payload)
            except Exception:
                pass

        # If no base_url was sent (e.g. the form field was blank) but the
        # server was not started in demo mode and a base_url is in the config,
        # use that rather than silently falling back to demo.
        effective_base_url = body.base_url
        editable = app.state.editable_settings
        effective_auth_mode = body.auth_mode or editable["auth_mode"]
        effective_min_interval = body.min_interval or editable["min_interval"]
        if not effective_base_url:
            effective_base_url = editable["base_url"]
        if not effective_base_url and not body.demo and not app.state.demo:
            from connections_export.config import load_config  # noqa: PLC0415

            try:
                _cfg = load_config({})
                effective_base_url = _cfg.base_url or None
            except Exception:
                pass

        # An explicit real base URL from the setup form wins over the server's
        # default demo mode. This lets `serve --demo` host the UI while a
        # dragged URL still starts a real authenticated import. Only an
        # explicit demo request or an absent URL uses the in-process fakeserver.
        #
        # `DEMO_SAMPLE_BASE_URL` is the exception: it is the placeholder host
        # the demo chips carry, and the demo fakeserver runs in-process behind
        # `run_demo` rather than being served over HTTP there. Treating it as a
        # real deployment sent a demo chip down the authenticated crawl path,
        # where it could only ever fail ("auth mode 'sspi' could not prepare a
        # session"). No real deployment can occupy that placeholder, so it
        # always means the demo -- the same reasoning `/api/feed-info` already
        # applies to it.
        # An update is an update OF something, and that something records the
        # deployment it came from. Read before deciding anything: extending an
        # archive means picking it from a list, not retyping an address, so
        # the field is normally blank -- and with a blank saved setting the
        # decision below fell through to the demo and wrote fabricated content
        # into a real archive.
        into_dir = resolve_archive(support.ARCHIVES_BASE, body.into) if body.into else None
        if body.into and into_dir is None:
            return JSONResponse(
                {"status": "error", "detail": f"no such archive: {body.into}"}, status_code=404
            )
        archive_base = _archive_base_url(into_dir) if into_dir is not None else None
        if not effective_base_url and archive_base:
            effective_base_url = archive_base

        use_demo = (
            body.demo
            or not effective_base_url
            or effective_base_url.rstrip("/") == DEMO_SAMPLE_BASE_URL
        )
        # ...but never INTO an archive that came from somewhere real. Getting
        # this wrong costs the one thing an archive is for, so it is refused
        # rather than resolved to something plausible.
        if (
            use_demo
            and not body.demo
            and body.into
            and (archive_base or "").rstrip("/") != DEMO_SAMPLE_BASE_URL
        ):
            # Including when the archive will not say where it came from: not
            # knowing is a reason to stop, not a reason to invent. Extending a
            # DEMO archive is exempt -- that is a real thing to want, and the
            # archive says so itself.
            came_from = (
                f"This archive was captured from {archive_base}."
                if archive_base
                else "This archive does not say which deployment it came from."
            )
            return JSONResponse(
                {
                    "status": "error",
                    "detail": (
                        f"{came_from} Updating it needs that deployment, and no address was "
                        "given — refusing rather than writing demo content into it."
                    ),
                },
                status_code=409,
            )

        # The run's archive dir -- a real, self-describing on-disk archive
        # (manifest + blobs), named so a demo announces itself as fake and
        # the user never has to choose a path. Attached
        # live *before* the crawl thread starts so `/api/model` derives a
        # growing snapshot and `/api/blob` resolves images while it runs.
        # An update writes INTO the archive it was pointed at. A new directory
        # would produce a second archive holding only the changes -- which is
        # the opposite of what "extend this one" means, and would leave the
        # user with two half-archives and no way to read them as one.
        if into_dir is not None and not writable_archive(into_dir):
            # A zipped archive reads but never takes a write. Refusing here,
            # before a run starts, is the difference between a clear no and a
            # crawl that appears to run and lands nowhere.
            return JSONResponse(
                {
                    "status": "error",
                    "detail": (
                        f"{body.into} is a zipped archive, which is read-only. "
                        "Unpack it to extend it."
                    ),
                },
                status_code=400,
            )
        run_dir = into_dir or _new_run_archive_dir(
            demo=use_demo,
            # Fall back through what the request does carry, so a run
            # started without a label still names something.
            label=(
                body.target_label
                # A set of communities is named for the first one added, which
                # is a guess a person can correct: the archives list offers a
                # rename, and it writes a name beside the data rather than
                # moving the directory the archive is identified by.
                or next(
                    (c.title for c in selected_communities(body) if c.title),
                    None,
                )
                or body.wiki_label
                or body.blog_handle
                or (body.forum_uuid if body.app == "forum" else None)
            ),
        )
        app.state.event_log_path = run_dir / "events.jsonl"
        run_author = (
            body.author.strip()
            if body.author and body.author.strip()
            else app.state.default_author_filter
        )
        app.state.model_source.attach_live(run_dir, author=run_author)
        print(f"connections-export: writing this run's archive to {run_dir}", flush=True)

        def _community_ids(kind: str) -> list[str] | None:
            """Ids of that kind across EVERY selected community.

            The console posts checkbox values as "kind:id" strings (console.js
            `selectedCommunityComponents`). A demo run of a set scopes to the
            union: two communities each contributing a forum means both
            forums, into the one archive.
            """
            out: list[str] = []
            for community in selected_communities(body):
                for value in community.components:
                    if not isinstance(value, str) or ":" not in value:
                        continue
                    kind_part, _, identifier = value.partition(":")
                    if kind_part == kind and identifier:
                        out.append(identifier)
            return out or None

        # A community is not one of the three apps a demo run can be filtered
        # to. Passing `app_filter="community"` matched none of them, so every
        # crawl block was skipped, the archive came out empty, and `derive`
        # then raised -- which is why the console switched to Ingest and then
        # sat there forever. Translate the selection into per-app scoping and
        # run exactly the apps it names.
        is_community = not body.demo and body.app == "community"
        community_wikis = _community_ids("wiki")
        community_blogs = (_community_ids("blog") or []) + (_community_ids("ideation_blog") or [])
        community_forums = _community_ids("forum")
        # Every kind the selection actually names. This was a hand-written
        # {wiki, blog, forum}, written when those were the only three apps;
        # Files and Rich Content landed the day after and this filter never
        # learned them, so a community ingest with every box ticked ingested
        # three kinds out of five -- offered by /api/community-components,
        # ticked by the user, then dropped here. `apps.BY_KIND` is the single
        # declaration (connections_export/apps.py), so a sixth app is in scope
        # the moment it is declared there rather than the day someone
        # remembers this line.
        # Every kind named by EVERY selected community: a demo run of a set
        # captures all of them into the one archive, exactly as a real run
        # does. Reading only the first community's components would have
        # silently dropped the rest.
        community_apps = {
            # An ideation blog is a blog with a different `kind`; the demo
            # gates both on the blog crawl, as crawler/dispatch.py does.
            "blog" if kind == "ideation_blog" else kind
            for community in selected_communities(body)
            # Same shape `selection_map` accepts: "kind:id" with a non-empty
            # id. A bare "kind:" would otherwise widen the run to that whole
            # app instead of the component that was ticked.
            for kind, ident in (
                (value.partition(":")[0], value.partition(":")[2])
                for value in community.components
                if isinstance(value, str)
            )
            if ident and kind in apps.BY_KIND
        }

        def _run_in_background() -> None:
            # Whether the end-of-run marker below has already gone out.
            emitted_final = False
            try:
                if use_demo:
                    if is_community and not community_apps:
                        # The same refusal `_run_real` raises for the real
                        # path. Without it an empty selection filtered the run
                        # down to no apps, so it crawled nothing and reported a
                        # clean, successful, empty run -- the invisible no-op
                        # that made "community ingest did nothing" so hard to
                        # see. Raised inside the run rather than returned as a
                        # 400 because `beginRun` does not check the response
                        # status: only an event reaches the user.
                        raise ValueError("select at least one community component")
                    # "Run the demo pipeline" (body.demo) = the whole thing (all
                    # apps, unlimited). "Start ingest" of a demo chip = scope to
                    # the identified app + honour the Preview limit. Either way the
                    # Stop button works (stop_event threaded in).
                    result = run_demo(
                        emit,
                        seed=demo_seed,
                        delay=demo_delay,
                        archive_dir=run_dir,
                        wave=body.demo_wave,
                        update=bool(body.into),
                        app_filter=(
                            None if body.demo else (community_apps if is_community else body.app)
                        ),
                        max_entries=None if body.demo else body.max_entries,
                        # Scope a chip's "Start ingest" to the identified wiki so
                        # the archive holds only that one wiki, not all of them
                        # ("I see all 3 wikis, I should only see one"). The whole
                        # demo pipeline (body.demo) stays unscoped.
                        # A scoped wiki capture records the community it
                        # was for; the demo has the same gap as a real run.
                        community_uuid=(
                            selected_communities(body)[0].uuid
                            if is_community and selected_communities(body)
                            else None
                        ),
                        community_title=(
                            selected_communities(body)[0].title
                            if is_community and selected_communities(body)
                            else None
                        ),
                        wiki_labels=(
                            community_wikis
                            if is_community
                            else (
                                [body.wiki_label]
                                if (not body.demo and body.app == "wiki" and body.wiki_label)
                                else None
                            )
                        ),
                        # Same scoping for blogs/forums: a chip's "Start ingest"
                        # imports only the identified blog / forum. Single-entry
                        # (one post/thread) is deliberately NOT scoped here yet:
                        # the entries/topics feed returns every sibling inline,
                        # so the reader would still show them (the #10/#32
                        # discovery-vs-derive gap); a "single post" import
                        # therefore brings in its whole blog for now.
                        blog_handles=(
                            (community_blogs or None)
                            if is_community
                            else (
                                [body.blog_handle]
                                if (not body.demo and body.app == "blog" and body.blog_handle)
                                else None
                            )
                        ),
                        forum_uuids=(
                            community_forums
                            if is_community
                            else (
                                [body.forum_uuid]
                                if (not body.demo and body.app == "forum" and body.forum_uuid)
                                else None
                            )
                        ),
                        stop_event=app.state.stop_event,
                    )
                    # the design, "Demo mode": stash the completed run's
                    # derived model so `/api/model` serves it (now marked
                    # `complete`) from here on, superseding the live snapshot.
                    app.state.model_source.stash(
                        result.interchange, archive_dir=result.archive_dir, author=run_author
                    )
                    write_archive_summary(result.archive_dir, result.interchange)
                else:
                    seed_topic_ids: list[str] | None = None
                    seed_blog_handles: list[str] | None = None
                    if body.source == "search" and (body.search_userid or "").strip():
                        s_base, s_auth = _lookup_base_auth(_app, effective_base_url)
                        tids, handles, s_url, s_err, hits, samples = _search_seeds(
                            body.app,
                            (body.search_userid or "").strip(),
                            (body.community_uuid or "").strip(),
                            s_base or effective_base_url,
                            s_auth,
                        )
                        print(
                            f"connections-export: search-seeded ingest — {hits} hits → "
                            f"{len(tids)} threads, {len(handles)} blogs from {s_url}",
                            flush=True,
                        )
                        # Always surface what search found, so an empty result is explained.
                        # When a forum search wasn't scoped to one identified forum
                        # (`forum_uuid` blank -- HCL Search has no server-side
                        # per-forum-instance filter, only community-level; see
                        # `crawl_forums`'s `restrict_to_forum_uuid`), say so up
                        # front so threads spanning several real forums the person
                        # posted in are never mistaken for a bug.
                        unscoped_note = (
                            " (no single forum identified — results may span every "
                            "forum this person posted in; drag/paste one forum's URL "
                            "first to restrict to just it)"
                            if body.app == "forum" and not body.forum_uuid
                            else ""
                        )
                        safe_emit(
                            emit,
                            crawler_events.Warning(
                                kind="truncation",
                                detail=(
                                    f"search seeding: {hits} hit(s) → {len(tids)} thread(s), "
                                    f"{len(handles)} blog(s)"
                                    + unscoped_note
                                    + (f" — {s_err}" if s_err else "")
                                ),
                                ref=s_url,
                            ),
                        )
                        seed_topic_ids = tids or None
                        seed_blog_handles = handles or None
                        if not seed_topic_ids and not seed_blog_handles:
                            # CRITICAL: never fall through to a global crawl of
                            # every forum/blog. Abort cleanly with a clear reason.
                            # Print (and surface) a couple of the actual unmatched
                            # hit URLs -- alternate_url and via_url -- right here,
                            # so the shape the seed parser choked on is visible in
                            # the run log without a separate preview step.
                            for alt, via in samples:
                                print(
                                    "connections-export: search seed unmatched — "
                                    f"alternate_url={alt!r} via_url={via!r}",
                                    flush=True,
                                )
                            sample_text = "; ".join(
                                f"alternate={alt or '(none)'} via={via or '(none)'}"
                                for alt, via in samples[:3]
                            )
                            detail = (
                                f"search-driven ingest found no usable seeds from "
                                f"{hits} hit(s) — nothing ingested. The hit URLs may "
                                f"have a shape the seed parser doesn't recognise."
                            )
                            if sample_text:
                                detail += f" Sample hit URL(s): {sample_text}"
                            safe_emit(
                                emit,
                                crawler_events.Warning(kind="truncation", detail=detail, ref=s_url),
                            )
                            safe_emit(
                                emit,
                                crawler_events.RunComplete(
                                    report=CrawlReport(
                                        counts={},
                                        failures_by_category={},
                                        possibly_truncated=[],
                                        orphans=[],
                                        pages_crawled=0,
                                    )
                                ),
                            )
                            return

                    _run_real(
                        base_url=effective_base_url,
                        auth_mode=effective_auth_mode,
                        hcl_app=body.app,
                        wiki_label=body.wiki_label,
                        blog_handle=body.blog_handle,
                        forum_uuid=body.forum_uuid,
                        topic_id=body.topic_id,
                        scope=body.scope,
                        max_entries=body.max_entries,
                        emit=emit,
                        output_dir=run_dir,
                        author=run_author,
                        topic_ids=seed_topic_ids,
                        blog_handles=seed_blog_handles,
                        community_selections=selected_communities(body),
                        min_interval=max(0.0, effective_min_interval),
                        # The archive this run adds to, when the console sent
                        # one. It is what turns "write into that directory"
                        # into an actual update.
                        into=into_dir,
                        recheck_comments=body.recheck_comments,
                        scope_components=(
                            archive_scope(into_dir, body.archive_components)
                            if into_dir is not None
                            else None
                        ),
                    )
                    # Best-effort: derive the final archive and stash it so
                    # the reader keeps working (and is marked `complete`)
                    # after the stream ends. A run that produced no feed
                    # (e.g. the auth gap) simply leaves the live snapshot
                    # pending -- nothing to stash.
                    try:
                        final = run_derive(Archive.open(run_dir))
                        app.state.model_source.stash(final, archive_dir=run_dir, author=run_author)
                        write_archive_summary(run_dir, final)
                    except Exception:
                        pass
            except Exception as exc:  # noqa: BLE001
                # Anything unhandled here would otherwise escape the thread,
                # leaving `finally` to close the stream with `_DONE` and
                # nothing else: no `run_complete`, no error, and a console
                # sitting on the Ingest screen forever. A run must always end
                # visibly, whatever went wrong.
                publish(
                    {
                        "type": "failed",
                        "url": effective_base_url or "",
                        "kind": "internal",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                publish(
                    {
                        "type": "run_complete",
                        "final": True,
                        "report": {
                            "counts": {},
                            "failures_by_category": {"internal": 1},
                            "possibly_truncated": [],
                            "orphans": [],
                            "pages_crawled": 0,
                        },
                    }
                )
                emitted_final = True
            finally:
                # The end of the RUN, once, whatever it was made of. Without
                # it a multi-component run has no moment that means "this is
                # over" -- only each component saying it is done.
                if not emitted_final:
                    publish(
                        {
                            "type": "run_complete",
                            "final": True,
                            "report": _merged_report(component_reports),
                        }
                    )
                event_queue.put(_DONE)

        thread = threading.Thread(target=_run_in_background, daemon=True)
        app.state.run_threads.append(thread)
        thread.start()
        return JSONResponse({"started": True})

    @app.post("/api/pace")
    def pace(body: _PaceRequest) -> JSONResponse:
        """Change the delay between requests of the run in flight.

        The reason to slow a capture down -- the deployment is struggling
        under it -- only becomes visible once it is running, and the
        alternative is to stop it, change the number and start again, which
        for a long crawl means throwing away hours.

        Applies to this run only. The saved default is a separate decision and
        is not touched here.
        """
        client = getattr(app.state, "live_client", None)
        if client is None:
            return JSONResponse(
                {"status": "idle", "detail": "no run is in flight"}, status_code=409
            )
        client.min_interval = body.min_interval
        return JSONResponse({"status": "ok", "min_interval": client.min_interval})

    @app.post("/api/stop")
    def stop_run() -> JSONResponse:
        """Signal the running crawl to stop gracefully after the current
        page/post/topic. The crawl thread finishes its current item, emits
        `run_complete`, and the stream closes normally."""
        app.state.stop_event.set()
        return JSONResponse({"stopped": True})

    @app.get("/events")
    async def stream_events() -> StreamingResponse:
        async def _generate():
            loop = asyncio.get_event_loop()
            # Idle "no run yet" state: wait for `/api/start`
            # to create a queue instead of erroring or starting a run
            # itself. A client that connects before any start just sees
            # an open stream that produces nothing yet -- exactly what a
            # setup screen that hasn't started an import should see.
            while app.state.event_queue is None:
                await asyncio.sleep(_IDLE_POLL_SECONDS)
            event_queue = app.state.event_queue
            while True:
                # Use a short timeout so the async task remains
                # cancellable during server shutdown without blocking
                # indefinitely in the thread-pool executor.
                try:
                    item = await loop.run_in_executor(None, lambda: event_queue.get(timeout=0.5))
                except queue.Empty:
                    continue
                if item is _DONE:
                    break
                yield f"data: {json.dumps(item)}\n\n"

        return StreamingResponse(_generate(), media_type="text/event-stream")
