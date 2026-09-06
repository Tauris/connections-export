"""`POST /api/start` (background-thread crawl into the
shared queue, demo vs real path), `POST /api/identify`, and `/events`
now just streaming whatever `/api/start` fed the shared queue -- all
driven in-process via `httpx.ASGITransport`, per the testing
section ("offline, no real sockets").

**The contract:** `/events` does not auto-start anything on connect --
a run is started explicitly via `POST /api/start`, and `/events` just
drains whatever queue that created. Every test below that reads
`/events` therefore `POST /api/start` first; `test_events_endpoint_without_demo_mode_
closes_immediately` (which asserted the *old* auto-start-then-
immediately-close behaviour for `demo=False`) is replaced by
`test_events_waits_idle_until_a_run_is_started`, which asserts the
*new* contract instead: with no run started, `/events` stays open and
produces nothing (bounded by a short timeout, so the test itself never
hangs) rather than erroring or auto-starting anything.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from tests._hostcheck import find_disallowed_hosts
from tests.gui.conftest import read_sse


def _run(coro):
    return asyncio.run(coro)


async def _post_json(client: httpx.AsyncClient, path: str, body: dict) -> httpx.Response:
    return await client.post(path, json=body)


async def _stream_events(
    app,
    *,
    read_limit: int | None = None,
    last_event_id: str | None = None,
    ids: list[str] | None = None,
) -> list[dict]:
    """Connect to `/events` and collect every event.

    A real SSE event is a block of `field: value` lines, not one `data:`
    line -- this reads them the way a browser does, so a stream that also
    carries `id:` (the thing a browser hands back when it reconnects) is
    parsed rather than skipped. `ids` collects those, and `last_event_id`
    sends one, which is how a dropped-and-resumed connection is reproduced
    without a real network.

    `read_limit` stops early, leaving the connection open -- the caller then
    exits the `async with` block, which is what actually disconnects.
    """
    transport = httpx.ASGITransport(app=app)
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    collected: list[dict] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        async with client.stream("GET", "/events", headers=headers) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            await read_sse(response, collected, read_limit=read_limit, ids=ids)
    return collected


async def _start_then_stream(app, body: dict, *, read_limit: int | None = None) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        start_response = await _post_json(client, "/api/start", body)
        assert start_response.status_code == 200
        assert start_response.json() == {"started": True}
        collected: list[dict] = []
        async with client.stream("GET", "/events") as response:
            assert response.status_code == 200
            await read_sse(response, collected, read_limit=read_limit)
    return collected


def test_health_endpoint():
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get("/api/health")

    response = _run(_do())

    assert response.status_code == 200
    # No `demo` key: there is no mode to report. Which deployment gets read
    # follows from the URL of each request, so a server-wide answer would be
    # a claim this process is not entitled to make.
    assert response.json() == {"status": "ok"}


# --- POST /api/start {demo: true} -> /events streams run_started..run_complete


def test_start_demo_then_events_streams_run_started_to_run_complete():
    app = make_app(demo=True, demo_seed=10, demo_delay=0)

    events = _run(_start_then_stream(app, {"demo": True}))

    assert events, "no events were streamed at all"
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_complete"
    assert any(e["type"] == "page_derived" for e in events)
    assert any(e["type"] == "fetched" for e in events)
    for event in events:
        assert isinstance(event, dict)
        assert "type" in event


def test_two_event_streams_each_receive_the_complete_run_history():
    """Two readers, each getting the whole run.

    One shared queue meant they stole events from each other -- a
    reconnecting console, a second tab, or a browser re-establishing a
    dropped EventSource each got a fraction of the run and no sign that
    anything was missing.

    The second connects strictly AFTER the first has seen the run end, which
    is the case that matters and the harder one: everything it is owed has
    already been sent to somebody else. Under a timeout, because the failure
    mode here is a stream that never ends rather than one that ends wrong --
    a hang is a failure, and a test that hangs reports nothing.
    """
    app = make_app(demo=True, demo_seed=10, demo_delay=0)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await _post_json(client, "/api/start", {"demo": True})
            assert response.status_code == 200
        first = await asyncio.wait_for(_stream_events(app), timeout=60)
        # The run is over: `_stream_events` only returns when the stream
        # terminates, and the stream only terminates at the end of the run.
        second = await asyncio.wait_for(_stream_events(app), timeout=60)
        return first, second

    first, second = _run(_do())

    assert first
    assert first == second
    assert first[0]["type"] == "run_started"
    assert first[-1]["type"] == "run_complete"
    assert any(event["type"] == "forum_topic_derived" for event in first)


def test_a_stream_opened_after_the_run_ended_still_ends():
    """The replayed history has to carry the run's ending, not just its
    events.

    A console reloaded after a capture finishes replays every event and then
    waits -- forever, if the terminator was only ever delivered live to
    whoever was listening at the time. Nothing on screen says so: the run
    reads as complete because the events say so, while the connection stays
    open behind it.
    """
    app = make_app(demo=True, demo_seed=10, demo_delay=0)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await _post_json(client, "/api/start", {"demo": True})
            assert response.status_code == 200
        # Drain it once so the run is definitely over and its terminator has
        # definitely been handed to somebody else.
        await asyncio.wait_for(_stream_events(app), timeout=60)
        return await asyncio.wait_for(_stream_events(app), timeout=30)

    replayed = _run(_do())
    assert replayed[-1]["type"] == "run_complete"


def test_a_dropped_connection_resumes_instead_of_replaying_the_run():
    """A browser re-establishing a dropped EventSource, over HTTP.

    The console aggregates in the browser -- the counters, the tree, the log
    rows are JS accumulators fed by these events, and the server keeps no
    running totals. So a reconnect that is answered with the run so far does
    not just waste bytes: every number on screen counts it twice, while the
    server-computed report in `run_complete` does not, and the discrepancy
    reads as a bug in the crawler rather than in the transport.

    EventSource sends `Last-Event-ID` on its own, without the page knowing
    it happened, so this is what a dropped connection silently does.
    """
    app = make_app(demo=True, demo_seed=10, demo_delay=0)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await _post_json(client, "/api/start", {"demo": True})
            assert response.status_code == 200
        # Read a few events, then drop the connection mid-run.
        ids: list[str] = []
        first = await asyncio.wait_for(_stream_events(app, read_limit=5, ids=ids), timeout=60)
        # ...and come back the way the browser does.
        rest = await asyncio.wait_for(_stream_events(app, last_event_id=ids[-1]), timeout=60)
        return first, rest

    first, rest = _run(_do())

    assert len(first) == 5
    assert rest, "the resumed connection got nothing"
    # Not one event delivered twice: the two halves are disjoint and, put
    # end to end, are the run.
    assert not [event for event in rest if event in first]
    assert first[0]["type"] == "run_started"
    assert rest[-1]["type"] == "run_complete"


def test_server_shutdown_wakes_an_open_event_stream():
    """The shutdown terminator has to travel the stream, not a queue beside
    it.

    An open SSE connection is a task uvicorn will otherwise cancel mid-read;
    the lifespan wakes it first so it closes cleanly. That wake-up went to a
    queue that stopped being the one readers listen on, and nothing failed --
    the reader simply stopped being woken, which shows up as a shutdown that
    hangs rather than as a test that goes red.
    """

    app = make_app(demo=True, demo_seed=10, demo_delay=0)
    app.state.event_stream.reset()
    subscription = app.state.event_stream.subscribe()
    assert subscription.history == []

    async def _cycle_lifespan():
        async with app.router.lifespan_context(app):
            pass

    _run(_cycle_lifespan())

    assert subscription.queue.get_nowait() is gui_support._DONE


def test_run_events_are_persisted_next_to_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_seed=10, demo_delay=0)

    events = _run(_start_then_stream(app, {"demo": True}))

    log_paths = list(tmp_path.glob("DEMO-FAKE-DATA-*/events.jsonl"))
    assert len(log_paths) == 1
    records = [json.loads(line) for line in log_paths[0].read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(events)
    assert records[0]["event"]["type"] == "run_started"
    assert records[-1]["event"]["type"] == "run_complete"
    assert all(record["timestamp"] for record in records)


def test_start_without_any_deployment_is_refused_rather_than_demoed():
    """Reading an omitted `base_url` as "the demo" means a console with
    nothing configured reads the synthetic deployment while a real
    community is in front of it, and writes fabricated content into
    whatever archive is open.

    Nothing to read is not a reason to read something else. It is refused,
    and the refusal says where to get an address -- including that the demo
    is one of the URLs on offer."""
    app = make_app(demo_seed=11, demo_delay=0)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await _post_json(client, "/api/start", {})

    response = _run(_do())

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "No deployment was given" in detail
    assert "demo" in detail.lower(), "the refusal does not mention what can be tried"


def test_naming_the_demos_address_runs_the_demo_pipeline():
    """The demo is reached the way every deployment is: by its address."""
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    app = make_app(demo_seed=11, demo_delay=0)

    events = _run(_start_then_stream(app, {"base_url": DEMO_SAMPLE_BASE_URL}))

    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_complete"


def test_explicit_base_url_leaves_demo_pipeline():
    """A real URL from the setup form must route to the live path even when
    the console server itself was started with `--demo`.

    The stand-in deliberately is NOT `DEMO_SAMPLE_BASE_URL`. That host belongs
    to the demo chips and the fakeserver only ever serves it in-process, so it
    now always means the demo -- using it here to represent "a real URL" was
    the very conflation that sent demo chips down the authenticated crawl path
    (see tests/gui/test_demo_chip_ingest.py)."""
    app = make_app(demo=True, demo_seed=14, demo_delay=0)

    events = _run(
        _start_then_stream(app, {"base_url": "https://connections.example.corp", "demo": False})
    )

    assert events[-1]["type"] == "run_complete"
    assert not any(e["type"] == "page_derived" for e in events), "real path used demo data"
    assert any(e["type"] == "failed" for e in events), "live path did not report its failure"


def test_start_returns_started_true():
    app = make_app(demo=True, demo_seed=12, demo_delay=0)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await _post_json(client, "/api/start", {"demo": True})
            # Drain to completion so the background thread doesn't
            # outlive the test process as a dangling daemon mid-run.
            async with client.stream("GET", "/events") as stream:
                async for _chunk in stream.aiter_text():
                    pass
            return response

    response = _run(_do())

    assert response.status_code == 200
    assert response.json() == {"started": True}


def test_disconnecting_mid_stream_does_not_error_and_the_crawl_completes():
    app = make_app(demo=True, demo_seed=13, demo_delay=0)

    events = _run(_start_then_stream(app, {"demo": True}, read_limit=1))

    assert events and events[0]["type"] == "run_started"
    assert app.state.run_threads, "no background run thread was recorded"
    thread = app.state.run_threads[-1]
    thread.join(timeout=10)
    assert not thread.is_alive(), "the background crawl never completed after client disconnect"


def test_events_waits_idle_until_a_run_is_started():
    """New contract:
    `/events` no longer auto-starts anything and no longer closes
    immediately either -- with no run started it just... waits.

    `httpx.ASGITransport` runs the ASGI app to full completion before
    handing back a response at all (it buffers the whole body), so an
    `/events` connection that's genuinely waiting forever can only be
    observed by bounding the *whole* request with a timeout and
    confirming it's still going -- not by streaming and timing out on
    the next chunk (there is no partial response to see until the ASGI
    call itself returns). `asyncio.wait_for` cancels the still-pending
    request after a short window; that the request was still pending
    (never got a chance to respond) *is* the "waiting for a start"
    behaviour, as opposed to an immediate close or an auto-started
    demo."""
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(client.get("/events"), timeout=0.2)

    _run(_do())


def test_index_serves_the_console_html():
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get("/")

    response = _run(_do())

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>" in response.text.lower() or "<title>" in response.text


def test_index_html_references_no_external_hosts():
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get("/")

    response = _run(_do())

    assert find_disallowed_hosts(response.text) == set()


# --- POST /api/identify {url} -----------------------------------------------


def test_identify_returns_the_parse_result_for_a_recognised_url():
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post(
                "/api/identify",
                json={
                    "url": "https://example.corp/wikis/basic/api/wiki/eng-handbook/nav/feed",
                },
            )

    response = _run(_do())

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["base_url"] == "https://example.corp"
    assert body["wiki_label"] == "eng-handbook"
    assert body["auth_root"] == "basic"


def test_identify_reports_ok_false_for_an_unrecognised_url():
    app = make_app(demo=True)

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post("/api/identify", json={"url": "https://example.com/x"})

    response = _run(_do())

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["base_url"] is None
    assert body["wiki_label"] is None
    assert body["reason"]


# --- real (non-demo) start: auth gap is surfaced, never a hang -------------


def test_real_start_with_a_stub_auth_strategy_surfaces_the_gap_not_a_hang():
    """the design scenario "A missing real-auth path is surfaced": a real
    (non-demo) start whose auth strategy can't authenticate (sspi/
    kerberos are stubs -- see connections_export/http/auth.py) must report that
    explicitly through the event stream rather than the run hanging
    forever. No real socket/domain involved: `auth_mode` defaults to
    `sspi`, which raises before ever touching the network."""
    app = make_app(demo=False)

    events = _run(
        _start_then_stream(
            app,
            {
                "base_url": "https://example.corp",
                "auth_mode": "sspi",
                "wiki_label": "eng-handbook",
            },
        )
    )

    assert events, "a real start with a broken auth strategy must still emit something"
    assert events[-1]["type"] == "run_complete", "the stream must terminate, never hang"
    assert any(e["type"] == "failed" for e in events), "the auth gap must be surfaced explicitly"
    failed = next(e for e in events if e["type"] == "failed")
    assert "auth" in failed["error"].lower() or "sspi" in failed["error"].lower()
