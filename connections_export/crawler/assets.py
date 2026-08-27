"""Body-HTML asset extraction and the same-deployment host boundary.

`scan` is deliberately narrow: it extracts `<img src>` references,
the one asset shape both the requirements and the fake's body-HTML
fixture actually exercise. Malformed or empty HTML degrades to an
empty list rather than raising -- asset discovery is best-effort and
must never be a reason to fail the containing page.

`classify` resolves a (possibly relative) URL against `base_url` and
decides whether it belongs to the same deployment (fetch it) or is
external (record it, never fetch it -- the crawler is not an open web
spider).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal
from urllib.parse import urljoin, urlparse

import lxml.etree
import lxml.html

Classification = Literal["same", "external"]


# Person avatar / profile-photo image endpoints. People consented to their
# avatar appearing in the *source* system, not in a derived export, so these
# images are a hard exclusion: never fetched, never stored as a blob, and
# stripped from exported body HTML.
# Patterns are INFERRED — no real-system sample yet — and
# deliberately broad: over-excluding a non-avatar named "avatar" is acceptable;
# leaking a real person's avatar is not.
_AVATAR_URL_RE = re.compile(
    r"/profiles/photo\.do"  # classic Profiles photo service
    r"|/profiles/html/photoServlet"  # servlet variant
    r"|photo\.do\b"  # any photo.do endpoint
    r"|/profiles/[^?#]*/photo\b"  # .../profiles/<id>/photo
    r"|avatar"  # anything explicitly an avatar
    r"|userphoto|user_photo|profilephoto|profile_photo",
    re.IGNORECASE,
)


def is_avatar_url(url: str) -> bool:
    """True if `url` looks like a person's profile photo / avatar — which
    is never captured or exported. INFERRED patterns; the open questions."""
    return bool(url) and bool(_AVATAR_URL_RE.search(url))


def scan(body_html: bytes | str) -> list[str]:
    """Extract `<img src>` hrefs from a page body's HTML, in document
    order. Avatar/profile-photo images are excluded (never captured).
    Returns `[]` for empty or unparseable input."""
    if not body_html:
        return []
    payload = body_html.encode("utf-8") if isinstance(body_html, str) else bytes(body_html)
    if not payload.strip():
        return []
    try:
        tree = lxml.html.fromstring(payload)
    except lxml.etree.ParserError:
        return []
    return [src for src in tree.xpath("//img/@src") if src and not is_avatar_url(src)]


#: A link to a Files DOCUMENT, in the three shapes a real deployment produces
#::
#:
#: /files/{root}/anonymous/api/library/{lib}/document/{doc}/media/{name}
#: /files/app/file/{doc}
#: communityview?...#...&file={doc}
#:
#: The first carries both ids and resolves to bytes directly; the other two
#: carry a document id only. `{root}` is `basic` or `form` -- the body used
#: `form` where the collection feed's enclosure uses `basic`, so neither can
#: be hard-coded.
_FILE_MEDIA_RE = re.compile(r"/files/[^/]+/anonymous/api/library/[^/]+/document/[^/]+/media/", re.I)
_FILE_DETAIL_RE = re.compile(r"/files/app/file/[^/?#]+", re.I)
#: The widget's own link. Its document id sits in the URL FRAGMENT, which a
#: normaliser strips as a matter of routine -- and the failure would be
#: silent: the link stays in the body and the bytes never arrive.
_FILE_WIDGET_RE = re.compile(r"communityview[^\s\"']*#[^\s\"']*[?&]file=[^\s\"'&]+", re.I)


def is_file_media_link(href: str) -> bool:
    """Whether `href` names a document's BYTES.

    The one shape that can be fetched as-is. The other two name a viewer page
    and carry a document id; fetching those would archive HTML under the name
    of a file -- the same class of error as storing a login page as the
    document it was refused.
    """
    return bool(href) and bool(_FILE_MEDIA_RE.search(href))


def is_file_detail_link(href: str) -> bool:
    """Whether `href` names a document by id, without saying where its bytes
    are. Recognisable, and not yet resolvable: see `scan_file_links`."""
    if not href:
        return False
    return bool(_FILE_DETAIL_RE.search(href) or _FILE_WIDGET_RE.search(href))


def is_file_link(href: str) -> bool:
    """Whether `href` names a Files document rather than a page, in any of
    the three measured shapes."""
    return is_file_media_link(href) or is_file_detail_link(href)


#: The document id inside each measured shape.
_DOC_ID_IN_MEDIA = re.compile(r"/document/([^/?#]+)/media/", re.I)
_DOC_ID_IN_DETAIL = re.compile(r"/files/app/file/([^/?#]+)", re.I)
_DOC_ID_IN_WIDGET = re.compile(r"[?&]file=([^&\s\"']+)", re.I)


def document_id_from_link(href: str) -> str | None:
    """The document a Files link names, or `None` if it names no document.

    All three shapes carry the id; only the media one also says where the
    bytes are. Pulling the id out is what lets a link be matched against a
    document the run ALREADY captured -- resolving it without inventing a
    lookup endpoint no capture has demonstrated.
    """
    if not href:
        return None
    for pattern in (_DOC_ID_IN_MEDIA, _DOC_ID_IN_DETAIL):
        found = pattern.search(href)
        if found:
            return found.group(1)
    # The widget's id lives after the `#`, so match on the fragment only --
    # a `file=` in the query string of some other URL is not a document.
    _, _, fragment = href.partition("#")
    if fragment:
        found = _DOC_ID_IN_WIDGET.search("&" + fragment)
        if found:
            return found.group(1)
    return None


def scan_file_links(body_html: bytes | str) -> list[str]:
    """Every `<a href>` in a body that names a Files document, in order.

    An export has to stand on its own, and a link stands only while the
    deployment is reachable. A body that embeds an image keeps the image; a
    body that links a document has to keep the document too, and this is what
    makes it recognisable.

    Deliberately narrow. Anything not recognisably a Files document is left as
    it is today -- recorded, not fetched -- because following every
    in-deployment link is how a user-level export becomes a deployment-wide
    crawl by accident.
    """
    if not body_html:
        return []
    payload = body_html.encode("utf-8") if isinstance(body_html, str) else bytes(body_html)
    if not payload.strip():
        return []
    try:
        tree = lxml.html.fromstring(payload)
    except lxml.etree.ParserError:
        return []
    return [href for href in tree.xpath("//a/@href") if is_file_link(href)]


def scan_file_media_links(body_html: bytes | str) -> list[str]:
    """The links in a body that resolve to a document's BYTES.

    Only these can be handed to the asset path as they stand. A link naming a
    document by id points at a viewer page, and fetching that would archive
    HTML as though it were the file -- worse than not capturing it, because
    the archive would then claim to hold something it does not.
    """
    return [href for href in scan_file_links(body_html) if is_file_media_link(href)]


def strip_avatars(body_html: str) -> str:
    """Remove `<img>` whose src is a person avatar / profile photo from a
    body, so a derived export never reproduces it. Returns the cleaned
    HTML; unparseable or avatar-free input is returned unchanged."""
    if not body_html or "<img" not in body_html.lower():
        return body_html
    try:
        wrapper = lxml.html.fragment_fromstring(body_html, create_parent="div")
    except (lxml.etree.ParserError, lxml.etree.XMLSyntaxError):
        return body_html
    removed = False
    for img in wrapper.xpath(".//img[@src]"):
        if is_avatar_url(img.get("src", "")):
            parent = img.getparent()
            # keep the img's tail text — removing the element drops it otherwise
            if img.tail:
                prev = img.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + img.tail
                else:
                    parent.text = (parent.text or "") + img.tail
            parent.remove(img)
            removed = True
    if not removed:
        return body_html
    out = lxml.html.tostring(wrapper, encoding="unicode")
    # strip the synthetic <div>…</div> wrapper create_parent added
    return out[len("<div>") : -len("</div>")]


def resolve(url: str, *, base_url: str) -> str:
    """Resolve a possibly-relative asset URL against `base_url`."""
    return urljoin(base_url, url)


def classify(url: str, *, base_url: str, hcl_hosts: Iterable[str] = ()) -> Classification:
    """Classify `url` (possibly relative) as `same`-deployment or
    `external`, resolving against `base_url` first.

    `same` if the resolved host equals the base host or is named in
    `hcl_hosts` (covers Files on another subdomain); `external`
    otherwise, or if the resolved URL carries no host at all.
    """
    resolved = resolve(url, base_url=base_url)
    host = (urlparse(resolved).hostname or "").lower()
    if not host:
        return "external"
    base_host = (urlparse(base_url).hostname or "").lower()
    allowed = {base_host, *(h.lower() for h in hcl_hosts)}
    return "same" if host in allowed else "external"
