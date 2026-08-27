"""Starting an ingest from a demo chip must run the demo pipeline.

A demo chip's URL carries the placeholder host `DEMO_SAMPLE_BASE_URL`. The
demo fakeserver runs in-process behind `run_demo` and never serves that host
over HTTP, so a "real" crawl against it can only ever fail -- as it did, with
`auth mode 'sspi' could not prepare a session`. `/api/feed-info` already
special-cases the placeholder for exactly this reason; `/api/start` did not.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL, demo_sample_urls


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _events(app, body, limit=8000, timeout=90):
    import json
    import time

    async def go():
        async with _client(app) as c:
            r = await c.post("/api/start", json=body)
            assert r.status_code == 200, r.text
            seen = []
            t0 = time.time()
            async with c.stream("GET", "/events") as s:
                async for line in s.aiter_lines():
                    if line.startswith("data: "):
                        seen.append(json.loads(line[6:]))
                        if len(seen) >= limit or time.time() - t0 > timeout:
                            break
                        if seen[-1].get("type") == "run_complete":
                            break
            return seen

    return asyncio.run(go())


def test_ingest_from_a_demo_chip_url_does_not_attempt_real_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    events = _events(app, {"base_url": DEMO_SAMPLE_BASE_URL, "auth_mode": "sspi", "demo": False})

    failures = [e for e in events if e.get("type") == "failed"]
    assert not any("sspi" in str(e.get("error", "")).lower() for e in failures), (
        f"a demo chip must never reach the authenticated crawl path: {failures}"
    )


def test_ingest_from_a_demo_chip_url_actually_captures_something(tmp_path, monkeypatch):
    """Not attempting auth is not enough -- the run must produce real pages."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    events = _events(app, {"base_url": DEMO_SAMPLE_BASE_URL, "auth_mode": "sspi", "demo": False})

    completed = [e for e in events if e.get("type") == "run_complete"]
    if completed:
        assert completed[0]["report"]["pages_crawled"] > 0
    else:
        assert any(e.get("type") in {"page", "fetched", "discovered"} for e in events), (
            "the demo pipeline produced no crawl activity at all"
        )


def test_demo_sample_urls_include_a_community():
    """A community is one of the four things the tool can be pointed at, and
    without a chip for it the whole community flow cannot be exercised without
    a live deployment."""
    kinds = {chip["kind"] for chip in demo_sample_urls()}

    assert "community-all" in kinds, f"no community chip among {sorted(kinds)}"


# --- community ingest -------------------------------------------------------
#
# `/api/start` scoped a demo run by app: `app_filter` is one of
# "wiki"|"blog"|"forum"|None, and the per-app id lists were each gated on
# `body.app` being that app. A community is none of those, so `app_filter`
# became "community", every crawl block was skipped, and the selected
# `community_components` were never consulted -- the console switched to
# Ingest and then nothing happened at all.


def _community_start_body(components):
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    return {
        "base_url": DEMO_SAMPLE_BASE_URL,
        "auth_mode": "sspi",
        "app": "community",
        "community_uuid": DEMO_COMMUNITY_UUID,
        "community_components": components,
        "demo": False,
    }


def _component_values(app):
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    async def go():
        async with _client(app) as c:
            r = await c.get(
                "/api/community-components",
                params={"community_uuid": DEMO_COMMUNITY_UUID, "base_url": DEMO_SAMPLE_BASE_URL},
            )
            return r.json()["components"]

    # The console posts the checkbox values, which are "kind:id" strings.
    return [f"{c['kind']}:{c['id']}" for c in asyncio.run(go())]


def test_community_ingest_actually_captures_the_selected_components(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)
    values = _component_values(app)
    assert values, "the demo community should offer components to select"

    events = _events(app, _community_start_body(values))

    completed = [e for e in events if e.get("type") == "run_complete"]
    assert completed, "the community run never completed -- it emitted nothing"
    assert completed[0]["report"]["pages_crawled"] > 0, "the community run captured nothing"


def test_community_ingest_captures_every_selected_kind_not_just_three(tmp_path, monkeypatch):
    """Selecting Files and Highlights must actually ingest them.

    The demo path filtered the run through a hand-written {wiki, blog, forum}
    set, written when those were the only three apps. Files and Rich Content
    landed afterwards and this filter never learned about them, so a community
    ingest with every box ticked silently captured three kinds out of five --
    the checkbox was honoured by the UI, offered by /api/community-components,
    and then dropped on the floor. The sibling test above only asserted that
    *something* was captured, which is why it never noticed.
    """
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)
    values = _component_values(app)
    selected_kinds = {v.partition(":")[0] for v in values}
    assert {"files", "rich_content"} <= selected_kinds, (
        "the demo community should offer Files and Highlights to select"
    )

    events = _events(app, _community_start_body(values))
    types = {e.get("type") for e in events}

    assert "community_file_derived" in types, "Files was selected but nothing was ingested from it"
    assert "rich_content_derived" in types or "rich_content_discovered" in types, (
        "Highlights (rich content) was selected but nothing was ingested from it"
    )


def test_community_ingest_scopes_to_only_the_selected_components(tmp_path, monkeypatch):
    """Selecting one forum must not drag in the community's wikis and blog."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)
    one_forum = [v for v in _component_values(app) if v.startswith("forum:")][:1]

    events = _events(app, _community_start_body(one_forum))

    completed = [e for e in events if e.get("type") == "run_complete"]
    assert completed, "the scoped community run never completed"
    counts = completed[0]["report"]["counts"]
    assert not any("wiki" in str(k).lower() for k in counts), f"wikis leaked in: {counts}"


def test_community_ingest_with_nothing_selected_fails_visibly(tmp_path, monkeypatch):
    """An empty selection must say so, not quietly capture nothing.

    The real path raises ValueError for this ("select at least one community
    component"). The demo path had no such guard: an empty selection filtered
    the run down to no apps at all, so it crawled nothing and reported a clean,
    successful, entirely empty run -- the same invisible no-op that made
    "community ingest did nothing" so hard to see the first time. The console
    blocks this client-side, which is exactly why the server must not rely on
    it.
    """
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    events = _events(app, _community_start_body([]))
    types = [e.get("type") for e in events]

    assert "failed" in types, f"an empty selection ran silently: {sorted(set(types))}"
    failed = next(e for e in events if e.get("type") == "failed")
    assert "component" in str(failed.get("error", "")).lower(), failed
    # A run must always end visibly, whatever went wrong.
    assert "run_complete" in types, "the failed run never ended"


def test_a_crashing_run_reports_a_failure_instead_of_going_silent(tmp_path, monkeypatch):
    """An exception in the background run put `_DONE` on the queue and nothing
    else, so `/events` closed with no `run_complete` and the console sat on the
    Ingest screen forever. Whatever goes wrong, the run must terminate visibly.
    """
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic crawl failure")

    # `run_demo` is called from `gui/routes/run.py` now -- patch where it is
    # looked up, not where it is defined.
    from connections_export.gui.routes import run as routes_run

    monkeypatch.setattr(routes_run, "run_demo", boom)
    app = make_app(demo=True, demo_delay=0)

    events = _events(app, {"demo": True})

    assert any(e.get("type") == "run_complete" for e in events), (
        "a crashed run must still close the stream with run_complete"
    )
    failures = [e for e in events if e.get("type") == "failed"]
    assert failures, "a crashed run must report why"
    assert "synthetic crawl failure" in str(failures[0].get("error", ""))


def test_read_only_lookups_do_not_authenticate_against_the_demo_placeholder(tmp_path, monkeypatch):
    """Identifying a demo chip puts `DEMO_SAMPLE_BASE_URL` in the setup
    screen's base-URL field, and every read-only lookup picked it up and tried
    to authenticate against a host that does not exist -- so "Only me"
    reported an SSPI package error naming the placeholder."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    async def go(path):
        async with _client(app) as c:
            return await c.get(path, params={"base_url": DEMO_SAMPLE_BASE_URL})

    current = asyncio.run(go("/api/current-user"))
    # The demo answers this now -- from its own fake, as one of the people in
    # its own data. What must never come back is evidence that a real
    # handshake was attempted against a host that does not exist, which is
    # what this guards and what the placeholder short-circuit is for.
    assert current.status_code == 200, current.text
    assert "sspi" not in current.text.lower()
    assert current.json()["name"]
