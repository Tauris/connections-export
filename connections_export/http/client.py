"""HttpClient: TLS-enforcing, retrying, self-throttling HTTP fetch.

Everything that would make tests slow or non-deterministic — the
network transport, the wait between retries/throttle, and the clock
used to measure elapsed time — is injected. Production code gets real
defaults (a real `httpx` transport, `time.sleep`, `time.monotonic`);
tests supply a `httpx.MockTransport` and recording fakes so retry and
throttle behavior is asserted with zero real delay.
"""

import threading
import time as _time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from connections_export.http.proxy import ProxyDecision, httpx_client_kwargs
from connections_export.http.results import FailureKind, Fetched, FetchFailure, FetchResult


@dataclass
class RetryPolicy:
    """Governs which failures are retried, and how long to wait between
    attempts. See the design for the backoff formula."""

    max_attempts: int = 4
    base_delay: float = 0.5  # seconds
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a `Retry-After` header value: either an integer number of
    seconds, or an HTTP-date. Returns `None` if absent or unparsable."""
    if value is None:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    now = _time.time()
    delta = target.timestamp() - now
    return max(delta, 0.0)


class HttpClient:
    """A resilient HTTP client: TLS-only, retrying, self-throttling,
    session-cookie-carrying. Fetches and returns results; never writes
    to any archive (see `connections_export.http.results`)."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        min_interval: float = 0.0,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        timeout: float | None = 30.0,
        stop_event: threading.Event | None = None,
        retry_callback: Callable[[float], None] | None = None,
        proxy: ProxyDecision | None = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._min_interval = min_interval
        self._sleep = sleep or _time.sleep
        self._clock = clock or _time.monotonic
        self._last_request_start: float | None = None
        self._stop_event = stop_event
        self._retry_callback = retry_callback
        # `proxy` is a decision already made (`http.proxy.resolve_proxy`),
        # not a URL: it carries where it came from, and "direct" in it means
        # direct even with HTTPS_PROXY in the environment. None keeps httpx's
        # own behaviour, which reads the environment and nothing else.
        #: The proxy decision this client was built with, so the
        #: `requests`-based auth handshake can obey the same one.
        self.proxy_decision = proxy
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            **(httpx_client_kwargs(proxy) if proxy is not None and transport is None else {}),
        )

    def set_stop_event(self, stop_event: threading.Event | None) -> None:
        """Attach a cooperative stop signal for long-running crawls."""
        self._stop_event = stop_event

    def set_retry_callback(self, callback: Callable[[float], None] | None) -> None:
        """Attach an observer for actual retry backoff waits."""
        self._retry_callback = callback

    @property
    def cookies(self) -> httpx.Cookies:
        """The session cookie jar, persisted across requests."""
        return self._client.cookies

    @property
    def headers(self) -> httpx.Headers:
        """Default headers sent with every request, to every host -- so never
        a credential; those go through `auth`, bound to one origin."""
        return self._client.headers

    @property
    def auth(self) -> httpx.Auth | None:
        """The httpx auth flow applied to each request (see `http.auth`)."""
        return self._client.auth

    @auth.setter
    def auth(self, value: httpx.Auth | None) -> None:
        self._client.auth = value

    @property
    def min_interval(self) -> float:
        """The pacing this client is running at.

        Readable because "is my delay actually being applied?" should be
        answerable without reading the source.
        """
        return self._min_interval

    @min_interval.setter
    def min_interval(self, value: float) -> None:
        """Change the pacing of a crawl already under way.

        The reason to slow a run down -- the deployment is struggling under it
        -- only becomes visible once it is running, and the alternative is to
        stop, change the number and start again. Raised to the same floor
        `Config` applies, so this cannot be used to go faster than that.
        """
        from connections_export.config import Config  # noqa: PLC0415 - avoids a cycle

        self._min_interval = max(float(value), Config.MIN_REQUEST_INTERVAL)

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResult:
        """Issue a GET request, retrying transient failures per the
        configured `RetryPolicy`. Raises `ValueError` for a non-https
        URL, before any network activity."""
        return self._request("GET", url, headers=headers)

    def _request(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> FetchResult:
        if not url.lower().startswith("https://"):
            raise ValueError(f"refusing to fetch a non-HTTPS URL: {url}")

        self._throttle()

        policy = self._retry_policy
        attempt = 1
        while True:
            if self._stop_event is not None and self._stop_event.is_set():
                return FetchFailure(
                    url=url, method=method, kind=FailureKind.transport, error="stopped"
                )
            response: httpx.Response | None
            kind: FailureKind | None
            error: str | None
            try:
                response = self._client.request(method, url, headers=headers)
                kind = None
                error = None
            except httpx.TimeoutException as exc:
                response = None
                kind = FailureKind.timeout
                error = str(exc) or exc.__class__.__name__
            except httpx.TransportError as exc:
                response = None
                kind = FailureKind.transport
                error = str(exc) or exc.__class__.__name__

            if response is not None and response.status_code == 407:
                # The proxy, not the deployment, refused -- and it wants
                # credentials this tool cannot supply (NTLM or Negotiate to a
                # proxy is not something httpx does). Named as such, with the
                # fix that is also the right one organisationally.
                return FetchFailure(
                    url=url,
                    method=method,
                    kind=FailureKind.http_error,
                    error=(
                        "407 from the proxy: it requires authentication this tool cannot "
                        "supply. Ask for the deployment's host to be added to the proxy "
                        "bypass list, or run with --proxy direct if it is reachable without one."
                    ),
                )
            if response is not None:
                exhausted = attempt >= policy.max_attempts
                if response.status_code not in policy.retry_statuses or exhausted:
                    return Fetched(
                        url=str(response.url),
                        method=method,
                        status=response.status_code,
                        headers=dict(response.headers),
                        content=response.content,
                    )
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            else:
                if attempt >= policy.max_attempts:
                    return FetchFailure(url=url, method=method, kind=kind, error=error)
                retry_after = None

            self._wait_before_retry(attempt, retry_after)
            if self._stop_event is not None and self._stop_event.is_set():
                return FetchFailure(
                    url=url, method=method, kind=FailureKind.transport, error="stopped"
                )
            attempt += 1

    def _wait_before_retry(self, attempt: int, retry_after: float | None) -> None:
        policy = self._retry_policy
        if retry_after is not None:
            delay = retry_after
        else:
            exponential = policy.base_delay * policy.backoff_factor ** (attempt - 1)
            delay = min(policy.max_delay, exponential)
        if delay > 0:
            if self._retry_callback is not None:
                self._retry_callback(delay)
            if self._stop_event is not None:
                self._stop_event.wait(delay)
            else:
                self._sleep(delay)

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            self._last_request_start = self._clock()
            return
        now = self._clock()
        if self._last_request_start is not None:
            target = self._last_request_start + self._min_interval
            wait = target - now
            if wait > 0:
                if self._stop_event is not None:
                    self._stop_event.wait(wait)
                else:
                    self._sleep(wait)
                now = target
        self._last_request_start = now
