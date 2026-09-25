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
        # The Settings panel is the source of truth for the current browser
        # session. Falling back to the file/environment is needed on startup,
        # but ignoring this state makes discovery use SSPI after the user has
        # selected Basic Auth in the UI.
        saved = app.state.editable_settings or {}
        # The app state contains defaults as well as user choices. Only a
        # persisted setting is an explicit UI choice; otherwise config-file /
        # environment auth must win over the built-in SSPI default.
        from connections_export.gui.settings_store import load_settings  # noqa: PLC0415

        persisted = load_settings()
        auth_mode = persisted.get("auth_mode") or cfg.auth_mode
        auth_root = persisted.get("auth_root") or cfg.auth_root
        # A configured address is a fallback, not the answer: the address a
        # capture uses comes from the URL you drop.
        base = base or persisted.get("base_url") or saved.get("base_url") or cfg.base_url
    except Exception:  # noqa: BLE001 - unreadable configuration is not fatal to a lookup
        pass
    # The address in hand may have come from a captured page the Reader is
    # showing; it is only used if the user chose that deployment.
    require_trusted(app, base)
    return base, auth_mode, auth_root


class UntrustedDeployment(Exception):
    """A lookup was asked to sign in to a host the user never chose.

    Raised before any client is built; the app answers it with a 403 (see
    `untrusted_response`)."""

    def __init__(self, base_url: str) -> None:
        from urllib.parse import urlparse  # noqa: PLC0415

        self.base_url = base_url
        self.host = urlparse(base_url).hostname or base_url
        super().__init__(
            f"{self.host} is not a deployment you chose in this console, so nothing "
            "was sent there. Drop a URL from it on the setup screen (or configure "
            "it) to look it up."
        )


def untrusted_response(exc: UntrustedDeployment):
    """The JSON a refused lookup answers with."""
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    return JSONResponse(
        {"status": "untrusted_host", "host": exc.host, "detail": str(exc)}, status_code=403
    )


def _chosen_origins(app) -> set:
    """Origins the user named through the console's POSTs this session.

    Created on first use rather than in `make_app`, so every route module
    that shares these helpers sees the same set without depending on the
    order the app was assembled in."""
    origins = getattr(app.state, "chosen_origins", None)
    if origins is None:
        origins = set()
        app.state.chosen_origins = origins
    return origins


def trust_deployment(app, base_url: str | None) -> None:
    """Record `base_url` as a deployment the user chose.

    Called only from POST routes -- identifying a dropped URL, starting a
    run. A captured page shown in the Reader can make the console issue
    GETs (an `<img>`, a CSS `url()`, a clicked link) but not a POST: the
    frame runs no script and submits no form. So the set can only grow by
    the user's own hand."""
    from connections_export.http.auth import origin_of  # noqa: PLC0415

    if not base_url or (base_url or "").rstrip("/") == DEMO_SAMPLE_BASE_URL:
        return
    origin = origin_of(base_url)
    if origin[1] and origin[0] in ("http", "https"):
        _chosen_origins(app).add(origin)


def is_trusted_deployment(app, base_url: str | None) -> bool:
    """Whether the user chose `base_url` as a deployment to sign in to: the
    configured one, the saved setting, the live session's, or one named
    through the console's POSTs (`trust_deployment`).

    A host that arrives in a GET's query or in an archive is data, not a
    choice -- a captured page or a handed archive can put any host there --
    so on its own it never earns the user's credentials."""
    from connections_export.config import load_config  # noqa: PLC0415
    from connections_export.http.auth import origin_of, same_origin  # noqa: PLC0415

    if not base_url:
        return False
    if origin_of(base_url) in _chosen_origins(app):
        return True
    known = [app.state.live_base_url]
    settings = getattr(app.state, "editable_settings", None) or {}
    known.append(settings.get("base_url"))
    try:
        known.append(load_config({}).base_url)
    except Exception:  # noqa: BLE001 - unreadable configuration names no deployment
        pass
    return any(same_origin(base_url, k) for k in known if k)


def require_trusted(app, base_url: str | None) -> None:
    """Raise `UntrustedDeployment` unless `base_url` is empty, the demo's
    placeholder, or a deployment the user chose."""
    if not base_url or base_url.rstrip("/") == DEMO_SAMPLE_BASE_URL:
        return
    if not is_trusted_deployment(app, base_url):
        raise UntrustedDeployment(base_url)


def _configured_hcl_hosts() -> list[str]:
    """The configured `hcl_hosts` -- the deployment's other hosts, among
    them a sign-in server on another domain. Empty when unreadable."""
    from connections_export.config import load_config  # noqa: PLC0415

    try:
        return list(load_config({}).hcl_hosts)
    except Exception:  # noqa: BLE001 - unreadable configuration names no hosts
        return []


def bound_live_cookies(app) -> list[dict]:
    """The live session's cookies, each with the domain it belongs to.

    A cookie recorded without one -- httpx reports a cookie set without a
    domain as `""` -- belongs to the deployment the session was started
    against. With no such deployment it is dropped: sent without a domain,
    httpx attaches it to every host."""
    from urllib.parse import urlparse  # noqa: PLC0415

    fallback = urlparse(app.state.live_base_url or "").hostname or ""
    bound = []
    for cookie in app.state.live_cookies or []:
        domain = cookie.get("domain") or fallback
        if cookie.get("name") and domain:
            bound.append({**cookie, "domain": domain, "path": cookie.get("path") or "/"})
    return bound


def live_cookie_jar(app):
    """An `httpx.Cookies` of the live session's cookies, domain-bound (see
    `bound_live_cookies`), so a request to any other host carries none."""
    import httpx  # noqa: PLC0415

    jar = httpx.Cookies()
    for cookie in bound_live_cookies(app):
        jar.set(
            cookie["name"], cookie.get("value", ""), domain=cookie["domain"], path=cookie["path"]
        )
    return jar


#: How long one lookup request may take. A lookup is a handful of reads,
#: not a crawl, and a slow deployment answering a community's feeds in 40
#: seconds is answering; the crawl's 30 seconds turned that into "the
#: deployment did not answer", which points somewhere else entirely.
LOOKUP_TIMEOUT_SECONDS = 120.0


class LookupSession:
    """One authenticated session for a run of read-only lookups.

    Built ONCE and reused: the alternative -- and what every lookup did --
    is a fresh client per request, which for integrated authentication is a
    fresh handshake per request, two or three round trips before the one
    that was asked for. Community discovery makes ten to fifteen requests,
    so it paid for ten to fifteen handshakes, and on a slow deployment that
    alone ran past the console's patience. It looked like a community with
    nothing in it.

    Unpaced, deliberately: `min_interval` exists so a crawl walking thousands
    of feeds is a good citizen, and a lookup is not that.
    """

    def __init__(
        self,
        app,
        base: str | None,
        auth_mode: str | None,
        *,
        timeout: float,
        transport=None,
    ):
        import httpx  # noqa: PLC0415

        self._client = None
        self._jar = None
        if app.state.live_cookies:
            # Domain-bound, so following a redirect or being asked about
            # another host sends that host none of the session. `transport`
            # is the test seam.
            self._jar = httpx.Client(
                cookies=live_cookie_jar(app),
                follow_redirects=True,
                timeout=timeout,
                transport=transport,
            )
            return
        from connections_export.cli import _build_default_client  # noqa: PLC0415

        self._sign_in_hosts = _configured_hcl_hosts()
        self._client = _build_default_client(
            Config(
                base_url=base or "",
                hcl_hosts=self._sign_in_hosts,
                **({"auth_mode": auth_mode} if auth_mode else {}),
            ),
            os.environ,
            min_interval=0.0,
            timeout=timeout,
        )

    def get(self, url: str):
        """`(status, content, error)` for a read-only GET, redirects followed
        while they stay on `url`'s origin or go to its sign-in hosts (the
        same rule as the handshake's, `http.auth.is_sign_in_host`).

        The client carries the user's credentials; a redirect elsewhere is
        answered as the redirect it is rather than followed there with them."""
        from urllib.parse import urljoin  # noqa: PLC0415

        from connections_export.http.auth import is_sign_in_host  # noqa: PLC0415
        from connections_export.http.results import Fetched  # noqa: PLC0415

        if self._jar is not None:
            try:
                r = self._jar.get(url)
                return r.status_code, r.content, None
            except Exception as exc:  # noqa: BLE001
                return None, b"", str(exc)
        try:
            res = self._client.get(url)
            for _ in range(3):
                if not isinstance(res, Fetched) or not 300 <= res.status < 400:
                    break
                location = res.headers.get("location")
                if not location:
                    break
                target = urljoin(res.url or url, location)
                if not is_sign_in_host(target, url, self._sign_in_hosts):
                    break
                res = self._client.get(target)
        except Exception as exc:  # noqa: BLE001
            return None, b"", str(exc)
        if isinstance(res, Fetched):
            return res.status, res.content, None
        return None, b"", getattr(res, "error", "the lookup request failed")

    def redirect_location(self, url: str) -> str | None:
        """Where `url` redirects to, without following it. None when it
        does not answer -- which is an answer of "I could not see", never a
        reason for the caller's request to fail."""
        try:
            if self._jar is not None:
                r = self._jar.get(url, follow_redirects=False)
                return r.headers.get("location", "")
            return self._client.get(url).headers.get("location", "")
        except Exception:  # noqa: BLE001
            return None


def _lookup_session(
    app, base: str | None, auth_mode: str | None, *, timeout: float = LOOKUP_TIMEOUT_SECONDS
) -> LookupSession:
    """A `LookupSession`. A function rather than the class so a test can
    stand in for it where it is used.

    Refuses a deployment the user did not choose, whatever the caller
    checked: this is the one door every route's session comes through."""
    require_trusted(app, base)
    return LookupSession(app, base, auth_mode, timeout=timeout)


def _authed_lookup(app, url: str, base: str | None, auth_mode: str | None):
    """`(status, content, error)` for ONE read-only GET. Reuses the live
    session's cookies when present; otherwise authenticates ON DEMAND from
    `base`+`auth_mode` -- so resolving a user id / previewing search never
    requires running a full ingest first.

    One request, one session. A caller making several should hold a
    `_lookup_session` and ask it each time, or it pays the handshake each
    time -- see `LookupSession`.
    """
    try:
        session = _lookup_session(app, base, auth_mode, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return None, b"", str(exc)
    return session.get(url)
