"""Asset resolution: map a body-referenced or attachment asset to its
archived blob.

Deliberately reuses `connections_export.crawler.assets`' `scan`/`resolve`/
`classify` rather than reimplementing the same-deployment host
boundary: the crawler decided what counted as "same-deployment" (and
therefore what to fetch) using exactly that rule, so derive must judge
presence against the identical rule or the two would silently
disagree about what "same-deployment" means. Read-only reuse of a
public function, not a modification of crawler code.
"""

from __future__ import annotations

from collections.abc import Iterable

from connections_export.crawler import assets as crawler_assets
from connections_export.crawler.assets import (
    strip_avatars,
)  # re-export: privacy filter lives with scan
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.model import ResolvedAsset

__all__ = ["resolve_asset", "resolve_body_assets", "strip_avatars"]


def resolve_asset(
    href: str, *, join_base: str, boundary_base: str, hcl_hosts: Iterable[str], index: ArchiveIndex
) -> ResolvedAsset:
    """Resolve one asset href to an archived blob.

    `join_base` is what a *relative* href resolves against (a page's
    own body URL, for body assets); `boundary_base` is the deployment
    root used for the same-deployment/external boundary judgement --
    the same split the crawler itself makes (`crawl.py`'s
    `handle_asset` resolves against the page body URL but classifies
    against `config.base_url`).
    """
    resolved = crawler_assets.resolve(href, base_url=join_base)
    scope = crawler_assets.classify(resolved, base_url=boundary_base, hcl_hosts=hcl_hosts)
    response = index.get(resolved)
    return ResolvedAsset(
        original_href=href,
        resolved_url=resolved,
        blob_hash=response.body_hash if response is not None else None,
        present=response is not None,
        scope=scope,
    )


def resolve_body_assets(
    body_html: str, *, page_url: str, base_url: str, hcl_hosts: Iterable[str], index: ArchiveIndex
) -> list[ResolvedAsset]:
    """Resolve every `<img src>` asset referenced from a page body.

    A same-deployment asset with a blob is `present=True`; without one,
    `present=False` and surfaced rather than dropped; anything off the
    deployment gets `scope="external"`.
    """
    return [
        resolve_asset(
            href, join_base=page_url, boundary_base=base_url, hcl_hosts=hcl_hosts, index=index
        )
        for href in crawler_assets.scan(body_html)
    ]
