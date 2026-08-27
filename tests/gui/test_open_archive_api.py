import asyncio
import json

import httpx

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.archives import DEMO_SOURCE_HOSTS


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1")


def _seed_archive(tmp_path, name="export-host-20260101-000000-aaaa"):
    d = tmp_path / name
    d.mkdir(parents=True)
    return d


def test_archives_lists_base(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    _seed_archive(tmp_path)
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.get("/api/archives")

    r = asyncio.run(go())
    assert r.status_code == 200
    names = [a["name"] for a in r.json()["archives"]]
    assert "export-host-20260101-000000-aaaa" in names


def test_archives_reads_persisted_summary_without_deriving(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    archive = _seed_archive(tmp_path)
    (archive / "archive-summary.json").write_text(
        '{"status":"ok","groups":[{"kind":"forum","title":"Python","count":99}]}',
        encoding="utf-8",
    )
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.get("/api/archives")

    response = asyncio.run(go())
    assert response.json()["archives"][0]["summary"]["groups"][0]["count"] == 99


def test_archives_include_derived_titles_and_app_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    from connections_export.gui.demo import run_demo

    archive_dir = tmp_path / "DEMO-FAKE-DATA-20260101-000000-demo"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            listing = await c.get("/api/archives")
            summary = await c.get("/api/archive-summary?name=DEMO-FAKE-DATA-20260101-000000-demo")
            return listing, summary

    listing, summary_response = asyncio.run(go())
    assert listing.status_code == 200
    # A run persists its summary, so the listing can hand it straight over --
    # the console only asks for one separately when an archive has none stored
    # (an archive written before summaries existed, or a killed run).
    listed = listing.json()["archives"][0]["summary"]
    assert listed and listed["status"] == "ok"
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["status"] == "ok"
    assert any(group["kind"] == "wiki" and group["count"] > 0 for group in summary["groups"])


def test_archive_summary_rejects_unknown_or_traversal_names(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.get("/api/archive-summary?name=../etc")

    response = asyncio.run(go())
    assert response.status_code == 404


def test_open_archive_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.post("/api/open-archive", json={"name": "../etc"})

    r = asyncio.run(go())
    assert r.status_code == 404


def test_open_archive_empty_returns_422_not_a_stuck_pending_reader(tmp_path, monkeypatch):
    # An empty/partial archive (e.g. a failed real crawl) has nothing derivable.
    # Opening it must return a clear 422 with a detail -- NOT attach a pending
    # model that leaves the reader polling /api/model (503) forever. The client
    # shows the detail instead of switching to a spinning reader.
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    (tmp_path / "export-x").mkdir()
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.post("/api/open-archive", json={"name": "export-x"})

    r = asyncio.run(go())
    assert r.status_code == 422
    assert "detail" in r.json()


def test_delete_demo_archives_removes_only_demo_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    demo_dir = _seed_archive(tmp_path, "DEMO-FAKE-DATA-20260101-000000-demo")
    real_dir = _seed_archive(tmp_path, "export-host-20260101-000000-real")
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.post(
                "/api/delete-demo-archives",
                json={"name": "demo-archives", "confirmation": "DELETE DEMO ARCHIVES"},
            )

    response = asyncio.run(go())
    assert response.status_code == 200
    assert not demo_dir.exists()
    assert real_dir.exists()


def test_archive_listing_marks_demo_source_hosts_as_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    demo_names = [f"export-{host}-20260101-000000-demo" for host in DEMO_SOURCE_HOSTS]
    # The non-demo archive. Deliberately an RFC 2606 placeholder rather than
    # a real deployment host, which must never enter this repo -- the point
    # of the assertion is only that the name matches no demo prefix.
    real_name = "export-connect.example.com-20260101-000000-real"
    for name in [*demo_names, real_name]:
        _seed_archive(tmp_path, name)
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.get("/api/archives")

    flags = {item["name"]: item["is_demo"] for item in asyncio.run(go()).json()["archives"]}
    for name in demo_names:
        assert flags[name] is True
    assert flags[real_name] is False


# --- bulk demo-archive deletion --------------------------------------------
#
# Reported: a bulk delete returned 422 and never started. Root cause was the
# confirmation comparison, which was exact and untrimmed -- an invisible
# leading/trailing space made the server reply "type DELETE DEMO ARCHIVES",
# which is precisely what had been typed.


def _delete_demo(app, body):
    async def go():
        async with _client(app) as c:
            return await c.post("/api/delete-demo-archives", json=body)

    return asyncio.run(go())


def _lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_bulk_delete_accepts_a_confirmation_with_stray_whitespace(tmp_path, monkeypatch):
    """The reported bug: whitespace is invisible, so rejecting it tells the
    user to type exactly what they did type."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    demo_dir = _seed_archive(tmp_path, "DEMO-FAKE-DATA-20260101-000000-demo")
    app = make_app(demo=True)

    response = _delete_demo(app, {"confirmation": "  DELETE DEMO ARCHIVES \n"})

    assert response.status_code == 200
    assert not demo_dir.exists()


def test_bulk_delete_does_not_require_a_name(tmp_path, monkeypatch):
    """`name` was required by the shared request model but never read by this
    handler, so any non-browser caller got an opaque validation 422."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    _seed_archive(tmp_path, "DEMO-FAKE-DATA-20260101-000000-demo")
    app = make_app(demo=True)

    response = _delete_demo(app, {"confirmation": "DELETE DEMO ARCHIVES"})

    assert response.status_code == 200


def test_bulk_delete_still_refuses_a_wrong_confirmation_and_says_what_it_got(tmp_path, monkeypatch):
    """The gate stays: only the exact phrase deletes. The error now echoes
    what arrived so a mismatch is self-diagnosing instead of baffling."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    demo_dir = _seed_archive(tmp_path, "DEMO-FAKE-DATA-20260101-000000-demo")
    app = make_app(demo=True)

    response = _delete_demo(app, {"confirmation": "delete demo archives"})

    assert response.status_code == 422
    assert demo_dir.exists(), "nothing may be deleted on a refused confirmation"
    assert "delete demo archives" in response.json()["error"]


def test_bulk_delete_streams_progress_per_archive(tmp_path, monkeypatch):
    """1200+ archives take real time, so the response streams one record per
    archive rather than going silent until every directory is gone."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    for i in range(5):
        _seed_archive(tmp_path, f"DEMO-FAKE-DATA-2026010{i}-000000-demo{i}")
    real_dir = _seed_archive(tmp_path, "export-host-20260101-000000-real")
    app = make_app(demo=True)

    response = _delete_demo(app, {"confirmation": "DELETE DEMO ARCHIVES"})
    records = _lines(response)

    progress = [r for r in records if r.get("event") == "deleted"]
    assert len(progress) == 5
    assert [r["index"] for r in progress] == [1, 2, 3, 4, 5]
    assert all(r["total"] == 5 for r in progress)

    final = records[-1]
    assert final["event"] == "done"
    assert final["deleted"] == 5
    assert real_dir.exists()


# --- selective multi-archive deletion ---------------------------------------
#
# Typing each archive's full name is protection against deleting the WRONG
# archives, but it does not scale to the pile that repeated attempts at one
# target produce. The batch is confirmed by typing how many are selected: a
# number read off the screen proves the scope was seen, and it changes every
# time so it never becomes muscle memory the way a fixed phrase does.


def _delete_selected(app, body):
    async def go():
        async with _client(app) as c:
            return await c.post("/api/delete-archives", json=body)

    return asyncio.run(go())


def test_selected_archives_are_deleted_when_the_count_is_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    doomed = [_seed_archive(tmp_path, f"export-host-2026010{i}-000000-x{i}") for i in range(3)]
    kept = _seed_archive(tmp_path, "export-host-20260109-000000-keep")
    app = make_app(demo=True)

    response = _delete_selected(app, {"names": [d.name for d in doomed], "confirmation": "3"})

    assert response.status_code == 200
    assert all(not d.exists() for d in doomed)
    assert kept.exists()


def test_a_count_that_disagrees_with_the_selection_deletes_nothing(tmp_path, monkeypatch):
    """The guard against a stale list: if what is selected no longer matches
    the number that was read off the screen, nothing is removed."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    a = _seed_archive(tmp_path, "export-host-20260101-000000-a")
    b = _seed_archive(tmp_path, "export-host-20260102-000000-b")
    app = make_app(demo=True)

    response = _delete_selected(app, {"names": [a.name, b.name], "confirmation": "3"})

    assert response.status_code == 422
    assert a.exists() and b.exists()


def test_an_empty_selection_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True)

    assert _delete_selected(app, {"names": [], "confirmation": "0"}).status_code == 422


def test_selected_delete_streams_progress_and_reports_unknown_names(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    real = _seed_archive(tmp_path, "export-host-20260101-000000-a")
    app = make_app(demo=True)

    response = _delete_selected(app, {"names": [real.name, "does-not-exist"], "confirmation": "2"})
    records = _lines(response)

    assert response.status_code == 200
    assert [r["index"] for r in records if r.get("event") == "deleted"] == [1, 2]
    final = records[-1]
    assert final["event"] == "done"
    assert final["deleted"] == 1
    assert [f["name"] for f in final["failed"]] == ["does-not-exist"]
    assert not real.exists()


def test_selected_delete_refuses_a_name_that_escapes_the_archive_base(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    outside = tmp_path.parent / "not-an-archive"
    outside.mkdir(exist_ok=True)
    app = make_app(demo=True)

    response = _delete_selected(app, {"names": ["../not-an-archive"], "confirmation": "1"})
    final = _lines(response)[-1]

    assert final["deleted"] == 0
    assert outside.exists(), "path traversal must never reach outside the archive base"


def test_archives_listing_flags_superseded_runs(tmp_path, monkeypatch):
    """So the console can offer 'select superseded' without re-deriving
    1200 archives to work out which target each one captured."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    summary = json.dumps(
        {
            "status": "ok",
            "base_url": "https://connections.example.com",
            "groups": [{"kind": "forum", "title": "Help & Support", "count": 2}],
            "communities": [],
        }
    )
    import os

    for i, mtime in ((0, 1000), (1, 2000)):
        d = _seed_archive(tmp_path, f"export-host-2026010{i}-000000-a{i}")
        (d / "archive-summary.json").write_text(summary, encoding="utf-8")
        os.utime(d, (mtime, mtime))
    app = make_app(demo=True)

    async def go():
        async with _client(app) as c:
            return await c.get("/api/archives")

    flags = {a["name"]: a["is_superseded"] for a in asyncio.run(go()).json()["archives"]}
    assert flags["export-host-20260100-000000-a0"] is True
    assert flags["export-host-20260101-000000-a1"] is False
