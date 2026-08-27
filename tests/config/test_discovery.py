"""Tests for config-file discovery order (spec: "Discoverable config
file"; the design "File discovery"): `$CONNECTIONS_EXPORT_CONFIG` explicit
override, then `./connections-export.toml`, then `$XDG_CONFIG_HOME/connections-export/
config.toml` (fallback `~/.config/connections-export/config.toml`), else None.
"""

import pytest

from connections_export.config import discover_config_file

# --- 3.1 explicit override wins ---


def test_explicit_override_wins_over_everything(tmp_path):
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("")
    cwd_file = tmp_path / "connections-export.toml"
    cwd_file.write_text("")

    found = discover_config_file(
        explicit=explicit, env={"CONNECTIONS_EXPORT_CONFIG": str(cwd_file)}
    )

    assert found == explicit


def test_env_var_override_wins_over_standard_locations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "elsewhere.toml"
    env_file.write_text("")
    cwd_file = tmp_path / "connections-export.toml"
    cwd_file.write_text("")

    found = discover_config_file(env={"CONNECTIONS_EXPORT_CONFIG": str(env_file)})

    assert found == env_file


def test_env_var_pointing_at_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "does-not-exist.toml"

    with pytest.raises(FileNotFoundError):
        discover_config_file(env={"CONNECTIONS_EXPORT_CONFIG": str(missing)})


# --- 3.1 standard location:./connections-export.toml ---


def test_cwd_connections_export_toml_found_when_no_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cwd_file = tmp_path / "connections-export.toml"
    cwd_file.write_text("")

    found = discover_config_file(env={})

    assert found == cwd_file


# --- XDG fallback ---


def test_xdg_config_home_used_when_no_cwd_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # empty dir, no./connections-export.toml
    xdg_home = tmp_path / "xdg"
    xdg_file = xdg_home / "connections-export" / "config.toml"
    xdg_file.parent.mkdir(parents=True)
    xdg_file.write_text("")

    found = discover_config_file(env={"XDG_CONFIG_HOME": str(xdg_home)})

    assert found == xdg_file


def test_home_config_fallback_used_when_no_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    home_file = fake_home / ".config" / "connections-export" / "config.toml"
    home_file.parent.mkdir(parents=True)
    home_file.write_text("")
    monkeypatch.setenv("HOME", str(fake_home))

    found = discover_config_file(env={})

    assert found == home_file


def test_nothing_found_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home-empty"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    found = discover_config_file(env={})

    assert found is None
