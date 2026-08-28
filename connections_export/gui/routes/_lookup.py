"""Read-only authenticated lookups against a live deployment.

Shared by three route groups -- `lookup`, `archives` and `run` -- so these take
`app` explicitly rather than closing over it. Closing over it would tie every
caller to one enclosing scope, which is what sharing them has to avoid.
"""

from __future__ import annotations

import os

from connections_export.config import Config
from connections_export.gui.demo import (
    DEMO_SAMPLE_BASE_URL,
)


def _lookup_base_auth(app, base_url_param: str | None):
    """(base_url, auth_mode) for a read-only lookup WITHOUT a prior ingest:
    an explicitly-passed base (the setup screen's field), else the live
    session's base, else the configured deployment (hcl-export.toml/env).

    `DEMO_SAMPLE_BASE_URL` never counts as a deployment. It is the
    placeholder the demo chips carry, and identifying one puts it in the
    setup screen's base-URL field -- from where every read-only lookup
    picked it up and tried to authenticate against a host that does not
    exist. Pressing "Only me" then reported an SSPI package error naming
    the placeholder host -- a true statement about a request that should
    never have been attempted. `/api/start` already treats this host as
    the demo; the read-only lookups now agree.
    """
    base, auth_mode, _auth_root = _lookup_deployment(app, base_url_param)
    return base, auth_mode


def _lookup_deployment(app, base_url_param: str | None):
    """`(base, auth_mode, auth_root)` for a read-only lookup.

    The ADDRESS comes from the URL in hand -- what was dropped on the setup
    screen, or the live session's -- because that is what a person is asking
    about. HOW to reach it is configuration: the authentication mode, and the
    path the deployment serves its wiki API under.

    Both are read whatever the address turns out to be. Reading them only
    when no address was given means never reading them, since the console
    sends what the dropped URL resolved to -- so a deployment configured for
    `kerberos` was asked with the default, and one serving its wiki API under
    another root was asked at `/wikis/basic/...`. The crawl reads both from
    the configuration; a lookup that reaches the same deployment another way
    is a second answer to a question with one right one.
    """
    from connections_export.config import load_config  # noqa: PLC0415

    base = (base_url_param or "").strip() or app.state.live_base_url
    if base and base.rstrip("/") == DEMO_SAMPLE_BASE_URL:
        return None, None, None

    auth_mode: str | None = None
    auth_root: str | None = None
    try:
        cfg = load_config({})
        auth_mode = cfg.auth_mode
        auth_root = cfg.auth_root
        # A configured address is a fallback, not the answer: the address a
        # capture uses comes from the URL you drop.
        base = base or cfg.base_url
    except Exception:  # noqa: BLE001 - unreadable configuration is not fatal to a lookup
        pass
    return base, auth_mode, auth_root


def _authed_lookup(app, url: str, base: str | None, auth_mode: str | None):
    """`(status, content, error)` for a read-only GET. Reuses the live
    session's cookies when present; otherwise authenticates ON DEMAND from
    `base`+`auth_mode` -- so resolving a user id / previewing search never
    requires running a full ingest first."""
    from urllib.parse import urljoin  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    if app.state.live_cookies:
        jar = {c["name"]: c["value"] for c in app.state.live_cookies}
        try:
            r = httpx.get(url, cookies=jar, follow_redirects=True, timeout=30)
            return r.status_code, r.content, None
        except Exception as exc:  # noqa: BLE001
            return None, b"", str(exc)
    from connections_export.cli import _build_default_client  # noqa: PLC0415
    from connections_export.http.results import Fetched  # noqa: PLC0415

    try:
        client = _build_default_client(
            Config(base_url=base or "", **({"auth_mode": auth_mode} if auth_mode else {})),
            os.environ,
        )
        res = client.get(url)
        for _ in range(3):
            if not isinstance(res, Fetched) or not 300 <= res.status < 400:
                break
            location = res.headers.get("location")
            if not location:
                break
            res = client.get(urljoin(url, location))
    except Exception as exc:  # noqa: BLE001
        return None, b"", str(exc)
    if isinstance(res, Fetched):
        return res.status, res.content, None
    return None, b"", getattr(res, "error", "the lookup request failed")
