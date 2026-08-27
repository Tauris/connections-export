"""An archive is named for its first community, and the user can rename it.

The directory name stays the archive's id. `resolve_archive`, "extend &
update" and every `into:` reference address an archive by it, so a rename that
moved directories would quietly break all of them. A rename writes a name
beside the data instead.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.archives import SUMMARY_FILENAME, list_archives


def _put(app, path, payload):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.put(path, json=payload)

    return asyncio.run(go())


def _archive(tmp_path, name="export-20260823-101010-platform-engineering-ab12"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "manifest.jsonl").write_text('{"url": "u"}\n', encoding="utf-8")
    (directory / SUMMARY_FILENAME).write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    return directory


def test_renaming_stores_a_name_and_leaves_the_directory_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    directory = _archive(tmp_path)
    app = make_app(demo=True, demo_delay=0)

    response = _put(app, f"/api/archives/{directory.name}/label", {"label": "Q3 handover"})

    assert response.status_code == 200
    assert directory.is_dir(), "the directory is the archive's id and must not move"
    stored = json.loads((directory / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert stored["display_name"] == "Q3 handover"
    assert stored["status"] == "ok", "renaming must not discard the rest of the summary"
    assert list_archives(tmp_path)[0].display_name == "Q3 handover"


def test_a_blank_label_restores_the_name_the_directory_implies(tmp_path, monkeypatch):
    """An empty label is not an empty name: it is "stop overriding"."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    directory = _archive(tmp_path)
    app = make_app(demo=True, demo_delay=0)
    _put(app, f"/api/archives/{directory.name}/label", {"label": "Temporary"})

    _put(app, f"/api/archives/{directory.name}/label", {"label": "   "})

    stored = json.loads((directory / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert "display_name" not in stored
    assert list_archives(tmp_path)[0].display_name != "Temporary"


def test_renaming_something_outside_the_archives_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    app = make_app(demo=True, demo_delay=0)

    response = _put(app, "/api/archives/..%2Fescape/label", {"label": "nope"})

    assert response.status_code in (400, 404)
    assert not (tmp_path.parent / "escape").exists()


def test_a_set_of_communities_is_named_for_the_first_one(tmp_path, monkeypatch):
    """A guess, and a reasonable one -- correctable by the rename above."""
    from connections_export.gui.requests import CommunitySelection, _StartRequest
    from connections_export.gui.routes.run import selected_communities

    body = _StartRequest(
        app="community",
        communities=[
            CommunitySelection(uuid="aaa", title="Platform Engineering", components=["wiki:w"]),
            CommunitySelection(uuid="bbb", title="Tooling", components=["forum:f"]),
        ],
    )

    first = next((c.title for c in selected_communities(body) if c.title), None)

    assert first == "Platform Engineering"
