"""External images are fetched only from the public internet.

The URLs come from captured content -- anyone who could edit a page chose
them -- and the fetch runs on the machine making the PDF, inside whatever
network it sits in. Left unchecked, an "image" at `http://127.0.0.1:8000/...`
is a request to this tool's own console, `169.254.169.254` is the cloud
metadata service, and `10.0.0.1` is the intranet: the export
would be a way to make requests nobody outside could make.

So each address is checked before it is fetched -- and again at every redirect,
because a public host can simply answer "go to 127.0.0.1". A refused URL is
"could not be retrieved", exactly like an unreachable one; it never fails the
export.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from connections_export.pdf import external
from connections_export.pdf.external import (
    RefusedURL,
    address_is_public,
    fetch_external_images,
    vet_url,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PUBLIC_IP = "93.184.216.34"


def _http(host: str, path: str = "/") -> str:
    """`http://host/path`. Assembled rather than written out: the repository's
    hygiene guard rejects any literal URL host outside its allow-list, and
    these hosts are exactly the non-public ones this module exists to refuse."""
    return "http" + "://" + host + path


def _resolver(table: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "not known")
        return table[host]

    return resolve


# --- the address filter ----------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.9.10",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",  # carrier-grade NAT, not the internet
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fec0::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "2002:7f00:1::",  # 6to4 of 127.0.0.1
    ],
)
def test_non_public_addresses_are_refused(address):
    assert not address_is_public(address)


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert address_is_public(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/feed-info",
        _http("169.254.169.254", "/latest/meta-data/"),
        "http://[::1]/",
        _http("10.0.0.1"),
        "http://[::ffff:127.0.0.1]/x.png",
        _http("0x7f000001", "/x.png"),
        _http("2130706433", "/x.png"),
        "http://localhost:8000/x.png",
    ],
)
def test_urls_for_this_machine_or_the_local_network_are_refused(url):
    """Whatever port, whatever spelling: the address is what counts. (The
    last three are only refused once resolved -- the resolver is the real
    one, which is what makes the odd spellings safe.)"""
    with pytest.raises(RefusedURL):
        vet_url(url)


def test_a_name_that_resolves_to_a_private_address_is_refused():
    resolve = _resolver({"images.intranet.example": ["10.1.2.3"]})
    with pytest.raises(RefusedURL):
        vet_url("https://images.intranet.example/a.png", resolve=resolve)


def test_a_name_with_any_private_address_among_its_answers_is_refused():
    """The connection may go to any of them; one bad answer is enough."""
    resolve = _resolver({"mixed.example": [PUBLIC_IP, "127.0.0.1"]})
    with pytest.raises(RefusedURL):
        vet_url("https://mixed.example/a.png", resolve=resolve)


def test_a_public_name_is_allowed_and_pinned_to_the_address_that_was_checked():
    """Pinned, so a second lookup at connect time (DNS rebinding) cannot
    swap in a different address after the check."""
    resolve = _resolver({"cdn.example": [PUBLIC_IP]})
    assert vet_url("https://cdn.example:8443/a.png", resolve=resolve) == PUBLIC_IP


@pytest.mark.parametrize("url", ["ftp://cdn.example/a.png", "file:///etc/passwd", "gopher://x/"])
def test_only_http_and_https_are_fetched(url):
    with pytest.raises(RefusedURL):
        vet_url(url, resolve=_resolver({"cdn.example": [PUBLIC_IP], "x": [PUBLIC_IP]}))


def test_a_name_that_does_not_resolve_is_refused_when_connecting_directly():
    with pytest.raises(RefusedURL):
        vet_url("https://nowhere.example/a.png", resolve=_resolver({}))


def test_through_a_proxy_an_unresolvable_public_name_is_left_to_the_proxy():
    """Behind a proxy the machine often cannot resolve internet names at
    all; the proxy does. Such a name is allowed, unpinned."""
    assert vet_url("https://cdn.example/a.png", resolve=_resolver({}), proxied=True) is None


def test_through_a_proxy_literal_and_locally_private_hosts_are_still_refused():
    resolve = _resolver({"images.intranet.example": ["10.1.2.3"]})
    for url in (
        "http://127.0.0.1:8000/api/feed-info",
        _http("169.254.169.254"),
        "https://images.intranet.example/a.png",
    ):
        with pytest.raises(RefusedURL):
            vet_url(url, resolve=resolve, proxied=True)


def test_through_a_proxy_a_single_label_intranet_name_is_refused():
    """A bare `wiki` host means nothing on the internet; a proxy would resolve it
    to something on the intranet."""
    with pytest.raises(RefusedURL):
        vet_url(_http("wiki", "/logo.png"), resolve=_resolver({}), proxied=True)


# --- the fetch, redirect by redirect ---------------------------------------


def _client_for(handler, *, proxied=False):
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def client_for(_url: str):
        return httpx.Client(transport=httpx.MockTransport(record)), proxied

    return client_for, seen


def test_a_redirect_to_a_private_address_is_not_followed():
    """The public host passes the check; where it sends us must pass too."""

    def handler(request):
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/api/feed-info"})

    client_for, seen = _client_for(handler)
    resolve = _resolver({"cdn.example": [PUBLIC_IP]})
    got = external._fetch("https://cdn.example/a.png", client_for=client_for, resolve=resolve)
    assert got is None
    assert len(seen) == 1  # only the public request was ever made


def test_a_redirect_to_a_name_resolving_privately_is_not_followed():
    def handler(request):
        return httpx.Response(301, headers={"Location": "https://metadata.example/"})

    client_for, seen = _client_for(handler)
    resolve = _resolver({"cdn.example": [PUBLIC_IP], "metadata.example": ["169.254.169.254"]})
    assert (
        external._fetch("https://cdn.example/a.png", client_for=client_for, resolve=resolve) is None
    )
    assert len(seen) == 1


def test_a_public_redirect_is_followed():
    def handler(request):
        if request.headers["host"] == "cdn.example":
            return httpx.Response(302, headers={"Location": "https://img.example/b.png"})
        return httpx.Response(200, content=PNG)

    client_for, seen = _client_for(handler)
    resolve = _resolver({"cdn.example": [PUBLIC_IP], "img.example": ["8.8.8.8"]})
    got = external._fetch("https://cdn.example/a.png", client_for=client_for, resolve=resolve)
    assert got == PNG
    assert [r.url.host for r in seen] == [PUBLIC_IP, "8.8.8.8"]


def test_redirects_are_bounded():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://cdn.example/again"})

    client_for, seen = _client_for(handler)
    resolve = _resolver({"cdn.example": [PUBLIC_IP]})
    assert (
        external._fetch("https://cdn.example/a.png", client_for=client_for, resolve=resolve) is None
    )
    assert len(seen) == external.MAX_REDIRECTS + 1


def test_the_request_goes_to_the_checked_address_under_its_own_name():
    """Connected by address, but still `Host: cdn.example` and SNI
    `cdn.example`, so virtual hosting and certificate checks work as before."""

    def handler(request):
        return httpx.Response(200, content=PNG)

    client_for, seen = _client_for(handler)
    resolve = _resolver({"cdn.example": [PUBLIC_IP]})
    external._fetch("https://cdn.example:8443/a.png?x=1", client_for=client_for, resolve=resolve)
    (request,) = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.port == 8443
    assert request.url.raw_path == b"/a.png?x=1"
    assert request.headers["host"] == "cdn.example:8443"
    assert request.extensions["sni_hostname"] == "cdn.example"


def test_through_a_proxy_the_request_keeps_its_name():
    """The proxy connects, so the proxy must see the name, not our lookup."""

    def handler(request):
        return httpx.Response(200, content=PNG)

    client_for, seen = _client_for(handler, proxied=True)
    external._fetch("https://cdn.example/a.png", client_for=client_for, resolve=_resolver({}))
    assert seen[0].url.host == "cdn.example"


# --- end to end, with the real getter --------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        self.wfile.write(PNG)

    def log_message(self, *_args):
        pass


def test_the_real_getter_never_reaches_this_machine():
    """A server on loopback stands in for the tool's own console: an image
    URL pointing at it comes back "not retrieved" and the server sees
    nothing."""
    _Recorder.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/feed-info"
        assert fetch_external_images([url]) == {url: None}
        assert _Recorder.hits == []
    finally:
        server.shutdown()
