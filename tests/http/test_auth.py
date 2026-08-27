"""Tests for auth strategies converging on the client's session (spec:
"Auth strategies converge on the session"). Basic and paste-token are
testable offline; Sspi/Kerberos are import-guarded thin wrappers with no
offline behavioral test, only existence + helpful-error-when-missing-dep.
"""

import base64

import httpx
import pytest

from connections_export.http.auth import BasicAuth, KerberosAuth, PasteTokenAuth, SspiAuth
from connections_export.http.client import HttpClient

# --- 8.1 PasteTokenAuth -> subsequent request carries the LtpaToken2 cookie ---


def test_paste_token_auth_makes_subsequent_request_carry_the_cookie():
    seen_cookie = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookie["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, content=b"ok")

    client = HttpClient(transport=httpx.MockTransport(handler))
    auth = PasteTokenAuth(ltpa_token="opaque-token-value")

    auth.prepare(client)
    client.get("https://example.com/anything")

    assert seen_cookie["cookie"] == "LtpaToken2=opaque-token-value"


# --- 8.2 BasicAuth -> subsequent request carries the Authorization header ---


def test_basic_auth_makes_subsequent_request_carry_authorization_header():
    seen_auth = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"ok")

    client = HttpClient(transport=httpx.MockTransport(handler))
    auth = BasicAuth(username="alice", password="s3cret")

    auth.prepare(client)
    client.get("https://example.com/anything")

    expected_token = base64.b64encode(b"alice:s3cret").decode()
    assert seen_auth["authorization"] == f"Basic {expected_token}"


# --- AuthStrategy protocol conformance ---


def test_basic_and_paste_token_satisfy_the_auth_strategy_protocol():
    from connections_export.http.auth import AuthStrategy

    assert isinstance(BasicAuth(username="a", password="b"), AuthStrategy)
    assert isinstance(PasteTokenAuth(ltpa_token="t"), AuthStrategy)


# --- 8.4 Sspi/Kerberos: import-guarded, exist, raise a helpful error
# when the optional dependency is missing. No offline behavioral test —
# they require a real domain. ---


def test_sspi_auth_exists_and_raises_helpful_error_without_dependency():
    client = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    auth = SspiAuth(base_url="https://connect.example.com")

    try:
        import requests_negotiate_sspi  # noqa: F401

        pytest.skip("requests-negotiate-sspi is installed; guard path not exercised")
    except ImportError:
        pass

    with pytest.raises(RuntimeError, match="requests-negotiate-sspi"):
        auth.prepare(client)


def test_kerberos_auth_exists_and_raises_helpful_error_without_dependency():
    client = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    auth = KerberosAuth(base_url="https://connect.example.com", principal="user@REALM")

    try:
        import requests_kerberos  # noqa: F401

        pytest.skip("requests-kerberos is installed; guard path not exercised")
    except ImportError:
        pass

    with pytest.raises(RuntimeError, match="requests-kerberos"):
        auth.prepare(client)
