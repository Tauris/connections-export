"""Tests for the `Config` model itself (spec: "Defaults never point at a
real server"; the design "Config model"). No loading/precedence here —
just field defaults, validation, and that nothing secret ever renders.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from connections_export.config import Config

# --- 1.1 Config validates fields; base_url default is None ---


def test_defaults_never_point_at_a_real_server():
    config = Config()

    assert config.base_url is None


def test_default_auth_mode_and_auth_root():
    config = Config()

    assert config.auth_mode == "sspi"
    assert config.auth_root == "basic"


def test_default_source_version():
    assert Config().source_version == "8.0"


def test_default_hcl_hosts_is_empty_list():
    assert Config().hcl_hosts == []


def test_default_min_interval_paces_requests():
    """Defaults to a deliberate 1s. An export is thousands of requests against
    someone's production Connections; going as fast as the server allows is how
    a tool gets noticed for the wrong reasons."""
    assert Config().min_interval == 1.0


def test_min_interval_is_raised_to_the_floor_rather_than_rejected():
    """Asking for 0 means "as fast as possible" -- a reasonable thing to want
    and an unreasonable thing to do to a shared deployment. Clamping honours
    the intent as far as it goes; refusing would move the argument to the
    command line."""
    assert Config(min_interval=0).min_interval == Config.MIN_REQUEST_INTERVAL
    assert Config(min_interval=0.05).min_interval == Config.MIN_REQUEST_INTERVAL
    assert Config(min_interval=3).min_interval == 3.0


def test_default_page_size_is_500():
    assert Config().page_size == 500


def test_default_output_dir_is_archive():
    assert Config().output_dir == Path("archive")


def test_default_refresh_is_false():
    assert Config().fetch == "resume"


def test_default_versions_is_list():
    assert Config().versions == "list"


def test_versions_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Config(versions="all")


def test_auth_mode_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Config(auth_mode="ntlm")


def test_auth_root_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Config(auth_root="saml")


def test_config_accepts_explicit_fields():
    config = Config(
        base_url="https://connections.example.corp",
        auth_mode="basic",
        auth_root="form",
        source_version="7.0",
        hcl_hosts=["connections.example.corp", "files.example.corp"],
        min_interval=0.5,
        page_size=250,
        output_dir=Path("out"),
        fetch="refresh",
        versions="full",
    )

    assert config.base_url == "https://connections.example.corp"
    assert config.hcl_hosts == ["connections.example.corp", "files.example.corp"]
    assert config.fetch == "refresh"
    assert config.versions == "full"
    assert config.page_size == 250


def test_unknown_field_is_rejected():
    """Extra/unknown keys are rejected rather than silently accepted —
    this is also what keeps a secret from sneaking in as a field: there
    is no field for it to land in."""
    with pytest.raises(ValidationError):
        Config(token="opaque-secret-value")


# --- repr/str render no secret material ---


def test_repr_lists_only_declared_fields():
    config = Config(base_url="https://connections.example.corp")

    text = repr(config)

    assert "connections.example.corp" in text
    assert "token" not in text.lower()
    assert "password" not in text.lower()


def test_str_matches_repr():
    config = Config()

    assert str(config) == repr(config)
