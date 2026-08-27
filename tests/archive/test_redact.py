"""Tests for connections_export.archive.redact: mandatory header redaction."""

from connections_export.archive.redact import (
    DEFAULT_DENY_HEADERS,
    REDACTED_PLACEHOLDER,
    redact_headers,
)


def test_default_deny_headers_are_redacted_with_placeholder():
    headers = {
        "Authorization": "Bearer super-secret-token",
        "Cookie": "session=abc123",
        "Set-Cookie": "session=abc123; Path=/",
        "Proxy-Authorization": "Basic dXNlcjpwYXNz",
    }

    redacted = redact_headers(headers)

    for name in headers:
        assert name in redacted, f"{name} presence should be preserved"
        assert redacted[name] == REDACTED_PLACEHOLDER
        assert redacted[name] != headers[name]


def test_redaction_matching_is_case_insensitive():
    headers = {"AUTHORIZATION": "secret", "cOOkie": "also secret"}

    redacted = redact_headers(headers)

    assert redacted["AUTHORIZATION"] == REDACTED_PLACEHOLDER
    assert redacted["cOOkie"] == REDACTED_PLACEHOLDER


def test_benign_headers_pass_through_unchanged():
    headers = {"Accept": "application/atom+xml", "Content-Type": "text/plain"}

    redacted = redact_headers(headers)

    assert redacted == headers


def test_extra_deny_header_is_redacted():
    headers = {"X-Api-Key": "top-secret", "Accept": "text/plain"}

    redacted = redact_headers(headers, deny=DEFAULT_DENY_HEADERS | {"x-api-key"})

    assert redacted["X-Api-Key"] == REDACTED_PLACEHOLDER
    assert redacted["Accept"] == "text/plain"


def test_default_deny_set_contains_the_required_headers():
    assert DEFAULT_DENY_HEADERS == {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
    }


def test_redact_headers_does_not_mutate_input():
    headers = {"Authorization": "secret"}
    original = dict(headers)

    redact_headers(headers)

    assert headers == original
