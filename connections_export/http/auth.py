"""Auth strategies: all converge on the client's session (cookie jar or
default headers), so `HttpClient` itself has no idea which one ran.

`BasicAuth` and `PasteTokenAuth` are testable offline. `SspiAuth` and
`KerberosAuth` perform a real SPNEGO handshake against a domain to
obtain an `LtpaToken2` — they require a real domain and are exercised
by the capture tool on first contact, not by offline unit tests. Their
third-party dependencies (`requests-negotiate-sspi`, `requests-kerberos`)
are optional extras (`pip install connections-export[sspi]`); importing this
module never requires them, only calling `prepare` on those two does.
"""

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urlparse

if TYPE_CHECKING:
    from connections_export.http.client import HttpClient


def _host(base_url: str) -> str:
    """Extract the hostname from a base URL for cookie scoping."""
    return urlparse(base_url).hostname or base_url


class AuthError(RuntimeError):
    """Authentication could not be set up: a missing optional package, a
    handshake that returned no session, absent credentials.

    Distinct from `RuntimeError` so a caller can report it as a message rather
    than a stack trace, without also catching bugs. It subclasses `RuntimeError`
    so anything that already handled these keeps working.
    """


@runtime_checkable
class AuthStrategy(Protocol):
    """Prepares an `HttpClient`'s session so subsequent requests are
    authenticated. Every strategy converges on the same seam: cookies
    or default headers on the client."""

    def prepare(self, client: "HttpClient") -> None: ...


@dataclass
class BasicAuth:
    """Sets an `Authorization: Basic` header on the client's default
    headers, from a username and password."""

    username: str
    password: str

    def prepare(self, client: "HttpClient") -> None:
        credentials = f"{self.username}:{self.password}".encode()
        token = base64.b64encode(credentials).decode("ascii")
        client.headers["Authorization"] = f"Basic {token}"


@dataclass
class PasteTokenAuth:
    """Injects an `LtpaToken2` cookie into the client's jar. The
    development-unblock path — a token pasted from a browser session,
    no domain or handshake needed."""

    ltpa_token: str

    def prepare(self, client: "HttpClient") -> None:
        client.cookies.set("LtpaToken2", self.ltpa_token)


@dataclass
@dataclass
class SspiAuth:
    """Performs a Windows SSPI (Negotiate/Kerberos) handshake via
    `requests-negotiate-sspi` to obtain an `LtpaToken2`, then hands the
    cookie to the client's jar.

    `base_url` is the root of the HCL Connections deployment (e.g.
    ``https://connect.example.com``). The handshake is performed against
    ``{base_url}/homepage/`` — a lightweight authenticated endpoint that
    reliably triggers the SPNEGO exchange and sets ``LtpaToken2``.

    Requires the optional `sspi` extra
    (``pip install connections-export[sspi]``); raises `RuntimeError` at
    ``prepare`` time if it is not installed.
    """

    base_url: str

    def prepare(self, client: "HttpClient") -> None:
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth  # noqa: PLC0415
        except ImportError as exc:
            raise AuthError(
                "SspiAuth requires the 'requests-negotiate-sspi' package. "
                "Install it with: pip install connections-export[sspi]  "
                "(Windows only; needs a real domain to authenticate against)."
            ) from exc

        import requests  # noqa: PLC0415

        url = self.base_url.rstrip("/") + "/homepage/"
        session = requests.Session()
        session.auth = HttpNegotiateAuth()
        response = session.get(url, allow_redirects=True, verify=True)
        if not session.cookies:
            raise AuthError(
                f"SspiAuth: SPNEGO handshake completed (HTTP {response.status_code}) "
                "but no session cookies were returned. "
                "Check that the server URL is correct and that your Windows "
                "session has a valid Kerberos/NTLM ticket for the domain."
            )
        # Copy all session cookies into the httpx client — works for both
        # LtpaToken2 (classic WAS/Liberty) and PD-S-SESSION-ID / PD-ID
        # (WebSEAL / Tivoli Access Manager deployments).
        for cookie in session.cookies:
            client.cookies.set(
                cookie.name, cookie.value, domain=cookie.domain or _host(self.base_url)
            )


@dataclass
class KerberosAuth:
    """Performs a Kerberos (SPNEGO) handshake via `requests-kerberos` to
    obtain an `LtpaToken2`, then hands the cookie to the client's jar.

    Requires a real domain (a valid ticket in the caller's credential
    cache) — not exercised by offline unit tests, only by the capture
    tool on first contact. Requires the optional `sspi` extra
    (`pip install connections-export[sspi]`); raises `RuntimeError` at
    `prepare` time if it is not installed.
    """

    base_url: str
    principal: str | None = None

    def prepare(self, client: "HttpClient") -> None:
        try:
            from requests_kerberos import OPTIONAL, HTTPKerberosAuth  # noqa: PLC0415
        except ImportError as exc:
            raise AuthError(
                "KerberosAuth requires the 'requests-kerberos' package. "
                "Install it with: pip install connections-export[sspi]  "
                "(needs a real domain / valid Kerberos ticket to authenticate against)."
            ) from exc

        import requests  # noqa: PLC0415

        url = self.base_url.rstrip("/") + "/homepage/"
        session = requests.Session()
        session.auth = HTTPKerberosAuth(
            mutual_authentication=OPTIONAL,
            principal=self.principal,
        )
        response = session.get(url, allow_redirects=True, verify=True)
        if not session.cookies:
            raise AuthError(
                f"KerberosAuth: SPNEGO handshake completed (HTTP {response.status_code}) "
                "but no session cookies were returned. "
                "Check that your Kerberos ticket cache has a valid ticket "
                "for the server's realm (run 'klist' to verify)."
            )
        for cookie in session.cookies:
            client.cookies.set(
                cookie.name, cookie.value, domain=cookie.domain or _host(self.base_url)
            )
