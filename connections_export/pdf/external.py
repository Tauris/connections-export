"""External (third-party) images for the PDF: find them, fetch them.

The archive keeps an external image's URL, never its bytes. A PDF that
includes them fetches them here, at export time, and hands the bytes to
`render_html(external_images=...)`, which embeds each one, marks it where it
sits and lists it on the "External content" page.

Fetched here in Python rather than left for Chromium to load: a slow or
unanswering third-party host would otherwise hold up pagination until the
render timeout, which is how external resources have stalled exports before.
Each request is bounded, runs alongside the others, and carries no
credentials -- the deployment's session never goes to a third party.

Only the PUBLIC internet is fetched from. The URLs come from captured content,
chosen by whoever could edit a page, and the request runs from the machine
making the PDF: unchecked, an "image" at `http://127.0.0.1:8000/...` is a
request to this tool's own console, `169.254.169.254` the cloud metadata
service, `10.x` the intranet. So every address is checked before it is
fetched -- at every redirect too, since a public host can answer "go to
127.0.0.1" -- and the connection is pinned to the address that was checked,
so a second DNS answer at connect time cannot swap in another. A refused URL
is "could not be retrieved", the same as an unreachable one.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from connections_export.derive.model import Interchange, ResolvedAsset

#: Seconds to wait on one external image before giving up on it.
FETCH_TIMEOUT = 15.0
#: Largest external image embedded; anything bigger is left out.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: Redirects followed for one image, each re-checked like the first request.
MAX_REDIRECTS = 5
_WORKERS = 8

Getter = Callable[[str], bytes | None]
#: host, port -> the addresses a connection could go to.
Resolver = Callable[[str, int], list[str]]


class RefusedURL(ValueError):
    """A URL this tool will not fetch: not http(s), or not on the public
    internet."""


#: NAT64's well-known prefix: the last 32 bits are an IPv4 address, and the
#: gateway connects to THAT -- so it is judged, not the IPv6 wrapper.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

#: Names that only mean something inside a network. Refused by name, before
#: any lookup: they matter most behind a proxy, where this machine may not
#: resolve them but the proxy would -- to the intranet.
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa")


def address_is_public(address: str) -> bool:
    """Is `address` on the public internet? Loopback, private, link-local,
    shared (carrier NAT), multicast, reserved, unspecified and site-local
    addresses are not -- nor is an IPv6 address that merely wraps one."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return address_is_public(str(embedded))
        if ip.teredo is not None or ip.is_site_local:
            return False
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return ip.is_global


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def vet_url(url: str, *, resolve: Resolver = _resolve, proxied: bool = False) -> str | None:
    """Check `url` may be fetched; raise `RefusedURL` if not.

    Returns the address to connect to -- the one that was checked -- or None
    when a proxy will make the connection (the proxy resolves the name, so
    there is nothing to pin). Behind a proxy this machine often cannot
    resolve internet names at all, so an unresolvable name is left to the
    proxy -- but a literal private address, a name that DOES resolve here to
    one, and an intranet-only name are refused all the same.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise RefusedURL(f"not http(s): {url}")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise RefusedURL(f"no host: {url}")
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise RefusedURL(f"bad port: {url}") from exc
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not address_is_public(host):
            raise RefusedURL(f"not a public address: {url}")
        return None if proxied else host
    # A single-label name (a bare `wiki`) means nothing on the internet.
    if "." not in host or host.lower().endswith(_INTERNAL_SUFFIXES):
        raise RefusedURL(f"not a public name: {url}")
    try:
        addresses = resolve(host, port)
    except OSError:
        addresses = []
    if not addresses:
        if proxied:
            return None
        raise RefusedURL(f"does not resolve: {url}")
    # The connection may go to any answer, so every one must be public.
    if not all(address_is_public(address) for address in addresses):
        raise RefusedURL(f"resolves to a non-public address: {url}")
    return None if proxied else addresses[0]


def _request(client, url: str, address: str | None):
    """The GET for `url`, sent to `address` when there is one -- under the
    URL's own name (`Host`, and SNI for https), so virtual hosting and
    certificate checks behave exactly as if the name had been connected to."""
    import httpx  # noqa: PLC0415

    target = httpx.URL(url)
    if address is None:
        return client.build_request("GET", target)
    extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
    return client.build_request(
        "GET",
        target.copy_with(host=address),
        headers={"Host": target.netloc.decode("ascii")},
        extensions=extensions,
    )


def _fetch(url: str, *, client_for, resolve: Resolver = _resolve) -> bytes | None:
    """GET `url`, following redirects by hand so each hop is checked.

    `client_for(url) -> (httpx.Client, proxied)` gives the client for one hop
    (the proxy decision can differ per host); tests pass one over a mock
    transport."""
    import httpx  # noqa: PLC0415

    for _hop in range(MAX_REDIRECTS + 1):
        client, proxied = client_for(url)
        with client:
            try:
                address = vet_url(url, resolve=resolve, proxied=proxied)
            except RefusedURL:
                return None
            response = client.send(_request(client, url, address), stream=True)
            try:
                if response.is_redirect:
                    # Joined to the URL as written, not the pinned address.
                    url = str(httpx.URL(url).join(response.headers["location"]))
                    continue
                if response.status_code != 200:
                    return None
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        return None
                return bytes(data)
            finally:
                response.close()
    return None


def _assets(value) -> Iterable[ResolvedAsset]:
    """Every `ResolvedAsset` anywhere in the model, in document order."""
    if isinstance(value, ResolvedAsset):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _assets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _assets(child)
    elif hasattr(value, "__pydantic_fields__"):
        for name in type(value).__pydantic_fields__:
            yield from _assets(getattr(value, name))


def collect_external_image_urls(interchange: Interchange) -> list[str]:
    """The external assets' URLs, each once, in the order they appear."""
    urls: dict[str, None] = {}
    for asset in _assets(interchange):
        if asset.scope == "external" and not asset.present:
            urls.setdefault(asset.resolved_url or asset.original_href, None)
    return list(urls)


def _is_image(data: bytes) -> bool:
    from connections_export.pdf.html import _guess_content_type  # noqa: PLC0415

    # Sniffed from the bytes, with no href to fall back on: a sign-in page
    # or an error document served at an image URL is not an image.
    return _guess_content_type("", data).startswith("image/")


def _default_get(url: str) -> bytes | None:
    import httpx  # noqa: PLC0415

    from connections_export.config import load_config  # noqa: PLC0415
    from connections_export.http.proxy import httpx_client_kwargs, resolve_proxy  # noqa: PLC0415

    try:
        explicit = load_config({}).proxy
    except Exception:  # noqa: BLE001 - unreadable configuration means no explicit proxy
        explicit = None

    def client_for(target: str):
        decision = resolve_proxy(target, explicit=explicit)
        # A fresh client per request: no cookie jar, no Authorization header.
        # Redirects are NOT followed by httpx -- `_fetch` checks each one.
        client = httpx.Client(
            timeout=FETCH_TIMEOUT, follow_redirects=False, **httpx_client_kwargs(decision)
        )
        return client, not decision.direct

    return _fetch(url, client_for=client_for)


def fetch_external_images(
    urls: Iterable[str], *, get: Getter | None = None
) -> dict[str, bytes | None]:
    """Fetch each URL; `None` for one that failed or is not an image.

    `get` is injected by tests; production uses a bounded, credential-free
    HTTP GET through the same proxy decision as the tool's other requests.
    """
    getter = get or _default_get
    wanted = list(dict.fromkeys(urls))

    def one(url: str) -> bytes | None:
        try:
            data = getter(url)
        except Exception:  # noqa: BLE001 - an unreachable host is "not retrieved", not a failed export
            return None
        return data if data and _is_image(data) else None

    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(wanted))) as pool:
        return dict(zip(wanted, pool.map(one, wanted), strict=True))


def prepare_external_images(
    interchange: Interchange, *, include: bool
) -> dict[str, bytes | None] | None:
    """The `external_images=` argument for `render_html`: None when the
    person chose to leave external images out, the fetched bytes otherwise."""
    if not include:
        return None
    return fetch_external_images(collect_external_image_urls(interchange))
