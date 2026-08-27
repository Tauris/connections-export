"""Body link classification: three-way, not binary.

**Normalize first, classify second.** A relative href is resolved
against the page's own body URL (`urljoin`); an already-absolute href
is kept as-is. Both end up as one comparable `resolved_url` -- the
load-bearing point being that an absolute href must NOT be assumed
external just because it is absolute: an absolute URL whose host is
the deployment itself still points back at the HCL system.

Classification, in order:

1. **Host scope.** If `resolved_url`'s host is the base host or a
   configured `hcl_hosts` entry, the link points at the HCL
   deployment. Otherwise it is `external` (the public web), kept
   verbatim, `resolved_url` still populated for reference.
2. **In-export vs. same-deployment.** For a deployment link, try to
   resolve it to a page this archive actually derived -- match against
   a known page's entry URL, uuid, or label, or (# HEURISTIC, Q15)
   extract a `/wiki/{wiki_label}/page/{page_label}/...` path segment
   and look that up. Found -> `scope="in_export"` + `target_page_id`.
   Not found (another wiki never exported, a Files document, another
   app entirely) -> `scope="hcl_deployment"`, kept clickable to its
   original HCL URL but not an in-app jump.
3. A pure fragment (`#anchor`) resolves to the current page ->
   `in_export`, `target_page_id` = the page it appears on.

# HEURISTIC MATCHER: the real HTML an HCL Wikis deployment emits
# for an internal link is unconfirmed -- absolute URL, relative path,
# `td:label`, or bare page uuid are all plausible, and the reference
# doesn't say which (docs/reference/the open questions, Q15). The
# host-scope decision (deployment vs. external) is robust regardless
# of path form; the page-resolution step is the part expected to be
# refined once real hrefs are seen on first contact with a live
# deployment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

import lxml.etree
import lxml.html

from connections_export.derive.model import LinkRef

_PAGE_PATH_RE = re.compile(r"/wiki/(?P<wiki_label>[^/]+)/page/(?P<page_label>[^/]+)(?:/|$)")


def scan_links(body_html: bytes | str) -> list[str]:
    """Extract `<a href>` hrefs from a page body's HTML, in document
    order. Returns `[]` for empty or unparseable input (link discovery
    is best-effort and must never fail the containing page -- mirrors
    `crawler.assets.scan`'s own leniency)."""
    if not body_html:
        return []
    payload = body_html.encode("utf-8") if isinstance(body_html, str) else bytes(body_html)
    if not payload.strip():
        return []
    try:
        tree = lxml.html.fromstring(payload)
    except lxml.etree.ParserError:
        return []
    return [href for href in tree.xpath("//a/@href") if href]


def _path_key(url: str) -> str | None:
    """`.../wiki/{wiki_label}/page/{page_label}/...` -> a lookup key
    good for any href suffix under that page (`/entry`, `/media`,
    `/feed`,...), not just the exact entry URL -- the path-based half
    of the Q15 heuristic."""
    match = _PAGE_PATH_RE.search(urlparse(url).path)
    if not match:
        return None
    return f"path:{match.group('wiki_label')}/{match.group('page_label')}"


def build_page_lookup(entries: list[tuple[str, str, str | None]]) -> dict[str, str]:
    """Build the lookup `resolve_link` matches a resolved deployment
    URL (or bare label/uuid) against, from `(page_id, entry_url,
    label)` triples -- one per page known so far (typically every node
    in a wiki's navigation tree)."""
    lookup: dict[str, str] = {}
    for page_id, entry_url, label in entries:
        lookup[entry_url] = page_id
        lookup[page_id] = page_id
        if label:
            lookup[label] = page_id
        path_key = _path_key(entry_url)
        if path_key:
            lookup[path_key] = page_id
    return lookup


def _is_deployment_host(url: str, *, base_url: str, hcl_hosts: Iterable[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    base_host = (urlparse(base_url).hostname or "").lower()
    allowed = {base_host, *(h.lower() for h in hcl_hosts)}
    return host in allowed


def resolve_link(
    href: str,
    *,
    page_url: str,
    self_page_id: str,
    base_url: str,
    hcl_hosts: Iterable[str],
    page_lookup: dict[str, str],
    file_lookup: dict[str, str] | None = None,
) -> LinkRef:
    """Classify one body href per the module docstring's three-way
    algorithm. `original_href` is always kept exactly as it appeared
    in the body (External links are kept verbatim)."""
    if href.startswith("#"):
        return LinkRef(
            original_href=href,
            resolved_url=urljoin(page_url, href),
            scope="in_export",
            target_page_id=self_page_id,
        )

    resolved_url = urljoin(page_url, href)

    if not _is_deployment_host(resolved_url, base_url=base_url, hcl_hosts=hcl_hosts):
        return LinkRef(original_href=href, resolved_url=resolved_url, scope="external")

    # A Files link resolves against the documents this run captured. No
    # lookup endpoint is involved: the archive either holds the document or it
    # does not, and if it does the link is answerable from the archive alone,
    # which is the entire point of capturing it.
    if file_lookup:
        from connections_export.crawler.assets import document_id_from_link  # noqa: PLC0415

        document_id = document_id_from_link(href) or document_id_from_link(resolved_url)
        if document_id:
            captured = file_lookup.get(document_id)
            if captured is not None:
                return LinkRef(
                    original_href=href,
                    resolved_url=resolved_url,
                    scope="in_export",
                    target_file_id=captured,
                )

    target_page_id = (
        page_lookup.get(resolved_url)
        or page_lookup.get(href)
        or page_lookup.get(_path_key(resolved_url) or "")
    )
    if target_page_id is not None:
        return LinkRef(
            original_href=href,
            resolved_url=resolved_url,
            scope="in_export",
            target_page_id=target_page_id,
        )
    return LinkRef(original_href=href, resolved_url=resolved_url, scope="hcl_deployment")


def resolve_body_links(
    body_html: str,
    *,
    page_url: str,
    self_page_id: str,
    base_url: str,
    hcl_hosts: Iterable[str],
    page_lookup: dict[str, str],
) -> list[LinkRef]:
    """Resolve every `<a href>` in a page body."""
    return [
        resolve_link(
            href,
            page_url=page_url,
            self_page_id=self_page_id,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
            page_lookup=page_lookup,
        )
        for href in scan_links(body_html)
    ]
