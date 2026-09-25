"""Authenticated lookups only reach a deployment the user chose.

The Fetch-Metadata guard stops other sites. It cannot stop the console's own
pages: the Reader shows captured bodies in a same-origin frame, where a
relative `<img src="/api/resolve-user?base_url=https://evil.example">` -- or
a CSS `url(...)`, or a link the user clicks -- is a same-origin GET. A
captured page is somebody else's content, and it was able to make the
console sign in to a host it named.

So the host itself must be one the user picked: the configured deployment,
the one a run was started against, or one identified by dropping a URL on
the setup screen -- all POSTs or configuration, which a sandboxed frame with
no scripts and no forms cannot produce. A lookup for any other host is
refused before a client is built.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui.routes import _lookup as lookup_support
from connections_export.gui.routes import lookup as lookup_routes

DEPLOYMENT = "https://connections.example.corp"
EVIL = "https://evil.example"
UUID = "9f3a1b2c-0000-4d5e-8f01-abcdef123456"

#: Every GET that authenticates against a host named in its query.
LOOKUPS = [
    ("/api/resolve-user", {"userid": "x"}),
    ("/api/search-preview", {"userid": "x"}),
    ("/api/current-user", {}),
    ("/api/community-of", {"app": "blog", "container": "b"}),
    ("/api/subcommunities", {"community_uuid": UUID}),
    ("/api/community-components", {"community_uuid": UUID}),
]


@pytest.fixture
def console(tmp_path, monkeypatch):
    """A console with nothing configured, and every way a lookup could open
    a client or a session recorded rather than performed."""
    import connections_export.cli as cli_module

    monkeypatch.chdir(tmp_path)
    built: list[str] = []

    def build(config, env, **overrides):
        built.append(config.base_url)
        raise RuntimeError("recorded, not built")

    def open_session(app, base, auth_mode, **k):
        built.append(base)
        raise RuntimeError("recorded, not opened")

    monkeypatch.setattr(cli_module, "_build_default_client", build)
    monkeypatch.setattr(lookup_routes, "_lookup_session", open_session)
    monkeypatch.setattr(lookup_support, "_lookup_session", open_session)
    app = make_app(demo_delay=0)
    return TestClient(app, base_url="http://127.0.0.1"), built


@pytest.mark.parametrize(("path", "params"), LOOKUPS)
def test_a_lookup_for_a_host_never_chosen_is_refused_before_any_client(console, path, params):
    """The attack: a captured page's `<img>` names the host. Refused with a
    reason, and nothing -- no client, no session, no handshake -- is built."""
    http, built = console

    response = http.get(path, params={**params, "base_url": EVIL})

    assert response.status_code == 403
    assert response.json()["status"] == "untrusted_host"
    assert "evil.example" in response.json()["detail"]
    assert built == []


@pytest.mark.parametrize(("path", "params"), LOOKUPS)
def test_after_identifying_a_dropped_url_its_lookups_go_ahead(console, path, params):
    """The console's normal flow: a URL is dropped (POST /api/identify), and
    the lookups that follow for that deployment are made."""
    http, built = console
    assert http.post("/api/identify", json={"url": f"{DEPLOYMENT}/blogs/b/entry/e"}).json()["ok"]

    response = http.get(path, params={**params, "base_url": DEPLOYMENT})

    assert response.status_code != 403
    assert built and set(built) == {DEPLOYMENT}


def test_a_deployment_typed_into_the_setup_screen_can_be_chosen_by_post(console):
    """The base-URL field can be typed into rather than filled by a drop;
    `POST /api/choose-deployment` is how the console says so."""
    http, built = console

    assert http.post("/api/choose-deployment", json={"url": DEPLOYMENT}).json()["ok"]
    http.get("/api/resolve-user", params={"userid": "x", "base_url": DEPLOYMENT})

    assert built == [DEPLOYMENT]


def test_choosing_a_non_web_address_is_refused(console):
    http, _built = console

    response = http.post("/api/choose-deployment", json={"url": "file:///etc"})

    assert response.status_code == 400


def test_identifying_one_deployment_does_not_vouch_for_another(console):
    """The set holds origins: another host -- or another port -- is not it."""
    http, built = console
    http.post("/api/identify", json={"url": f"{DEPLOYMENT}/blogs/b/entry/e"})

    other_port = http.get(
        "/api/resolve-user", params={"userid": "x", "base_url": f"{DEPLOYMENT}:8443"}
    )

    assert other_port.status_code == 403
    assert built == []


def test_the_stream_refuses_an_unchosen_host_too(console):
    http, built = console

    response = http.get(
        "/api/community-components/stream", params={"community_uuid": UUID, "base_url": EVIL}
    )

    assert response.status_code == 403
    assert built == []


def test_the_feed_count_for_an_unchosen_host_is_refused(console):
    http, built = console

    response = http.get("/api/feed-info", params={"url": f"{EVIL}/blogs/b/entry/e"})

    assert response.status_code == 403
    assert response.json()["status"] == "untrusted_host"
    assert built == []


def test_the_live_pdf_for_an_unchosen_host_is_refused(console, monkeypatch):
    """The browser would carry the session to the page and the dropped URL
    is fetched for its entries -- both need the host to be the user's."""
    import connections_export.pdf.browser as browser_module
    import connections_export.pdf.live as live_module

    monkeypatch.setattr(browser_module, "CHROMIUM_AVAILABLE", True)
    rendered = []
    monkeypatch.setattr(live_module, "render_live_pdf", lambda urls, **k: rendered.append(urls))
    http, _built = console

    response = http.get("/api/live-pdf", params={"url": f"{EVIL}/wikis/home/wiki/w/page/p"})

    assert response.status_code == 403
    assert rendered == []


def test_the_configured_deployment_needs_no_identifying(console, tmp_path):
    """Configuration is the user's choice, made before the console started."""
    (tmp_path / "connections-export.toml").write_text(
        f'base_url = "{DEPLOYMENT}"\n', encoding="utf-8"
    )
    http, built = console

    http.get("/api/resolve-user", params={"userid": "x", "base_url": DEPLOYMENT})

    assert built == [DEPLOYMENT]


def test_the_live_sessions_deployment_needs_no_identifying(console):
    """A run was started against it -- itself a POST the user made."""
    http, built = console
    http.app.state.live_base_url = DEPLOYMENT

    http.get("/api/resolve-user", params={"userid": "x", "base_url": DEPLOYMENT})

    assert built == [DEPLOYMENT]


def test_starting_a_run_marks_its_deployment_as_chosen(console, monkeypatch):
    """`/api/start` names the deployment in its body, which only the
    console's own script can send."""
    from connections_export.gui.routes import run as run_routes

    http, _built = console
    monkeypatch.setattr(run_routes.threading, "Thread", _NoThread)

    http.post("/api/start", json={"base_url": DEPLOYMENT, "app": "blog"})

    assert lookup_support.is_trusted_deployment(http.app, DEPLOYMENT)
    assert not lookup_support.is_trusted_deployment(http.app, EVIL)


class _NoThread:
    """Stands in for the run's thread: the test is about what `/api/start`
    records before it starts one, not the crawl."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, *a, **k):
        pass


def test_the_demo_keeps_answering(console):
    """The demo's placeholder host is never a real deployment and never
    needed choosing; its lookups answer from its own data as before."""
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    http, built = console

    response = http.get("/api/current-user", params={"base_url": DEMO_SAMPLE_BASE_URL})

    assert response.status_code == 200
    assert built == []
