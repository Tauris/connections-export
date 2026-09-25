"""Auth strategies: all converge on the client's session (cookie jar or
a host-bound auth flow), so `HttpClient` itself has no idea which one ran.

Whatever a strategy adds is bound to the deployment it was given for: a
cookie carries the deployment's domain, a Basic password is attached only to
requests for the deployment's origin. A client asked to fetch another host
-- a URL from a page, an archive, a redirect -- sends it nothing.

`BasicAuth` and `PasteTokenAuth` are testable offline. `SspiAuth` and
`KerberosAuth` perform a real SPNEGO handshake against a domain to
obtain an `LtpaToken2` — they require a real domain and are exercised
by the capture tool on first contact, not by offline unit tests. Their
third-party dependencies (`requests-negotiate-sspi`, `requests-kerberos`)
are optional extras (`pip install connections-export[sspi]`); importing this
module never requires them, only calling `prepare` on those two does.
"""

import base64
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

import httpx

if TYPE_CHECKING:
    from connections_export.http.client import HttpClient


def _host(base_url: str) -> str:
    """Extract the hostname from a base URL for cookie scoping."""
    return urlparse(base_url).hostname or base_url


_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str | httpx.URL) -> tuple[str, str, int | None]:
    """`(scheme, host, port)` of a URL, the default port made explicit, so
    a host with no port and the same host on 443 are one origin over https,
    and the same host on 8443 is another. Credentials go to an origin, never
    merely to a hostname."""
    parsed = urlparse(str(url))
    scheme = (parsed.scheme or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    return scheme, (parsed.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme)


def same_origin(a: str | httpx.URL, b: str | httpx.URL) -> bool:
    """Whether two URLs are the same origin -- the test for "may this
    request carry what was set up for that one"."""
    first = origin_of(a)
    return bool(first[1]) and first == origin_of(b)


class _OriginBoundHeader(httpx.Auth):
    """Adds one header, but only to requests for one origin.

    A client-wide default header goes wherever the client is sent; this is
    consulted per request, so a URL naming another host gets nothing. httpx
    also drops an `auth` on a redirect that leaves the origin."""

    def __init__(self, base_url: str, name: str, value: str) -> None:
        self._base_url = base_url
        self._name = name
        self._value = value

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if same_origin(request.url, self._base_url):
            request.headers[self._name] = self._value
        yield request


def _is_ip_address(host: str) -> bool:
    import ipaddress  # noqa: PLC0415

    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _listed_host_matches(entry: str, target) -> bool:
    """Whether an `hcl_hosts` entry names `target`'s host: a bare host
    matches it on any port (as `crawler.assets.classify` compares hosts),
    `host:port` only on that port."""
    listed = urlparse("//" + entry.strip().lower())
    if not listed.hostname or listed.hostname != (target.hostname or "").lower():
        return False
    try:
        return listed.port is None or listed.port == origin_of(target.geturl())[2]
    except ValueError:
        return False


def is_sign_in_host(target_url: str, base_url: str, sign_in_hosts: Iterable[str] = ()) -> bool:
    """Whether a redirect to `target_url` may carry the sign-in for the
    deployment at `base_url`.

    The deployment's own origin always may. Another host may only over
    https (a downgrade would put the handshake on the wire in the clear),
    and only when it belongs to the deployment's organisation: listed in
    `hcl_hosts`, or under the deployment host's parent domain --
    connections.example.com trusts login.example.com. The parent must have
    two labels itself, so a deployment at example.com does not trust all of
    `.com`; an IP address has no parent at all."""
    if same_origin(target_url, base_url):
        return True
    target = urlparse(target_url)
    if (target.scheme or "").lower() != "https" or not target.hostname:
        return False
    if any(_listed_host_matches(entry, target) for entry in sign_in_hosts):
        return True
    base_host = (urlparse(base_url).hostname or "").lower()
    if not base_host or _is_ip_address(base_host):
        return False
    parent = base_host.split(".", 1)[1] if "." in base_host else ""
    if parent.count(".") < 1:
        return False
    host = target.hostname.lower()
    return host == parent or host.endswith("." + parent)


def refused_sign_in_redirect(target_url: str, base_url: str) -> str:
    """What to tell the user when sign-in was sent somewhere untrusted --
    phrased as the fix, since a legitimate login server looks exactly like
    this until it is listed."""
    target = urlparse(target_url)
    host = target.hostname or target_url
    base_host = urlparse(base_url).hostname or base_url
    downgrade = (
        " over plain http" if (target.scheme or "").lower() == "http" and target.hostname else ""
    )
    return (
        f"Sign-in was redirected to {host}{downgrade}, which is not part of {base_host}, "
        "so the sign-in was not sent there. If that is your organisation's sign-in "
        "server, add it to hcl_hosts in connections-export.toml"
        + (" (and make sure it is reached over https)." if downgrade else ".")
    )


def _sign_in_get(session, url: str, *, sign_in_hosts: Iterable[str] = (), max_redirects: int = 10):
    """GET `url` with a `requests` session, following redirects only to the
    deployment itself or its sign-in hosts (`is_sign_in_host`).

    `requests` keeps the auth's response hooks on a redirected request, so a
    Negotiate handshake is carried out with whatever host a redirect names
    -- a Kerberos ticket requested for it, or an NTLM response it could
    relay. Each hop is judged against the deployment, not the previous hop,
    so a trusted login server cannot pass the handshake further on. A
    redirect anywhere else raises `AuthError` naming the host."""
    response = session.get(url, allow_redirects=False)
    for _ in range(max_redirects):
        location = response.headers.get("location") if response.is_redirect else None
        if not location:
            break
        target = urljoin(response.url or url, location)
        if not is_sign_in_host(target, url, sign_in_hosts):
            raise AuthError(refused_sign_in_redirect(target, url))
        response = session.get(target, allow_redirects=False)
    return response


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


def _apply_proxy(session, client, env=None) -> None:
    """Make a `requests` handshake session obey the SAME proxy decision the
    crawl's client uses, instead of reading proxy env vars on its own.

    `trust_env=False` stops requests picking up `HTTP_PROXY`/`HTTPS_PROXY`
    (the shadowing that sent an internal-host handshake to a proxy). The
    deployment's own CA bundle is preserved explicitly, since turning off
    `trust_env` also drops `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` -- a corporate
    TLS-inspecting proxy relies on it.
    """
    import os as _os  # noqa: PLC0415

    env = _os.environ if env is None else env
    decision = getattr(client, "proxy_decision", None)
    if decision is None:
        return
    from connections_export.http.proxy import requests_session_proxies  # noqa: PLC0415

    session.trust_env = False
    session.proxies = requests_session_proxies(decision)
    ca = env.get("REQUESTS_CA_BUNDLE") or env.get("SSL_CERT_FILE")
    if ca:
        session.verify = ca


def _require_base(strategy: str, base_url: str) -> str:
    if not origin_of(base_url)[1]:
        raise AuthError(
            f"{strategy} needs the deployment's address, so the credentials are only "
            "ever sent there. Set base_url (hcl-export.toml or the environment)."
        )
    return base_url


@dataclass
class BasicAuth:
    """Sends `Authorization: Basic` from a username and password -- to the
    deployment at `base_url` and nowhere else.

    It was a client-wide default header, which went to any host the client
    was asked to fetch; a lookup pointed at another host sent it the
    password."""

    username: str
    password: str
    base_url: str = ""

    def prepare(self, client: "HttpClient") -> None:
        base = _require_base("Basic sign-in", self.base_url)
        credentials = f"{self.username}:{self.password}".encode()
        token = base64.b64encode(credentials).decode("ascii")
        client.auth = _OriginBoundHeader(base, "Authorization", f"Basic {token}")


@dataclass
class PasteTokenAuth:
    """Injects an `LtpaToken2` cookie into the client's jar, for the
    deployment's domain. The development-unblock path — a token pasted from
    a browser session, no handshake needed.

    Set without a domain, httpx sent it to every host the client fetched."""

    ltpa_token: str
    base_url: str = ""

    def prepare(self, client: "HttpClient") -> None:
        base = _require_base("A pasted token", self.base_url)
        client.cookies.set("LtpaToken2", self.ltpa_token, domain=_host(base))


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
    #: Hosts besides the deployment's own that sign-in may be redirected to
    #: (`hcl_hosts`); see `is_sign_in_host` for which are trusted without it.
    sign_in_hosts: tuple[str, ...] = ()

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
        _apply_proxy(session, client)
        response = _sign_in_get(session, url, sign_in_hosts=self.sign_in_hosts)
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
    #: As for `SspiAuth.sign_in_hosts`.
    sign_in_hosts: tuple[str, ...] = ()

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
        _apply_proxy(session, client)
        response = _sign_in_get(session, url, sign_in_hosts=self.sign_in_hosts)
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
