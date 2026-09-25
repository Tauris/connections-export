"""`POST /api/ingest` with `archives: [...]` exports archives by name, and
combines several into one export; `dry_run: true` says what would happen
without writing anything.

The names are archive ids from the Archives screen, resolved through
`resolve_archive` exactly as every other archive route resolves them -- a
name that walks out of the archives folder is no archive at all.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from connections_export.gui import support
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo


def _post(app, body):
    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post("/api/ingest", json=body)

    return asyncio.run(_do())


@pytest.fixture
def two_archives():
    base = support.ARCHIVES_BASE
    run_demo(lambda _e: None, archive_dir=base / "wiki-run", delay=0, app_filter="wiki")
    run_demo(lambda _e: None, archive_dir=base / "blog-run", delay=0, app_filter="blog")
    return ["wiki-run", "blog-run"]


def test_several_archives_are_combined_into_one_folder_beside_them(two_archives):
    app = make_app(demo=True)

    response = _post(app, {"format": "hugo", "archives": two_archives})

    assert response.status_code == 200, response.text
    body = response.json()
    out = support.ARCHIVES_BASE / body["path"].rsplit("/", 1)[-1]
    assert body["path"] == str(out)
    assert out.name.startswith("combined-") and out.name.endswith("-hugo-content")
    assert (out / "content" / "wikis").is_dir() and (out / "content" / "blogs").is_dir()
    assert body["combine"]["archives"] == two_archives
    assert "2 archives combined" in body["summary"]
    assert "links_resolved" in body["combine"]


def test_the_same_selection_writes_the_same_folder():
    """The preview names the folder the export then writes, so the name
    cannot depend on the moment the button is pressed."""
    base = support.ARCHIVES_BASE
    run_demo(lambda _e: None, archive_dir=base / "a", delay=0, app_filter="wiki")
    run_demo(lambda _e: None, archive_dir=base / "b", delay=0, app_filter="wiki")
    app = make_app(demo=True)

    preview = _post(app, {"format": "jekyll", "archives": ["a", "b"], "dry_run": True}).json()
    written = _post(app, {"format": "jekyll", "archives": ["a", "b"]}).json()

    assert preview["path"] == written["path"]


def test_a_dry_run_previews_and_writes_nothing(two_archives):
    base = support.ARCHIVES_BASE
    run_demo(lambda _e: None, archive_dir=base / "wiki-again", delay=0, app_filter="wiki")
    app = make_app(demo=True)

    response = _post(
        app,
        {"format": "obsidian", "archives": [*two_archives, "wiki-again"], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["counts"]["wikis"] == 3 and body["counts"]["blogs"] == 2
    assert body["counts"]["pages"] > 0 and body["counts"]["posts"] > 0
    winners = {(c["kind"], c["winner"]) for c in body["combine"]["containers"]}
    assert winners == {("wiki", "wiki-again")}, "the newer capture of each wiki wins"
    assert body["combine"]["duplicates_total"] > 0
    assert [a["name"] for a in body["archives"]] == [*two_archives, "wiki-again"]
    assert all(a["display_name"] for a in body["archives"])
    before = sorted(p.name for p in base.iterdir())
    assert not any(name.startswith("combined-") for name in before)


def test_one_archive_by_name_is_an_ordinary_export(two_archives):
    app = make_app(demo=True)

    body = _post(app, {"format": "jekyll", "archives": ["blog-run"]}).json()

    assert body["path"] == str(support.ARCHIVES_BASE / "blog-run-jekyll-site")
    assert body["combine"] is None
    assert not (support.ARCHIVES_BASE / "blog-run-jekyll-site" / "sources.md").exists()


@pytest.mark.parametrize("name", ["../outside", "a/b", "..", "", "/etc"])
def test_a_name_outside_the_archives_folder_is_refused(two_archives, name):
    app = make_app(demo=True)

    response = _post(app, {"format": "hugo", "archives": ["wiki-run", name]})

    assert response.status_code == 404
    assert "no archive" in response.json()["error"]


def test_an_unknown_archive_is_named_in_the_error(two_archives):
    app = make_app(demo=True)

    response = _post(app, {"format": "hugo", "archives": ["wiki-run", "gone-run"]})

    assert response.status_code == 404
    assert "gone-run" in response.json()["error"]


def test_a_dry_run_of_the_open_archive_writes_nothing(tmp_path):
    archive_dir = tmp_path / "archive"
    run_demo(lambda _e: None, archive_dir=archive_dir, delay=0)
    app = make_app(demo=True, archive_dir=archive_dir)

    body = _post(app, {"format": "hugo", "dry_run": True}).json()

    assert body["dry_run"] is True and body["counts"]["wikis"] > 0
    assert body["path"] == str(tmp_path / "archive-hugo-content")
    assert not (tmp_path / "archive-hugo-content").exists()


def test_the_same_archive_twice_is_counted_once(two_archives):
    app = make_app(demo=True)

    body = _post(app, {"format": "hugo", "archives": ["wiki-run", "wiki-run"], "dry_run": True})

    assert body.json()["combine"] is None
