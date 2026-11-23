"""A zipped archive is read-only: it says so, unzips into a folder, and is
repaired by unzipping first.

A zip opens and reads but never takes a write, so it cannot be updated or
repaired where it is. The list marks it; Unzip writes a normal archive
folder beside it and leaves the zip as it was; Repair unzips first, repairs
the folder, and the zip then names its newer copy instead of asking again.
"""

from __future__ import annotations

import asyncio
import hashlib
import zipfile
from pathlib import Path

import httpx
import pytest

from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from connections_export.gui.archives import list_archives, unzip_archive
from tests.crawler.test_assets_in_crawl import _empty_wikiset
from tests.crawler.test_repair_reads_only_what_is_missing import (
    _client,
    _forumset,
    _old_archive,
    _Recording,
)


def _zip_up(folder: Path) -> Path:
    target = folder.with_suffix(".zip")
    with zipfile.ZipFile(target, "w") as handle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                handle.write(path, f"{folder.name}/{path.relative_to(folder).as_posix()}")
    return target


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def zipped(tmp_path, monkeypatch):
    base = tmp_path / "archives"
    base.mkdir()
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", base)
    forumset = _forumset()
    folder = _old_archive(base, forumset).root
    renamed = folder.rename(base / "forum-archive")
    archive_zip = _zip_up(renamed)
    import shutil

    shutil.rmtree(renamed)
    return archive_zip, forumset


def test_unzipping_writes_a_folder_beside_the_zip_and_leaves_the_zip(zipped):
    archive_zip, _forumset = zipped
    before = _digest(archive_zip)
    first = unzip_archive(archive_zip)
    second = unzip_archive(archive_zip)
    assert first.name == "forum-archive" and second.name == "forum-archive-2"
    assert (first / "manifest.jsonl").is_file()
    assert _digest(archive_zip) == before


def test_an_entry_pointing_outside_the_folder_is_not_written(tmp_path):
    folder = tmp_path / "a"
    folder.mkdir()
    (folder / "manifest.jsonl").write_text("", encoding="utf-8")
    archive_zip = _zip_up(folder)
    with zipfile.ZipFile(archive_zip, "a") as handle:
        handle.writestr("a/../../escaped.txt", "no")
    import shutil

    shutil.rmtree(folder)
    unzip_archive(archive_zip)
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_the_list_marks_a_zip_as_read_only_and_offers_its_repair(zipped):
    archive_zip, _forumset = zipped
    (info,) = list_archives(archive_zip.parent)
    assert info.repair_keys == ["forum-attachments"]

    async def go():
        transport = httpx.ASGITransport(app=make_app(demo=True, demo_delay=0))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return (await c.get("/api/archives")).json()

    (listed,) = asyncio.run(go())["archives"]
    assert listed["is_zip"] is True


def test_repairing_a_zip_unzips_it_repairs_the_folder_and_keeps_the_zip(zipped, monkeypatch):
    archive_zip, forumset = zipped
    before = _digest(archive_zip)
    import connections_export.cli as cli_module
    from connections_export.fakeserver.app import make_app as make_fake

    monkeypatch.setattr(
        cli_module,
        "_build_default_client",
        lambda *a, **k: _client(_Recording(make_fake(_empty_wikiset(), forumset=forumset))),
    )
    # A forum-only repair must not build the archive's model to count blog
    # comments: on a big archive that is minutes of nothing on screen.
    from connections_export.gui.routes import archives as archives_routes

    counted = []
    monkeypatch.setattr(archives_routes, "_blog_comment_count", lambda d: counted.append(d))
    app = make_app(demo=True, demo_delay=0)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return await c.post(
                "/api/repair-archives", json={"names": [archive_zip.name], "confirmation": "1"}
            )

    response = asyncio.run(go())
    assert response.status_code == 200, response.text
    import threading
    import time

    deadline = time.time() + 60
    while getattr(app.state, "repair_in_progress", False) and time.time() < deadline:
        time.sleep(0.1)
    assert not threading.active_count() == 0
    job = app.state.repair_job
    assert job["results"][0]["ok"], job
    assert job["results"][0]["unpacked_as"] == "forum-archive"
    assert counted == []
    assert job["results"][0]["attachments_restored"] == 1
    assert job["threads_total"] == 2
    # The banner can say which archive and what is being repaired.
    assert job["current_label"] and job["repairing"] == "forum attachments"

    assert _digest(archive_zip) == before
    infos = {info.name: info for info in list_archives(archive_zip.parent)}
    assert infos["forum-archive"].repair_needed is False
    assert infos[archive_zip.name].repair_needed is False
    assert infos[archive_zip.name].captured_again_as == "forum-archive"
    # The zip itself is not repaired, and keeps saying what it lacks.
    assert [i["key"] for i in infos[archive_zip.name].issues] == ["forum-attachments"]
    assert infos["forum-archive"].issues == []


def test_a_zip_beside_its_own_folder_is_a_copy_and_only_the_folder_is_repaired(tmp_path):
    """`X.zip` next to `X`: one archive, not two to repair -- and unzipping the
    zip would make a third copy of it."""
    base = tmp_path / "archives"
    base.mkdir()
    folder = _old_archive(base, _forumset()).root.rename(base / "forum-archive")
    _zip_up(folder)
    infos = {info.name: info for info in list_archives(base)}
    assert infos["forum-archive"].repair_needed is True
    zipped = infos["forum-archive.zip"]
    assert zipped.copy_of == "forum-archive"
    assert zipped.repair_needed is False
    assert [i["key"] for i in zipped.issues] == ["forum-attachments"]


def test_a_repair_recorded_before_1_0_1b5_is_not_trusted(tmp_path):
    """b3 and b4 recorded a repair as made even when it was stopped part-way."""
    from connections_export.archive.records import RunMetadata
    from connections_export.archive.store import Archive
    from connections_export.gui.archives import archive_repair_status

    for version, still_needed in (("1.0.1b4", True), ("1.0.1b5", False)):
        archive = _old_archive(tmp_path / version, _forumset())
        Archive.open(archive.root).write_run_metadata(
            RunMetadata(
                run_id="repair",
                base_url="https://fake",
                principal="someone",
                adapter_version="forums-2",
                source_system_version="8.0",
                started_at="2026-11-23T10:00:00Z",
                completed_at="2026-11-23T10:01:00Z",
                repairs=["forum-attachments"],
                generator_version=version,
            )
        )
        assert archive_repair_status(archive.root)["repair_needed"] is still_needed, version
