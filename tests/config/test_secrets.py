"""Tests for secret resolution (spec: "Secrets never persist in the repo
or archive"; the design "Secrets"). Secrets are never `Config` fields, so
these test the resolver functions directly, plus that a resolved secret
never leaks into `repr(config)`.

The `getpass`/`input` prompt path is guarded so these tests never block:
`resolve_basic` only prompts when both env vars are absent AND stdin is
a tty. Under pytest stdin is not a tty, so the default (unpatched) call
already can't hang; the prompt-path tests below patch `sys.stdin.isatty`
explicitly and inject fake `input`/`getpass` callables so no real
blocking I/O is ever reached.
"""

import pytest

from connections_export.config import Config, ConfigError, resolve_basic, resolve_token

# --- 4.1 token resolved from CONNECTIONS_EXPORT_TOKEN; absent -> None ---


def test_token_resolved_from_env():
    token = resolve_token(env={"CONNECTIONS_EXPORT_TOKEN": "opaque-token-value"})

    assert token == "opaque-token-value"


def test_token_absent_is_none():
    assert resolve_token(env={}) is None


def test_token_empty_string_is_none():
    assert resolve_token(env={"CONNECTIONS_EXPORT_TOKEN": ""}) is None


def test_resolved_token_never_appears_in_config_repr():
    token = resolve_token(env={"CONNECTIONS_EXPORT_TOKEN": "super-secret-value"})
    config = Config(base_url="https://connections.example.corp", auth_mode="paste_token")

    text = repr(config)

    assert token is not None
    assert token not in text


# --- resolve_basic: env pair ---


def test_basic_resolved_from_env_pair():
    creds = resolve_basic(
        env={"CONNECTIONS_EXPORT_USER": "alice", "CONNECTIONS_EXPORT_PASSWORD": "s3cret"}
    )

    assert creds == ("alice", "s3cret")


def test_basic_partial_env_does_not_leak_without_prompt():
    creds = resolve_basic(env={"CONNECTIONS_EXPORT_USER": "alice"}, allow_prompt=False)

    assert creds is None


def test_basic_absent_and_prompt_disallowed_returns_none():
    """This is the guard that keeps tests from ever blocking: with no
    env credentials and prompting disallowed, resolve_basic returns
    immediately."""
    creds = resolve_basic(env={}, allow_prompt=False)

    assert creds is None


def test_basic_absent_and_non_tty_returns_none_even_with_prompt_allowed(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    creds = resolve_basic(env={}, allow_prompt=True)

    assert creds is None


def test_basic_prompts_only_when_tty_and_uses_injected_callables(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    creds = resolve_basic(
        env={},
        allow_prompt=True,
        input_func=lambda _prompt: "bob",
        getpass_func=lambda _prompt: "hunter2",
    )

    assert creds == ("bob", "hunter2")


def test_resolved_basic_password_never_appears_in_config_repr(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    creds = resolve_basic(
        env={},
        allow_prompt=True,
        input_func=lambda _prompt: "bob",
        getpass_func=lambda _prompt: "hunter2-secret",
    )
    config = Config(base_url="https://connections.example.corp", auth_mode="basic")

    text = repr(config)

    assert creds == ("bob", "hunter2-secret")
    assert "hunter2-secret" not in text


# --- 4.2 missing base_url -> clear error ---


def test_require_base_url_raises_clear_error_when_unset():
    config = Config()

    with pytest.raises(ConfigError, match="base_url"):
        config.require_base_url()


def test_require_base_url_returns_value_when_set():
    config = Config(base_url="https://connections.example.corp")

    assert config.require_base_url() == "https://connections.example.corp"
