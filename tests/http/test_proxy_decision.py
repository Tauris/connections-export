"""Which proxy a request goes through, decided the way a browser decides it.

`httpx` reads the environment and nothing else. In an estate where the proxy
lives in Windows Internet Options -- what the browser uses -- and never in
an environment variable, that is a direct connection to a deployment that
cannot be reached directly, reported as "the deployment did not answer".

The Windows engine (WinHTTP through ctypes) cannot run here; everything
around it can, and does, with each source stubbed. The one rule that sits
above every source -- loopback is never proxied -- is pinned first, because
a PAC script that says otherwise exists and the proxy refusing it is right.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.http import proxy as px
from connections_export.http.client import HttpClient
from connections_export.http.results import FetchFailure

DEPLOYMENT = "https://connections.example.corp/wikis/basic/api/wikis/feed"


def _says(server, detail="stubbed"):
    """A source with an opinion."""
    return lambda url: (server, detail)


def _silent(url):
    """A source with no opinion, which lets the next one speak."""
    return None


# --- loopback -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/health",
        "http://localhost:8000/",
        "http://[::1]:8000/",
    ],
)
def test_loopback_is_direct_whatever_any_source_says(url):
    decision = px.resolve_proxy(
        url,
        resolvers=[
            ("explicit", _says("http://proxy.example.com:8080")),
            ("pac", _says("http://p.example.com:1")),
        ],
    )
    assert decision.direct
    assert decision.source == "loopback"


def test_link_local_counts_as_this_machine():
    """`169.254/16` is the link, not the network; no proxy can reach it."""
    link_local = "http://" + ".".join(("169", "254", "1", "1")) + "/"
    assert px.is_loopback(link_local)


def test_a_deployment_is_not_loopback():
    assert not px.is_loopback(DEPLOYMENT)


# --- precedence -----------------------------------------------------------------


def test_the_first_source_with_an_opinion_decides():
    decision = px.resolve_proxy(
        DEPLOYMENT,
        resolvers=[
            ("explicit", _silent),
            ("environment", _silent),
            ("pac", _says("http://pac.example.com:3128", "the PAC script names it")),
            ("system", _says("http://static.example.com:8080")),
        ],
    )
    assert decision.server == "http://pac.example.com:3128"
    assert decision.source == "pac"
    assert "PAC" in decision.detail


def test_a_direct_opinion_stops_the_chain_too():
    """`NO_PROXY` matching, or a PAC saying DIRECT, is an answer -- not an
    invitation for the static system proxy to have a go."""
    decision = px.resolve_proxy(
        DEPLOYMENT,
        resolvers=[
            ("environment", _says(None, "NO_PROXY lists the host")),
            ("system", _says("http://static.example.com:8080")),
        ],
    )
    assert decision.direct
    assert decision.source == "environment"


def test_nothing_configured_means_direct_and_says_so():
    decision = px.resolve_proxy(DEPLOYMENT, resolvers=[("pac", _silent), ("system", _silent)])
    assert decision.direct
    assert decision.source == "default"


def test_a_source_that_raises_is_reported_not_fatal():
    def broken(url):
        raise OSError("registry unreadable")

    decision = px.resolve_proxy(DEPLOYMENT, resolvers=[("system", broken)])
    assert decision.direct
    assert "failed" in decision.detail and "OSError" in decision.detail


# --- the sources themselves ---------------------------------------------------------


def test_explicit_direct_overrides_everything():
    decision = px.resolve_proxy(
        DEPLOYMENT,
        explicit="direct",
        resolvers=None,
        env={"HTTPS_PROXY": "http://env.example.com:1"},
    )
    assert decision.direct and decision.source == "explicit"


def test_explicit_url_gains_a_scheme():
    resolve = px.explicit_resolver("proxy.example.net:8080")
    assert resolve(DEPLOYMENT) == ("http://proxy.example.net:8080", "told to use it")


def test_environment_honours_no_proxy():
    resolve = px.environment_resolver(
        {"HTTPS_PROXY": "http://env.example.com:8080", "NO_PROXY": ".example.corp,localhost"}
    )
    assert resolve(DEPLOYMENT)[0] is None
    assert resolve("https://elsewhere.example.com/")[0] == "http://env.example.com:8080"


def test_environment_with_nothing_set_has_no_opinion():
    assert px.environment_resolver({})(DEPLOYMENT) is None


@pytest.mark.parametrize(
    ("host", "bypass", "expected"),
    [
        ("intranet", "<local>", True),
        ("intranet.example.corp", "<local>", False),
        ("wiki.example.corp", "*.example.corp", True),
        ("wiki.example.corp", ".example.corp", True),
        ("wiki.example.corp", "example.corp", True),
        ("example.corp.evil.com", "example.corp", False),
        ("anything", "*", True),
        ("wiki.example.corp", "", False),
        ("wiki.example.corp", "<-loopback>", False),
    ],
)
def test_bypass_list_matching(host, bypass, expected):
    assert px._bypassed(host, bypass) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("proxy.example.com:8080", "http://proxy.example.com:8080"),
        ("a.example.com:8080;b.example.com:8080", "http://a.example.com:8080"),
        ("http=a.example.com:80;https=b.example.com:443", "http://b.example.com:443"),
        ("http=a.example.com:80", "http://a.example.com:80"),
        ("", None),
    ],
)
def test_windows_proxy_lists_are_read_the_way_a_browser_reads_them(value, expected):
    assert px._first_server(value) == expected


def test_pac_has_no_opinion_off_windows(monkeypatch):
    monkeypatch.setattr(px.sys, "platform", "linux")
    assert px.pac_resolver()(DEPLOYMENT) is None
    assert px.pac_configured() is None


# --- carrying the decision into httpx ---------------------------------------------


def test_a_direct_decision_is_direct_even_with_https_proxy_set(monkeypatch):
    """The whole point of deciding ourselves: `--proxy direct`, or a PAC that
    says DIRECT, must win over an environment variable httpx would otherwise
    obey on its own."""
    monkeypatch.setenv("HTTPS_PROXY", "http://env.example.com:8080")
    decision = px.ProxyDecision(DEPLOYMENT, None, "explicit", "told to connect directly")
    client = httpx.Client(**px.httpx_client_kwargs(decision))
    transport = client._transport_for_url(httpx.URL(DEPLOYMENT))
    assert transport is client._transport, "an env proxy mount answered for a direct decision"


def test_a_proxy_decision_never_applies_to_loopback():
    decision = px.ProxyDecision(
        DEPLOYMENT, "http://proxy.example.com:8080", "pac", "the PAC script names it"
    )
    client = httpx.Client(**px.httpx_client_kwargs(decision))
    assert client._transport_for_url(httpx.URL("http://127.0.0.1:8000/x")) is client._transport
    assert client._transport_for_url(httpx.URL("http://localhost:8000/x")) is client._transport
    assert client._transport_for_url(httpx.URL(DEPLOYMENT)) is not client._transport


def test_the_client_accepts_a_decision():
    decision = px.ProxyDecision(
        DEPLOYMENT, "http://proxy.example.com:8080", "system", "the system proxy setting"
    )
    client = HttpClient(proxy=decision)
    inner = client._client
    assert inner._transport_for_url(httpx.URL(DEPLOYMENT)) is not inner._transport


def test_a_407_names_the_proxy_and_the_fix():
    """The proxy, not the deployment, refused -- and wants credentials this
    tool cannot supply. Said so, with the fix that is also the right one
    organisationally."""

    def handler(request):
        return httpx.Response(407)

    client = HttpClient(transport=httpx.MockTransport(handler))
    result = client.get(DEPLOYMENT)
    assert isinstance(result, FetchFailure)
    assert "407" in result.error and "bypass" in result.error and "--proxy direct" in result.error


def test_the_browser_gets_the_same_decision():
    direct = px.ProxyDecision(DEPLOYMENT, None, "pac", "the PAC script says DIRECT")
    assert px.playwright_proxy_kwargs(direct) == {"args": ["--no-proxy-server"]}
    via = px.ProxyDecision(DEPLOYMENT, "http://proxy.example.com:8080", "pac", "names it")
    kwargs = px.playwright_proxy_kwargs(via)
    assert kwargs["proxy"]["server"] == "http://proxy.example.com:8080"
    assert "127.0.0.1" in kwargs["proxy"]["bypass"] and "localhost" in kwargs["proxy"]["bypass"]


def test_explain_reads_as_one_line():
    decision = px.ProxyDecision(
        DEPLOYMENT, "http://proxy.example.com:8080", "pac", "the PAC script names it"
    )
    line = px.explain(decision)
    assert DEPLOYMENT in line and "via http://proxy.example.com:8080" in line and "[pac]" in line
