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
    auth = PasteTokenAuth(ltpa_token="opaque-token-value", base_url="https://example.com")

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
    auth = BasicAuth(username="alice", password="s3cret", base_url="https://example.com")

    auth.prepare(client)
    client.get("https://example.com/anything")

    expected_token = base64.b64encode(b"alice:s3cret").decode()
    assert seen_auth["authorization"] == f"Basic {expected_token}"


# --- credentials are bound to the deployment they were given for ---
#
# The console's lookups can be pointed at a URL, and a client that attaches
# the user's password or session to every request hands it to whichever
# host that URL names. Each strategy binds what it adds to the deployment.


def _recording_client():
    seen: dict[str, dict[str, str | None]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = {
            "authorization": request.headers.get("authorization"),
            "cookie": request.headers.get("cookie"),
        }
        return httpx.Response(200, content=b"ok")

    return HttpClient(transport=httpx.MockTransport(handler)), seen


def test_basic_auth_never_sends_the_password_to_another_host():
    """The Authorization header used to sit in the client's default headers,
    so a lookup asked about `evil.example` sent the password there too."""
    client, seen = _recording_client()
    BasicAuth(username="alice", password="s3cret", base_url="https://connect.example.com").prepare(
        client
    )

    client.get("https://connect.example.com/homepage/")
    client.get("https://evil.example/steal")

    assert seen["connect.example.com"]["authorization"] is not None
    assert seen["evil.example"]["authorization"] is None


def test_basic_auth_is_not_sent_to_the_same_host_on_another_port():
    """An origin is scheme, host AND port: another port on the same machine
    can be another service altogether."""
    client, seen = _recording_client()
    BasicAuth(username="alice", password="s3cret", base_url="https://connect.example.com").prepare(
        client
    )

    client.get("https://connect.example.com:8443/elsewhere")

    assert seen["connect.example.com"]["authorization"] is None


def test_basic_auth_without_a_deployment_address_refuses_rather_than_sending_everywhere():
    """With no address to bind the password to, the only safe answer is not
    to prepare one -- as a message, the way other sign-in problems read."""
    from connections_export.http.auth import AuthError

    client, _seen = _recording_client()

    with pytest.raises(AuthError, match="address"):
        BasicAuth(username="alice", password="s3cret").prepare(client)


def test_a_pasted_token_is_only_sent_to_the_deployment():
    """Set without a domain, httpx sent the pasted session cookie to every
    host the client was asked to fetch."""
    client, seen = _recording_client()
    PasteTokenAuth(ltpa_token="opaque", base_url="https://connect.example.com").prepare(client)

    client.get("https://connect.example.com/homepage/")
    client.get("https://evil.example/steal")

    assert seen["connect.example.com"]["cookie"] == "LtpaToken2=opaque"
    assert seen["evil.example"]["cookie"] is None


class _FakeResponse:
    def __init__(self, url: str, status: int, location: str | None = None) -> None:
        self.url = url
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.is_redirect = location is not None


class _FakeSession:
    """Stands in for a `requests` session: answers from a table, records
    every URL the handshake was carried to."""

    def __init__(self, table: dict[str, _FakeResponse]) -> None:
        self.table = table
        self.visited: list[str] = []

    def get(self, url: str, allow_redirects: bool = True):
        assert allow_redirects is False, "requests would follow any redirect itself"
        self.visited.append(url)
        return self.table[url]


# --- the sign-in handshake follows redirects only to sign-in hosts ---------
#
# `requests` re-runs the Negotiate hook on a redirected request, so a
# redirect carries the Windows sign-in to wherever it points (a Kerberos
# ticket for that host, or an NTLM response it could relay). Real
# deployments DO send sign-in to a second host -- the organisation's login
# server -- so the handshake follows a redirect to a host that belongs to
# the deployment's organisation, and refuses, by name, any other.

DEPLOYMENT = "https://connections.example.com/homepage/"


def _walk(table: dict[str, tuple[int, str | None]], start: str = DEPLOYMENT, hosts=()):
    from connections_export.http.auth import _sign_in_get

    session = _FakeSession(
        {url: _FakeResponse(url, status, location) for url, (status, location) in table.items()}
    )
    return _sign_in_get(session, start, sign_in_hosts=hosts), session.visited


def test_a_redirect_within_the_deployment_is_followed():
    response, visited = _walk(
        {
            DEPLOYMENT: (302, "/homepage/web/"),
            "https://connections.example.com/homepage/web/": (200, None),
        }
    )

    assert visited[-1] == "https://connections.example.com/homepage/web/"
    assert response.status_code == 200


def test_sign_in_on_a_sibling_host_of_the_same_organisation_is_followed():
    """connections.example.com -> login.example.com: the organisation's
    own login server, a common arrangement. Sign-in depends on following
    it."""
    login = "https://login.example.com/sso?return=/homepage/"
    response, visited = _walk(
        {
            DEPLOYMENT: (302, login),
            login: (302, DEPLOYMENT.replace("/homepage/", "/homepage/web/")),
            "https://connections.example.com/homepage/web/": (200, None),
        }
    )

    assert login in visited
    assert response.status_code == 200


def test_a_foreign_sign_in_host_listed_in_hcl_hosts_is_followed():
    """An organisation's login server on another domain is allowed by naming
    it, in the same setting that already names the deployment's hosts."""
    login = "https://idp.example.net/login"
    response, visited = _walk(
        {DEPLOYMENT: (302, login), login: (200, None)}, hosts=("idp.example.net",)
    )

    assert visited == [DEPLOYMENT, login]
    assert response.status_code == 200


def test_an_unrelated_host_is_refused_by_name_with_the_way_to_allow_it():
    """Not followed -- and the message is the fix, since a legitimate login
    server looks exactly like this until it is listed."""
    from connections_export.http.auth import AuthError

    with pytest.raises(AuthError) as refused:
        _walk({DEPLOYMENT: (302, "https://login.other.example/grab")})

    message = str(refused.value)
    assert "login.other.example" in message
    assert "connections.example.com" in message
    assert "hcl_hosts" in message


def test_a_deployment_at_a_two_label_domain_does_not_trust_its_whole_tld():
    """The parent of example.com is `com`: trusting it would trust every
    `.com` host there is."""
    from connections_export.http.auth import AuthError

    start = "https://example.com/homepage/"
    with pytest.raises(AuthError):
        _walk({start: (302, "https://www.example.com/sso")}, start=start)


def test_a_deployment_at_an_ip_address_has_no_parent_domain_to_trust():
    """Dropping the first octet of an address names nothing at all."""
    from connections_export.http.auth import AuthError

    start = "https://" + "192.0.2.10" + "/homepage/"
    with pytest.raises(AuthError):
        _walk({start: (302, "https://" + "sso.0.2.10" + "/login")}, start=start)


def test_a_cross_host_redirect_down_to_plain_http_is_refused():
    """Even to a sibling host: the handshake would go over the wire in the
    clear."""
    from connections_export.http.auth import AuthError

    with pytest.raises(AuthError, match="login.example.com"):
        _walk({DEPLOYMENT: (302, "http://login.example.com/sso")})


# --- AuthStrategy protocol conformance ---


def test_basic_and_paste_token_satisfy_the_auth_strategy_protocol():
    from connections_export.http.auth import AuthStrategy

    base = "https://connect.example.com"
    assert isinstance(BasicAuth(username="a", password="b", base_url=base), AuthStrategy)
    assert isinstance(PasteTokenAuth(ltpa_token="t", base_url=base), AuthStrategy)


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
