"""Tests for `load_config`'s layering (spec: "Layered configuration with
precedence"; the design "Loading precedence"). Every test here sets up at
least two disagreeing layers so the winner is unambiguous.
"""

from connections_export.config import load_config

# --- 2.1 Four layers disagree on one key -> CLI wins; per-key merge ---


def test_cli_overrides_env_overrides_file_overrides_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CONNECTIONS_EXPORT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text('source_version = "file-value"\n')

    env = {
        "CONNECTIONS_EXPORT_SOURCE_VERSION": "env-value",
    }
    cli_overrides = {"source_version": "cli-value"}

    config = load_config(cli_overrides, env=env)

    assert config.source_version == "cli-value"


def test_a_file_only_key_still_takes_effect(tmp_path):
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text('source_version = "file-only-value"\n')

    config = load_config({}, config_path=config_file, env={})

    assert config.source_version == "file-only-value"


def test_env_wins_over_file_when_cli_silent(tmp_path):
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text('source_version = "file-value"\n')

    config = load_config(
        {}, config_path=config_file, env={"CONNECTIONS_EXPORT_SOURCE_VERSION": "env-value"}
    )

    assert config.source_version == "env-value"


def test_file_wins_over_default_when_cli_and_env_silent(tmp_path):
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text("min_interval = 1.5\n")

    config = load_config({}, config_path=config_file, env={})

    assert config.min_interval == 1.5


def test_keys_not_set_anywhere_fall_back_to_defaults(tmp_path):
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text('source_version = "file-value"\n')

    config = load_config({}, config_path=config_file, env={})

    # source_version came from the file; auth_mode was never set by any
    # layer, so it must still be the Config default.
    assert config.source_version == "file-value"
    assert config.auth_mode == "sspi"


def test_none_valued_cli_overrides_do_not_shadow_lower_layers(tmp_path):
    """argparse produces None for flags the user didn't pass — those
    must not be treated as an override."""
    config_file = tmp_path / "connections-export.toml"
    config_file.write_text('source_version = "file-value"\n')

    config = load_config({"source_version": None}, config_path=config_file, env={})

    assert config.source_version == "file-value"


def test_no_config_anywhere_yields_pure_defaults(tmp_path, monkeypatch):
    # Isolate cwd so a real connections-export.toml in the developer's
    # working directory can never leak into this "nothing configured" case.
    monkeypatch.chdir(tmp_path)
    config = load_config({}, config_path=None, env={})

    assert config.base_url is None
    assert config.auth_mode == "sspi"
