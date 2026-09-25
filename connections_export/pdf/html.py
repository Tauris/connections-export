"""`render_html(interchange, *, blob_bytes) -> str` (render_html (the core)): the tested core of
the pdf
capability. Pure -- no browser, no filesystem, no wall clock -- an
`Interchange` in, one self-contained print-ready HTML document out.

`blob_bytes: Callable[[str], bytes | None]` is the caller's hash ->
bytes lookup (an archive's `read_blob` or a package's `blobs/`), kept
as an injected callable so this module never touches the archive/
package filesystem details itself (mirrors `interchange/package.py`'s
`open_blob` boundary).

Order, sanitization, embedding, and link classification all follow the
model as assembled by `derive` -- this module never re-derives or
re-classifies anything (it trusts `DerivedPage.assets`/`.links` were
built by scanning the same `body_html`, exactly as `derive/assets.py`
and `derive/links.py` do), it only *renders* what's already there.
"""

from __future__ import annotations

import base64
import contextlib
import mimetypes
import re
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from html import escape as _escape
from urllib.parse import urlsplit

import lxml.etree
import lxml.html

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)

#: A fixed, non-wall-clock placeholder for the "generated" date shown
#: on the title page when the caller doesn't inject one (the design,
#: "Determinism: no wall clock; any 'generated' date is injected").
DEFAULT_GENERATED_AT = "(generation date not provided)"

#: The visible marker for an image whose bytes were never captured
#: (A not-captured image is a visible gap) -- shown, never
#: silently dropped.
NOT_CAPTURED_MARKER = "[image not captured]"
NOT_CAPTURED_ATTACHMENT_MARKER = "not captured"

BlobBytes = Callable[[str], bytes | None]

#: The default size at or below which, in both directions, an external image
#: is an icon in running text (a status tick, an emoji, a badge): marked with
#: a superscript E-number, since a frame and caption would break the line.
#: The user can change it (settings `pdf_small_image_px`); 0 frames them all.
SMALL_IMAGE_PX = 48


class _ExternalRegister:
    """One render's external images: numbered E1, E2, ... in document order,
    each URL once. `fetched` is None when the person chose to leave them out."""

    def __init__(
        self, fetched: Mapping[str, bytes | None] | None, small_px: int = SMALL_IMAGE_PX
    ) -> None:
        self.fetched = fetched
        self.small_px = small_px
        self.numbers: dict[str, int] = {}

    @property
    def included(self) -> bool:
        return self.fetched is not None

    def number(self, url: str) -> tuple[int, bool]:
        """The URL's number, and whether this is its first appearance."""
        if url in self.numbers:
            return self.numbers[url], False
        self.numbers[url] = len(self.numbers) + 1
        return self.numbers[url], True


#: Unset outside a render that opened a register: external images left out.
_EXTERNAL: ContextVar[_ExternalRegister | None] = ContextVar("external images", default=None)


@contextlib.contextmanager
def external_images_register(
    fetched: Mapping[str, bytes | None] | None, small_px: int | None = None
) -> Iterator[_ExternalRegister]:
    """Scope one render's external-image numbering. Every renderer that walks
    bodies (portable and browser fidelity) opens one around its walk."""
    register = _ExternalRegister(fetched, SMALL_IMAGE_PX if small_px is None else small_px)
    token = _EXTERNAL.set(register)
    try:
        yield register
    finally:
        _EXTERNAL.reset(token)


# --- content-type sniffing ---------------------------------------------
#
# `ResolvedAsset` carries no content-type (only `derive`'s pipeline saw
# the response header, and it isn't threaded through the interchange
# model) -- so the data-URI's MIME type is recovered here, from the
# blob's own magic bytes first (robust regardless of what the href
# looked like), falling back to a guess from the href's extension.

_MAGIC_SNIFFERS: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF-", "application/pdf"),
]


def _guess_content_type(href: str, data: bytes) -> str:
    for magic, content_type in _MAGIC_SNIFFERS:
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # SVG is text, so it has no binary magic and its href often lies (the demo
    # diagrams are SVG served at a `.png` URL). Sniff it the same way the
    # reader's blob endpoint does (model_source._sniff_content_type) -- without
    # this, an SVG data-URI is mislabelled image/png from the href and the
    # image renders blank in the exported PDF ("diagrams not visible"), even
    # though the reader shows it fine.
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head.lower()):
        return "image/svg+xml"
    guessed, _ = mimetypes.guess_type(href)
    return guessed or "application/octet-stream"


def _blob_digest(blob_hash: str) -> str | None:
    """`ResolvedAsset.blob_hash` carries the archive's `"sha256:<hex>"`
    form; `blob_bytes` is keyed by the bare hex digest. Anything that is not
    a digest is `None` -- the archive's one rule (`archive.blobs.valid_digest`),
    so a hash can never be a path here either."""
    from connections_export.archive.blobs import valid_digest  # noqa: PLC0415

    return valid_digest(blob_hash)


def _data_uri(href: str, data: bytes) -> str:
    content_type = _guess_content_type(href, data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


# --- body/comment sanitization ------------------------------------------


#
# Captured bodies are untrusted: anyone who could edit a page wrote them, and
# they are printed in a Chromium with JavaScript on (paged.js needs it) and the
# whole export in the same document. So the cleanup is an allowlist in effect:
# whatever makes a page LOOK like itself -- author `<style>`, classes, inline
# styles, tables, embedded images, links -- stays; whatever executes, embeds
# another document, redirects the render or loads from elsewhere goes. The
# renderers also block the network outright (`pdf/browser.py`), so this is the
# first of two layers, not the only one.

#: Removed with everything inside them. None has anything to show on paper;
#: each can run script, embed another document, redirect or restyle the whole
#: render from elsewhere (`<base>`, `<meta refresh>`, `<link>`), or submit.
_DROP_ELEMENTS = frozenset(
    {
        "script",
        "iframe",
        "frame",
        "frameset",
        "object",
        "applet",
        "meta",
        "base",
        "link",
        "input",
        "button",
        "textarea",
        "select",
        "noscript",
        "template",
        "portal",
    }
)

#: Removed, but whatever is inside kept. A form around ordinary paragraphs is
#: structure, and removing it would take the paragraphs with it. The SVG
#: animation elements can set an `href` to `javascript:` and animate nothing a
#: printed page would show. None of the others has content of its own --
#: `<embed>` is void, the SVG animation elements are usually written
#: self-closing -- but the HTML parser knows neither, so it nests whatever
#: FOLLOWS them inside them; removing them with their content would delete
#: the rest of the page.
_UNWRAP_ELEMENTS = frozenset(
    {"form", "embed", "set", "animate", "animatemotion", "animatetransform"}
)

#: Attributes whose only job is to load (or submit to) something elsewhere.
#: `srcset` matters most: Chromium prefers it to the rewritten data-URI `src`.
_DROP_ATTRIBUTES = frozenset(
    {
        "srcset",
        "imagesrcset",
        "poster",
        "background",
        "formaction",
        "action",
        "ping",
        "lowsrc",
        "dynsrc",
        "longdesc",
        "srcdoc",
        "codebase",
        "archive",
        "manifest",
    }
)

#: Link targets on these elements are navigation, shown and clicked on
#: paper; the same attribute anywhere else is a resource the browser loads.
_NAVIGATION_ELEMENTS = frozenset({"a", "area"})
_URL_ATTRIBUTES = frozenset({"href", "xlink:href", "src", "data", "cite"})

#: Schemes that run code when followed. `data:` is judged separately: an
#: image is fine, anything else (an HTML document) is not.
_SCRIPT_SCHEMES = frozenset({"javascript", "vbscript", "livescript"})

#: Browsers ignore tabs and newlines anywhere in a URL and leading control
#: characters, so `java\tscript:` is `javascript:` to them.
_URL_NOISE = re.compile(r"[\x00-\x20]")
_URL_SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):")


def _url_scheme(value: str) -> str | None:
    match = _URL_SCHEME.match(_URL_NOISE.sub("", value).lower())
    return match.group(1) if match else None


def _is_data_image(value: str) -> bool:
    return _URL_NOISE.sub("", value).lower().startswith("data:image/")


def _safe_navigation(value: str) -> bool:
    """A link may go anywhere a reader might follow it, except to script."""
    scheme = _url_scheme(value)
    if scheme in _SCRIPT_SCHEMES:
        return False
    return scheme != "data" or _is_data_image(value)


def _safe_resource(value: str) -> bool:
    """A resource may only come from inside the document: an embedded image or
    a same-document reference (`<use href="#icon">`). Anything else is a load
    from elsewhere at render time."""
    stripped = _URL_NOISE.sub("", value)
    return stripped.startswith("#") or _is_data_image(value)


# --- author CSS -------------------------------------------------------------
#
# Kept, because it is how the page looked -- but with every remote load taken
# out. CSS can load through `@import`, `url(...)`/`src(...)` and the bare
# strings of `image-set(...)`, and any of those names may be written with
# escapes (`\75 rl(` is `url(`). Escapes that stand for a plain letter are
# decoded first: a letter never needs escaping, so decoding changes nothing
# about what the CSS means, and it makes the disguised forms visible.

_CSS_LETTER_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|([g-zG-Z]))")
_CSS_HEX_ESCAPE_AT_END = re.compile(r"\\[0-9a-fA-F]{1,6}$")
_CSS_IMPORT = re.compile(
    r"@import\b(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\((?:[^)])*\)|[^;\"'(}])*;?",
    re.IGNORECASE,
)
_CSS_URL = re.compile(
    r"\b(?:url|src)\(\s*(?:\"((?:\\.|[^\"\\])*)\"|'((?:\\.|[^'\\])*)'|([^)\"'\s]*))\s*\)",
    re.IGNORECASE,
)
_CSS_IMAGE_SET = re.compile(r"(?:-webkit-)?image-set\(", re.IGNORECASE)
_CSS_STRING = re.compile(r"\"((?:\\.|[^\"\\])*)\"|'((?:\\.|[^'\\])*)'")


def _decode_letter_escapes(css: str) -> str:
    out: list[str] = []
    last = 0
    for match in _CSS_LETTER_ESCAPE.finditer(css):
        hex_digits, literal = match.groups()
        char = literal or chr(min(int(hex_digits, 16), 0x10FFFF))
        if not char.isascii() or not char.isalpha():
            continue  # a digit, a symbol, a non-ASCII character: needed, leave it
        out.append(css[last : match.start()])
        # A hex digit straight after an unterminated hex escape would join it
        # (`\31` + `a` reads as `\31a`), so terminate that escape first.
        if char in "abcdefABCDEF" and _CSS_HEX_ESCAPE_AT_END.search("".join(out)):
            out.append(" ")
        out.append(char)
        last = match.end()
    out.append(css[last:])
    return "".join(out)


def _strip_image_sets(css: str) -> str:
    """`image-set("a.png" 1x)` loads its bare strings like `url()`s. Any whose
    candidates are not all embedded images becomes `none`."""
    out: list[str] = []
    i = 0
    for match in _CSS_IMAGE_SET.finditer(css):
        if match.start() < i:
            continue
        depth, j = 1, match.end()
        while j < len(css) and depth:
            if css[j] == "(":
                depth += 1
            elif css[j] == ")":
                depth -= 1
            j += 1
        inner = css[match.end() : j - 1]
        strings = [a if a is not None else b for a, b in _CSS_STRING.findall(inner)]
        out.append(css[i : match.start()])
        out.append(css[match.start() : j] if all(_is_data_image(s) for s in strings) else "none")
        i = j
    out.append(css[i:])
    return "".join(out)


def _sanitize_css(css: str) -> str:
    """Author CSS with `@import`s removed and every non-embedded `url()` (or
    `src()`, or `image-set()` candidate) replaced by `none`."""
    css = _decode_letter_escapes(css)
    css = _CSS_IMPORT.sub("", css)

    def _url(match: re.Match) -> str:
        target = next((g for g in match.groups() if g is not None), "")
        return match.group(0) if _is_data_image(target) else "none"

    css = _CSS_URL.sub(_url, css)
    return _strip_image_sets(css)


def _missing_image_marker(tree: lxml.html.HtmlElement, url: str | None):
    marker = tree.makeelement("span", {"class": "hcl-missing-image"})
    marker.text = f"[image not captured: {url}]" if url else NOT_CAPTURED_MARKER
    return marker


def _drop_active_elements(tree: lxml.html.HtmlElement) -> None:
    """Remove `_DROP_ELEMENTS` (tag and content) and unwrap `_UNWRAP_ELEMENTS`.

    Run BEFORE the image rewrite so an image inside, say, a `<noscript>` is
    not given an external-image number for something that never prints."""
    for element in list(tree.iter()):
        tag = element.tag if isinstance(element.tag, str) else ""
        name = tag.rsplit("}", 1)[-1].lower()
        if element is tree:
            continue
        if name in _DROP_ELEMENTS:
            # `drop_tree` keeps the element's tail -- the text after it,
            # which belongs to the parent and must not go with it.
            element.drop_tree()
        elif name in _UNWRAP_ELEMENTS:
            element.drop_tag()


def _clean_attributes(
    tree: lxml.html.HtmlElement,
    *,
    resource_ok: Callable[[str], bool] = _safe_resource,
    mark_unembedded_images: bool = True,
) -> None:
    """Handlers, loading attributes, script URLs and remote CSS -- run AFTER
    the image and link rewrites, so it also sees what they produced.

    `resource_ok` and `mark_unembedded_images` are the two places where a
    printed page and a published one differ: the PDF loads nothing, so only an
    embedded resource may stay; an exported site links its images as files and
    may show one from elsewhere, so there a resource is judged like a link
    (`clean_for_export`)."""
    for element in list(tree.iter()):
        if not isinstance(element.tag, str):
            continue
        name = element.tag.rsplit("}", 1)[-1].lower()
        original_src = element.get("src")
        if name == "style" and element.text:
            element.text = _sanitize_css(element.text)
        for attr in list(element.attrib):
            key = attr.lower()
            value = element.attrib[attr]
            if key.startswith("on") or key in _DROP_ATTRIBUTES:
                del element.attrib[attr]
            elif key == "style":
                element.attrib[attr] = _sanitize_css(value)
            elif key in _URL_ATTRIBUTES:
                navigation = name in _NAVIGATION_ELEMENTS and key != "src"
                if not (
                    _safe_navigation(value) if navigation or key == "cite" else resource_ok(value)
                ):
                    del element.attrib[attr]
        # An image nobody rewrote (a comment has no asset list) would be
        # fetched at render time; its address is kept as visible text instead.
        if (
            mark_unembedded_images
            and name == "img"
            and not _is_data_image(element.get("src") or "")
        ):
            _replace(element, _missing_image_marker(tree, original_src))


def drop_active_for_export(tree: lxml.html.HtmlElement) -> None:
    """The first half of `clean_for_export`, run BEFORE an exporter rewrites
    images and links -- so an image inside a dropped `<noscript>` is never
    copied into the export for something nobody will see.

    Beyond the PDF's list, a `<style>` element and HTML comments go too: a
    stylesheet inside a page body applies to the whole page of the site it is
    published in, theme included, and a comment shows nothing."""
    _drop_active_elements(tree)
    for element in list(tree.iter()):
        if element is tree:
            continue
        if not isinstance(element.tag, str) or element.tag.lower() == "style":
            element.drop_tree()


def clean_for_export(tree: lxml.html.HtmlElement) -> None:
    """The second half, run AFTER the rewrites: the PDF's allowlist cleaner
    (handlers, loading attributes, script URLs, remote CSS), except that a
    resource is judged like a link -- an exported image is a file beside the
    page, not a data URI -- and an image nothing rewrote keeps its `src`, as
    it does in the Markdown the same exporter writes."""
    _clean_attributes(tree, resource_ok=_safe_navigation, mark_unembedded_images=False)


def _strip_scripts_and_handlers(tree: lxml.html.HtmlElement) -> None:
    """The whole cleanup for markup nothing else rewrites (comments)."""
    _drop_active_elements(tree)
    _clean_attributes(tree)


def _asset_lookup(assets: list[ResolvedAsset]) -> dict[str, ResolvedAsset]:
    lookup: dict[str, ResolvedAsset] = {}
    for asset in assets:
        lookup.setdefault(asset.original_href, asset)
        if asset.resolved_url:
            lookup.setdefault(asset.resolved_url, asset)
    return lookup


def _rewrite_images(
    tree: lxml.html.HtmlElement, assets: list[ResolvedAsset], blob_bytes: BlobBytes
) -> None:
    lookup = _asset_lookup(assets)
    for img in tree.xpath("//img"):
        src = img.get("src")
        asset = lookup.get(src) if src is not None else None
        data = None
        if asset is not None and asset.present and asset.blob_hash:
            digest = _blob_digest(asset.blob_hash)
            data = blob_bytes(digest) if digest else None
        if data is not None:
            img.set("src", _data_uri(asset.original_href, data))
            continue
        if asset is not None and asset.scope == "external":
            _rewrite_external_image(tree, img, asset.resolved_url or asset.original_href)
            continue
        # Not present, unresolved, or the blob lookup came back empty:
        # a visible marker, never a silently dropped image -- with the
        # address, so it can still be looked up.
        url = (asset.resolved_url or asset.original_href) if asset is not None else src
        marker = tree.makeelement("span", {"class": "hcl-missing-image"})
        marker.text = f"[image not captured: {url}]" if url else NOT_CAPTURED_MARKER
        _replace(img, marker)


def _replace(old: lxml.html.HtmlElement, new: lxml.html.HtmlElement) -> None:
    parent = old.getparent()
    if parent is not None:
        new.tail = old.tail
        parent.replace(old, new)


_CSS_PX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$")


def _px(value: str | None) -> float | None:
    match = _CSS_PX.match(value or "")
    return float(match.group(1)) if match else None


def _declared_size(img) -> tuple[float | None, float | None]:
    """The size the page gives the image: inline style first, then attributes.
    A percentage or any other unit is not a pixel size and counts as unknown."""
    style = {}
    for declaration in (img.get("style") or "").split(";"):
        name, _, value = declaration.partition(":")
        style[name.strip().lower()] = value
    width = _px(style.get("width")) if "width" in style else _px(img.get("width"))
    height = _px(style.get("height")) if "height" in style else _px(img.get("height"))
    return width, height


def _intrinsic_size(data: bytes) -> tuple[int, int] | None:
    """Pixel size read from the file's own header; None if not recognised."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            length = int.from_bytes(data[i + 2 : i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(
                    data[i + 5 : i + 7], "big"
                )
            i += 2 + length
        return None
    head = data[:1024].lstrip()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        width = re.search(rb'<svg[^>]*\swidth="(\d+(?:\.\d+)?)(?:px)?"', head)
        height = re.search(rb'<svg[^>]*\sheight="(\d+(?:\.\d+)?)(?:px)?"', head)
        if width and height:
            return round(float(width.group(1))), round(float(height.group(1)))
    return None


def _is_small(img, data: bytes | None, limit: int) -> bool:
    """Declared size decides; the file's own size only when none is declared.
    Unknown counts as a figure, so nothing is marked more lightly by accident."""
    width, height = _declared_size(img)
    if width is not None or height is not None:
        return all(side is None or side <= limit for side in (width, height))
    size = _intrinsic_size(data) if data else None
    return size is not None and max(size) <= limit


def _rewrite_external_image(tree: lxml.html.HtmlElement, img, url: str) -> None:
    """A third-party image: embedded and marked when included, otherwise a
    marker that keeps its address. Never left as a live `src` -- the browser
    rendering the PDF does not fetch anything from a third party."""
    register = _EXTERNAL.get()
    if register is None or not register.included:
        marker = tree.makeelement("span", {"class": "hcl-missing-image"})
        marker.text = f"[external image not included: {url}]"
        _replace(img, marker)
        return
    number, first = register.number(url)
    data = register.fetched.get(url)
    small = _is_small(img, data, register.small_px)
    if data is None:
        marker = tree.makeelement(
            "span", {"class": "hcl-missing-image" + ("" if small else " hcl-external-image")}
        )
        # In running text the address would swamp the sentence; the External
        # content page carries it.
        marker.text = (
            f"[E{number} not retrieved]"
            if small
            else f"[external image E{number} could not be retrieved: {url}]"
        )
        if first:
            marker.set("id", f"ext-{number}")
        _replace(img, marker)
        return
    img.set("src", _data_uri(url, data))
    wrapper = tree.makeelement(
        "span", {"class": "hcl-external-inline" if small else "hcl-external-image"}
    )
    if first:
        wrapper.set("id", f"ext-{number}")
    if small:
        mark = tree.makeelement("sup", {"class": "hcl-external-ref"})
        mark.text = f"E{number}"
    else:
        mark = tree.makeelement("span", {"class": "hcl-external-caption"})
        mark.text = f"External image E{number} · {urlsplit(url).hostname or url}"
    _replace(img, wrapper)
    img.tail = None
    wrapper.append(img)
    wrapper.append(mark)


def _link_lookup(links: list[LinkRef]) -> dict[str, LinkRef]:
    lookup: dict[str, LinkRef] = {}
    for link in links:
        lookup.setdefault(link.original_href, link)
    return lookup


def _rewrite_links(tree: lxml.html.HtmlElement, links: list[LinkRef]) -> None:
    lookup = _link_lookup(links)
    for anchor in tree.xpath("//a[@href]"):
        link = lookup.get(anchor.get("href"))
        if link is None:
            continue
        if link.scope == "in_export" and link.target_page_id:
            anchor.set("href", f"#p-{link.target_page_id}")
            continue
        # hcl_deployment/external: keep the resolved URL, and show it
        # visibly too so it survives on paper (the design, "External/
        # deployment link kept visible").
        url = link.resolved_url or link.original_href
        anchor.set("href", url)
        visible = anchor.makeelement("span", {"class": "hcl-link-url"})
        visible.text = f" ({url})"
        anchor.addnext(visible)


def _parse_fragment(html_fragment: str) -> lxml.html.HtmlElement | None:
    if not html_fragment or not html_fragment.strip():
        return None
    try:
        return lxml.html.fragment_fromstring(html_fragment, create_parent="div")
    except lxml.etree.ParserError:
        return None


def _sanitize_html(
    body_html: str,
    assets: list[ResolvedAsset],
    links: list[LinkRef],
    blob_bytes: BlobBytes,
) -> str:
    """The sanitize+embed+rewrite core shared by pages, blog posts, and
    forum topics/replies (all of which carry their own `assets`/`links`):
    scripts/handlers stripped, present images embedded as data URIs, links
    reclassified to internal anchors or kept-visible URLs -- and everything
    that could execute or load from elsewhere removed (see the section note
    above `_DROP_ELEMENTS`)."""
    tree = _parse_fragment(body_html)
    if tree is None:
        return ""
    _drop_active_elements(tree)
    _rewrite_images(tree, assets, blob_bytes)
    _rewrite_links(tree, links)
    _clean_attributes(tree)
    return lxml.html.tostring(tree, encoding="unicode")


def _sanitize_body(page: DerivedPage, blob_bytes: BlobBytes) -> str:
    return _sanitize_html(page.content_html, page.assets, page.links, blob_bytes)


def _sanitize_fragment(html_fragment: str | None) -> str:
    """Comments carry no asset/link lists of their own (`DerivedComment`
    has neither), so there is nothing to embed or reclassify -- only the
    same cleanup as a body: nothing that executes or loads from elsewhere.

    Plain-text comment bodies (no block-level HTML, just bare `\\n`) have
    their newlines converted to `<br>` first so line breaks survive HTML
    rendering. HCL comment content often arrives this way."""
    if not html_fragment:
        return ""
    # Convert bare newlines to <br> before parsing. If the fragment already
    # has block-level elements the extra <br> is harmless whitespace.
    fragment = html_fragment.replace("\n", "<br>\n")
    tree = _parse_fragment(fragment)
    if tree is None:
        return ""
    _strip_scripts_and_handlers(tree)
    return lxml.html.tostring(tree, encoding="unicode")


# --- page walk (hierarchy + ordinal order) ----------------------------------


def _walk_pages(wiki: DerivedWiki):
    """Depth-first walk of `root_page_ids` then each page's `child_ids`
    -- both already ordered by `derive` (sibling order lives in
    `ordinal`, baked into `child_ids`/`root_page_ids` at assembly time)
    -- so this never re-sorts, it only follows the given order."""

    def _walk(page_id: str, depth: int):
        page = wiki.pages[page_id]
        yield page, depth
        for child_id in page.child_ids:
            yield from _walk(child_id, depth + 1)

    for root_id in wiki.root_page_ids:
        yield from _walk(root_id, 0)


# --- comments: continuous thread, replies indented --------------------------


def _thread_comments(comments: list[DerivedComment]) -> list[tuple[DerivedComment, int]]:
    """Group replies immediately under their parent (via
    `parent_comment_id`), recursively -- "the whole comment flow, not a
    paging widget". A comment whose `parent_comment_id`
    doesn't match any comment on the page is treated as top-level (flat
    threading is the documented fallback, the design model.py
    docstring). A defensive `seen` guard means a malformed cycle can
    never infinite-loop this walk."""
    by_id = {comment.id for comment in comments}
    children: dict[str | None, list[DerivedComment]] = {}
    for comment in comments:
        parent = comment.parent_comment_id if comment.parent_comment_id in by_id else None
        children.setdefault(parent, []).append(comment)

    ordered: list[tuple[DerivedComment, int]] = []
    seen: set[str] = set()

    def _walk(parent_id: str | None, depth: int) -> None:
        for comment in children.get(parent_id, []):
            if comment.id in seen:
                continue
            seen.add(comment.id)
            ordered.append((comment, depth))
            _walk(comment.id, depth + 1)

    _walk(None, 0)
    return ordered


def _render_comment(comment: DerivedComment, depth: int) -> str:
    author = _escape(comment.author) if comment.author else "(unknown author)"
    # Wiki comments date via `created`; blog comments via `published`.
    when_raw = getattr(comment, "created", None) or getattr(comment, "published", None)
    when = _escape(when_raw) if when_raw else ""
    body = _sanitize_fragment(comment.content_html)
    meta = f"{author}" + (f" &middot; {when}" if when else "")
    return (
        f'<div class="comment" id="comment-{_escape(comment.id)}" data-depth="{depth}" '
        f'style="margin-left: {depth * 1.5}em">'
        f'<div class="comment-meta">{meta}</div>'
        f'<div class="comment-body">{body}</div>'
        "</div>"
    )


def _render_comments(comments: list[DerivedComment]) -> str:
    if not comments:
        return ""
    threaded = _thread_comments(comments)
    rendered = "".join(_render_comment(comment, depth) for comment, depth in threaded)
    return (
        f'<div class="comments"><h3>Comments</h3><div class="comment-thread">{rendered}</div></div>'
    )


# --- attachments -------------------------------------------------------------


def _render_attachment(attachment: DerivedAttachment, blob_bytes: BlobBytes) -> str:
    filename = _escape(attachment.filename or attachment.id)
    data = None
    if attachment.asset.present and attachment.asset.blob_hash:
        digest = _blob_digest(attachment.asset.blob_hash)
        data = blob_bytes(digest) if digest else None
    if data is not None:
        uri = _data_uri(attachment.asset.original_href, data)
        return f'<li><a href="{uri}" download="{filename}">{filename}</a></li>'
    marker = f'<span class="hcl-missing-attachment">[{NOT_CAPTURED_ATTACHMENT_MARKER}]</span>'
    return f"<li>{filename} {marker}</li>"


def _render_attachments(attachments: list[DerivedAttachment], blob_bytes: BlobBytes) -> str:
    if not attachments:
        return ""
    items = "".join(_render_attachment(attachment, blob_bytes) for attachment in attachments)
    return f'<div class="attachments"><h3>Attachments</h3><ul>{items}</ul></div>'


# --- table of contents ---------------------------------------------------


def _render_toc_list(wiki: DerivedWiki, page_ids: list[str]) -> str:
    items = []
    for page_id in page_ids:
        page = wiki.pages[page_id]
        title = _escape(page.title or page.label or page.id)
        children = _render_toc_list(wiki, page.child_ids) if page.child_ids else ""
        items.append(f'<li><a href="#p-{_escape(page.id)}">{title}</a>{children}</li>')
    return f"<ul>{''.join(items)}</ul>"


#: Contents rows per page. paged.js loses a fragment when it has to split one
#: long list across pages -- an entire page of entries disappeared between two
#: that rendered -- so the list is split HERE, into blocks that each fit a
#: page, and paged.js is never asked to fragment one. Conservative: a wrapped
#: two-line title costs a row, and a short last page is harmless where a
#: dropped one is not.
_TOC_ROWS_PER_PAGE = 26


def _toc_page_rows(wiki: DerivedWiki, page_ids: list[str], depth: int = 0) -> list[str]:
    """Wiki pages flattened depth-first, one row each.

    Nested `<ul>`s would make a single row render as many lines, and the
    split below counts rows -- so the hierarchy is carried by an indent
    class instead of by nesting.
    """
    rows: list[str] = []
    for page_id in page_ids:
        page = wiki.pages[page_id]
        title = _escape(page.title or page.label or page.id)
        rows.append(
            f'<li class="toc-d{min(depth, 4)}"><a href="#p-{_escape(page.id)}">{title}</a></li>'
        )
        if page.child_ids:
            rows.extend(_toc_page_rows(wiki, page.child_ids, depth + 1))
    return rows


def _render_external_content(register: _ExternalRegister) -> str:
    """The reference page for the included external images: each E-number
    with its original address and whether it made it in."""
    rows = []
    for url, number in register.numbers.items():
        status = "included" if register.fetched.get(url) is not None else "could not be retrieved"
        href = _escape(url)
        rows.append(
            f'<tr><td><a class="hcl-external-mark" href="#ext-{number}">E{number}</a></td>'
            f'<td class="hcl-external-url"><a href="{href}">{href}</a></td>'
            f"<td>{status}</td></tr>"
        )
    return (
        '<section id="external-content" class="page-break">'
        "<h1>External content</h1>"
        '<p class="hcl-external-note">The images marked <em>External image E&hellip;</em> in '
        "this document &mdash; or, for small images in running text such as icons, with a "
        "superscript <em>E&hellip;</em> after them &mdash; are not part of the captured "
        "archive. They are third-party material, "
        "loaded from the addresses below when this PDF was made and reproduced as they were "
        "found there. They belong to their respective owners. This tool does not assess who "
        "holds the rights to them or whether they may be reproduced or shared; that decision "
        "rests with whoever makes or passes on this document.</p>"
        '<table class="hcl-external-table"><thead><tr><th>Mark</th><th>Original address</th>'
        "<th>In this document</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "</section>"
    )


def _render_toc(interchange: Interchange, *, external_content: bool = False) -> str:
    """The table of contents, pre-split into page-sized blocks.

    Container names are list ITEMS, not headings: a heading between lists is a
    block of its own, and paged.js drops such a block when it lands exactly on
    a page boundary, so whichever container fell at the break lost its name
    while its entries stayed.
    """
    rows: list[str] = []

    def group(name: str) -> None:
        rows.append('<li class="toc-group">' + _escape(name) + "</li>")

    for wiki in interchange.wikis:
        group(wiki.title or wiki.label)
        rows.extend(_toc_page_rows(wiki, wiki.root_page_ids))
    for blog in interchange.blogs:
        group(blog.title or blog.handle or blog.id)
        for post_id in blog.post_ids:
            post = blog.posts.get(post_id)
            if post is None:
                continue
            rows.append(
                '<li><a href="#b-'
                + _escape(post.id)
                + '">'
                + _escape(post.title or post.id)
                + "</a></li>"
            )
    for forum in interchange.forums:
        group(forum.title or forum.id)
        for topic_id in forum.topic_ids:
            topic = forum.topics.get(topic_id)
            if topic is None:
                continue
            rows.append(
                '<li><a href="#t-'
                + _escape(topic.id)
                + '">'
                + _escape(topic.title or topic.id)
                + "</a></li>"
            )
    if external_content:
        # Its own group, not an entry under whichever container came last.
        rows.append('<li class="toc-group"><a href="#external-content">External content</a></li>')

    rows.insert(0, '<li class="toc-title">Table of contents</li>')
    blocks = [
        '<ul class="toc-list'
        + (" toc-continued" if start else "")
        + '">'
        + "".join(rows[start : start + _TOC_ROWS_PER_PAGE])
        + "</ul>"
        for start in range(0, max(len(rows), 1), _TOC_ROWS_PER_PAGE)
    ]
    # The "Table of contents" title is the first ROW of the first block, not a
    # standalone heading: a heading of its own is a block paged.js can drop at
    # a page boundary -- which it did, taking the title with it and leaving a
    # blank page where it had been.
    return '<nav id="toc">' + "".join(blocks) + "</nav>"


# --- page sections -------------------------------------------------------


_MAX_HEADING_LEVEL = 6


def _heading_level(depth: int) -> int:
    return min(depth + 1, _MAX_HEADING_LEVEL)


def _render_page_section(
    page: DerivedPage, depth: int, blob_bytes: BlobBytes, *, include_comments: bool = True
) -> str:
    level = _heading_level(depth)
    title = _escape(page.title or page.label or page.id)
    tags = _render_tags(page.tags)
    body = _sanitize_body(page, blob_bytes)
    comments = _render_comments(page.comments) if include_comments else ""
    attachments = _render_attachments(page.attachments, blob_bytes)
    # Every wiki page starts on a fresh PDF page (not just depth-0 roots): a
    # subpage is its own document to the reader, so it earns its own page.
    page_break_class = " page-break"
    section_id = _escape(page.id)
    return (
        f'<section id="p-{section_id}" class="hcl-page{page_break_class}" data-depth="{depth}">'
        f'<h{level} class="pdf-sectitle">{title}</h{level}>'
        f"{tags}"
        f'<div class="page-body">{body}</div>'
        f"{comments}{attachments}"
        "</section>"
    )


def _render_wiki(wiki: DerivedWiki, blob_bytes: BlobBytes, *, include_comments: bool = True) -> str:
    heading = _escape(wiki.title or wiki.label)
    sections = "".join(
        _render_page_section(page, depth, blob_bytes, include_comments=include_comments)
        for page, depth in _walk_pages(wiki)
    )
    return f'<section class="hcl-wiki"><h1>{heading}</h1>{sections}</section>'


# --- blogs & forums ----------------------------------------------------------
#
# Blog posts and forum topics/replies each carry their own `content_html`
# plus `assets`/`links` (same shape as a page body), so they share the page
# sanitize+embed+rewrite core (`_sanitize_html`). `ScopeBody` is the seam
# Path B uses to scope each entry's author CSS to its own section; Path A
# passes `_identity_scope` (no scoping), so both paths render the same
# structure from one set of functions.

#: (sanitized_html, scope_id) -> html. Path A: identity. Path B: CSS scoping.
ScopeBody = Callable[[str, str], str]


def _identity_scope(html: str, _scope: str) -> str:
    return html


def _render_flags(flags: list[str]) -> str:
    if not flags:
        return ""
    labels = ", ".join(_escape(flag) for flag in flags)
    return f'<div class="hcl-flags">{labels}</div>'


def _render_tags(tags: list[str]) -> str:
    """A page's / post's tags as a row of chips. Empty -> nothing."""
    if not tags:
        return ""
    chips = "".join(f'<span class="hcl-tag">{_escape(tag)}</span>' for tag in tags)
    return f'<div class="hcl-tags">{chips}</div>'


def _render_entry_meta(author: str | None, when: str | None) -> str:
    who = _escape(author) if author else "(unknown author)"
    date = _escape(when) if when else ""
    text = who + (f" &middot; {date}" if date else "")
    return f'<div class="entry-meta">{text}</div>'


def _render_blog_post(
    post: DerivedBlogPost,
    blob_bytes: BlobBytes,
    scope_body: ScopeBody = _identity_scope,
    *,
    include_comments: bool = True,
) -> str:
    section_id = _escape(post.id)
    title = _escape(post.title or post.id)
    meta = _render_entry_meta(post.author, post.created)
    tags = _render_tags(post.tags)
    body = scope_body(
        _sanitize_html(post.content_html, post.assets, post.links, blob_bytes),
        f"#b-{section_id}",
    )
    comments = _render_comments(post.comments) if include_comments else ""
    return (
        f'<section id="b-{section_id}" class="hcl-post page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{tags}'
        f'<div class="post-body">{body}</div>'
        f"{comments}"
        "</section>"
    )


def _render_blog(
    blog: DerivedBlog,
    blob_bytes: BlobBytes,
    scope_body: ScopeBody = _identity_scope,
    *,
    include_comments: bool = True,
) -> str:
    heading = _escape(blog.title or blog.handle or blog.id)
    posts = "".join(
        _render_blog_post(
            blog.posts[post_id], blob_bytes, scope_body, include_comments=include_comments
        )
        for post_id in blog.post_ids
        if post_id in blog.posts
    )
    return f'<section class="hcl-blog"><h1>{heading}</h1>{posts}</section>'


def _render_forum_reply(
    reply: DerivedForumReply, depth: int, blob_bytes: BlobBytes, scope_body: ScopeBody
) -> str:
    reply_id = _escape(reply.id)
    meta = _render_entry_meta(reply.author, reply.created)
    flags = _render_flags(reply.flags)
    body = scope_body(
        _sanitize_html(reply.content_html or "", reply.assets, reply.links, blob_bytes),
        f"#r-{reply_id}",
    )
    return (
        f'<div class="forum-reply" id="r-{reply_id}" data-depth="{depth}" '
        f'style="margin-left: {depth * 1.5}em">'
        f"{meta}{flags}"
        f'<div class="reply-body">{body}</div>'
        "</div>"
    )


def _render_reply_thread(
    topic: DerivedForumTopic, blob_bytes: BlobBytes, scope_body: ScopeBody
) -> str:
    """Render the id-keyed reply tree depth-first: each top-level
    `reply_id`, then recursively each reply's `child_ids`, indenting by
    depth. A `seen` guard means a malformed cycle can never infinite-loop,
    mirroring `_thread_comments`."""
    if not topic.reply_ids:
        return ""
    seen: set[str] = set()

    def _walk(reply_id: str, depth: int) -> str:
        if reply_id in seen or reply_id not in topic.replies:
            return ""
        seen.add(reply_id)
        reply = topic.replies[reply_id]
        rendered = _render_forum_reply(reply, depth, blob_bytes, scope_body)
        children = "".join(_walk(child_id, depth + 1) for child_id in reply.child_ids)
        return rendered + children

    body = "".join(_walk(reply_id, 0) for reply_id in topic.reply_ids)
    return f'<div class="forum-replies"><h3>Replies</h3>{body}</div>'


def _render_forum_topic(
    topic: DerivedForumTopic, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    section_id = _escape(topic.id)
    title = _escape(topic.title or topic.id)
    meta = _render_entry_meta(topic.author, topic.created)
    flags = _render_flags(topic.flags)
    body = scope_body(
        _sanitize_html(topic.content_html, topic.assets, topic.links, blob_bytes),
        f"#t-{section_id}",
    )
    replies = _render_reply_thread(topic, blob_bytes, scope_body)
    return (
        f'<section id="t-{section_id}" class="hcl-topic page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{flags}'
        f'<div class="topic-body">{body}</div>'
        f"{replies}"
        "</section>"
    )


def _human_size(size: int | None) -> str:
    """`148213` -> `145 KB`. A byte count is data; a size is information."""
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _bytes_note(item, captured: bool) -> str:
    if captured:
        return "in package"
    if getattr(item, "excluded_by_author_filter", False):
        return "outside the filter"
    return NOT_CAPTURED_ATTACHMENT_MARKER


def _render_file_library(library, blob_bytes: BlobBytes) -> str:
    """A community's files, as a listing.

    Deliberately a table and not an attempt to render content. A PDF cannot show
    a spreadsheet or a zip, and pretending otherwise would be worse than saying
    plainly what the archive holds -- so this is the inventory: what is in the
    library, how big, which version, in which folder, and by whom. The documents
    themselves are in the package under `files/`.
    """
    heading = _escape(library.title or "Files")
    folder_names = {f.id: (f.name or f.id) for f in library.folders}
    rows = []
    for file_id in library.file_ids:
        item = library.files.get(file_id)
        if item is None:
            continue
        folders = ", ".join(sorted(folder_names.get(fid, fid) for fid in item.folder_ids))
        captured = item.asset is not None and item.asset.present
        rows.append(
            "<tr>"
            f"<td>{_escape(item.name or item.id)}</td>"
            f'<td class="hcl-file-meta">{_escape(folders)}</td>'
            f'<td class="hcl-file-meta">{_escape(_human_size(item.size))}</td>'
            f'<td class="hcl-file-meta">{_escape(item.version_label or "")}</td>'
            f'<td class="hcl-file-meta">{_escape(item.author or "")}</td>'
            # Said per row, because "this document is listed but its bytes were
            # never captured" is exactly the thing a reader must not have to
            # guess at -- and a document left out by an author filter is a
            # different statement from one that would not come down.
            f'<td class="hcl-file-meta">{_bytes_note(item, captured)}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<section class="hcl-files">'
        f"<h1>{heading}</h1>"
        '<table class="hcl-file-table">'
        "<thead><tr><th>Name</th><th>Folder</th><th>Size</th>"
        "<th>Version</th><th>Author</th><th>Bytes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="hcl-file-note">The documents themselves are in the package '
        "under <code>files/</code>, named as listed here.</p>"
        "</section>"
    )


def _render_forum(
    forum: DerivedForum, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    heading = _escape(forum.title or forum.id)
    topics = "".join(
        _render_forum_topic(forum.topics[topic_id], blob_bytes, scope_body)
        for topic_id in forum.topic_ids
        if topic_id in forum.topics
    )
    return f'<section class="hcl-forum"><h1>{heading}</h1>{topics}</section>'


def _render_rich_content_page(
    page, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    """One Highlights page. Unlike a file, this HAS a body worth rendering --
    it is prose, tables and images, the same as a wiki page."""
    section_id = _escape(page.id)
    title = _escape(page.title or page.resource_id)
    meta = _render_entry_meta(page.author, page.created)
    version = (
        f'<span class="hcl-rc-version">version {_escape(page.version_label)}</span>'
        if page.version_label
        else ""
    )
    body = scope_body(
        _sanitize_html(page.content_html or "", page.assets, page.links, blob_bytes),
        f"#rc-{section_id}",
    )
    return (
        f'<section id="rc-{section_id}" class="hcl-rc-page page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{version}'
        f'<div class="rc-body">{body}</div>'
        "</section>"
    )


def _render_rich_content(
    highlights, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    """A community's Highlights area.

    Carries a note when the community had widgets nobody ever wrote in. That
    difference exists in the source system and is invisible in the output
    otherwise -- a reader counting three pages has no way to know a fourth
    space was set aside and left empty.
    """
    if not highlights.page_ids:
        return ""
    heading = _escape(highlights.title or "Highlights")
    pages = "".join(
        _render_rich_content_page(highlights.pages[page_id], blob_bytes, scope_body)
        for page_id in highlights.page_ids
        if page_id in highlights.pages
    )
    empty = highlights.placed - highlights.initialized
    note = (
        f'<p class="hcl-rc-note">{empty} further rich content '
        f"{'area was' if empty == 1 else 'areas were'} placed on this community "
        "but never written in.</p>"
        if empty > 0
        else ""
    )
    return f'<section class="hcl-rc"><h1>{heading}</h1>{note}{pages}</section>'


# --- print CSS -------------------------------------------------------------


# Note on the footer: there is deliberately no CSS `@page { @bottom-right
# { content: string(sectitle) " · " counter(page); } }` footer here, keyed
# off `.pdf-sectitle`'s `string-set`. Chromium's print-to-PDF engine does
# NOT implement CSS named strings (`string`/`string-set`) in `@page`
# margin boxes, so such a footer renders silently empty -- no page number,
# no name. The real footer (page number + document title) is wired
# through Chromium's own `page.pdf(display_header_footer=...,...)`
# mechanism in `pdf/browser.py`'s `html_to_pdf` -- see that module for the
# `footer_template`. `.pdf-sectitle` stays on the heading markup (kept as
# a semantic marker for a possible future paged-media engine -- WeasyPrint,
# Prince -- that *does* support `string`/`target-counter`, which would
# also be what's needed for per-section running footer names and TOC "p.
# N" page numbers; neither is achievable with Chromium's print engine
# today), but the CSS rule that tried to use it for a footer is gone.
#: Token names a reader may override, without the `--pdf-` prefix. Derived from
#: the stylesheet itself so the two cannot drift: add a token below and it is
#: settable, with no second list to remember.
def style_token_names() -> list[str]:
    """Every `--pdf-*` token the stylesheet defines, without the prefix."""
    import re  # noqa: PLC0415

    return sorted({m.group(1) for m in re.finditer(r"--pdf-([a-z0-9-]+):", _STYLE)})


def style_overrides_css(overrides: dict[str, str]) -> str:
    """A `:root` block setting the named tokens, or "" for no overrides.

    Unknown names are a mistake worth reporting: a silently ignored setting is
    indistinguishable from one that did not work.
    """
    if not overrides:
        return ""
    # TOML keys read better with underscores (`body_size`), CSS custom
    # properties use hyphens. Accept either rather than making that a thing
    # anyone has to remember.
    overrides = {name.replace("_", "-"): value for name, value in overrides.items()}
    known = set(style_token_names())
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(
            f"unknown PDF style token(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
        )
    body = " ".join(f"--pdf-{name}: {value};" for name, value in sorted(overrides.items()))
    return f":root {{ {body} }}"


#: The design tokens are the supported surface for changing how a PDF looks.
#: Everything below is expressed in terms of them, so a reader can override a
#: handful of names without depending on our rule structure -- and so we can
#: restyle the internals without breaking anyone. `connections-export style
#: --dump` writes this stylesheet out, tokens first, for editing.
_STYLE = """
:root {
  /* Type */
  --pdf-font: sans-serif;
  --pdf-body-size: 10.5pt;
  --pdf-line-height: 1.4;
  /* Headings inside captured content. Chosen for A4 at the body size above;
     without these the browser's screen defaults apply (2em/1.5em/1.17em),
     which is how an h1 ended up at 24pt. */
  --pdf-h1-size: 17pt;
  --pdf-h2-size: 13.5pt;
  --pdf-h3-size: 11.5pt;
  /* Secondary text: link URLs, bylines, tag chips, comment headers. */
  --pdf-meta-size: 8.5pt;
  /* Contents list */
  --pdf-toc-title-size: 15pt;
  --pdf-toc-group-size: 11.5pt;
  --pdf-toc-entry-size: 10pt;
  --pdf-toc-indent: 1.2em;
  /* Colour */
  --pdf-text: #111;
  --pdf-muted: #444;
  --pdf-rule: #cbd2dc;
}
body {
  font-family: var(--pdf-font);
  font-size: var(--pdf-body-size);
  line-height: var(--pdf-line-height);
}
/* Headings in captured bodies. Scoped so the cover and TOC keep their own
   sizes, and so an author's own heading rules still win inside their page. */
.page-body h1, .post-body h1, .topic-body h1,
.hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1 { font-size: var(--pdf-h1-size); }
.page-body h2, .post-body h2, .topic-body h2 { font-size: var(--pdf-h2-size); }
.page-body h3, .post-body h3, .topic-body h3 { font-size: var(--pdf-h3-size); }
/* --- Contents list -------------------------------------------------------
   These rules used to live only in the paged-media stylesheet, so the
   ordinary renderer produced a contents list with no hierarchy at all: its
   heading, the container names and every entry at the body size. They belong
   here, where both renderers see them; only the page-number machinery is
   specific to the paged one. */
#toc { color: var(--pdf-text); }
#toc ul { list-style: none; margin: 0; padding: 0; }
#toc a { text-decoration: none; color: inherit; }
#toc .toc-title {
  font-size: var(--pdf-toc-title-size); font-weight: 700; margin: 0 0 0.6em;
}
#toc .toc-group {
  font-size: var(--pdf-toc-group-size); font-weight: 700; margin-top: 0.9em;
  break-after: avoid;
}
#toc li { font-size: var(--pdf-toc-entry-size); line-height: 1.6; }
#toc .toc-d1 { padding-left: var(--pdf-toc-indent); }
#toc .toc-d2 { padding-left: calc(var(--pdf-toc-indent) * 2); }
#toc .toc-d3 { padding-left: calc(var(--pdf-toc-indent) * 3); }
/* Print safety: a PDF page can't scroll, so nothing may exceed the page's
   content box (paged.js/@page reserves the margins). Force wide content --
   images/media, preformatted text, tables, and long unbroken tokens/URLs --
   to wrap or scale down to the available width instead of being clipped off
   the right edge. Scoped to the captured author bodies so our own chrome
   (title page, TOC) is untouched. */
img, svg, video, canvas, iframe, object, embed {
  max-width: 100% !important; height: auto;
}
.page-body, .post-body, .topic-body, .reply-body, .comment-body {
  overflow-wrap: break-word; word-break: break-word;
}
.page-body *, .post-body *, .topic-body *, .reply-body *, .comment-body * {
  max-width: 100%; box-sizing: border-box;
}
.page-body pre, .post-body pre, .topic-body pre, .reply-body pre, .comment-body pre {
    margin: 0.75em 0 !important; padding: 0.6em !important;
    height: auto !important; min-height: 0 !important; max-height: none !important;
    break-inside: auto !important; page-break-inside: auto !important;
    white-space: pre-wrap !important; overflow-wrap: anywhere; word-break: break-word;
}
.page-body code, .post-body code, .topic-body code, .reply-body code, .comment-body code {
    max-width: 100%; height: auto !important; min-height: 0 !important;
    white-space: pre-wrap !important; overflow-wrap: anywhere; word-break: break-word;
}
.page-body table, .post-body table, .topic-body table,
.reply-body table, .comment-body table {
  table-layout: fixed; border-collapse: collapse;
}
.page-body td, .page-body th, .post-body td, .post-body th, .topic-body td,
.topic-body th, .reply-body td, .reply-body th, .comment-body td, .comment-body th {
  overflow-wrap: anywhere; word-break: break-word;
}
.hcl-link-url { word-break: break-all; }
/* Files: an inventory, not a rendering. */
.hcl-file-table { width: 100%; border-collapse: collapse; margin: 0.6em 0; }
.hcl-file-table th, .hcl-file-table td {
  text-align: left; padding: 0.25em 0.5em; border-bottom: 1px solid var(--pdf-rule);
  vertical-align: top;
}
.hcl-file-table th { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
.hcl-file-meta { font-size: var(--pdf-meta-size); color: var(--pdf-muted); white-space: nowrap; }
.hcl-file-note { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
.hcl-missing-image, .hcl-missing-attachment { color: #a00; font-style: italic; }
/* A third-party image: recognisable on paper, not only by colour. */
.hcl-external-image {
  display: inline-block; max-width: 100%; border: 1px dashed #8a6d00; padding: 2px;
}
.hcl-external-image img { display: block; }
.hcl-external-caption {
  display: block; font-size: var(--pdf-meta-size); color: #6b5500; font-style: italic;
}
/* A small one in running text: a footnote-style mark, the line left alone. */
.hcl-external-inline { white-space: nowrap; }
.hcl-external-ref { font-size: 0.7em; color: #6b5500; font-style: italic; margin-left: 0.1em; }
.hcl-external-note { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
.hcl-external-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.hcl-external-table th, .hcl-external-table td {
  text-align: left; padding: 0.25em 0.5em; border-bottom: 1px solid var(--pdf-rule);
  vertical-align: top; font-size: var(--pdf-meta-size);
}
.hcl-external-table th:first-child, .hcl-external-table td:first-child { width: 6em; }
.hcl-external-table th:last-child, .hcl-external-table td:last-child { width: 9em; }
.hcl-external-url { overflow-wrap: anywhere; word-break: break-all; }
.hcl-link-url { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
/* Tag chips (wiki pages + blog posts). */
.hcl-tags { margin: 0.2em 0 0.6em; }
.hcl-tag {
  display: inline-block; font-size: var(--pdf-meta-size); color: #334; background: #eef1f6;
  border: 1px solid #d2d8e2; border-radius: 10px; padding: 0.05em 0.6em;
  margin: 0.15em 0.3em 0.15em 0;
}
/* Threaded/indented discussion view -- blog/wiki comments AND forum replies
   share it, so a forum reply thread is indented with the same connector line as
   a comment thread (per-level indent comes from an inline margin-left). */
.comment, .forum-reply {
  border-left: 2px solid var(--pdf-rule); padding-left: 0.6em; margin-top: 0.5em;
}
.comment-meta, .forum-reply .entry-meta {
  font-weight: bold; font-size: var(--pdf-meta-size); color: #333;
}
.comments > h3, .forum-replies > h3 {
  font-size: 0.95em; margin: 1em 0 0.3em; padding-bottom: 2px;
  border-bottom: 1px solid var(--pdf-rule);
}
@media print {
  .page-break { break-before: page; }
  /* Each wiki, blog and forum starts its own sheet. Without this a
     container's <h1> was stranded at the foot of the previous page, because
     the first item inside it carries `.page-break` and jumped to the next
     one -- a heading alone at the bottom, then its content overleaf. */
  .hcl-wiki, .hcl-blog, .hcl-forum, .hcl-files, .hcl-rc { break-before: page; }
  .hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1, .hcl-files > h1,
  .hcl-rc > h1 { break-after: avoid; }
  /* The first item inside a container must NOT break again: its container
     just started a page, so breaking would leave that page empty but for the
     heading. */
  .hcl-wiki > .page-break:first-of-type,
  .hcl-blog > .page-break:first-of-type,
  .hcl-forum > .page-break:first-of-type { break-before: auto; }
  * { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  @page {
    size: A4;
  }
}
"""


# --- top-level assembly ------------------------------------------------------


def _document_title(interchange: Interchange) -> str:
    """The document `<title>` -- rendered into the PDF footer by
    Chromium's `class="title"` template class (`pdf/browser.py`'s
    `_FOOTER_TEMPLATE`), so this is the one "name" the footer can
    actually carry. Per-section running names (the footer showing which
    wiki page/post/topic a given printed page came from) aren't
    achievable with Chromium's print engine -- see the `_STYLE` comment
    above -- so a single, export-level name is what "footer with a name"
    means here. Includes `base_url` when the interchange carries one, so
    the footer names *which* deployment this export came from."""
    if interchange.base_url:
        return f"HCL Connections Export — {interchange.base_url}"
    return "HCL Connections Export"


def _export_heading(interchange: Interchange) -> str:
    """The title-page heading, named for what the export actually contains --
    so a forum export doesn't say "Wiki Export"."""
    apps = []
    if interchange.wikis:
        apps.append("Wiki")
    if interchange.blogs:
        apps.append("Blog")
    if interchange.forums:
        apps.append("Forum")
    return f"HCL {apps[0]} Export" if len(apps) == 1 else "HCL Connections Export"


def _pluralize(count: int, noun: str) -> str:
    """`1 wiki` / `3 wikis`, and `1 file library` / `3 file libraries`.

    A plain trailing `s` covered every noun on the cover until `library`
    arrived and produced "3 file librarys". A consonant before a final `y`
    takes `-ies`; a vowel does not (`day` -> `days`).
    """
    if count == 1:
        return f"{count} {noun}"
    if noun.endswith("y") and len(noun) > 1 and noun[-2] not in "aeiou":
        return f"{count} {noun[:-1]}ies"
    return f"{count} {noun}s"


#: Inline, because both render paths (`html.py` and `browser_fidelity.py`)
#: share `_render_title_page` and neither `_STYLE` block defines
#: `#title-page` -- styling it here reaches both without duplicating a rule
#: into each. `page-break-after` matters most: without it the cover ran
#: straight into the first section instead of standing as its own page.
_COVER_STYLE = (
    "page-break-after: always; text-align: center; display: flex; "
    "flex-direction: column; align-items: center; justify-content: center; "
    # 24cm plus the page margins exceeded an A4 text block, so the cover
    # spilled a few millimetres onto a second sheet and produced a blank
    # page 2. 20cm fills the page without reaching its edge.
    "min-height: 20cm;"
)
_COVER_NAMES_STYLE = "margin: 0.4em 0 0; font-size: 1.05em; color: #333;"
_COVER_COUNTS_STYLE = "margin: 1em 0 0; font-size: 1.1em; color: #555;"
#: The metadata block sits well below the content lines; the second line
#: tucks under the first rather than repeating the gap.
_COVER_META_STYLE = "margin: 2em 0 0.15em; font-size: 0.9em; color: #666;"
_COVER_META_NEXT_STYLE = "margin: 0.15em 0 0; font-size: 0.9em; color: #666;"


def _cover_counts(interchange: Interchange) -> str:
    """The aggregate line: `1 wiki - 2 blogs - 47 pages`. The page total is
    the one figure the name lines above cannot convey, which is the reason
    this line earns its place next to them."""
    counts = []
    if interchange.wikis:
        counts.append(_pluralize(len(interchange.wikis), "wiki"))
    if interchange.blogs:
        counts.append(_pluralize(len(interchange.blogs), "blog"))
    if interchange.forums:
        counts.append(_pluralize(len(interchange.forums), "forum"))
    if interchange.file_libraries:
        counts.append(_pluralize(len(interchange.file_libraries), "file library"))
    # Each app's own leaf items, so the line still says something new on a
    # single-container export, where the container count alone would only
    # repeat the name line above it.
    for total, noun in (
        (sum(len(wiki.pages) for wiki in interchange.wikis), "page"),
        (sum(len(blog.posts) for blog in interchange.blogs), "post"),
        (sum(len(forum.topics) for forum in interchange.forums), "topic"),
        (sum(len(library.files) for library in interchange.file_libraries), "file"),
    ):
        if total:
            counts.append(_pluralize(total, noun))
    if not counts:
        return ""
    return f'<p style="{_COVER_COUNTS_STYLE}">' + " &middot; ".join(counts) + "</p>"


def _render_title_page(interchange: Interchange, generated_at: str) -> str:
    wiki_titles = ", ".join(_escape(wiki.title or wiki.label) for wiki in interchange.wikis)
    blogs = ", ".join(_escape(blog.title or blog.handle or blog.id) for blog in interchange.blogs)
    forums = ", ".join(_escape(forum.title or forum.id) for forum in interchange.forums)
    names = f' style="{_COVER_NAMES_STYLE}"'
    wikis_p = f"<p{names}>Wikis: {wiki_titles}</p>" if interchange.wikis else ""
    blogs_p = f"<p{names}>Blogs: {blogs}</p>" if interchange.blogs else ""
    forums_p = f"<p{names}>Forums: {forums}</p>" if interchange.forums else ""
    base_url = (
        f'<p style="{_COVER_META_NEXT_STYLE}">Source: {_escape(interchange.base_url)}</p>'
        if interchange.base_url
        else ""
    )
    return (
        f'<section id="title-page" style="{_COVER_STYLE}">'
        f'<h1 style="font-size: 2.4em; margin: 0; color: #111;">'
        f"{_escape(_export_heading(interchange))}</h1>"
        f"{wikis_p}{blogs_p}{forums_p}"
        f"{_cover_counts(interchange)}"
        f'<p style="{_COVER_META_STYLE}">Generated: {_escape(generated_at)}</p>'
        f"{base_url}"
        "</section>"
    )


def render_html(
    interchange: Interchange,
    *,
    blob_bytes: BlobBytes,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
    chrome: bool = True,
    style_overrides: dict[str, str] | None = None,
    extra_css: str | None = None,
    external_images: Mapping[str, bytes | None] | None = None,
    small_image_px: int | None = None,
) -> str:
    """Render `interchange` to one self-contained, print-ready HTML
    document (render_html (the core)). Pure: no
    browser, no filesystem, no wall clock.

    `external_images` is the choice about third-party images: None leaves
    them out (each keeps its address as text); a URL -> bytes mapping
    (`pdf.external.prepare_external_images`) includes them, each marked
    where it sits and listed on an "External content" page. `small_image_px`
    is the size at or below which one is marked as an icon in running text
    (default `SMALL_IMAGE_PX`).

    Pages appear in hierarchy + ordinal order (depth-first); each is a
    `<section id="p-{page_id}">` with its sanitized body, its comments
    inlined as a continuous thread, and its attachments. `blob_bytes`
    resolves a present asset's blob hash to bytes for `data:` URI
    embedding; a not-present or unresolved image is a visible marker,
    never a silent drop.
    """
    # `chrome=False` renders just the body sections -- no cover, no TOC -- for
    # the live per-entity tiles, where each entity is rendered on its own and
    # a repeated cover/TOC per tile would be noise. The real export keeps both.
    with external_images_register(external_images, small_image_px) as register:
        body_sections = _render_bodies(interchange, blob_bytes, include_comments=include_comments)
    external_content = chrome and register.included and bool(register.numbers)
    title_page = _render_title_page(interchange, generated_at) if chrome else ""
    toc = _render_toc(interchange, external_content=external_content) if chrome else ""
    if external_content:
        body_sections += _render_external_content(register)

    # Both go LAST, after the captured pages' own <style> blocks. Author CSS
    # is emitted inside the body and outranks anything in <head> at equal
    # specificity -- which is exactly how a sample page's `body { font: 16px }`
    # once decided the size of every export. A reader's overrides have to sit
    # after it to mean anything.
    reader_css = ""
    if style_overrides:
        reader_css += f"<style>{style_overrides_css(style_overrides)}</style>"
    if extra_css:
        reader_css += f"<style>{extra_css}</style>"

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_escape(_document_title(interchange))}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body>"
        f"{title_page}{toc}{body_sections}"
        f"{reader_css}"
        "</body></html>"
    )


def _render_bodies(
    interchange: Interchange, blob_bytes: BlobBytes, *, include_comments: bool
) -> str:
    # Comments are always ingested/archived; a caller (the PDF export) may omit
    # them from the rendered document. Forum replies are the thread body, not
    # secondary commentary, so they are never suppressed.
    return (
        "".join(
            _render_wiki(wiki, blob_bytes, include_comments=include_comments)
            for wiki in interchange.wikis
        )
        + "".join(
            _render_blog(blog, blob_bytes, include_comments=include_comments)
            for blog in interchange.blogs
        )
        + "".join(_render_forum(forum, blob_bytes) for forum in interchange.forums)
        + "".join(
            _render_rich_content(highlights, blob_bytes) for highlights in interchange.rich_content
        )
        + "".join(
            _render_file_library(library, blob_bytes) for library in interchange.file_libraries
        )
    )
