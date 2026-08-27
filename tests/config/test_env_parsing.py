"""Tests for CONNECTIONS_EXPORT_* environment parsing in isolation:
comma-split lists, bool coercion, float coercion. Exercises the parsing
function directly rather than the full `load_config` layering.
"""

from pathlib import Path

import pytest

from connections_export.config import parse_env_layer

# --- CONNECTIONS_EXPORT_HCL_HOSTS comma-split ---


def test_hcl_hosts_is_comma_split():
    layer = parse_env_layer(
        {"CONNECTIONS_EXPORT_HCL_HOSTS": "connections.example.corp,files.example.corp"}
    )

    assert layer["hcl_hosts"] == ["connections.example.corp", "files.example.corp"]


def test_hcl_hosts_strips_whitespace_around_entries():
    layer = parse_env_layer(
        {"CONNECTIONS_EXPORT_HCL_HOSTS": " connections.example.corp , files.example.corp "}
    )

    assert layer["hcl_hosts"] == ["connections.example.corp", "files.example.corp"]


def test_hcl_hosts_single_value_no_comma():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_HCL_HOSTS": "connections.example.corp"})

    assert layer["hcl_hosts"] == ["connections.example.corp"]


# --- fetch mode coercion ---


@pytest.mark.parametrize("raw", ["resume", "update", "refresh", "UPDATE", " update "])
def test_fetch_mode_values(raw):
    layer = parse_env_layer({"CONNECTIONS_EXPORT_FETCH": raw})

    assert layer["fetch"] == raw.strip().lower()


def test_an_unknown_fetch_mode_raises():
    """Silently falling back to `resume` would look like a successful update
    that fetched nothing -- the worst possible failure for this feature."""
    with pytest.raises(ValueError):
        parse_env_layer({"CONNECTIONS_EXPORT_FETCH": "incremental"})


def test_the_archive_to_extend_comes_through_the_environment():
    from pathlib import Path as _Path

    layer = parse_env_layer({"CONNECTIONS_EXPORT_INTO": "archive/export-1"})

    assert layer["into"] == _Path("archive/export-1")


# --- float coercion (min_interval) ---


def test_min_interval_is_parsed_as_float():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_MIN_INTERVAL": "0.75"})

    assert layer["min_interval"] == 0.75
    assert isinstance(layer["min_interval"], float)


def test_min_interval_unparseable_value_raises():
    with pytest.raises(ValueError):
        parse_env_layer({"CONNECTIONS_EXPORT_MIN_INTERVAL": "not-a-number"})


# --- int coercion (page_size) ---


def test_page_size_is_parsed_as_int():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_PAGE_SIZE": "250"})

    assert layer["page_size"] == 250
    assert isinstance(layer["page_size"], int)


def test_page_size_unparseable_value_raises():
    with pytest.raises(ValueError):
        parse_env_layer({"CONNECTIONS_EXPORT_PAGE_SIZE": "not-a-number"})


# --- output_dir becomes a Path ---


def test_output_dir_is_parsed_as_path():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_OUTPUT_DIR": "somewhere"})

    assert layer["output_dir"] == Path("somewhere")


# --- unrelated / unset env vars are ignored ---


def test_unset_vars_are_absent_from_the_layer():
    layer = parse_env_layer({"PATH": "/usr/bin", "HOME": "/home/x"})

    assert layer == {}


def test_empty_string_is_treated_as_unset():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_BASE_URL": ""})

    assert "base_url" not in layer


def test_plain_string_fields_pass_through():
    layer = parse_env_layer(
        {
            "CONNECTIONS_EXPORT_BASE_URL": "https://connections.example.corp",
            "CONNECTIONS_EXPORT_AUTH_MODE": "basic",
            "CONNECTIONS_EXPORT_AUTH_ROOT": "form",
            "CONNECTIONS_EXPORT_SOURCE_VERSION": "7.0",
        }
    )

    assert layer["base_url"] == "https://connections.example.corp"
    assert layer["auth_mode"] == "basic"
    assert layer["auth_root"] == "form"
    assert layer["source_version"] == "7.0"


# --- CONNECTIONS_EXPORT_VERSIONS ---


def test_versions_env_var_passes_through():
    layer = parse_env_layer({"CONNECTIONS_EXPORT_VERSIONS": "full"})

    assert layer["versions"] == "full"
