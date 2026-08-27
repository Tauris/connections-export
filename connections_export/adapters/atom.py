"""Shared Atom core: namespace-aware lxml helpers over Wikis (and,
later, Blogs/Forums) feeds and entries. Thin
on purpose -- `wikis.py` is where the substantial, app-specific parsing
lives.

Namespaces.
`ca` (composite-applications) is documented but nothing here reads it,
so it is omitted, matching the fake server's own choice.
"""

from __future__ import annotations

import re

from lxml import etree

from connections_export.adapters.errors import AdapterError

ATOM_NS = "http://www.w3.org/2005/Atom"
TD_NS = "urn:ibm.com/td"
SNX_NS = "http://www.ibm.com/xmlns/prod/sn"
THR_NS = "http://purl.org/syndication/thread/1.0"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
APP_NS = "http://www.w3.org/2007/app"

NS = {
    "atom": ATOM_NS,
    "td": TD_NS,
    "snx": SNX_NS,
    "thr": THR_NS,
    # Blogs declares this URI under the prefix `openSearch` (capital S,
    # Blogs "Namespaces"). Our
    # own alias below is lowercase -- that's fine, lxml resolves
    # `namespaces=NS` lookups by URI, never by the source document's
    # own prefix spelling, so both spellings parse identically.
    "opensearch": OPENSEARCH_NS,
    # AtomPub, used by Blogs' `<app:control><snx:comments.../></app:control>`.
    "app": APP_NS,
}

_THR_COUNT_ATTR = f"{{{THR_NS}}}count"

# Known documentation defect #1: the reference's own pages disagree on
# the category scheme spelling for the same entry types. Match
# categories by `term`, never by exact scheme string (the design,
# "Category leniency").
_CATEGORY_SCHEMES = frozenset(
    {
        "http://www.ibm.com/xmlns/prod/sn/type",
        "tag:ibm.com,2006:td/type",
    }
)

# Atom entry ids are `urn:lsid:ibm.com:<infix>:<uuid>` -- `td:` for
# Wikis, `forum:` for Forums, both a single colon-terminated segment.
# Blogs uses two segments, `blogs:entry-` (colon *and* a hyphen-suffixed
# word) -- Blogs "Entry shape":
# `urn:lsid:ibm.com:blogs:entry-<uuid>` (the design, "entry_uuid
# already strips... generalize to strip any... (or `-entry-`) form").
#
# The known-infix list is matched *before* the generic single-segment
# fallback, and deliberately as literal strings rather than a general
# "alpha run followed by `:`/`-`" pattern: uuids are hex and can start
# with an all-letters segment (e.g. `aaaaaaaa-1111-...`), which such a
# pattern would misidentify as part of the infix and strip along with
# it -- silently truncating the uuid. Matching known literal infixes
# first avoids that trap entirely.
#: `files:document-` joins the list for the same reason `blogs:entry-` is
#: there: the generic rule strips only to the last colon, which would leave
#: a `document-` prefix on every file id and make it not match the ids the
#: collection feed reports.
_KNOWN_LSID_INFIXES = ("td:", "blogs:entry-", "forum:", "files:document-")
_LSID_KNOWN_INFIX_RE = re.compile(
    r"^urn:lsid:ibm\.com:(?:" + "|".join(re.escape(infix) for infix in _KNOWN_LSID_INFIXES) + r")"
)
# Fallback for an id using the general one-segment shape but an infix
# not in the known list above (a future app, or an undocumented
# variant) -- still stripped, just without the extra hyphen-segment
# handling only Blogs needs.
_LSID_GENERIC_RE = re.compile(r"^urn:lsid:ibm\.com:[^:]+:")


#: Strips characters that are illegal in XML 1.0 (e.g. vertical tab \x0b,
#: form feed \x0c) which some HCL Connections forum feeds contain.
#: Legal set: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF].
#: Same filter a working client applies against a real deployment.
_XML_ILLEGAL = re.compile(
    r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010FFFF]", re.UNICODE
)

#: The XML prolog's own `encoding="..."` declaration, read from the raw bytes
#: BEFORE any decoding is attempted -- some HCL Connections deployments (e.g.
#: non-English/Western-European ones) declare and use a non-UTF-8 encoding
#: (`ISO-8859-1`/`windows-1252`), and blindly decoding those bytes as UTF-8
#: turns every accented/non-ASCII character into a `\ufffd` replacement
#: character ("unreadable" text after ingest). Matched on the first bytes
#: only -- the prolog is always at the very start of the document.
_XML_DECL_ENCODING_RE = re.compile(rb"<\?xml[^>]*\bencoding=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _strip_xml_illegal(data: bytes) -> bytes:
    """Remove XML 1.0 illegal characters from raw bytes before parsing.
    Decodes using the XML prolog's own declared encoding (defaulting to
    UTF-8, the XML spec default, when none is declared or the declared
    codec name is unrecognized) -- NOT a blind UTF-8 decode -- so a feed
    that actually uses e.g. `windows-1252` round-trips its non-ASCII text
    instead of every such character silently becoming `\ufffd`. Always
    re-encodes the cleaned text as UTF-8 bytes, so lxml (which trusts the
    prolog's declaration over the actual bytes) is handed a prolog that
    matches what it will actually see."""
    declared = _XML_DECL_ENCODING_RE.search(data[:200])
    encoding = declared.group(1).decode("ascii", errors="ignore") if declared else "utf-8"
    try:
        text = data.decode(encoding, errors="replace")
    except LookupError:
        # Unrecognized codec name -- fall back rather than raising.
        text = data.decode("utf-8", errors="replace")
        encoding = "utf-8"
    cleaned = _XML_ILLEGAL.sub("", text)
    encoded = cleaned.encode("utf-8")
    if declared and encoding.lower() not in ("utf-8", "utf8"):
        encoded = _XML_DECL_ENCODING_RE.sub(
            lambda m: m.group(0).replace(m.group(1), b"UTF-8"), encoded, count=1
        )
    return encoded


def parse_xml(data: bytes | str, *, expected_root: str | None = None) -> etree._Element:
    """Parse `data` as XML, raising `AdapterError` -- never a raw lxml
    exception -- on malformed input or an unexpected root element.
    Strips XML 1.0 illegal control characters (e.g. `\\x0b` vertical tab)
    that appear in some HCL Connections forum feeds before parsing."""
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    payload = _strip_xml_illegal(payload)
    try:
        root = etree.fromstring(payload)
    except etree.XMLSyntaxError as exc:
        raise AdapterError("malformed XML", detail=str(exc)) from exc
    if expected_root is not None:
        local = etree.QName(root.tag).localname
        if local != expected_root:
            raise AdapterError(
                f"unexpected root element: expected <{expected_root}>, got <{local}>",
                detail=etree.tostring(root, encoding="unicode")[:200],
            )
    return root


def find_text(el: etree._Element, path: str) -> str | None:
    """Namespace-aware `find`, returning the found element's text, or
    None if the element is absent or has no text."""
    found = el.find(path, namespaces=NS)
    if found is None:
        return None
    return found.text


def findall(el: etree._Element, path: str) -> list[etree._Element]:
    """Namespace-aware `findall`."""
    return el.findall(path, namespaces=NS)


def entries(root: etree._Element) -> list[etree._Element]:
    """Every `<entry>` child of a feed root."""
    return root.findall("atom:entry", namespaces=NS)


def links_by_rel(entry: etree._Element) -> dict[str, etree._Element]:
    """Every `<link>` child keyed by its `rel`. Last-one-wins on a
    duplicate rel, which none of the documented shapes produce."""
    out: dict[str, etree._Element] = {}
    for link in entry.findall("atom:link", namespaces=NS):
        rel = link.get("rel")
        if rel:
            out[rel] = link
    return out


def categories(entry: etree._Element) -> list[tuple[str | None, str | None]]:
    """Every `<category>` child as `(term, scheme)` pairs, in document
    order."""
    return [(c.get("term"), c.get("scheme")) for c in entry.findall("atom:category", namespaces=NS)]


def category_term(entry: etree._Element, *, term: str | None = None) -> str | None:
    """A category term, accepting either documented scheme spelling. If `term` is given, only a
    matching category on either scheme counts; otherwise the first
    recognized-scheme category's term wins."""
    for cat_term, scheme in categories(entry):
        if scheme not in _CATEGORY_SCHEMES:
            continue
        if term is not None and cat_term != term:
            continue
        if cat_term:
            return cat_term
    return None


def ranks(entry: etree._Element) -> dict[str, int]:
    """`<snx:rank scheme="...">` counters keyed by the scheme's final
    path segment (e.g. `.../sn/comment` -> "comment"). Entries with a
    non-numeric or empty value are skipped rather than raising --
    counters are supplementary, not required fields."""
    out: dict[str, int] = {}
    for rank in entry.findall("snx:rank", namespaces=NS):
        scheme = (rank.get("scheme") or "").rstrip("/")
        suffix = scheme.rsplit("/", 1)[-1]
        if not suffix:
            continue
        try:
            out[suffix] = int((rank.text or "").strip())
        except ValueError:
            continue
    return out


def thr_count(entry: etree._Element, *, rel: str = "replies") -> int | None:
    """The `thr:count` attribute off the link with the given `rel`
    (default `replies`), or None if the link or attribute is absent."""
    link = links_by_rel(entry).get(rel)
    if link is None:
        return None
    value = link.get(_THR_COUNT_ATTR)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def feed_next_link(data: bytes | str) -> str | None:
    """The feed-level `<link rel="next">` href, or `None` if absent
    (adapters -- expose the next link). This is
    undocumented for Wikis feeds -- the crawler's page-walker
    treats it as a secondary signal, never the sole stop condition."""
    root = parse_xml(data, expected_root="feed")
    link = links_by_rel(root).get("next")
    return link.get("href") if link is not None else None


def feed_total_results(data: bytes | str) -> int | None:
    """The `<opensearch:totalResults>` element from a feed, or `None`
    if absent. Blog, forum and wiki feeds all carry it. Used to show the
    user how many items will be imported before they start."""
    root = parse_xml(data, expected_root="feed")
    for element in root.xpath("//*[local-name()='totalResults']"):
        if element.text:
            try:
                return int(element.text.strip())
            except ValueError:
                pass
    return None


def feed_title(data: bytes | str) -> str | None:
    """The feed-level `<atom:title>` text -- the blog/forum/wiki's own
    display name -- or `None` if the element is absent or empty.
    (Finding #26: shown alongside the identify result/tailoring so the
    user sees the entity's real name, e.g. "Team Updates", not just the
    handle/uuid `parse_url` extracts from the URL.)"""
    root = parse_xml(data, expected_root="feed")
    el = root.find("atom:title", namespaces=NS)
    if el is None or not el.text:
        return None
    text = el.text.strip()
    return text or None


def entry_uuid(id_text: str | None) -> str | None:
    """Strip the `urn:lsid:ibm.com:<infix>:` prefix from an Atom `<id>`
    value, leaving the bare uuid -- `td:` (Wikis), `blogs:entry-`
    (Blogs), and `forum:` (Forums) all recognized. Passes unrecognized
    forms through unchanged rather than raising -- real ids that don't
    match a documented lsid shape are still useful as opaque
    identifiers. Keep the raw `id_text` elsewhere (a model's
    `provenance` field) if it's needed later -- this only ever returns
    the stripped form."""
    if not id_text:
        return None
    known = _LSID_KNOWN_INFIX_RE.sub("", id_text, count=1)
    if known != id_text:
        return known
    return _LSID_GENERIC_RE.sub("", id_text, count=1)


def in_reply_to(entry: etree._Element) -> dict[str, str | None] | None:
    """The `<thr:in-reply-to>` element's three attributes -- `ref`
    (immediate parent), `source` (top-level topic), `href` (fetchable
    URL of the immediate parent) -- or `None` if the element is absent. Blogs
    only ever populates `ref` in practice, but all three are read
    uniformly; a missing attribute is `None` in the returned dict."""
    el = entry.find("thr:in-reply-to", namespaces=NS)
    if el is None:
        return None
    return {"ref": el.get("ref"), "source": el.get("source"), "href": el.get("href")}


def bare_category_terms(entry: etree._Element) -> list[str]:
    """`<category>` elements with no `scheme` attribute, in document
    order -- a user tag, not a type/flags marker (docs/reference/
    hcl-blogs-forums-api.md, Forums "Identifiers and markers": "A bare
    <category term="..."/> with no scheme is a user tag." Blogs'
    `<category term="tag"/>` follows the same shape)."""
    return [term for term, scheme in categories(entry) if scheme is None and term]


def author_name(entry: etree._Element) -> str | None:
    """`<author><name>` text, or `None` if there's no `<author>`
    element -- the generic Atom author shape shared by all three apps."""
    author_el = entry.find("atom:author", namespaces=NS)
    if author_el is None:
        return None
    return find_text(author_el, "atom:name")


def author_userid(entry: etree._Element) -> str | None:
    """`<author><snx:userid>` text -- the stable account id under the Atom
    author, or `None`. More reliable than the display name for identifying who
    authored something (names collide/change); used by the author filter."""
    author_el = entry.find("atom:author", namespaces=NS)
    if author_el is None:
        return None
    return find_text(author_el, "snx:userid")


def contributor_names(entry: etree._Element) -> list[str]:
    """Every `<contributor><name>` text, in document order. HCL Connections
    records participation as `<author>` *and* `<contributor>`, so the author
    filter must consider contributors too (some entities carry the acting user
    only as a contributor)."""
    names: list[str] = []
    for contributor in entry.findall("atom:contributor", namespaces=NS):
        name = find_text(contributor, "atom:name")
        if name:
            names.append(name)
    return names


def contributor_userids(entry: etree._Element) -> list[str]:
    """Every `<contributor><snx:userid>` text, in document order -- the stable
    account ids of contributors (search results carry these for exact matching)."""
    ids: list[str] = []
    for contributor in entry.findall("atom:contributor", namespaces=NS):
        uid = find_text(contributor, "snx:userid")
        if uid:
            ids.append(uid)
    return ids


def link_href(entry: etree._Element, rel: str) -> str | None:
    """The `href` of this entry's `rel="..."` link, or `None`.

    `is not None`, not truthiness: an lxml element with no children is falsey,
    so `link or {}` silently discarded every link that had no sub-element.
    That bug had to be found once and fixed in FOUR copies of this helper --
    blogs, files, forums and wikis each had their own -- which is why there is
    now one.
    """
    link = links_by_rel(entry).get(rel)
    return link.get("href") if link is not None else None


def content_html(entry: etree._Element) -> str | None:
    """`<content>` text, or `None` when the entry carries no content element."""
    element = entry.find("atom:content", namespaces=NS)
    return element.text if element is not None else None


def require_entry_id(entry: etree._Element, *, what: str) -> tuple[str, str]:
    """`(bare uuid, raw LSID text)`; raises `AdapterError` if `<id>` is absent.

    `what` names the caller ("blogs entry", "forum entry") so the error says
    which feed was being read -- the only thing the two former copies differed
    in.
    """
    raw = find_text(entry, "atom:id")
    if not raw:
        raise AdapterError(f"{what} missing id")
    return entry_uuid(raw), raw
