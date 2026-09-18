"""A lookup reaches the deployment the way a crawl does.

The console's read-only lookups -- who am I, what does this community
hold -- authenticate on demand rather than requiring a crawl first. They
did it with a `Config` built from the base URL alone, so the configured
authentication mode and authentication root were replaced by defaults:
a deployment configured for `kerberos` was asked with SSPI, and one
serving its wiki API under a different root was asked at
`/wikis/basic/...` whatever `connections-export.toml` said.

The crawl reads both from the configuration (`crawler/engine.py` passes
`config.auth_root`). Anything that reaches the same deployment by
another route is a second answer to a question with one right one, and
"works when capturing, finds nothing when looking" is what it feels
like from outside.

Worse, the configuration was consulted ONLY when no base URL had been
given -- and the console always gives one, since it sends what the
dropped URL resolved to. So in the one case that matters it was never
read at all.
"""

from __future__ import annotations

import pytest

from connections_export.gui import make_app
from connections_export.gui.routes import _lookup as lookup_support


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """A deployment configured the way a real one is."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "connections-export.toml").write_text(
        'base_url = "https://connections.example.corp"\n'
        'auth_mode = "kerberos"\n'
        'auth_root = "form"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_the_configuration_is_read_even_when_a_base_url_is_given(configured):
    """The console always sends one, so a rule of "only when absent" means
    "never" in practice."""
    app = make_app()

    base, auth_mode, auth_root = lookup_support._lookup_deployment(
        app, "https://connections.example.corp"
    )

    assert base == "https://connections.example.corp"
    assert auth_mode == "kerberos", "the configured auth mode was not read"
    assert auth_root == "form", "the configured auth root was not read"


def test_an_explicit_base_url_still_wins_over_the_configured_one(configured):
    """Dropping a URL from a second deployment must read that one."""
    app = make_app()

    base, _mode, _root = lookup_support._lookup_deployment(app, "https://other.example.corp")

    assert base == "https://other.example.corp"


def test_the_demos_address_is_never_treated_as_a_deployment(configured):
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    app = make_app()

    base, _mode, _root = lookup_support._lookup_deployment(app, DEMO_SAMPLE_BASE_URL)

    assert base is None


def test_the_client_a_lookup_builds_carries_the_configured_auth_mode(configured, monkeypatch):
    """The mode was accepted as an argument and then dropped, so every
    lookup authenticated as whatever the default happened to be."""
    seen = {}

    def capture(config, env, **overrides):
        seen["auth_mode"] = config.auth_mode
        raise RuntimeError("stop here: the config is what is being tested")

    import connections_export.cli as cli_module

    monkeypatch.setattr(cli_module, "_build_default_client", capture)
    app = make_app()
    app.state.live_cookies = None

    lookup_support._authed_lookup(
        app, "https://connections.example.corp/x", "https://connections.example.corp", "kerberos"
    )

    assert seen["auth_mode"] == "kerberos"
