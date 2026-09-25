"""The console's lookups never hand the user's sign-in to another host.

The lookups take a URL -- one the user dropped, one an archive recorded, one
a redirect names -- and authenticate on the user's behalf. Each way that
URL could name a host other than the deployment used to carry credentials
there:

- session cookies from a crawl were rebuilt as a bare `{name: value}`
  mapping, which httpx sends to EVERY host;
- the manual redirect walk re-sent the Basic password or cookies wherever
  a `Location` pointed;
- opening an archive looked up its live state by authenticating against
  the address written inside the archive -- a file someone else can hand
  you;
- the live PDF navigated a browser, carrying the session, to any URL,
  `file:///` included.

Each test uses `httpx.MockTransport` to record which host saw what.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support
from connections_export.gui.routes import _lookup as lookup_support
from connections_export.http.client import HttpClient

DEPLOYMENT = "https://connections.example.corp"
EVIL = "https://evil.example"


def _recorder(routes: dict[str, httpx.Response] | None = None):
    """A transport that answers from `routes` (by full URL) or 200, and
    records the Cookie and Authorization each host was sent."""
    seen: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.host,
                request.headers.get("cookie"),
                request.headers.get("authorization"),
            )
        )
        return (routes or {}).get(str(request.url), httpx.Response(200, content=b"ok"))

    return httpx.MockTransport(handler), seen


def _signed_in_app():
    """A console after a crawl: cookies stored, one with its domain, one
    without (httpx reports a pasted token's domain as empty)."""
    app = make_app()
    app.state.live_base_url = DEPLOYMENT
    app.state.live_cookies = [
        {"name": "LtpaToken2", "value": "L", "domain": "connections.example.corp", "path": "/"},
        {"name": "JSESSIONID", "value": "J", "domain": "", "path": "/"},
    ]
    return app


# --- cookies carry their domain ----------------------------------------------


def test_a_lookups_session_cookies_go_only_to_the_deployment():
    """The jar was `{name: value}`, which httpx sends to any host -- so a
    lookup pointed at another address sent it the user's session."""
    transport, seen = _recorder()
    session = lookup_support.LookupSession(
        _signed_in_app(), DEPLOYMENT, None, timeout=5, transport=transport
    )

    session.get(f"{DEPLOYMENT}/homepage/")
    session.get(f"{EVIL}/steal")

    cookies = {host: cookie for host, cookie, _auth in seen}
    assert "LtpaToken2=L" in cookies["connections.example.corp"]
    assert "JSESSIONID=J" in cookies["connections.example.corp"], (
        "a cookie recorded without a domain belongs to the live session's deployment"
    )
    assert cookies["evil.example"] is None


def test_a_cookie_with_no_domain_and_no_deployment_to_bind_it_to_is_dropped():
    """Better to ask without it than to send it everywhere."""
    app = make_app()
    app.state.live_base_url = None
    app.state.live_cookies = [{"name": "JSESSIONID", "value": "J", "domain": "", "path": "/"}]

    assert lookup_support.bound_live_cookies(app) == []


def test_the_live_pdf_browser_gets_every_cookie_with_a_domain():
    """Playwright needs a domain for each cookie; one recorded without used
    to be passed through empty."""
    cookies = lookup_support.bound_live_cookies(_signed_in_app())

    assert {c["domain"] for c in cookies} == {"connections.example.corp"}


def test_the_feed_count_fallback_does_not_send_the_session_to_the_dropped_host(monkeypatch):
    """When authentication cannot be prepared, `/api/feed-info` falls back to
    the crawl's cookies -- which it passed as `{name: value}`, so a URL
    naming another host received the user's session."""
    import connections_export.cli as cli_module

    transport, seen = _recorder()

    def refuse(*a, **k):
        raise RuntimeError("no sign-in available")

    def fake_get(url, *, cookies=None, **kwargs):
        with httpx.Client(transport=transport, cookies=cookies) as client:
            return client.get(url)

    monkeypatch.setattr(cli_module, "_build_default_client", refuse)
    monkeypatch.setattr(httpx, "get", fake_get)
    http = TestClient(_signed_in_app(), base_url="http://127.0.0.1")

    http.get("/api/feed-info", params={"url": f"{EVIL}/blogs/b/entry/e"})
    http.get("/api/feed-info", params={"url": f"{DEPLOYMENT}/blogs/b/entry/e"})

    cookies = {host: cookie for host, cookie, _auth in seen}
    assert cookies.get("evil.example") is None
    assert "LtpaToken2=L" in cookies["connections.example.corp"]


# --- redirects stay on the deployment ---------------------------------------


def test_a_redirect_to_another_host_is_not_followed_with_the_users_password(monkeypatch):
    """The walk re-sent the request -- Basic password and all -- wherever a
    `Location` pointed. A redirect off the deployment ends the walk."""
    from connections_export.http.auth import BasicAuth

    transport, seen = _recorder(
        {f"{DEPLOYMENT}/x": httpx.Response(302, headers={"location": f"{EVIL}/grab"})}
    )

    def build(config, env, **overrides):
        client = HttpClient(transport=transport)
        BasicAuth(username="u", password="p", base_url=config.base_url).prepare(client)
        return client

    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", build)
    app = make_app()
    app.state.live_cookies = []

    status, _content, _error = lookup_support.LookupSession(
        app, DEPLOYMENT, "basic", timeout=5
    ).get(f"{DEPLOYMENT}/x")

    assert [host for host, _c, _a in seen] == ["connections.example.corp"]
    assert status == 302


def test_a_redirect_within_the_deployment_is_still_followed(monkeypatch):
    """The walk exists for the deployment's own redirects; those keep working."""
    transport, seen = _recorder(
        {f"{DEPLOYMENT}/x": httpx.Response(302, headers={"location": "/y"})}
    )

    import connections_export.cli as cli_module

    monkeypatch.setattr(
        cli_module, "_build_default_client", lambda *a, **k: HttpClient(transport=transport)
    )
    app = make_app()
    app.state.live_cookies = []

    status, content, _error = lookup_support.LookupSession(app, DEPLOYMENT, "basic", timeout=5).get(
        f"{DEPLOYMENT}/x"
    )

    assert status == 200
    assert content == b"ok"
    assert len(seen) == 2


def test_a_redirect_to_the_organisations_sign_in_host_is_followed(monkeypatch):
    """The same rule as the handshake's: a deployment that sends sign-in to
    a sibling login host is still read. The Basic password stays bound to
    the deployment's origin, so the login host is asked without it."""
    from connections_export.http.auth import BasicAuth

    login = "https://login.example.corp/sso"
    transport, seen = _recorder(
        {f"{DEPLOYMENT}/x": httpx.Response(302, headers={"location": login})}
    )

    def build(config, env, **overrides):
        client = HttpClient(transport=transport)
        BasicAuth(username="u", password="p", base_url=config.base_url).prepare(client)
        return client

    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", build)
    app = make_app()
    app.state.live_cookies = []

    status, _content, _error = lookup_support.LookupSession(
        app, DEPLOYMENT, "basic", timeout=5
    ).get(f"{DEPLOYMENT}/x")

    assert [host for host, _c, _a in seen] == ["connections.example.corp", "login.example.corp"]
    assert seen[1][2] is None, "the password went to the login host"
    assert status == 200


# --- an archive does not choose where to sign in ----------------------------


@pytest.fixture
def ledger_for(tmp_path, monkeypatch):
    """Serve `/api/archive-ledger` for an archive that says it came from
    `base_url`, recording every authenticated lookup it makes."""
    import connections_export.gui.archives as gui_archives
    from connections_export.gui.routes import archives as archive_routes

    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    monkeypatch.chdir(tmp_path)  # no configuration from the developer's tree
    (tmp_path / "handed").mkdir()
    monkeypatch.setattr(archive_routes, "resolve_archive", lambda base, name: tmp_path / "handed")
    looked_up: list[tuple[str, str | None]] = []

    def fake_lookup(app, url, base, auth_mode):
        looked_up.append((url, base))
        return 404, b"", None

    monkeypatch.setattr(archive_routes, "_authed_lookup", fake_lookup)

    def serve(
        base_url: str,
        *,
        live_base_url: str | None = None,
        query: str = "",
        identify: str | None = None,
        choose: str | None = None,
    ):
        monkeypatch.setattr(
            gui_archives,
            "archive_ledger",
            lambda target: {
                "base_url": base_url,
                "rows": [],
                "communities": [{"id": "11111111-2222-3333-4444-555555555555"}],
            },
        )
        app = make_app(demo_delay=0)
        app.state.live_base_url = live_base_url
        http = TestClient(app, base_url="http://127.0.0.1")
        if identify:
            assert http.post("/api/identify", json={"url": identify}).json()["ok"]
        if choose:
            assert http.post("/api/choose-deployment", json={"url": choose}).json()["ok"]
        return http.get(f"/api/archive-ledger?name=handed{query}").json(), looked_up

    return serve


def test_an_archive_from_another_host_is_not_signed_in_to_automatically(ledger_for):
    """Opening the update screen looked up the archive's live state by
    authenticating against the address inside the archive -- so a handed
    archive chose where the user's sign-in went."""
    body, looked_up = ledger_for(EVIL, live_base_url=DEPLOYMENT)

    assert looked_up == []
    assert body["status"] == "ok", "the update half must stay usable"
    assert body["available"] == []
    assert "evil.example" in body["live_error"]
    # Named, so the screen can offer to choose it rather than dead-end.
    assert body["untrusted_host"] == EVIL


def test_an_archive_of_the_signed_in_deployment_is_still_looked_up(ledger_for):
    """The normal case -- an archive of the deployment you are working with."""
    body, looked_up = ledger_for(DEPLOYMENT, live_base_url=DEPLOYMENT)

    assert looked_up, "the extend half was not asked"
    assert all(base == DEPLOYMENT for _url, base in looked_up)
    assert body["live_error"] is None


def test_an_archive_of_the_configured_deployment_is_looked_up_before_any_crawl(
    ledger_for, tmp_path
):
    """No live session yet, but the configuration names this deployment."""
    (tmp_path / "connections-export.toml").write_text(
        f'base_url = "{DEPLOYMENT}"\n', encoding="utf-8"
    )

    _body, looked_up = ledger_for(DEPLOYMENT)

    assert looked_up


def test_a_query_parameter_cannot_vouch_for_the_archives_host(ledger_for):
    """A GET can be fired from inside a captured page the Reader shows --
    the archive's own content -- so no GET parameter may say "sign in
    there". Only a deployment the user named through the console counts."""
    body, looked_up = ledger_for(EVIL, live_base_url=DEPLOYMENT, query="&trust_host=1")

    assert looked_up == []
    assert "evil.example" in body["live_error"]


def test_an_archive_from_a_deployment_the_user_identified_is_looked_up(ledger_for):
    """Dropping a URL from that deployment on the setup screen (a POST) is
    the user choosing it."""
    _body, looked_up = ledger_for(EVIL, identify=f"{EVIL}/blogs/b/entry/e")

    assert looked_up
    assert all(base == EVIL for _url, base in looked_up)


def test_choosing_the_archives_host_through_the_console_lets_the_lookup_go_ahead(ledger_for):
    """The update screen's "use this deployment" button posts the archive's
    own address; after that the ledger looks it up like any chosen one."""
    body, looked_up = ledger_for(EVIL, choose=EVIL)

    assert looked_up
    assert body.get("untrusted_host") is None


# --- the live PDF only visits web pages -------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<h1>x</h1>",
        "javascript:alert(1)",
        "chrome://settings",
    ],
)
def test_the_live_pdf_browser_never_navigates_off_the_web(url):
    """The browser carries the session and can read local files; only an
    http(s) page is something to render from the deployment."""
    from connections_export.pdf.live import navigable

    assert not navigable(url)


def test_the_live_pdf_browser_still_visits_web_pages():
    from connections_export.pdf.live import navigable

    assert navigable(f"{DEPLOYMENT}/wikis/home/wiki/x/page/y")
    assert navigable("http://intranet.example/blogs/b/entry/e")


def test_the_live_pdf_route_refuses_a_non_web_url(monkeypatch):
    """Refused at the route, before any browser starts, with a reason."""
    import connections_export.pdf.browser as browser_module

    monkeypatch.setattr(browser_module, "CHROMIUM_AVAILABLE", True)
    rendered = []
    import connections_export.pdf.live as live_module

    monkeypatch.setattr(
        live_module, "render_live_pdf", lambda urls, **k: rendered.append(urls) or None
    )
    http = TestClient(make_app(), base_url="http://127.0.0.1")

    response = http.get("/api/live-pdf", params={"url": "file:///etc/passwd"})

    assert rendered == []
    assert response.status_code == 400
