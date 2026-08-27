"""Tests for the shipped `connections-export.example.toml` (spec: "Shipped
example configuration"). It must parse, build into a valid `Config`,
use only placeholder hosts, and set no secret value.
"""

import tomllib
from pathlib import Path

from connections_export.config import Config

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "connections-export.example.toml"

_SECRET_KEYS = {"token", "password", "user", "username", "ltpa_token"}


def test_example_toml_exists():
    assert EXAMPLE_PATH.is_file()


def test_example_toml_parses():
    with EXAMPLE_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    assert isinstance(data, dict)
    assert data  # not empty


def test_example_toml_builds_a_valid_config():
    with EXAMPLE_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    config = Config(**data)

    assert config.base_url is not None


def test_example_toml_uses_only_placeholder_hosts():
    with EXAMPLE_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    assert "example.corp" in data["base_url"]
    for host in data["hcl_hosts"]:
        assert "example.corp" in host


def test_example_toml_sets_no_secret_value():
    with EXAMPLE_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    assert _SECRET_KEYS.isdisjoint(data.keys())


def test_example_toml_is_commented():
    text = EXAMPLE_PATH.read_text(encoding="utf-8")

    assert "#" in text
