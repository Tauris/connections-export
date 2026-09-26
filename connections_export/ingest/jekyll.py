"""A reference ingester that writes a conventional Jekyll Markdown site.

The output is deliberately a site fragment rather than a complete site:
``_posts/`` contains dated Markdown posts and body images are placed below
``assets/images/imported/``.  A site's own ``_config.yml`` remains its
responsibility, including ``url`` and ``baseurl``. Each post names
``layout: post``, which Jekyll's starter site and most themes provide, so a
post shows its title, author and a UTF-8 page head out of the box.

Liquid runs over every page before kramdown does, and the content is its
authors' -- so ``{{ site | jsonify }}`` in a post would publish the site's
config and every other page when the site is built. ``render_with_liquid:
false`` would stop that, but only on Jekyll 4, and GitHub Pages builds with
Jekyll 3; it would also stop the ``relative_url`` filter this exporter's own
links need to honour ``baseurl``. Instead every piece of content is escaped
for Liquid where it is written (``_liquid_escape``), and the only Liquid left
in a page is the exporter's own ``relative_url`` expressions over paths it
built and percent-encoded itself (``_liquid_url``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from connections_export.derive.combine import CombineReport
from connections_export.derive.model import DerivedItem, Interchange
from connections_export.ingest._bodies import (
    BlockCounts,
    BodyTarget,
    attribute_sentinels,
    html_mode_for,
    render_body,
    rewrite_tree,
)
from connections_export.ingest._combined import combined_section, several
from connections_export.ingest._markdown import (
    BRACKET_TEXT,
    code_span,
    comment_lines,
    escape_inline,
    paragraphs_from_plain_text,
    quote_block,
    readable_time,
)
from connections_export.ingest.obsidian import (
    BlobReader,
    _ext_for,
    _md,
    _safe,
    _sniff,
    _yaml_scalar,
)

_ASSET = "jekyll-asset:"
_LINK = "jekyll-link:"
_MISSING = "jekyll-missing:"
_FILELINK = "jekyll-file:"  # a href pointing at a library file, by file id
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass
class JekyllStats:
    posts: int = 0
    libraries: int = 0
    files: int = 0
    assets_written: int = 0
    assets_missing: int = 0
    notes: list[str] | None = None
    #: How bodies were written (`_bodies.HTML_MODES`), and how many of their
    #: blocks went out as Markdown and as HTML.
    html_mode: str = "mixed"
    markdown_blocks: int = 0
    html_blocks: int = 0


class _AssetStore:
    def __init__(self, assets_dir: Path, blob_reader: BlobReader, stats: JekyllStats) -> None:
        self._dir = assets_dir
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

        name = _safe(preferred_name, "image")
        sniffed = _sniff(data)
        if sniffed:
            name = Path(name).stem + sniffed
        elif "." not in Path(name).name:
            name += _ext_for(content_type=content_type, href=preferred_name)

        stem, dot, suffix = name.rpartition(".")
        base = stem if dot else name
        candidate, index = name, 1
        while candidate in self._used:
            candidate = f"{base}-{index}{('.' + suffix) if dot else ''}"
            index += 1

        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / candidate).write_bytes(data)
        self._used.add(candidate)
        self._by_hash[blob_hash] = candidate
        self._stats.assets_written += 1
        return candidate


def _slug(value: str, fallback: str) -> str:
    result = _SLUG.sub("-", value.lower()).strip("-")
    return result or fallback


def _date_parts(value: str | None) -> tuple[str, str]:
    """Return Jekyll front matter and filename date values deterministically."""
    if value:
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.strftime("%Y-%m-%d %H:%M:%S %z"), parsed.date().isoformat()
        except ValueError:
            pass
    return "1970-01-01 00:00:00 +0000", "1970-01-01"


def _asset_path(name: str) -> str:
    return f"/assets/images/imported/{name}"


#: A `{` Liquid could read as the start of `{{` or `{%`. At the end of a piece
#: too: whatever is written next could begin with the other brace.
_LIQUID_OPENER = re.compile(r"\{(?=[{%]|\Z)")


def _liquid_escape(text: str) -> str:
    """`text` with no Liquid in it, rendering back to exactly `text`.

    Each `{` that could open a tag is written as Liquid printing a `{`, so
    `{{ site }}` reaches the page as those characters -- in prose and inside
    code alike, where Markdown escaping cannot reach. Works on Jekyll 3 and 4.
    """
    return _LIQUID_OPENER.sub('{{ "{" }}', text)


def _liquid_url(path: str) -> str:
    """The exporter's own Liquid: `path` through `relative_url`, so the link
    honours the site's `baseurl`. `path` includes a file name that came from
    content, so it is percent-encoded first -- a quote or a brace in a name can
    then neither end the string literal nor the tag."""
    return "{{ '" + quote(path, safe="/-._~") + "' | relative_url }}"


def _text(value: str | None) -> str:
    """Content on one line of a page: Markdown-, kramdown- and Liquid-inert."""
    return _liquid_escape(escape_inline(value, braces=True))


def _code(value: str) -> str:
    return _liquid_escape(code_span(value))


class _Body:
    """One body's images and links, pointed at sentinels and resolved after
    conversion. A present image is copied and remembered by position, not by
    name: the name goes through the converter's URL encoding, and back out of
    it, otherwise."""

    def __init__(
        self,
        item: DerivedItem,
        store: _AssetStore,
        id_to_permalink: dict[str, str],
        file_targets: dict[str, str] | None,
    ) -> None:
        self.store = store
        self.id_to_permalink = id_to_permalink
        self.files = file_targets or {}
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
            name = self.store.store(
                blob_hash=asset.blob_hash,
                preferred_name=Path(urlsplit(asset.original_href).path).name or "image",
                content_type=None,
            )
            if name:
                self.embedded.append(name)
                return f"{_ASSET}{len(self.embedded) - 1}"
            return f"{_MISSING}{asset.original_href}"
        self.store._stats.assets_missing += 1
        return f"{_MISSING}{asset.original_href}"

    def link(self, href: str) -> str | None:
        link = self.links.get(href)
        if not link or link.scope != "in_export":
            return None
        if link.target_page_id in self.id_to_permalink:
            return f"{_LINK}{link.target_page_id}"
        if link.target_file_id and link.target_file_id in self.files:
            # A body link to a community file, resolved to the copied file.
            return f"{_FILELINK}{link.target_file_id}"
        return None

    def finish_markdown(self, markdown: str) -> str:
        """Escaped for Liquid -- before the sentinels below add the
        exporter's own Liquid, which must survive."""
        markdown = _liquid_escape(markdown)
        embedded, id_to_permalink, files = self.embedded, self.id_to_permalink, self.files

        def replace_asset(match: re.Match[str]) -> str:
            position = int(match.group(2))
            if position >= len(embedded):
                # A body that spelled out a sentinel itself; nothing was stored.
                return _code(f"[image not captured: {match.group(2)}]")
            return f"![{match.group(1)}]({_liquid_url(_asset_path(embedded[position]))})"

        markdown = re.sub(
            r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_ASSET) + r"([0-9]{1,9})\)",
            replace_asset,
            markdown,
        )
        markdown = re.sub(
            r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_MISSING) + r"([^)]+)\)",
            lambda match: _code(f"[image not captured: {match.group(2)}]"),
            markdown,
        )

        def replace_link(match: re.Match[str]) -> str:
            text, target = match.group(1), unquote(match.group(2))
            permalink = id_to_permalink.get(target, target)
            return f"[{text}]({_liquid_url(permalink)})"

        markdown = re.sub(
            r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_LINK) + r"([^)]+)\)", replace_link, markdown
        )

        def replace_file(match: re.Match[str]) -> str:
            text, fid = match.group(1), unquote(match.group(2))
            name = files.get(fid, fid)
            return f"[{text or _text(name)}]({_liquid_url(f'/assets/files/{name}')})"

        return re.sub(
            r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_FILELINK) + r"([^)]+)\)",
            replace_file,
            markdown,
        )

    def finish_html(self, html: str) -> str:
        """An HTML block: escaped for Liquid, then its sentinel attributes
        replaced by the exporter's own `relative_url` Liquid -- which holds no
        double quote, so it stays inside the attribute."""
        html = _liquid_escape(html)

        def asset(_attribute: str, value: str) -> str:
            if value.isdigit() and int(value) < len(self.embedded):
                return _liquid_url(_asset_path(self.embedded[int(value)]))
            return "#"

        def link(_attribute: str, value: str) -> str:
            permalink = self.id_to_permalink.get(value)
            return _liquid_url(permalink) if permalink else "#"

        def file(_attribute: str, value: str) -> str:
            name = self.files.get(value)
            return _liquid_url(f"/assets/files/{name}") if name else "#"

        html = attribute_sentinels(html, _ASSET, asset)
        html = attribute_sentinels(html, _LINK, link)
        return attribute_sentinels(html, _FILELINK, file)

    def target(self) -> BodyTarget:
        return BodyTarget(
            convert=lambda html: _md()(html, heading_style="ATX", bullets="-", escape_braces=True),
            finish_markdown=self.finish_markdown,
            finish_html=self.finish_html,
            missing_prefix=_MISSING,
        )


def _rewrite_body(
    item: DerivedItem,
    store: _AssetStore,
    id_to_permalink: dict[str, str],
    file_targets: dict[str, str] | None = None,
    *,
    mode: str = "markdown",
    counts: BlockCounts | None = None,
) -> str:
    body = _Body(item, store, id_to_permalink, file_targets)
    if mode != "markdown":
        return render_body(
            item.content_html,
            mode,
            rewrite=lambda tree: rewrite_tree(tree, image=body.image, link=body.link),
            target=body.target(),
            counts=counts if counts is not None else BlockCounts(),
        )

    from bs4 import BeautifulSoup  # noqa: PLC0415 - optional ingester dependency

    soup = BeautifulSoup(item.content_html or "", "html.parser")
    for image in soup.find_all("img"):
        new = body.image(image.get("src", ""))
        if new is not None:
            image["src"] = new
    for anchor in soup.find_all("a"):
        new = body.link(anchor.get("href", ""))
        if new is not None:
            anchor["href"] = new

    # Escaped for Markdown (kramdown attribute lists included) by the
    # converter, then for Liquid by `finish_markdown`.
    markdown = _md()(str(soup), heading_style="ATX", bullets="-", escape_braces=True)
    return body.finish_markdown(markdown)


def _frontmatter(
    item: DerivedItem,
    *,
    kind: str,
    date_value: str,
    tags: list[str],
    source_archive: str | None = None,
) -> str:
    lines = ["---", f"title: {_yaml_scalar(item.title or item.id)}", f"date: {date_value}"]
    if item.provenance.source_url:
        lines.append(f"source_url: {_yaml_scalar(item.provenance.source_url)}")
    if item.author:
        lines.append(f"author: {_yaml_scalar(item.author)}")
    if tags:
        lines.append("tags: [" + ", ".join(_yaml_scalar(tag) for tag in tags) + "]")
    lines.append(f"kind: {kind}")
    if source_archive:
        # Only in a site combined from several archives: which one this
        # post's copy came from.
        lines.append(f"source_archive: {_yaml_scalar(source_archive)}")
    # Without a layout Jekyll renders the post bare: no title or author (only
    # a layout prints front matter), and no page head, so no <meta charset> --
    # the browser guesses the encoding and a "‘" shows as "â€˜". `post` is the
    # layout Jekyll's own starter site and most themes provide; a site that
    # wants another changes it here or overrides it per path in _config.yml.
    lines.append("layout: post")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _comments(item: DerivedItem) -> str:
    comments = getattr(item, "comments", None) or []
    if not comments:
        return ""
    children: dict[str | None, list] = {}
    for comment in comments:
        children.setdefault(comment.parent_comment_id, []).append(comment)
    lines = ["", "## Comments", ""]

    def render(parent_id: str | None, depth: int) -> None:
        for comment in children.get(parent_id, []):
            # The commenter's text, parsed rather than regex-stripped (which
            # left an unclosed `<img ... onerror=`) and escaped for Markdown,
            # kramdown and Liquid alike -- its paragraphs and line breaks
            # indented under the list item, where one line lost every break.
            when = f" ({_text(readable_time(comment.created))})" if comment.created else ""
            pad = "  " * depth
            head = f"{pad}- **{_text(comment.author or 'Unknown')}**{when}:"
            lines.extend(
                comment_lines(head, comment.content_html, _text, content_indent=pad + "    ")
            )
            render(comment.id, depth + 1)

    render(None, 0)
    return "\n".join(lines) + "\n"


def _replies(
    topic,
    store: _AssetStore,
    id_to_permalink: dict[str, str],
    file_targets: dict[str, str],
    *,
    mode: str,
    counts: BlockCounts,
) -> str:
    """The reply tree beneath a forum topic. Each reply goes through the same
    conversion as the topic -- body, images and links, escaped for Markdown
    and Liquid -- under a heading of its own; a reply to a reply goes one
    blockquote deeper per level, which a rendered page indents."""
    replies = getattr(topic, "replies", None) or {}
    if not replies:
        return ""
    lines: list[str] = ["", "## Replies", ""]
    rendered: set[str] = set()

    def render(reply_id: str, depth: int) -> None:
        reply = replies.get(reply_id)
        if reply is None or reply_id in rendered:
            return
        rendered.add(reply_id)
        who = _text(reply.author or "Unknown")
        when = f" · {_text(readable_time(reply.created))}" if reply.created else ""
        answer = " · *answer*" if "answer" in (reply.flags or []) else ""
        # Plain-text replies' line breaks become paragraphs and breaks first.
        shaped = reply.model_copy(
            update={"content_html": paragraphs_from_plain_text(reply.content_html)}
        )
        body = _rewrite_body(
            shaped, store, id_to_permalink, file_targets, mode=mode, counts=counts
        ).strip()
        block = [f"### {who}{when}{answer}", "", body if body else "*(no text)*", ""]
        attachments = _attachments(reply, store).strip()
        if attachments:
            block.extend([attachments, ""])
        lines.extend(quote_block("\n".join(block), depth))
        for child in reply.child_ids:
            render(child, depth + 1)

    for reply_id in topic.reply_ids:
        render(reply_id, 0)
    # A reply whose parent never made it into the tree is still a reply.
    for reply_id in replies:
        render(reply_id, 0)
    return "\n".join(lines).rstrip() + "\n"


def _attachments(item: DerivedItem, store: _AssetStore) -> str:
    attachments = getattr(item, "attachments", None) or []
    if not attachments:
        return ""
    lines = ["", "## Attachments", ""]
    for attachment in attachments:
        name = _safe(attachment.filename or attachment.id, "attachment")
        if attachment.asset.present and attachment.asset.blob_hash:
            stored = store.store(
                blob_hash=attachment.asset.blob_hash,
                preferred_name=attachment.filename or attachment.id,
                content_type=attachment.content_type,
            )
            if stored:
                lines.append(f"- [{_text(name)}]({_liquid_url(_asset_path(stored))})")
                continue
        lines.append(f"- {_code(f'[not captured: {name}]')}")
    return "\n".join(lines) + "\n"


def _records(interchange: Interchange) -> list[tuple[str, DerivedItem]]:
    records: list[tuple[str, DerivedItem]] = []
    for wiki in interchange.wikis:
        records.extend(("wiki", page) for page in wiki.pages.values())
    for blog in interchange.blogs:
        records.extend(("blog", post) for post in blog.posts.values())
    for forum in interchange.forums:
        records.extend(("forum", topic) for topic in forum.topics.values())
    return records


def _store_library_files(interchange: Interchange, store: _AssetStore) -> dict[str, str]:
    """Copy every present library file into ``assets/files/`` and return
    ``file_id -> stored name``; an uncaptured file is absent from the map and
    shows as a gap in the files listing."""
    targets: dict[str, str] = {}
    for library in interchange.file_libraries:
        for file_id, derived in library.files.items():
            asset = derived.asset
            if asset and asset.present and asset.blob_hash:
                name = store.store(
                    blob_hash=asset.blob_hash,
                    preferred_name=derived.name or derived.title or file_id,
                    content_type=derived.content_type,
                )
                if name:
                    targets[file_id] = name
    return targets


def _write_files_index(
    root: Path, interchange: Interchange, file_targets: dict[str, str], stats: JekyllStats
) -> None:
    """A ``files.md`` page listing every library and its documents -- each a
    link to the copied file under ``assets/files/``, or a visible gap when its
    bytes were not captured."""
    if not interchange.file_libraries:
        return
    lines = ["---", "title: Files", "---", ""]
    for library in interchange.file_libraries:
        stats.libraries += 1
        lines.append(f"## {_text(library.title or library.id)}")
        lines.append("")
        ordered = [library.files[i] for i in library.file_ids if i in library.files]
        ordered += [f for i, f in library.files.items() if i not in library.file_ids]
        for derived in ordered:
            stats.files += 1
            label = derived.name or derived.title or derived.id
            stored = file_targets.get(derived.id)
            if stored:
                lines.append(f"- [{_text(label)}]({_liquid_url(f'/assets/files/{stored}')})")
            else:
                lines.append(f"- {_code(f'[not captured: {label}]')}")
        lines.append("")
    (root / "files.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jekyll_site(
    interchange: Interchange,
    blob_reader: BlobReader,
    out_dir: Path | str,
    *,
    html_mode: str = "mixed",
    combined: CombineReport | None = None,
) -> JekyllStats:
    """Write a generic Jekyll site fragment under ``out_dir``. ``html_mode``
    is how bodies are written (``_bodies.HTML_MODES``); an HTML block reaches
    the built page because kramdown passes block-level HTML through.
    ``combined`` is the report of combining several archives into
    ``interchange`` (``derive.combine``): every post then names the archive it
    came from, and a ``sources.md`` page lists them."""
    mode = html_mode_for("jekyll", html_mode)
    combined = several(combined)
    counts = BlockCounts()
    root = Path(out_dir)
    posts_dir = root / "_posts"
    stats = JekyllStats(html_mode=mode)
    store = _AssetStore(root / "assets" / "images" / "imported", blob_reader, stats)
    # Files get their own dir and share the stats. Built before the posts so a
    # body link to a file resolves to the copied file, not the dead URL.
    file_store = _AssetStore(root / "assets" / "files", blob_reader, stats)
    file_targets = _store_library_files(interchange, file_store)
    records = _records(interchange)
    used_paths: set[str] = set()
    id_to_permalink: dict[str, str] = {}
    paths: list[tuple[str, DerivedItem, Path, str, list[str]]] = []

    for kind, item in records:
        date_value, filename_date = _date_parts(item.created or item.modified)
        fallback = _slug(item.id, "item")[:24]
        slug = _slug(item.title or item.id, fallback)
        filename = f"{filename_date}-{slug}.md"
        if filename in used_paths:
            filename = f"{filename_date}-{slug}-{_slug(item.id, 'item')[:12]}.md"
        while filename in used_paths:
            filename = f"{filename_date}-{slug}-{len(used_paths)}.md"
        used_paths.add(filename)
        path = posts_dir / filename
        permalink = (
            f"/posts/{filename_date[:4]}/{filename_date[5:7]}/"
            f"{filename_date[8:10]}/{filename[:-3]}/"
        )
        id_to_permalink[item.id] = permalink
        paths.append((kind, item, path, date_value, getattr(item, "tags", None) or []))

    posts_dir.mkdir(parents=True, exist_ok=True)
    for kind, item, path, date_value, tags in paths:
        body = _rewrite_body(
            item, store, id_to_permalink, file_targets, mode=mode, counts=counts
        ).strip()
        content = _frontmatter(
            item,
            kind=kind,
            date_value=date_value,
            tags=tags,
            source_archive=combined.origins.get(item.id) if combined else None,
        )
        content += body + "\n" if body else ""
        content += _attachments(item, store) + _comments(item)
        if kind == "forum":
            content += _replies(
                item, store, id_to_permalink, file_targets, mode=mode, counts=counts
            )
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    stats.posts = len(paths)
    stats.markdown_blocks, stats.html_blocks = counts.markdown, counts.html
    _write_files_index(root, interchange, file_targets, stats)
    if combined is not None:
        # A page of the site rather than a README: Jekyll publishes every
        # Markdown file with front matter, and this is worth publishing.
        page = ["---", "title: Sources", "---", "", *combined_section(combined, _text)[1:]]
        (root / "sources.md").write_text("\n".join(page) + "\n", encoding="utf-8")
    return stats


def from_source(source, out_dir: Path | str, *, html_mode: str = "mixed") -> JekyllStats:
    """Write a Jekyll site fragment from any compatible model source."""
    interchange = source.get_model()
    if interchange is None:
        raise ValueError("nothing to ingest: the source holds no derivable content")

    def blob_reader(blob_hash: str) -> bytes | None:
        result = source.get_blob(blob_hash)
        return result[0] if result is not None else None

    return write_jekyll_site(
        interchange,
        blob_reader,
        out_dir,
        html_mode=html_mode,
        combined=getattr(source, "combine_report", None),
    )
