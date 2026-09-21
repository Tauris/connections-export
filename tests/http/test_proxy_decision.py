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


# --- the probe --------------------------------------------------------------------


def test_probe_proxy_answers_without_a_configured_deployment(capsys, monkeypatch):
    """ "probe proxy did not return anything": with no deployment configured
    it exited 2 with one line on stderr. The question is what this machine
    does; an address to ask about is an example, not a precondition."""
    from connections_export import cli

    monkeypatch.delenv("CONNECTIONS_EXPORT_BASE_URL", raising=False)
    code = cli.probe_main(["proxy"])

    out = capsys.readouterr().out
    assert code == 0
    assert "probe proxy" in out and "127.0.0.1" in out and "order of authority" in out


def test_probe_proxy_honours_an_explicit_proxy(capsys):
    from connections_export import cli

    code = cli.probe_main(["proxy", "--base-url", DEPLOYMENT, "--proxy", "direct"])

    assert code == 0
    assert "told to connect directly [explicit]" in capsys.readouterr().out


def test_a_slow_pac_evaluation_does_not_hold_the_caller(monkeypatch):
    """WPAD can probe DHCP and DNS for a long time on a network that answers
    neither. The run -- and the probe -- must not sit behind it."""
    import time

    def never_answers(url):
        time.sleep(5)
        return "http://late.example.com:1", "too late"

    monkeypatch.setattr(px, "_winhttp_proxy_for_unbounded", never_answers)
    started = time.monotonic()
    answer = px._winhttp_proxy_for(DEPLOYMENT, timeout=0.2)

    assert time.monotonic() - started < 2
    assert answer[0] is None and "had not answered" in answer[1]


def test_a_pac_evaluation_that_raises_is_an_opinion_of_direct(monkeypatch):
    def explodes(url):
        raise OSError("winhttp missing")

    monkeypatch.setattr(px, "_winhttp_proxy_for_unbounded", explodes)
    answer = px._winhttp_proxy_for(DEPLOYMENT, timeout=1)

    assert answer[0] is None and "OSError" in answer[1]


# --- precedence per platform, and surfaced uncertainty ----------------------


def test_windows_puts_pac_and_system_before_the_environment(monkeypatch):
    """PAC-first on Windows: the org's per-URL policy wins over a blunt global
    HTTP_PROXY that cannot say "except this internal host"."""
    monkeypatch.setattr(px.sys, "platform", "win32")
    order = [name for name, _ in px.default_chain(None, {})]
    assert order == ["explicit", "pac", "system", "environment"]


def test_non_windows_leads_with_the_environment(monkeypatch):
    monkeypatch.setattr(px.sys, "platform", "linux")
    assert [n for n, _ in px.default_chain(None, {})] == ["explicit", "environment"]


def test_an_undetermined_pac_does_not_silently_become_direct():
    """ "could not tell" must never read as "no proxy needed". It is surfaced,
    tried directly, and flagged so a failure can point at a missing proxy."""
    decision = px.resolve_proxy(
        DEPLOYMENT,
        resolvers=[
            ("pac", lambda url: (px.UNDETERMINED, "the PAC script could not be evaluated")),
            ("environment", _silent),
        ],
    )
    assert decision.direct  # tried directly...
    assert decision.undetermined  # ...but NOT presented as a confident direct
    assert decision.source == "undetermined"
    assert "a proxy may be required" in decision.detail
    assert "could not be evaluated" in decision.detail


def test_an_undetermined_pac_yields_to_a_later_source_that_knows():
    """If the PAC can't tell but the environment names a proxy, use it -- the
    uncertainty is not the final word."""
    decision = px.resolve_proxy(
        DEPLOYMENT,
        resolvers=[
            ("pac", lambda url: (px.UNDETERMINED, "timed out")),
            ("environment", _says("http://env.example.com:8080", "from the environment")),
        ],
    )
    assert decision.server == "http://env.example.com:8080"
    assert decision.source == "environment"


def test_a_real_pac_direct_is_confident_not_undetermined():
    decision = px.resolve_proxy(
        DEPLOYMENT, resolvers=[("pac", lambda url: (None, "the PAC script says DIRECT"))]
    )
    assert decision.direct and not decision.undetermined and decision.source == "pac"


# --- the auth handshake obeys the same decision -----------------------------


def test_requests_proxies_from_a_decision():
    via = px.ProxyDecision(DEPLOYMENT, "http://proxy.example.com:8080", "pac", "names it")
    assert px.requests_session_proxies(via) == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }
    direct = px.ProxyDecision(DEPLOYMENT, None, "explicit", "direct")
    assert px.requests_session_proxies(direct) == {}


def test_the_auth_handshake_stops_reading_env_proxies_and_keeps_the_ca_bundle(monkeypatch):
    """The reported bug: requests read HTTP_PROXY on its own, so an internal
    handshake went through a proxy even when the crawl was direct. The helper
    disables that and preserves the corporate CA bundle."""
    from connections_export.http import auth as auth_mod

    class _Client:
        proxy_decision = px.ProxyDecision(DEPLOYMENT, None, "pac", "the PAC says DIRECT")

    class _Session:
        trust_env = True
        proxies: dict = {}
        verify = True

    session = _Session()
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/corp/ca.pem")
    auth_mod._apply_proxy(session, _Client())

    assert session.trust_env is False  # env proxies no longer shadow the decision
    assert session.proxies == {}  # direct, per the decision
    assert session.verify == "/etc/corp/ca.pem"  # corporate CA preserved
