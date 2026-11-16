"""Every setting a user chooses survives a restart -- an update included.

The archives folder was the one setting held only in memory: it worked until
the program stopped, and every restart pointed the console back at the default
folder, which reads as the archives having gone.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui import settings_store, support
from connections_export.gui.app import make_app

HEADERS = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _put_dir(app, path) -> httpx.Response:
    async def go():
        async with _client(app) as c:
            return await c.put("/api/archives-dir", json={"path": str(path)}, headers=HEADERS)

    return asyncio.run(go())


def _settings(app) -> dict:
    async def go():
        async with _client(app) as c:
            return (await c.get("/api/settings", headers=HEADERS)).json()

    return asyncio.run(go())


def test_the_archives_folder_is_still_chosen_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.delenv(support.ARCHIVES_DIR_ENV, raising=False)
    chosen = tmp_path / "my-archives"
    chosen.mkdir()
    answer = _put_dir(make_app(demo=True), chosen)
    assert answer.status_code == 200 and answer.json()["saved"] is True
    assert settings_store.load_settings()["archives_dir"] == str(chosen.resolve())

    # A new process: the module default first, as a fresh import would have it.
    monkeypatch.setattr(support, "ARCHIVES_BASE", tmp_path / "default")
    restarted = make_app(demo=True)
    assert support.ARCHIVES_BASE == chosen.resolve()
    assert _settings(restarted)["archives_dir"] == str(chosen.resolve())
    assert _settings(restarted)["archives_dir_missing"] is None


def test_a_saved_folder_that_is_gone_is_reported_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.delenv(support.ARCHIVES_DIR_ENV, raising=False)
    gone = tmp_path / "unplugged-drive"
    settings_store.save_settings({"archives_dir": str(gone)})
    default = tmp_path / "default"
    monkeypatch.setattr(support, "ARCHIVES_BASE", default)
    app = make_app(demo=True)
    assert support.ARCHIVES_BASE == default
    assert _settings(app)["archives_dir_missing"] == str(gone)
    # ...and the saved choice is kept, so reconnecting the drive brings it back.
    assert settings_store.load_settings()["archives_dir"] == str(gone)


def test_the_environment_variable_still_wins(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    saved.mkdir()
    settings_store.save_settings({"archives_dir": str(saved)})
    monkeypatch.setenv(support.ARCHIVES_DIR_ENV, str(tmp_path / "from-env"))
    monkeypatch.setattr(support, "ARCHIVES_BASE", tmp_path / "from-env")
    make_app(demo=True)
    assert support.ARCHIVES_BASE == tmp_path / "from-env"


def test_every_setting_the_console_saves_is_one_that_is_kept():
    """A setting the Settings page saves but the store does not keep is the
    same bug again, waiting for the next restart."""
    app = make_app(demo=True)
    editable = set(app.state.editable_settings)
    assert editable <= set(settings_store.PERSISTED_KEYS), sorted(
        editable - set(settings_store.PERSISTED_KEYS)
    )


def _put_settings(app, body: dict) -> httpx.Response:
    async def go():
        async with _client(app) as c:
            return await c.put("/api/settings", json=body, headers=HEADERS)

    return asyncio.run(go())


def test_saving_other_settings_keeps_the_archives_folder(tmp_path, monkeypatch):
    """The Settings page's Save rebuilt the settings from its own fields, and
    the archives folder is not one of them: choosing a folder, then saving
    anything else, erased the folder from disk -- and the next start, an
    update most visibly, was back at the default."""
    monkeypatch.delenv(support.ARCHIVES_DIR_ENV, raising=False)
    chosen = tmp_path / "my-archives"
    chosen.mkdir()
    app = make_app(demo=True)
    assert _put_dir(app, chosen).status_code == 200
    assert _put_settings(app, {"min_interval": 2.0}).status_code == 200

    assert settings_store.load_settings()["archives_dir"] == str(chosen.resolve())
    assert settings_store.load_settings()["min_interval"] == 2.0
    monkeypatch.setattr(support, "ARCHIVES_BASE", tmp_path / "default")
    make_app(demo=True)
    assert support.ARCHIVES_BASE == chosen.resolve()


def test_saving_other_settings_keeps_a_folder_that_is_unplugged(tmp_path, monkeypatch):
    """A saved folder missing at start-up is reported, and must still be the
    saved choice after any other save -- reconnecting the drive brings it back."""
    monkeypatch.delenv(support.ARCHIVES_DIR_ENV, raising=False)
    gone = tmp_path / "unplugged-drive"
    settings_store.save_settings({"archives_dir": str(gone)})
    monkeypatch.setattr(support, "ARCHIVES_BASE", tmp_path / "default")
    app = make_app(demo=True)
    assert _put_settings(app, {"min_interval": 2.0}).status_code == 200
    assert settings_store.load_settings()["archives_dir"] == str(gone)
