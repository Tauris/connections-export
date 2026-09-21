"""Which proxy, if any, a request should go through -- decided the way a
browser decides it, and explained.

`httpx` reads `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and nothing else. In an
estate where the proxy is configured in Windows *Internet Options* -- the
settings Edge and Chrome use -- and never as environment variables, that is
a direct connection to a deployment that cannot be reached directly, and it
reads as "the deployment did not answer", which points somewhere else
entirely.

So the decision is made here, in the order a person would expect, most
explicit first:

1. what the command was told (`--proxy URL`, `--proxy direct`, or the
   `proxy` configuration key);
2. the environment (`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY`);
3. a PAC script or auto-detection, evaluated for THIS URL by the same engine
   the browser uses (Windows only; elsewhere it is reported and not run);
4. the static system proxy and its bypass list (Windows registry, macOS
   network preferences);
5. direct.

One rule sits above all five: **loopback is never proxied.** A PAC script
that returns a proxy for `127.0.0.1` exists in the wild, and a proxy that
refuses to forward to it is behaving correctly -- so the request must never
be offered. The tool talks to its own console when it renders a PDF, and that
is where such a script bites.

Every decision carries where it came from, so `connections-export probe
proxy` can print the chain and a report of "proxy issues" becomes a fact.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

#: Hostnames that are this machine, whatever the proxy configuration says.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

#: A resolver answers `(server, detail)` -- `server` None meaning direct --
#: or `None` meaning "this source has no opinion", which lets the next one
#: speak. Injected so the decision logic is testable without a registry, a
#: PAC script or a Windows machine.
Resolver = Callable[[str], "tuple[str | None, str] | None"]


@dataclass(frozen=True)
class ProxyDecision:
    """What a request to `url` goes through, and why.

    `server` is a proxy URL (`http://host:port`) or None for a direct
    connection. `source` names the step that decided: `loopback`,
    `explicit`, `environment`, `pac`, `system`, or `default`. `detail` is the
    sentence a probe prints.
    """

    url: str
    server: str | None
    source: str
    detail: str

    @property
    def direct(self) -> bool:
        return self.server is None


def is_loopback(url: str) -> bool:
    """Is `url` addressed to this machine?"""
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if not host:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_link_local


def _normalize_server(server: str) -> str:
    """`proxy.example.com:8080` -> `http://proxy.example.com:8080`; a scheme
    already present is kept.

    Windows stores proxies without a scheme and WinHTTP returns them that
    way; `httpx` needs one.
    """
    server = server.strip()
    if "://" not in server:
        server = "http://" + server
    return server


def _first_server(value: str) -> str | None:
    """The first usable proxy in a Windows-style list.

    `a.example.com:8080;b.example.com:8080` (alternatives) and
    `http=a.example.com:80;https=b.example.com:443` (per-scheme) both
    occur. The first entry is what a browser tries first;
    a per-scheme list prefers the `https` entry because that is what a
    deployment is.
    """
    entries = [e.strip() for e in value.replace(",", ";").split(";") if e.strip()]
    if not entries:
        return None
    per_scheme = {}
    plain = []
    for entry in entries:
        scheme, eq, rest = entry.partition("=")
        if eq and scheme.lower() in ("http", "https", "socks", "ftp"):
            per_scheme[scheme.lower()] = rest
        else:
            plain.append(entry)
    chosen = per_scheme.get("https") or per_scheme.get("http") or (plain[0] if plain else None)
    return _normalize_server(chosen) if chosen else None


def _bypassed(host: str, bypass: str | None) -> bool:
    """Does `host` match a Windows/`NO_PROXY`-style bypass list?

    Entries are separated by `;` or `,`. `<local>` means a hostname without
    a dot. `*.example.com` and `.example.com` match subdomains; a bare name
    matches exactly or as a suffix, which is how `NO_PROXY` is commonly
    read. `*` alone bypasses everything.
    """
    if not bypass:
        return False
    host = host.lower()
    for raw in bypass.replace(",", ";").split(";"):
        entry = raw.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        if entry == "<local>":
            if "." not in host:
                return True
            continue
        if entry == "<-loopback>":
            continue
        pattern = entry.lstrip("*").lstrip(".")
        if host == pattern or host.endswith("." + pattern):
            return True
    return False


# --- the sources ----------------------------------------------------------


def explicit_resolver(setting: str | None) -> Resolver:
    """What the command or configuration said. `direct`/`none`/`off` means
    "no proxy, whatever else is configured"."""

    def resolve(url: str):
        if setting is None or not setting.strip():
            return None
        value = setting.strip()
        if value.lower() in ("direct", "none", "off", "no"):
            return None, "told to connect directly"
        return _normalize_server(value), "told to use it"

    return resolve


def environment_resolver(env: Mapping[str, str] | None = None) -> Resolver:
    """`HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`, honouring `NO_PROXY`."""
    env = os.environ if env is None else env

    def lookup(*names: str) -> str | None:
        for name in names:
            for candidate in (name, name.lower(), name.upper()):
                value = env.get(candidate)
                if value:
                    return value
        return None

    def resolve(url: str):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        scheme = (parts.scheme or "https").lower()
        server = lookup("HTTPS_PROXY" if scheme == "https" else "HTTP_PROXY") or lookup("ALL_PROXY")
        if not server:
            return None
        no_proxy = lookup("NO_PROXY")
        if _bypassed(host, no_proxy):
            return None, f"NO_PROXY lists {host}"
        return _normalize_server(server), "from the environment"

    return resolve


def system_resolver() -> Resolver:
    """The static system proxy: Windows *Internet Options*, macOS network
    preferences. The standard library reads both; it does not evaluate a PAC
    script, which is `pac_resolver`'s job."""

    def resolve(url: str):
        import urllib.request  # noqa: PLC0415

        if sys.platform == "win32":
            reader = getattr(urllib.request, "getproxies_registry", None)
        elif sys.platform == "darwin":
            reader = getattr(urllib.request, "getproxies_macosx_sysconf", None)
        else:
            reader = None
        if reader is None:
            return None
        try:
            proxies = reader()
        except Exception:  # noqa: BLE001 - an unreadable registry is no opinion
            return None
        if not proxies:
            return None
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if _bypassed(host, proxies.get("no")):
            return None, f"the system bypass list covers {host}"
        server = proxies.get(parts.scheme or "https") or proxies.get("http")
        if not server:
            return None
        return _normalize_server(server), "the system proxy setting"

    return resolve


def pac_resolver() -> Resolver:
    """A PAC script or WPAD auto-detection, evaluated for the URL.

    On Windows this is WinHTTP -- the engine the browser itself uses, run on
    the same script -- through `ctypes`, with no dependency. On other
    platforms the standard library cannot run a PAC script; the resolver
    then has no opinion, and the probe says so.
    """

    def resolve(url: str):
        if sys.platform != "win32":
            return None
        return _winhttp_proxy_for(url)

    return resolve


def pac_configured() -> str | None:
    """A sentence describing the PAC configuration, or None when there is
    none (or this is not Windows). For the probe."""
    if sys.platform != "win32":
        return None
    try:
        config = _winhttp_ie_config()
    except Exception:  # noqa: BLE001
        return None
    if config is None:
        return None
    auto_detect, pac_url, _proxy, _bypass = config
    if pac_url:
        return f"a PAC script at {pac_url}"
    if auto_detect:
        return "automatic detection (WPAD)"
    return None


# --- Windows: WinHTTP through ctypes ----------------------------------------
# Unexercised anywhere but Windows, by construction. Everything above it is
# tested with stubs; this is the seam a real machine proves.

_WINHTTP_ACCESS_TYPE_NO_PROXY = 1
_WINHTTP_AUTOPROXY_AUTO_DETECT = 0x00000001
_WINHTTP_AUTOPROXY_CONFIG_URL = 0x00000002
_WINHTTP_AUTO_DETECT_TYPE_DHCP = 0x00000001
_WINHTTP_AUTO_DETECT_TYPE_DNS_A = 0x00000002


def _winhttp_ie_config():  # pragma: no cover - Windows only
    """`(auto_detect, pac_url, proxy, bypass)` from the current user's
    Internet Options, or None when the call fails."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class IEProxyConfig(ctypes.Structure):
        _fields_ = [
            ("fAutoDetect", wintypes.BOOL),
            ("lpszAutoConfigUrl", wintypes.LPWSTR),
            ("lpszProxy", wintypes.LPWSTR),
            ("lpszProxyBypass", wintypes.LPWSTR),
        ]

    winhttp = ctypes.windll.winhttp
    config = IEProxyConfig()
    if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
        return None
    result = (
        bool(config.fAutoDetect),
        config.lpszAutoConfigUrl,
        config.lpszProxy,
        config.lpszProxyBypass,
    )
    kernel32 = ctypes.windll.kernel32
    for field in ("lpszAutoConfigUrl", "lpszProxy", "lpszProxyBypass"):
        pointer = getattr(config, field)
        if pointer:
            kernel32.GlobalFree(ctypes.cast(pointer, ctypes.c_void_p))
    return result


def _winhttp_proxy_for(url: str):  # pragma: no cover - Windows only
    """Ask WinHTTP what the current user's PAC/WPAD says for `url`."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    config = _winhttp_ie_config()
    if config is None:
        return None
    auto_detect, pac_url, _proxy, _bypass = config
    if not auto_detect and not pac_url:
        return None  # no PAC: the static setting, if any, is the system resolver's

    class AutoProxyOptions(ctypes.Structure):
        _fields_ = [
            ("dwFlags", wintypes.DWORD),
            ("dwAutoDetectFlags", wintypes.DWORD),
            ("lpszAutoConfigUrl", wintypes.LPCWSTR),
            ("lpvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("fAutoLogonIfChallenged", wintypes.BOOL),
        ]

    class ProxyInfo(ctypes.Structure):
        _fields_ = [
            ("dwAccessType", wintypes.DWORD),
            ("lpszProxy", wintypes.LPWSTR),
            ("lpszProxyBypass", wintypes.LPWSTR),
        ]

    winhttp = ctypes.windll.winhttp
    winhttp.WinHttpOpen.restype = ctypes.c_void_p
    winhttp.WinHttpOpen.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
    winhttp.WinHttpGetProxyForUrl.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(AutoProxyOptions),
        ctypes.POINTER(ProxyInfo),
    ]
    winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]

    session = winhttp.WinHttpOpen(
        "connections-export", _WINHTTP_ACCESS_TYPE_NO_PROXY, None, None, 0
    )
    if not session:
        return None, "WinHTTP could not open a session; treating as direct"
    options = AutoProxyOptions()
    if pac_url:
        options.dwFlags |= _WINHTTP_AUTOPROXY_CONFIG_URL
        options.lpszAutoConfigUrl = pac_url
    if auto_detect:
        options.dwFlags |= _WINHTTP_AUTOPROXY_AUTO_DETECT
        options.dwAutoDetectFlags = _WINHTTP_AUTO_DETECT_TYPE_DHCP | _WINHTTP_AUTO_DETECT_TYPE_DNS_A
    options.fAutoLogonIfChallenged = True
    info = ProxyInfo()
    try:
        ok = winhttp.WinHttpGetProxyForUrl(session, url, ctypes.byref(options), ctypes.byref(info))
        if not ok:
            code = ctypes.GetLastError()
            where = f"the PAC script at {pac_url}" if pac_url else "automatic detection"
            return (
                None,
                f"{where} could not be evaluated (WinHTTP error {code}); treating as direct",
            )
        if info.dwAccessType == _WINHTTP_ACCESS_TYPE_NO_PROXY or not info.lpszProxy:
            return None, ("the PAC script" if pac_url else "automatic detection") + " says DIRECT"
        server = _first_server(info.lpszProxy)
        source = "the PAC script" if pac_url else "automatic detection"
        return server, f"{source} names {server}"
    finally:
        kernel32 = ctypes.windll.kernel32
        for field in ("lpszProxy", "lpszProxyBypass"):
            pointer = getattr(info, field)
            if pointer:
                kernel32.GlobalFree(ctypes.cast(pointer, ctypes.c_void_p))
        winhttp.WinHttpCloseHandle(session)


# --- the decision ---------------------------------------------------------


def resolve_proxy(
    url: str,
    *,
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    resolvers: list[tuple[str, Resolver]] | None = None,
) -> ProxyDecision:
    """Decide what a request to `url` goes through.

    `resolvers` overrides the chain (for tests); the default is explicit,
    environment, PAC, system. Loopback is decided before any of them.
    """
    if is_loopback(url):
        return ProxyDecision(url, None, "loopback", "this machine is never reached through a proxy")
    chain = (
        resolvers
        if resolvers is not None
        else [
            ("explicit", explicit_resolver(explicit)),
            ("environment", environment_resolver(env)),
            ("pac", pac_resolver()),
            ("system", system_resolver()),
        ]
    )
    for source, resolver in chain:
        try:
            answer = resolver(url)
        except Exception as exc:  # noqa: BLE001 - a source that fails is reported, not fatal
            return ProxyDecision(
                url, None, source, f"{source} failed ({type(exc).__name__}); treating as direct"
            )
        if answer is None:
            continue
        server, detail = answer
        return ProxyDecision(url, server, source, detail)
    return ProxyDecision(url, None, "default", "nothing configured; connecting directly")


def httpx_client_kwargs(decision: ProxyDecision) -> dict:
    """The `httpx.Client(...)` arguments that carry a decision out.

    `trust_env` is left on -- it also governs `SSL_CERT_FILE`, which a
    corporate machine may rely on -- so the environment's PROXY variables
    are overridden rather than ignored wholesale: an explicit `proxy=`, or for a
    direct decision an `all://` mount to the default transport, which is
    what makes "direct" mean direct even with `HTTPS_PROXY` set. Loopback
    always goes to the default transport whatever was decided.
    """
    loopback = {
        "all://localhost": None,
        "all://127.0.0.1": None,
        "all://[::1]": None,
    }
    if decision.direct:
        # Per scheme as well as `all://`: httpx mounts the environment's
        # proxies under `http://` and `https://`, and a more specific pattern
        # outranks a broader one, so an `all://` override alone leaves
        # HTTPS_PROXY in charge of every https request. That is exactly the
        # decision this is meant to override.
        return {"mounts": {"all://": None, "http://": None, "https://": None, **loopback}}
    return {"proxy": decision.server, "mounts": loopback}


def playwright_proxy_kwargs(decision: ProxyDecision) -> dict:
    """The same decision, for a browser this tool launches.

    Chromium takes the system PAC on its own, so left alone it would make
    its own decision -- including, with a script that returns a proxy for
    loopback, sending the console's own pages to a proxy that refuses them.
    A direct decision becomes `--no-proxy-server`; a proxy decision is passed
    with loopback in the bypass list.
    """
    if decision.direct:
        return {"args": ["--no-proxy-server"]}
    return {"proxy": {"server": decision.server, "bypass": "localhost,127.0.0.1,[::1],<-loopback>"}}


def explain(decision: ProxyDecision) -> str:
    """One line for a probe or a log."""
    via = "direct" if decision.direct else f"via {decision.server}"
    return f"{decision.url}: {via} — {decision.detail} [{decision.source}]"
