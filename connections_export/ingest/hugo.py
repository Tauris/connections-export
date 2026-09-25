"""A reference ingester that writes Hugo content: a ``content/`` tree to drop
into a Hugo site of your own.

Content only, on purpose. A Hugo site is its templates, theme and
configuration as much as its pages, and all of those are the site owner's:
this writes no layouts and no ``hugo.toml``. What it writes is the shape Hugo
itself reads content in:

* a section per container -- ``content/wikis/<wiki>/``, ``content/blogs/
  <blog>/``, ``content/forums/<forum>/``, ``content/files/<library>/`` and
  ``content/highlights/<community>/`` -- each with an ``_index.md``;
* a wiki's page tree as nested bundles: a page with children is a branch
  bundle (``<slug>/_index.md``), a page without a leaf bundle
  (``<slug>/index.md``), ``weight`` keeping sibling order;
* blog posts, forum topics and Highlights pages as leaf bundles, a topic's
  reply tree rendered beneath it as nested headings;
* every image and attachment copied INTO the bundle of the page that shows
  it -- a page resource, linked by its bare name -- and a library's files
  into the library's own bundle.

Links between exported items are ``{{< relref "/wikis/.../index.md" >}}``
shortcodes rather than written-out URLs. Hugo resolves a ``relref`` against
the site's own ``baseURL``, permalink rules and URL settings, so the link is
right however the site is configured, and it checks every one at build time:
a target that is not there fails the build instead of publishing a dead
link. The paths are from the root of ``content/``, which is why the README
tells the reader to merge this ``content/`` into theirs rather than into a
subfolder of it.

Hugo does not run Go templates in content files -- ``{{ .Site }}`` in a page
is text -- but it does run shortcodes, anywhere in the file, code blocks
included: ``{{< ... >}}`` and ``{{% ... %}}``. An author's shortcode would
run with the site's templates, and a malformed one fails the build. Every
shortcode opener in content is therefore written in Hugo's own comment
form, ``{{</* ... */>}}``, which Hugo renders as exactly the original
characters (``_hugo_escape``). The only shortcodes left in a page are the
exporter's own ``relref``s, added after escaping, over paths it built itself.

Front matter is YAML through the shared ``_yaml_scalar``, so no title can
end it early; custom fields are under ``params:``, Hugo's own place for
them. The README written beside ``content/`` documents every field.

Asked for (``starter_site``), a small site to view the content with is
written beside ``content/`` as well -- ``hugo_starter`` -- leaving
``content/`` itself exactly what it is without one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from connections_export.derive.combine import CombineReport
from connections_export.derive.model import (
    DerivedForumTopic,
    DerivedItem,
    DerivedPage,
    DerivedWiki,
    Interchange,
)
from connections_export.ingest._bodies import (
    BlockCounts,
    BodyTarget,
    attribute_sentinels,
    html_mode_for,
    html_mode_note,
    render_body,
    rewrite_tree,
)
from connections_export.ingest._combined import combined_section, several
from connections_export.ingest._markdown import (
    BRACKET_TEXT,
    code_span,
    escape_inline,
    html_to_text,
    quote_block,
    readable_time,
    to_markdown,
)
from connections_export.ingest.hugo_starter import readme_section, write_starter_site
from connections_export.ingest.obsidian import (
    BlobReader,
    _ext_for,
    _safe,
    _sniff,
    _yaml_scalar,
)
from connections_export.interchange.filenames import unreserve

_ASSET = "hugo-asset:"  # an image copied into the bundle, by position
_LINK = "hugo-link:"  # a link to another exported item, by id
_FILELINK = "hugo-file:"  # a link to a library file, by file id
_MISSING = "hugo-missing:"  # an image that was never captured

_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 60

#: File extensions Hugo reads as CONTENT wherever they sit under `content/`
#: -- Markdown, HTML, AsciiDoc, Pandoc, Org, reStructuredText -- plus the
#: template extension of a content adapter (`_content.gotmpl`), which Hugo
#: executes. A captured attachment called `notes.md` or `_content.gotmpl`
#: dropped into a bundle would be rendered, or run, as part of the site; it is
#: written with `.txt` appended instead, and published as the file it is.
_CONTENT_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".mdown",
        ".mkd",
        ".mkdn",
        ".html",
        ".htm",
        ".xhtml",
        ".adoc",
        ".asciidoc",
        ".ad",
        ".pandoc",
        ".pdc",
        ".org",
        ".rst",
        ".gotmpl",
    }
)

#: Top-level sections and their titles, in the order they are written.
_SECTIONS = {
    "wikis": "Wikis",
    "blogs": "Blogs",
    "forums": "Forums",
    "files": "Files",
    "highlights": "Highlights",
}


@dataclass
class HugoStats:
    wikis: int = 0
    pages: int = 0
    blogs: int = 0
    posts: int = 0
    forums: int = 0
    topics: int = 0
    replies: int = 0
    libraries: int = 0
    files: int = 0
    highlights: int = 0
    highlight_pages: int = 0
    assets_written: int = 0
    assets_missing: int = 0
    #: How bodies were written (`_bodies.HTML_MODES`), and how many of their
    #: blocks went out as Markdown and as HTML.
    html_mode: str = "mixed"
    markdown_blocks: int = 0
    html_blocks: int = 0
    #: Whether the starter site (`hugo_starter`) was written beside `content/`.
    starter_site: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def items(self) -> int:
        """Every content page written for a piece of content, whatever its app."""
        return self.pages + self.posts + self.topics + self.highlight_pages


# --- escaping ------------------------------------------------------------------

_SHORTCODE_OPEN = re.compile(r"\{\{([<%])")
#: Written between the braces of an opener with no closer, which Hugo
#: cannot parse and would stop the build on (see `_hugo_escape`).
_WORD_JOINER = "\u2060"


def _hugo_escape(text: str) -> str:
    """`text` with no shortcode in it, rendering back to exactly `text`.

    Hugo finds shortcodes by their delimiters in the raw file, before any
    Markdown is parsed -- in prose, in code and in HTML alike -- so escaping
    has to work at that level too. Each ``{{<``/``{{%`` is paired with the
    first matching ``>}}``/``%}}`` after it and written as Hugo's comment
    form, ``{{</* ... */>}}``: Hugo drops the ``/*`` and ``*/`` and emits the
    rest as text, which is the original. An opener with no closer at all
    has no comment form; Hugo would refuse to build the page, so an
    invisible word joiner goes between its braces instead.
    """
    out: list[str] = []
    position = 0
    while True:
        match = _SHORTCODE_OPEN.search(text, position)
        if match is None:
            out.append(text[position:])
            return "".join(out)
        kind = match.group(1)
        closer = (">" if kind == "<" else "%") + "}}"
        end = text.find(closer, match.end())
        out.append(text[position : match.start()])
        if end == -1:
            out.append("{" + _WORD_JOINER + "{" + kind)
            position = match.end()
        else:
            out.append("{{" + kind + "/*" + text[match.end() : end] + "*/" + closer)
            position = end + len(closer)


def _text(value: str | None) -> str:
    """Content on one line of a page: Markdown- and shortcode-inert. Braces
    are escaped too: Hugo's Markdown reads ``{.class onclick=...}`` after a
    heading as its attributes."""
    return _hugo_escape(escape_inline(value, braces=True))


def _code(value: str) -> str:
    return _hugo_escape(code_span(value))


def _relref(path: str) -> str:
    """The exporter's own shortcode. `path` is built from slugs and fixed
    names only (`[a-z0-9-/._]`), so it cannot end the quoted argument."""
    return '{{< relref "' + path + '" >}}'


# --- names -----------------------------------------------------------------------


def _slug(value: str | None, fallback: str) -> str:
    """A folder name for a section or bundle: lower-case ASCII, which is what
    Hugo turns a path into for its URL anyway, so the folder, the URL and a
    link built from either all agree -- and a name that can hold no path
    separator, dot or device name."""
    result = _SLUG.sub("-", (value or "").lower()).strip("-")[:_MAX_SLUG].strip("-")
    if not result:
        result = _SLUG.sub("-", fallback.lower()).strip("-")[:_MAX_SLUG].strip("-") or "item"
    return unreserve(result)


def _unique(name: str, used: set[str]) -> str:
    candidate, index = name, 2
    while candidate in used:
        candidate = f"{name}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def _resource_name(name: str) -> str:
    """A page resource's file name: safe on every filesystem (`_safe`),
    lower-case and without spaces so its URL is its name, and never one Hugo
    would read as content (`_CONTENT_EXTENSIONS`)."""
    cleaned = re.sub(r"\s+", "-", _safe(name, "file")).lower()
    if Path(cleaned).suffix in _CONTENT_EXTENSIONS:
        cleaned += ".txt"
    return cleaned


class _Bundle:
    """A page bundle's folder, and the files copied into it -- each blob once,
    each under a name unique in the folder."""

    def __init__(self, directory: Path, blob_reader: BlobReader, stats: HugoStats) -> None:
        self.directory = directory
        self._read = blob_reader
        self._stats = stats
        self._by_hash: dict[str, str] = {}
        self._used: set[str] = set()

    def store(
        self, *, blob_hash: str | None, preferred_name: str, content_type: str | None
    ) -> str | None:
        if not blob_hash:
            return None
        if blob_hash in self._by_hash:
            return self._by_hash[blob_hash]
        data = self._read(blob_hash)
        if data is None:
            self._stats.assets_missing += 1
            return None
        name = _safe(preferred_name, "file")
        sniffed = _sniff(data)
        if sniffed:
            name = Path(name).stem + sniffed
        elif "." not in Path(name).name:
            name += _ext_for(content_type=content_type, href=preferred_name)
        name = _resource_name(name)
        stem, dot, suffix = name.rpartition(".")
        base = stem if dot else name
        candidate, index = name, 1
        while candidate in self._used:
            candidate = f"{base}-{index}{('.' + suffix) if dot else ''}"
            index += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / candidate).write_bytes(data)
        self._used.add(candidate)
        self._by_hash[blob_hash] = candidate
        self._stats.assets_written += 1
        return candidate


# --- bodies ------------------------------------------------------------------------


@dataclass
class _Site:
    """What every body needs to resolve its links: each exported item's page
    file (from the root of `content/`) and each captured library file's
    library page and name."""

    page_files: dict[str, str]
    file_targets: dict[str, tuple[str, str]]
    mode: str
    counts: BlockCounts


class _Body:
    """One body's images and links, pointed at sentinels on the parsed tree
    and resolved after conversion -- the same for a Markdown block and an HTML
    block."""

    def __init__(self, item: DerivedItem, bundle: _Bundle, site: _Site) -> None:
        self.bundle = bundle
        self.site = site
        self.embedded: list[str] = []
        self.assets = {}
        for asset in item.assets:
            self.assets[asset.original_href] = asset
            if asset.resolved_url:
                self.assets[asset.resolved_url] = asset
        self.links = {link.original_href: link for link in item.links}

    def image(self, src: str) -> str | None:
        asset = self.assets.get(src)
        if asset is None:
            return None
        if asset.present and asset.blob_hash:
            name = self.bundle.store(
                blob_hash=asset.blob_hash,
                preferred_name=Path(urlsplit(asset.original_href).path).name or "image",
                content_type=None,
            )
            if name:
                self.embedded.append(name)
                return f"{_ASSET}{len(self.embedded) - 1}"
            return f"{_MISSING}{asset.original_href}"
        self.bundle._stats.assets_missing += 1
        return f"{_MISSING}{asset.original_href}"

    def link(self, href: str) -> str | None:
        link = self.links.get(href)
        if not link or link.scope != "in_export":
            return None
        if link.target_page_id in self.site.page_files:
            return f"{_LINK}{link.target_page_id}"
        if link.target_file_id and link.target_file_id in self.site.file_targets:
            return f"{_FILELINK}{link.target_file_id}"
        return None

    def _asset_url(self, position: str) -> str | None:
        if position.isdigit() and int(position) < len(self.embedded):
            return quote(self.embedded[int(position)], safe="-._~")
        return None

    def _page_url(self, item_id: str) -> str | None:
        path = self.site.page_files.get(item_id)
        return _relref(path) if path else None

    def _file_url(self, file_id: str) -> str | None:
        target = self.site.file_targets.get(file_id)
        if target is None:
            return None
        library_page, name = target
        # A resource is published beside its page, so the library page's
        # own URL plus the file's name is the file's URL.
        return _relref(library_page) + quote(name, safe="-._~")

    def finish_markdown(self, markdown: str) -> str:
        """Escaped for shortcodes first -- then the sentinels add the
        exporter's own `relref`s, which must survive."""
        markdown = _hugo_escape(markdown)

        def asset(match: re.Match[str]) -> str:
            url = self._asset_url(match.group(2))
            if url is None:
                return _code(f"[image not captured: {match.group(2)}]")
            return f"![{match.group(1)}]({url})"

        def page(match: re.Match[str]) -> str:
            url = self._page_url(unquote(match.group(2)))
            # A sentinel no rewrite produced: its text, and no link -- a
            # `relref` to nothing would fail the build.
            return f"[{match.group(1)}]({url})" if url else match.group(1)

        def file(match: re.Match[str]) -> str:
            url = self._file_url(unquote(match.group(2)))
            if url is None:
                return match.group(1)
            text = match.group(1) or _text(self.site.file_targets[unquote(match.group(2))][1])
            return f"[{text}]({url})"

        markdown = re.sub(
            r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_ASSET) + r"([0-9]{1,9})\)", asset, markdown
        )
        markdown = re.sub(
            r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_MISSING) + r"([^)]+)\)",
            lambda match: _code(f"[image not captured: {match.group(2)}]"),
            markdown,
        )
        markdown = re.sub(
            r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_LINK) + r"([^)]+)\)", page, markdown
        )
        return re.sub(
            r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_FILELINK) + r"([^)]+)\)", file, markdown
        )

    def finish_html(self, html: str) -> str:
        """An HTML block, escaped for shortcodes, its sentinel attributes then
        resolved. A `relref` inside an attribute holds double quotes; Hugo
        replaces the shortcode before the HTML is ever parsed."""
        html = _hugo_escape(html)
        html = attribute_sentinels(html, _ASSET, lambda _a, v: self._asset_url(v) or "#")
        html = attribute_sentinels(html, _LINK, lambda _a, v: self._page_url(v) or "#")
        return attribute_sentinels(html, _FILELINK, lambda _a, v: self._file_url(v) or "#")

    def render(self, content_html: str | None) -> str:
        target = BodyTarget(
            convert=lambda html: to_markdown(
                html, heading_style="ATX", bullets="-", escape_braces=True
            ),
            finish_markdown=self.finish_markdown,
            finish_html=self.finish_html,
            missing_prefix=_MISSING,
        )
        return render_body(
            content_html,
            self.site.mode,
            rewrite=lambda tree: rewrite_tree(tree, image=self.image, link=self.link),
            target=target,
            counts=self.site.counts,
        ).strip()


# --- front matter --------------------------------------------------------------------


def _date(value: str | None) -> str | None:
    """`value` when Hugo can read it as a date; a value it cannot would stop
    the build, so it is left out (the source keeps it)."""
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_yaml_scalar(str(entry)) for entry in value) + "]"
    return _yaml_scalar(str(value))


def _front_matter(
    title: str,
    *,
    date: str | None = None,
    lastmod: str | None = None,
    tags: list[str] | None = None,
    weight: int | None = None,
    params: dict,
) -> str:
    lines = ["---", f"title: {_yaml_scalar(title)}"]
    if _date(date):
        lines.append(f"date: {_yaml_scalar(date)}")
    if _date(lastmod):
        lines.append(f"lastmod: {_yaml_scalar(lastmod)}")
    if tags:
        lines.append(f"tags: {_yaml_value(tags)}")
    if weight:
        lines.append(f"weight: {weight}")
    kept = {key: value for key, value in params.items() if value not in (None, "", [], ())}
    if kept:
        lines.append("params:")
        lines.extend(f"  {key}: {_yaml_value(value)}" for key, value in kept.items())
    lines.extend(["---", ""])
    return "\n".join(lines)


def _item_params(item: DerivedItem, kind: str, **extra) -> dict:
    return {
        "kind": kind,
        "source_url": item.provenance.source_url or item.alternate_url,
        "source_id": item.provenance.hcl_id,
        "author": item.author,
        "contributors": [name for name in item.contributors if name != item.author],
        **extra,
    }


# --- page parts -------------------------------------------------------------------------


def _comments(item: DerivedItem) -> str:
    comments = getattr(item, "comments", None) or []
    if not comments:
        return ""
    children: dict[str | None, list] = {}
    known = {comment.id for comment in comments}
    for comment in comments:
        parent = comment.parent_comment_id if comment.parent_comment_id in known else None
        children.setdefault(parent, []).append(comment)
    lines = ["## Comments", ""]

    def render(parent_id: str | None, depth: int) -> None:
        for comment in children.get(parent_id, []):
            body = _text(html_to_text(comment.content_html))
            when = f" ({_text(comment.created)})" if comment.created else ""
            lines.append(f"{'  ' * depth}- **{_text(comment.author or 'Unknown')}**{when}: {body}")
            render(comment.id, depth + 1)

    render(None, 0)
    return "\n".join(lines)


def _attachments(item: DerivedItem, bundle: _Bundle, heading: str = "## Attachments") -> str:
    attachments = getattr(item, "attachments", None) or []
    if not attachments:
        return ""
    lines = [heading, ""]
    for attachment in attachments:
        label = attachment.filename or attachment.id
        stored = None
        if attachment.asset.present and attachment.asset.blob_hash:
            stored = bundle.store(
                blob_hash=attachment.asset.blob_hash,
                preferred_name=label,
                content_type=attachment.content_type,
            )
        if stored:
            lines.append(f"- [{_text(label)}]({quote(stored, safe='-._~')})")
            continue
        if not attachment.asset.present:
            bundle._stats.assets_missing += 1  # a present one `store` already counted
        lines.append(f"- {_code(f'[not captured: {label}]')}")
    return "\n".join(lines)


def _replies(topic: DerivedForumTopic, bundle: _Bundle, site: _Site, stats: HugoStats) -> str:
    """The reply tree beneath a topic, each reply a heading at its depth with
    its own body, converted like the topic's -- images and all."""
    if not topic.replies:
        return ""
    lines: list[str] = ["## Replies", ""]
    rendered: set[str] = set()

    def render(reply_id: str, depth: int) -> None:
        reply = topic.replies.get(reply_id)
        if reply is None or reply_id in rendered:
            return
        rendered.add(reply_id)
        stats.replies += 1
        who = _text(reply.author or "Unknown")
        when = f" · {_text(readable_time(reply.created))}" if reply.created else ""
        answer = " · *answer*" if "answer" in (reply.flags or []) else ""
        # One heading level for every reply: the quote depth below shows the
        # nesting, and headings shrinking level by level only made deep
        # replies unreadable.
        block = [f"### {who}{when}{answer}", ""]
        body = _Body(reply, bundle, site).render(reply.content_html)
        block.extend([body if body else "*(no text)*", ""])
        attachments = _attachments(reply, bundle, heading="**Attachments**")
        if attachments:
            block.extend([attachments, ""])
        # A reply to a reply goes one blockquote deeper per level.
        lines.extend(quote_block("\n".join(block), depth))
        for child in reply.child_ids:
            render(child, depth + 1)

    for reply_id in topic.reply_ids:
        render(reply_id, 0)
    # A reply whose parent never made it into the tree is still a reply.
    for reply_id in topic.replies:
        render(reply_id, 0)
    return "\n".join(lines).rstrip()


def _page(front_matter: str, *parts: str) -> str:
    body = "\n\n".join(part.strip("\n") for part in parts if part and part.strip())
    return front_matter + (body + "\n" if body else "")


# --- layout -------------------------------------------------------------------------------


@dataclass
class _Planned:
    item: DerivedItem
    directory: Path  # the bundle folder
    page_file: str  # from the root of content/, as `relref` takes it
    weight: int = 0
    branch: bool = False


def _wiki_order(wiki: DerivedWiki) -> list[tuple[DerivedPage, DerivedPage | None, int]]:
    """Every page of `wiki` once, parents before children, with its parent
    and its 1-based position among its siblings. A page whose parent is not
    in the export stands at the top level rather than being lost."""
    pages = wiki.pages
    ordered: list[tuple[DerivedPage, DerivedPage | None, int]] = []
    seen: set[str] = set()

    def visit(page_id: str, parent: DerivedPage | None, weight: int) -> None:
        page = pages.get(page_id)
        if page is None or page_id in seen:
            return
        seen.add(page_id)
        ordered.append((page, parent, weight))
        children = [child for child in page.child_ids if child in pages and child not in seen]
        for index, child in enumerate(children, start=1):
            visit(child, page, index)

    roots = [page_id for page_id in wiki.root_page_ids if page_id in pages]
    roots += [
        page_id
        for page_id, page in pages.items()
        if page_id not in roots and (not page.parent_id or page.parent_id not in pages)
    ]
    for index, page_id in enumerate(roots, start=1):
        visit(page_id, None, index)
    # Anything left sits in a cycle of parents; it still gets a page.
    for page_id in pages:
        if page_id not in seen:
            visit(page_id, None, len(roots) + 1)
    return ordered


def _ordered(ids: list[str], items: dict) -> list:
    result = [items[i] for i in ids if i in items]
    result += [item for i, item in items.items() if i not in ids]
    return result


def write_hugo_content(
    interchange: Interchange,
    blob_reader: BlobReader,
    out_dir: Path | str,
    *,
    html_mode: str = "mixed",
    combined: CombineReport | None = None,
    starter_site: bool = False,
) -> HugoStats:
    """Write `interchange` as Hugo content under `out_dir`: a `content/`
    tree and a `README.md` beside it describing the fields. `html_mode` is how
    bodies are written (`_bodies.HTML_MODES`). `combined` is the report of
    combining several archives into `interchange` (`derive.combine`): every
    page then names the archive it came from, and the README lists them.
    `starter_site` also writes a `hugo.toml`, templates and a stylesheet
    beside `content/`, so the export can be viewed with `hugo server`."""
    mode = html_mode_for("hugo", html_mode)
    combined = several(combined)

    def origin(entity_id: str) -> dict:
        # Only when several archives went in, so a single archive's content
        # is exactly what it always was.
        return {"source_archive": combined.origins.get(entity_id)} if combined else {}

    root = Path(out_dir)
    content = root / "content"
    stats = HugoStats(html_mode=mode, starter_site=starter_site)
    counts = BlockCounts()
    site = _Site(page_files={}, file_targets={}, mode=mode, counts=counts)

    def page_file(directory: Path, name: str) -> str:
        return "/" + (directory / name).relative_to(content).as_posix()

    # Plan every path first, so a link can point at a page written later.
    wikis: list[tuple[DerivedWiki, Path, list[_Planned]]] = []
    used_wikis: set[str] = set()
    for wiki in interchange.wikis:
        wiki_dir = content / "wikis" / _unique(_slug(wiki.title or wiki.label, wiki.id), used_wikis)
        planned: dict[str, _Planned] = {}
        used_in: dict[Path, set[str]] = {}
        order = _wiki_order(wiki)
        with_children = {parent.id for _, parent, _ in order if parent is not None}
        for page, parent, weight in order:
            parent_dir = planned[parent.id].directory if parent is not None else wiki_dir
            slug = _unique(
                _slug(page.title or page.label, page.id), used_in.setdefault(parent_dir, set())
            )
            directory = parent_dir / slug
            branch = page.id in with_children
            planned[page.id] = _Planned(
                page,
                directory,
                page_file(directory, "_index.md" if branch else "index.md"),
                weight,
                branch,
            )
            site.page_files[page.id] = planned[page.id].page_file
        wikis.append((wiki, wiki_dir, list(planned.values())))

    def plan_leaves(section: str, containers, children_of, title_of):
        planned_containers = []
        used: set[str] = set()
        for container in containers:
            directory = content / section / _unique(_slug(title_of(container), container.id), used)
            used_items: set[str] = set()
            leaves = []
            for item in children_of(container):
                leaf_dir = directory / _unique(_slug(item.title, item.id), used_items)
                leaves.append(_Planned(item, leaf_dir, page_file(leaf_dir, "index.md")))
                site.page_files[item.id] = leaves[-1].page_file
            planned_containers.append((container, directory, leaves))
        return planned_containers

    blogs = plan_leaves(
        "blogs",
        interchange.blogs,
        lambda blog: _ordered(blog.post_ids, blog.posts),
        lambda blog: blog.title or blog.handle,
    )
    forums = plan_leaves(
        "forums",
        interchange.forums,
        lambda forum: _ordered(forum.topic_ids, forum.topics),
        lambda forum: forum.title,
    )
    highlights = plan_leaves(
        "highlights",
        interchange.rich_content,
        lambda area: _ordered(area.page_ids, area.pages),
        lambda area: area.community_title or area.title,
    )

    # Library files next, so a body link to one resolves to the copied file.
    libraries = []
    used_libraries: set[str] = set()
    for library in interchange.file_libraries:
        directory = (
            content
            / "files"
            / _unique(_slug(library.community_title or library.title, library.id), used_libraries)
        )
        bundle = _Bundle(directory, blob_reader, stats)
        for file_id, derived in library.files.items():
            asset = derived.asset
            if asset and asset.present and asset.blob_hash:
                name = bundle.store(
                    blob_hash=asset.blob_hash,
                    preferred_name=derived.name or derived.title or file_id,
                    content_type=derived.content_type,
                )
                if name:
                    site.file_targets[file_id] = (page_file(directory, "_index.md"), name)
        libraries.append((library, directory))

    # Then the pages themselves.
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_section(name: str) -> None:
        write(
            content / name / "_index.md",
            _front_matter(_SECTIONS[name], weight=list(_SECTIONS).index(name) + 1, params={}),
        )

    if wikis:
        write_section("wikis")
    for position, (wiki, wiki_dir, planned) in enumerate(wikis, start=1):
        stats.wikis += 1
        write(
            wiki_dir / "_index.md",
            _front_matter(
                wiki.title or wiki.label,
                weight=position,
                params={
                    "kind": "wiki",
                    "source_url": wiki.alternate_url,
                    "community": wiki.community_title,
                    "page_count": len(wiki.pages),
                    **origin(wiki.id),
                },
            ),
        )
        for entry in planned:
            page = entry.item
            bundle = _Bundle(entry.directory, blob_reader, stats)
            body = _Body(page, bundle, site).render(page.content_html)
            front = _front_matter(
                page.title or page.label or page.id,
                date=page.created,
                lastmod=page.modified,
                tags=page.tags,
                weight=entry.weight,
                params=_item_params(
                    page,
                    "wiki_page",
                    label=page.label,
                    wiki=wiki.title or wiki.label,
                    community=wiki.community_title,
                    comment_count=len(page.comments),
                    attachment_count=len(page.attachments),
                    version_count=len(page.versions),
                    **origin(page.id),
                ),
            )
            write(
                entry.directory / ("_index.md" if entry.branch else "index.md"),
                _page(front, body, _attachments(page, bundle), _comments(page)),
            )
            stats.pages += 1

    if blogs:
        write_section("blogs")
    for position, (blog, directory, posts) in enumerate(blogs, start=1):
        stats.blogs += 1
        write(
            directory / "_index.md",
            _front_matter(
                blog.title or blog.handle or blog.id,
                weight=position,
                params={
                    "kind": "ideation_blog" if blog.kind == "ideation_blog" else "blog",
                    "source_url": blog.alternate_url,
                    "community": blog.community_title,
                    "post_count": len(blog.posts),
                    **origin(blog.id),
                },
            ),
        )
        for entry in posts:
            post = entry.item
            bundle = _Bundle(entry.directory, blob_reader, stats)
            body = _Body(post, bundle, site).render(post.content_html)
            front = _front_matter(
                post.title or post.id,
                date=post.created,
                lastmod=post.modified,
                tags=post.tags,
                params=_item_params(
                    post,
                    "blog_post",
                    blog=blog.title or blog.handle,
                    community=blog.community_title,
                    comment_count=len(post.comments),
                    **origin(post.id),
                ),
            )
            write(entry.directory / "index.md", _page(front, body, _comments(post)))
            stats.posts += 1

    if forums:
        write_section("forums")
    for position, (forum, directory, topics) in enumerate(forums, start=1):
        stats.forums += 1
        write(
            directory / "_index.md",
            _front_matter(
                forum.title or forum.id,
                weight=position,
                params={
                    "kind": "forum",
                    "source_url": forum.alternate_url,
                    "community": forum.community_title,
                    "topic_count": len(forum.topics),
                    **origin(forum.id),
                },
            ),
        )
        for entry in topics:
            topic = entry.item
            bundle = _Bundle(entry.directory, blob_reader, stats)
            body = _Body(topic, bundle, site).render(topic.content_html)
            attachments = _attachments(topic, bundle)
            replies = _replies(topic, bundle, site, stats)
            front = _front_matter(
                topic.title or topic.id,
                date=topic.created,
                lastmod=topic.modified,
                tags=topic.tags,
                params=_item_params(
                    topic,
                    "forum_topic",
                    forum=forum.title,
                    community=forum.community_title,
                    flags=topic.flags,
                    reply_count=len(topic.replies),
                    **origin(topic.id),
                ),
            )
            write(entry.directory / "index.md", _page(front, body, attachments, replies))
            stats.topics += 1

    if libraries:
        write_section("files")
    for position, (library, directory) in enumerate(libraries, start=1):
        stats.libraries += 1
        lines = []
        for derived in _ordered(library.file_ids, library.files):
            stats.files += 1
            label = derived.name or derived.title or derived.id
            target = site.file_targets.get(derived.id)
            if target:
                details = [_text(part) for part in (derived.author, derived.modified) if part]
                suffix = f" — {', '.join(details)}" if details else ""
                lines.append(f"- [{_text(label)}]({quote(target[1], safe='-._~')}){suffix}")
            elif derived.excluded_by_author_filter:
                # Never fetched, on purpose: not a gap in the capture.
                lines.append(f"- {_code(f'[outside the author filter: {label}]')}")
            else:
                if not (derived.asset and derived.asset.present):
                    stats.assets_missing += 1  # a present one `store` already counted
                lines.append(f"- {_code(f'[not captured: {label}]')}")
        front = _front_matter(
            library.title or library.community_title or library.id,
            weight=position,
            params={
                "kind": "file_library",
                "source_url": library.alternate_url,
                "community": library.community_title,
                "file_count": len(library.files),
                **origin(library.id),
            },
        )
        write(directory / "_index.md", _page(front, "\n".join(lines)))

    if highlights:
        write_section("highlights")
    for position, (area, directory, pages) in enumerate(highlights, start=1):
        stats.highlights += 1
        write(
            directory / "_index.md",
            _front_matter(
                area.community_title or area.title or area.id,
                weight=position,
                params={
                    "kind": "highlights",
                    "source_url": area.alternate_url,
                    "community": area.community_title,
                    "placed": area.placed,
                    "initialized": area.initialized,
                    **origin(area.id),
                },
            ),
        )
        for weight, entry in enumerate(pages, start=1):
            page = entry.item
            bundle = _Bundle(entry.directory, blob_reader, stats)
            body = _Body(page, bundle, site).render(page.content_html)
            front = _front_matter(
                page.title or page.id,
                date=page.created,
                lastmod=page.modified,
                weight=weight,
                params=_item_params(
                    page,
                    "highlights_page",
                    community=area.community_title,
                    version_label=page.version_label,
                    **origin(page.id),
                ),
            )
            write(entry.directory / "index.md", _page(front, body))
            stats.highlight_pages += 1

    stats.markdown_blocks, stats.html_blocks = counts.markdown, counts.html
    root.mkdir(parents=True, exist_ok=True)
    if starter_site:
        write_starter_site(root, title=_site_title(interchange, combined), html_mode=mode)
    (root / "README.md").write_text(_readme(interchange, stats, combined), encoding="utf-8")
    return stats


def _site_title(interchange: Interchange, combined: CombineReport | None) -> str:
    """The starter site's title: the community, when everything exported is
    from one, as it usually is; otherwise a plain description."""
    containers = [
        *interchange.wikis,
        *interchange.blogs,
        *interchange.forums,
        *interchange.file_libraries,
        *interchange.rich_content,
    ]
    communities = {c.community_title for c in containers if c.community_title}
    if len(communities) == 1:
        return communities.pop()
    if combined is not None:
        return f"Connections content from {len(combined.archives)} archives"
    return "Connections content"


# --- README -------------------------------------------------------------------------------

#: Every front matter field the exporter writes: (field, where, what).
_FIELDS = (
    ("`title`", "every page", "The item's title as it was in Connections."),
    (
        "`date`",
        "items",
        "When the item was created (ISO 8601); left out when Hugo could not read it.",
    ),
    ("`lastmod`", "items", "When it was last modified."),
    ("`tags`", "pages, posts, topics", "The item's tags, as Hugo's built-in `tags` taxonomy."),
    (
        "`weight`",
        "wiki pages, Highlights pages, sections",
        "Order among siblings (1 = first), so a wiki's page tree keeps its order.",
    ),
    (
        "`params.kind`",
        "every page",
        "What the page is: `wiki`, `wiki_page`, `blog`, `ideation_blog`, `blog_post`, "
        "`forum`, `forum_topic`, `file_library`, `highlights`, `highlights_page`.",
    ),
    (
        "`params.source_url`",
        "every page, when known",
        "The item's address on the Connections deployment it was captured from.",
    ),
    ("`params.source_id`", "items", "The item's identifier on that deployment."),
    ("`params.community`", "every page, when known", "The community the item belongs to."),
    ("`params.author`", "items", "Who created it."),
    ("`params.contributors`", "items", "Others who edited it."),
    (
        "`params.comment_count`",
        "wiki pages, posts",
        "How many comments are listed beneath the body.",
    ),
    ("`params.label`", "wiki pages", "The page's short name in its wiki."),
    (
        "`params.wiki`, `params.blog`, `params.forum`",
        "items",
        "The title of the container the item came from.",
    ),
    (
        "`params.attachment_count`, `params.version_count`",
        "wiki pages",
        "How many attachments it has, and how many versions were recorded.",
    ),
    (
        "`params.flags`",
        "forum topics",
        "`pinned`, `locked`, `question`, `answered`, as set on the topic.",
    ),
    ("`params.reply_count`", "forum topics", "How many replies are rendered beneath it."),
    (
        "`params.page_count`, `params.post_count`, `params.topic_count`, `params.file_count`",
        "sections",
        "How many items the container holds.",
    ),
    (
        "`params.placed`, `params.initialized`",
        "Highlights sections",
        "Rich Content widgets placed on the community, and how many of those held content.",
    ),
    ("`params.version_label`", "Highlights pages", "The page's version in Connections."),
)


#: Written only when several archives were combined into one export.
_COMBINED_FIELD = (
    "`params.source_archive`",
    "every page, when several archives were combined",
    "The archive this page's copy came from (the most recent capture of it).",
)


def _field_table(combined: bool = False) -> str:
    rows = ["| Field | Where | What it holds |", "| --- | --- | --- |"]
    fields = (*_FIELDS, _COMBINED_FIELD) if combined else _FIELDS
    rows += [f"| {name} | {where} | {what} |" for name, where, what in fields]
    return "\n".join(rows) + "\n"


def _readme(
    interchange: Interchange, stats: HugoStats, combined: CombineReport | None = None
) -> str:
    source = f" of {code_span(interchange.base_url)}" if interchange.base_url else ""
    lines = [
        "# Hugo content",
        "",
        f"Content captured from HCL Connections{source}, written for a "
        "[Hugo](https://gohugo.io/) site of your own: "
        f"{stats.wikis} wiki(s) with {stats.pages} page(s), {stats.blogs} blog(s) with "
        f"{stats.posts} post(s), {stats.forums} forum(s) with {stats.topics} topic(s), "
        f"{stats.libraries} file librar(ies) with {stats.files} file(s), "
        f"{stats.highlight_pages} Highlights page(s); {stats.assets_written} file(s) copied.",
        "",
        "Hugo is an independent third-party application, not part of this tool. This "
        "exporter is provided for convenience and is not an endorsement; Hugo is governed "
        "by its own licence and terms.",
        "",
        "## Using it",
        "",
        (
            "`content/` is the content, and all of it. The `hugo.toml`, `layouts/` and "
            "`static/` beside it are the optional starter site (below), for viewing it; "
            "an existing site brings its own. "
            if stats.starter_site
            else "This folder is content only — no layouts, no theme, no `hugo.toml`. Your "
            "site brings those. "
        )
        + "Copy or merge `content/` into your site's `content/` folder, at its "
        "root: links between pages are `relref` shortcodes to paths from the root of "
        "`content/` (`/wikis/…/index.md`), which Hugo resolves through your own URL "
        "settings and checks at build time. Moved into a subfolder, those paths would no "
        "longer match.",
        "",
        "- `content/wikis/<wiki>/` — a section per wiki. A page with sub-pages is a "
        "branch bundle (`<page>/_index.md`), a page without is a leaf bundle "
        "(`<page>/index.md`); `weight` keeps the wiki's order.",
        "- `content/blogs/<blog>/`, `content/forums/<forum>/`, "
        "`content/highlights/<community>/` — a leaf bundle per post, topic and page. A "
        "topic's replies are rendered beneath it as nested headings.",
        "- `content/files/<library>/` — the library's files, listed in its `_index.md`, "
        "with the files themselves in the same folder.",
        "- Images and attachments sit inside the bundle of the page that shows them "
        "(page resources), linked by name. A file whose name Hugo would read as content "
        "(`.md`, `.html`, `.gotmpl` …) has `.txt` added to its name.",
        "- Anything never captured is shown as a visible `[not captured: …]` marker.",
        "",
        "## Page content",
        "",
        html_mode_note(stats.html_mode, stats.markdown_blocks, stats.html_blocks),
        "",
    ]
    if stats.html_mode != "markdown":
        lines += [
            "Hugo leaves raw HTML out of a page unless the site allows it. For the HTML "
            "parts of these pages to show, your site configuration needs:",
            "",
            "```toml",
            "[markup.goldmark.renderer]",
            "  unsafe = true",
            "```",
            "",
            "Whether to allow it is your site's decision. Without it, those parts are "
            "left out of the built pages; with `markdown` mode no HTML is written at all.",
            "",
        ]
    lines += [
        "Text that looks like a Hugo shortcode (`{{< … >}}`, `{{% … %}}`) is written "
        "in Hugo's comment form, `{{</* … */>}}`, which Hugo shows as the original "
        "characters instead of running it. An opener with no closer at all gets an "
        "invisible word joiner between its braces, as Hugo would otherwise refuse to "
        "build the page.",
        "",
        "## Front matter",
        "",
        "YAML. Custom fields are under `params:`, which Hugo (0.123 and later) merges "
        "into `.Params` — a template reads `.Params.source_url`. On an older Hugo they "
        "are under `.Params.params`.",
        "",
        _field_table(combined is not None),
    ]
    if stats.starter_site:
        lines += readme_section()
    if combined is not None:
        lines += [*combined_section(combined, _text), ""]
    if stats.assets_missing:
        lines.append(
            f"{stats.assets_missing} referenced asset(s) were not captured — shown as "
            "visible gaps, not dropped."
        )
        lines.append("")
    return "\n".join(lines)


def from_source(
    source, out_dir: Path | str, *, html_mode: str = "mixed", starter_site: bool = False
) -> HugoStats:
    """Write Hugo content from any compatible model source -- with the
    starter site beside it when `starter_site` is set."""
    interchange = source.get_model()
    if interchange is None:
        raise ValueError("nothing to ingest: the source holds no derivable content")

    def blob_reader(blob_hash: str) -> bytes | None:
        result = source.get_blob(blob_hash)
        return result[0] if result is not None else None

    return write_hugo_content(
        interchange,
        blob_reader,
        out_dir,
        html_mode=html_mode,
        combined=getattr(source, "combine_report", None),
        starter_site=starter_site,
    )
