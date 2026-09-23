"""Saved settings survive a restart.

`editable_settings` lived only in `app.state`, so Save held until the process
stopped and every value went back to its default on the next start. Nothing
said so -- the field showed the saved number until the restart, and the
default afterwards.

Stored beside the user rather than beside the working directory: settings that
depend on where you happened to launch from are the same trap in a different
place.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import settings_store
from connections_export.gui.app import make_app

HOST = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(settings_store.SETTINGS_DIR_ENV, str(tmp_path))
    return tmp_path / settings_store.SETTINGS_FILENAME


def test_saving_writes_a_file(store):
    client = TestClient(make_app(demo=True))
    response = client.put("/api/settings", json={"min_interval": 0.2}, headers=HOST)
    assert response.status_code == 200
    assert store.is_file()
    assert json.loads(store.read_text(encoding="utf-8"))["min_interval"] == 0.2


def test_a_new_server_starts_with_what_was_saved(store):
    TestClient(make_app(demo=True)).put("/api/settings", json={"min_interval": 0.2}, headers=HOST)

    # A different process would do exactly this: build the app afresh.
    restarted = TestClient(make_app(demo=True))
    assert restarted.get("/api/settings", headers=HOST).json()["min_interval"] == 0.2


def test_a_run_started_after_a_restart_uses_the_saved_delay(store):
    TestClient(make_app(demo=True)).put("/api/settings", json={"min_interval": 0.2}, headers=HOST)
    restarted = make_app(demo=True)
    assert restarted.state.editable_settings["min_interval"] == 0.2


def test_an_unreadable_settings_file_does_not_stop_the_console(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ this is not json", encoding="utf-8")
    app = make_app(demo=True)  # must not raise
    assert app.state.editable_settings["min_interval"] == 1.0


def test_the_location_is_the_users_config_directory_not_the_working_one(monkeypatch, tmp_path):
    """Otherwise the settings you saved depend on where you launched from."""
    monkeypatch.delenv(settings_store.SETTINGS_DIR_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(settings_store.sys, "platform", "win32")
    assert settings_store.settings_dir().is_relative_to(tmp_path / "appdata")

    monkeypatch.setattr(settings_store.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert settings_store.settings_dir().is_relative_to(tmp_path / "xdg")


def test_what_is_stored_is_only_what_the_user_chose(store):
    """Not a dump of app state: a stored key nobody set is a value that
    quietly outlives the version that introduced it."""
    client = TestClient(make_app(demo=True))
    client.put("/api/settings", json={"min_interval": 0.2}, headers=HOST)
    stored = json.loads(store.read_text(encoding="utf-8"))
    assert set(stored) <= set(settings_store.PERSISTED_KEYS)


def test_the_pdf_render_timeout_is_configurable_and_persists(store):
    """The PDF render timeout was plumbed through the backend but had no visible
    control; it must be saveable, survive a restart, and refuse a non-positive
    value."""
    client = TestClient(make_app(demo=True))

    # It is exposed to the console...
    shown = client.get("/api/settings", headers=HOST).json()
    assert "pdf_timeout" in shown

    # ...saved...
    saved = client.put("/api/settings", json={"pdf_timeout": 45}, headers=HOST)
    assert saved.status_code == 200
    assert json.loads(store.read_text(encoding="utf-8"))["pdf_timeout"] == 45

    # ...read back by a fresh server...
    again = TestClient(make_app(demo=True))
    assert again.get("/api/settings", headers=HOST).json()["pdf_timeout"] == 45

    # ...and a non-positive timeout is refused rather than stored.
    bad = client.put("/api/settings", json={"pdf_timeout": 0}, headers=HOST)
    assert bad.status_code == 422
