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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from connections_export.derive.model import Interchange, ResolvedAsset

#: Seconds to wait on one external image before giving up on it.
FETCH_TIMEOUT = 15.0
#: Largest external image embedded; anything bigger is left out.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_WORKERS = 8

Getter = Callable[[str], bytes | None]


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
    decision = resolve_proxy(url, explicit=explicit)
    # A fresh client per image: no cookie jar, no Authorization header.
    with (
        httpx.Client(
            timeout=FETCH_TIMEOUT, follow_redirects=True, **httpx_client_kwargs(decision)
        ) as client,
        client.stream("GET", url) as response,
    ):
        if response.status_code != 200:
            return None
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                return None
        return bytes(data)


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
