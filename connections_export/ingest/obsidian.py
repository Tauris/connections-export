"""Reference ingester: an interchange package -> an Obsidian vault.

A worked, runnable example of the "Reconstructing content in a target
wiki" algorithm (docs/reference/interchange-format.md §7) and proof the
contract is buildable with **no HCL knowledge** -- it reads only the
normalized model + blobs. It converts every item's `content_html` to
Markdown with `markdownify` and lays the content out as an Obsidian vault:

- one `.md` per wiki page, the hierarchy mirrored as nested folders (a
  page's children live in a folder named for the page), so `parent_id`/
  `child_ids`/`ordinal` (§7 steps 2-3) drive the tree;
- one `.md` per blog post, in a folder per blog, in feed order; and one
  `.md` per forum topic, in a folder per forum, with the reply tree
  rendered beneath the topic. A post, a topic and a reply carry the same
  body, assets, links and provenance a page does (`DerivedItem`), so they
  go through the same conversion -- the first version of this writer laid
  out wikis only, and a blog captured for exactly this purpose produced an
  empty vault;
- **in-export links -> `[[wikilinks]]`** to the target page's note, other
  links kept verbatim (§7 step 5);
- **images and attachments** copied out of `blobs/` and embedded/linked by
  name (`![[file]]` / `[[file]]`); a not-captured asset becomes a **visible
  marker**, never a silent drop (§7 steps 4-5, and §1's losslessness rule);
- **comments** appended as a threaded list (indented by `parent_comment_id`
  when the manifest says threading is present);
- **tags** (interchange §3.3) written as Obsidian-native `tags:` YAML
  frontmatter, so every tag is searchable and filterable in the vault; the
  key is omitted only when the page has no tags;
- YAML frontmatter also carries title/author/dates and the source
  provenance (`hcl_id`) for traceability.

Loss, if any, happens *here* -- in this writer, mapping onto Obsidian's
model -- against a package that still has the data (§7 step 6). The
converter (`markdownify`) is a base dependency, so a plain install runs it.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from connections_export.derive.model import (
    DerivedForumTopic,
    DerivedItem,
    DerivedPage,
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
from connections_export.ingest._markdown import (
    BRACKET_TEXT,
    code_span,
    escape_inline,
    html_to_text,
    to_markdown,
)
from connections_export.interchange.filenames import MAX_STEM, unreserve

#: `blob_hash -> bytes` (or None when a referenced blob isn't present).
#: `interchange.open_blob`-backed in production; injectable for tests.
BlobReader = Callable[[str], bytes | None]

_EMBED = "obsidian-embed:"  # sentinel img src, rewritten to ![[name]] after markdownify
_LINK = "obsidian-link:"  # sentinel a href, rewritten to [[Title|text]]
_MISSING = "obsidian-missing:"  # sentinel for a referenced-but-absent asset
_FILELINK = "obsidian-file:"  # sentinel a href pointing at a library file, by file id

#: Unsafe in a filename or an Obsidian link. Control characters too: NUL is
#: refused by every filesystem (and raised mid-export), and a newline in a
#: wikilink ends it.
_FORBIDDEN = re.compile(r'[/\\:*?"<>|#\^\[\]\x00-\x1f\x7f]')
#: Well under the 255 bytes a filename may hold, leaving room for `.md` and
#: the `-N` a colliding attachment gets.
_MAX_NAME_BYTES = 200
_CONTENT_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}


@dataclass
class VaultStats:
    wikis: int = 0
    pages: int = 0
    blogs: int = 0
    posts: int = 0
    forums: int = 0
    topics: int = 0
    libraries: int = 0
    files: int = 0
    assets_written: int = 0
    assets_missing: int = 0
    notes: list[str] = field(default_factory=list)
    #: How bodies were written (`_bodies.HTML_MODES`), and how many of their
    #: blocks went out as Markdown and as HTML.
    html_mode: str = "markdown"
    markdown_blocks: int = 0
    html_blocks: int = 0

    @property
    def items(self) -> int:
        """Every note written for a piece of content, whatever its app."""
        return self.pages + self.posts + self.topics


def _md():
    """The HTML -> Markdown converter: `markdownify`, with the text of a body
    made inert (`ingest._markdown`). The body is its author's, and a vault
    renders it."""
    return to_markdown


def _safe(name: str, fallback: str) -> str:
    """`name` as a note, folder or attachment name any filesystem can hold.

    Also the target of every wikilink to that note, so it must not be able to
    close one. A title is whatever its author typed: a Windows device name
    (`CON`, `lpt9.txt`) made a file Windows cannot open, NUL made the whole
    export raise, `..` walked out of its folder, and a long title in a script
    of four-byte characters passed the 255-byte limit.
    """
    cleaned = _FORBIDDEN.sub("-", (name or "").strip()).strip(". ")
    cleaned = cleaned[:MAX_STEM]
    while len(cleaned.encode("utf-8")) > _MAX_NAME_BYTES:
        cleaned = cleaned[:-1]
    cleaned = unreserve(cleaned.rstrip(". "))
    return cleaned or fallback


def _sniff(data: bytes) -> str | None:
    """The real file type from the bytes, for the formats where an
    embed *needs* a truthful extension to render (Obsidian keys off it).
    Bytes are authoritative: an asset's URL extension can lie -- e.g. an
    SVG served at a `.png` path -- and a mislabeled image renders broken.
    Returns None for anything not confidently one of these, leaving the
    declared/href extension in place (so e.g. a `.docx` ZIP stays `.docx`)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:512].lstrip().lstrip(b"\xef\xbb\xbf").lstrip()
    if head[:5].lower() == b"<?xml":
        head = head[head.find(b"?>") + 2 :].lstrip() if b"?>" in head else head
    if head[:4].lower() == b"<svg":
        return ".svg"
    return None


def _ext_for(*, content_type: str | None, href: str | None) -> str:
    if content_type and content_type in _CONTENT_EXT:
        return _CONTENT_EXT[content_type]
    if href:
        suffix = Path(urlsplit(href).path).suffix
        if 0 < len(suffix) <= 6:
            return suffix
    return ".bin"


class _AssetStore:
    """Copies present blobs into the vault's `attachments/` folder once
    each (deduped by hash), returning a stable vault-relative name to
    embed. Obsidian resolves `![[name]]`/`[[name]]` by name across the
    whole vault, so nested pages need no relative paths."""

    def __init__(self, attachments_dir: Path, blob_reader: BlobReader, stats: VaultStats) -> None:
        self._dir = attachments_dir
        self._read = blob_reader
        self._stats = stats
        self._by_hash: dict[str, str] = {}
        self._used: set[str] = set()

    def note_missing(self) -> None:
        """Record a referenced-but-not-captured asset (a visible gap)."""
        self._stats.assets_missing += 1

    def store(
        self, *, blob_hash: str | None, preferred_name: str, content_type: str | None
    ) -> str | None:
        """Write the blob and return its vault name, or `None` if it isn't
        present (the caller then leaves a visible gap)."""
        if not blob_hash:
            return None
        if blob_hash in self._by_hash:
            return self._by_hash[blob_hash]
        data = self._read(blob_hash)
        if data is None:
            self._stats.assets_missing += 1
            return None
        name = _safe(preferred_name, "attachment")
        sniffed = _sniff(data)
        if sniffed:
            # the bytes win over a possibly-lying URL extension (§1: an
            # asset the reader can't render is a defect, not fidelity)
            name = Path(name).stem + sniffed
        elif "." not in Path(name).name:
            name += _ext_for(content_type=content_type, href=preferred_name)
        # keep it unique if two different blobs want the same name
        stem, dot, suffix = name.rpartition(".")
        base = stem if dot else name
        candidate, i = name, 1
        while candidate in self._used:
            candidate = f"{base}-{i}{('.' + suffix) if dot else ''}"
            i += 1
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / candidate).write_bytes(data)
        self._used.add(candidate)
        self._by_hash[blob_hash] = candidate
        self._stats.assets_written += 1
        return candidate


#: YAML double-quoted escapes for what a scalar cannot hold literally. A raw
#: newline in a title ended the front matter (`---`) and let the title write
#: keys of its own; YAML also breaks lines at NEL, LS and PS, and refuses
#: other control characters outright.
_YAML_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_YAML_UNSAFE = re.compile(r'[\\"\x00-\x1f\x7f-\x9f  \ud800-\udfff﻿]')


def _yaml_escape(match: re.Match) -> str:
    char = match.group()
    if char in _YAML_ESCAPES:
        return _YAML_ESCAPES[char]
    code = ord(char)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def _yaml_scalar(value: str) -> str:
    """`value` as a YAML double-quoted scalar, on one line whatever it holds."""
    return '"' + _YAML_UNSAFE.sub(_yaml_escape, value) + '"'


def _label(item: DerivedItem) -> str | None:
    """A wiki page's label; nothing for a post or a topic, which have none."""
    return getattr(item, "label", None)


def _frontmatter(page: DerivedItem, *, kind: str | None = None) -> str:
    lines = ["---", f"title: {_yaml_scalar(page.title or _label(page) or page.id)}"]
    if kind:
        # So a vault search can tell a post from a page from a topic; a wiki
        # page carries none, as it did before other kinds arrived.
        lines.append(f"kind: {kind}")
    if _label(page):
        lines.append(f"aliases: [{_yaml_scalar(_label(page))}]")
    if page.author:
        lines.append(f"author: {_yaml_scalar(page.author)}")
    if page.created:
        lines.append(f"created: {_yaml_scalar(page.created)}")
    if page.modified:
        lines.append(f"modified: {_yaml_scalar(page.modified)}")
    tags = getattr(page, "tags", None) or []
    if tags:
        lines.append("tags: [" + ", ".join(_yaml_scalar(t) for t in tags) + "]")
    flags = getattr(page, "flags", None) or []
    if flags:
        # A topic's pinned / locked / question / answered, kept where a
        # vault query can find "every answered question".
        lines.append("flags: [" + ", ".join(_yaml_scalar(f) for f in flags) + "]")
    if page.provenance and page.provenance.hcl_id:
        lines.append(f"hcl_id: {_yaml_scalar(page.provenance.hcl_id)}")
    if page.provenance and page.provenance.source_url:
        lines.append(f"source_url: {_yaml_scalar(page.provenance.source_url)}")
    lines.append("---\n")
    return "\n".join(lines)


def _resolvers(
    page: DerivedItem,
    store: _AssetStore,
    id_to_title: dict[str, str],
    files: dict[str, str],
) -> tuple[Callable[[str], str | None], Callable[[str], str | None]]:
    """What a body's `<img src>` and `<a href>` become: a sentinel resolved
    after conversion, or `None` to leave it. Copies present image blobs and
    maps in-export links as a side effect, whichever tree they are run on."""
    asset_by_href = {}
    for asset in page.assets:
        asset_by_href[asset.original_href] = asset
        if asset.resolved_url:
            asset_by_href[asset.resolved_url] = asset

    def image(src: str) -> str | None:
        asset = asset_by_href.get(src)
        if asset is None:
            return None
        if asset.present and asset.blob_hash:
            name = store.store(
                blob_hash=asset.blob_hash,
                preferred_name=Path(urlsplit(asset.original_href).path).name or "image",
                content_type=None,
            )
            # store already counts a present-but-uncaptured blob as missing
            return f"{_EMBED}{name}" if name else f"{_MISSING}{asset.original_href}"
        store.note_missing()
        return f"{_MISSING}{asset.original_href}"

    link_by_href = {link.original_href: link for link in page.links}

    def link(href: str) -> str | None:
        found = link_by_href.get(href)
        if not found or found.scope != "in_export":
            return None  # hcl_deployment / external links keep their href (§7 step 5)
        if found.target_page_id in id_to_title:
            return f"{_LINK}{found.target_page_id}"
        if found.target_file_id and found.target_file_id in files:
            # A body link to a community file -- "especially referenced":
            # it now points at the file this exporter wrote, not the dead
            # deployment URL.
            return f"{_FILELINK}{found.target_file_id}"
        return None

    return image, link


@dataclass
class _HtmlContext:
    """What a body written in an HTML mode needs beyond the Markdown path:
    where its note is, so an HTML link can be a relative path (a wikilink
    means nothing inside HTML)."""

    mode: str
    counts: BlockCounts
    note: Path
    id_to_note: dict[str, Path]
    vault: Path


def _relative(target: Path, note: Path) -> str:
    return quote(posixpath.relpath(target.as_posix(), note.parent.as_posix()), safe="/-._~")


def _body_target(
    context: _HtmlContext, id_to_title: dict[str, str], files: dict[str, str]
) -> BodyTarget:
    attachments = context.vault / "attachments"

    def resolve(prefix: str):
        def inner(_attribute: str, value: str) -> str:
            if prefix == _EMBED:
                return _relative(attachments / value, context.note)
            if prefix == _FILELINK and value in files:
                return _relative(attachments / files[value], context.note)
            if prefix == _LINK and value in context.id_to_note:
                return _relative(context.id_to_note[value], context.note)
            return "#"

        return inner

    def finish_html(html: str) -> str:
        for prefix in (_EMBED, _LINK, _FILELINK):
            html = attribute_sentinels(html, prefix, resolve(prefix))
        return html

    return BodyTarget(
        convert=lambda html: _md()(html, heading_style="ATX", bullets="-"),
        finish_markdown=lambda markdown: _apply_sentinels(markdown, id_to_title, files),
        finish_html=finish_html,
        missing_prefix=_MISSING,
    )


def _rewrite_body(
    page: DerivedItem,
    store: _AssetStore,
    id_to_title: dict[str, str],
    file_targets: dict[str, str] | None = None,
    context: _HtmlContext | None = None,
) -> str:
    """Rewrite the page body's `<img>`/`<a>` to sentinels (resolved after
    markdownify), copying present image blobs and mapping in-export links.
    Uses `markdownify`'s own BeautifulSoup so parsing matches the converter.

    In an HTML mode (`context`) the body goes through `_bodies.render_body`
    instead, with the same sentinels."""
    files = file_targets or {}
    image, link = _resolvers(page, store, id_to_title, files)
    if context is not None and context.mode != "markdown":
        rendered = render_body(
            page.content_html,
            context.mode,
            rewrite=lambda tree: rewrite_tree(tree, image=image, link=link),
            target=_body_target(context, id_to_title, files),
            counts=context.counts,
        )
        return rendered + "\n" if rendered else ""

    from bs4 import BeautifulSoup  # noqa: PLC0415 - ships with markdownify

    soup = BeautifulSoup(page.content_html or "", "html.parser")
    for img in soup.find_all("img"):
        new = image(img.get("src", ""))
        if new is not None:
            img["src"] = new
    for a in soup.find_all("a"):
        new = link(a.get("href", ""))
        if new is not None:
            a["href"] = new

    markdown = _md()(str(soup), heading_style="ATX", bullets="-")
    return _apply_sentinels(markdown, id_to_title, files)


def _apply_sentinels(
    markdown: str, id_to_title: dict[str, str], file_targets: dict[str, str] | None = None
) -> str:
    # ![alt](obsidian-embed:NAME) -> ![[NAME]]
    markdown = re.sub(
        r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_EMBED) + r"([^)]+)\)", r"![[\2]]", markdown
    )
    # ![alt](obsidian-missing:HREF) -> a visible, non-silent gap
    markdown = re.sub(
        r"!\[" + BRACKET_TEXT + r"\]\(" + re.escape(_MISSING) + r"([^)]+)\)",
        lambda match: code_span(f"[image not captured: {match.group(2)}]"),
        markdown,
    )

    def _link(match: re.Match) -> str:
        text, target = match.group(1), match.group(2)
        title = id_to_title.get(target) or _safe(target, "link")
        return f"[[{title}|{text}]]" if text and text != title else f"[[{title}]]"

    # [text](obsidian-link:ID) -> [[Title|text]]
    markdown = re.sub(
        r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_LINK) + r"([^)]+)\)", _link, markdown
    )

    files = file_targets or {}

    def _file(match: re.Match) -> str:
        text, fid = match.group(1), match.group(2)
        name = files.get(fid) or _safe(fid, "file")
        return f"[[{name}|{text}]]" if text and text != name else f"[[{name}]]"

    # [text](obsidian-file:ID) -> [[stored-file|text]] (Obsidian links a file by name)
    markdown = re.sub(
        r"\[" + BRACKET_TEXT + r"\]\(" + re.escape(_FILELINK) + r"([^)]+)\)", _file, markdown
    )
    return markdown


def _comments_md(page: DerivedItem) -> str:
    comments = getattr(page, "comments", None) or []
    if not comments:
        return ""
    children: dict[str | None, list] = {}
    for comment in comments:
        children.setdefault(comment.parent_comment_id, []).append(comment)

    def render(parent_id: str | None, depth: int, out: list[str]) -> None:
        for comment in children.get(parent_id, []):
            # Every part is the commenter's text on one list line: parsed, not
            # regex-stripped (which left an unclosed `<img ... onerror=`), and
            # escaped so none of it becomes Markdown or HTML.
            who = escape_inline(comment.author or "Unknown")
            when = f" · {escape_inline(comment.created)}" if comment.created else ""
            body = escape_inline(html_to_text(comment.content_html))
            out.append(f"{'    ' * depth}- **{who}**{when}: {body}")
            render(comment.id, depth + 1, out)

    lines: list[str] = []
    render(None, 0, lines)
    # comments whose parent isn't in this page (orphaned reply) stay top-level
    seen = {c.id for c in comments}
    for comment in comments:
        if comment.parent_comment_id and comment.parent_comment_id not in seen:
            children.setdefault(None, []).append(comment)
    return "\n## Comments\n\n" + "\n".join(lines) + "\n" if lines else ""


def _attachments_md(page: DerivedItem, store: _AssetStore) -> str:
    attachments = getattr(page, "attachments", None) or []
    if not attachments:
        return ""
    rows: list[str] = []
    for att in attachments:
        name = _safe(att.filename or att.id, "attachment")
        if att.asset.present and att.asset.blob_hash:
            vault_name = store.store(
                blob_hash=att.asset.blob_hash,
                preferred_name=att.filename or att.id,
                content_type=att.content_type,
            )
            gap = f"- {code_span(f'[not captured: {name}]')}"
            rows.append(f"- [[{vault_name}]]" if vault_name else gap)
        else:
            rows.append(f"- {code_span(f'[not captured: {name}]')}")
    return "\n## Attachments\n\n" + "\n".join(rows) + "\n" if rows else ""


def _versions_note(page: DerivedItem) -> str:
    versions = getattr(page, "versions", None) or []
    if not versions:
        return ""
    full = sum(1 for v in versions if v.content_present)
    detail = f"{len(versions)} revision(s)" + (
        f", {full} with captured content" if full else " (metadata only)"
    )
    return f"\n## History\n\n> {detail}\n"


def _replies_md(
    topic: DerivedForumTopic,
    store: _AssetStore,
    id_to_title: dict[str, str],
    file_targets: dict[str, str] | None = None,
    context: _HtmlContext | None = None,
) -> str:
    """The reply tree beneath a topic, nested by `child_ids`.

    A reply is not a comment: it carries a body with assets and links of
    its own, so it goes through the same conversion as the topic rather
    than being stripped to a line of text. Each reply is a heading at its
    depth, which is what keeps a long thread readable in a vault and
    keeps every reply's images embedded where they were.
    """
    if not topic.replies:
        return ""
    lines: list[str] = ["", "## Replies", ""]

    def render(reply_id: str, depth: int) -> None:
        reply = topic.replies.get(reply_id)
        if reply is None:
            return
        who = escape_inline(reply.author or "Unknown")
        when = f" · {escape_inline(reply.created)}" if reply.created else ""
        answer = " · *answer*" if "answer" in (reply.flags or []) else ""
        lines.append(f"{'#' * min(3 + depth, 6)} {who}{when}{answer}")
        lines.append("")
        body = _rewrite_body(reply, store, id_to_title, file_targets, context).strip()
        lines.append(body if body else "*(no text)*")
        attachments = _attachments_md(reply, store)
        if attachments:
            lines.append(attachments.replace("\n## Attachments\n", "\n**Attachments**\n"))
        lines.append("")
        for child in reply.child_ids:
            render(child, depth + 1)

    for reply_id in topic.reply_ids:
        render(reply_id, 0)
    # A reply whose parent never made it into the tree is still a reply:
    # shown at the top level rather than lost.
    reachable: set[str] = set()

    def walk(reply_id: str) -> None:
        if reply_id in reachable or reply_id not in topic.replies:
            return
        reachable.add(reply_id)
        for child in topic.replies[reply_id].child_ids:
            walk(child)

    for reply_id in topic.reply_ids:
        walk(reply_id)
    for reply_id in topic.replies:
        if reply_id not in reachable:
            render(reply_id, 0)
    return "\n".join(lines) + "\n"


def _ancestors(page: DerivedPage, pages: dict[str, DerivedPage]) -> list[DerivedPage]:
    chain: list[DerivedPage] = []
    current = page
    seen: set[str] = set()
    while current.parent_id and current.parent_id in pages and current.parent_id not in seen:
        seen.add(current.parent_id)
        current = pages[current.parent_id]
        chain.append(current)
    chain.reverse()
    return chain


def _note_path(
    vault: Path, wiki_dir: str, page: DerivedPage, pages: dict[str, DerivedPage]
) -> Path:
    parts = [wiki_dir] + [_safe(a.title or a.label or a.id, a.id) for a in _ancestors(page, pages)]
    directory = vault.joinpath(*parts)
    return directory / (_safe(page.title or page.label or page.id, page.id) + ".md")


def _store_library_files(interchange: Interchange, store: _AssetStore) -> dict[str, str]:
    """Write every present library file's bytes into the vault and return
    `file_id -> stored name`, so body links to those files can resolve. A
    file whose bytes were not captured is absent from the map and shows as a
    gap in the Files index instead."""
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
    vault: Path, interchange: Interchange, file_targets: dict[str, str], stats: VaultStats
) -> None:
    """One `Files.md` listing every community library and its files -- each a
    link to the copied file, or a visible gap when its bytes were not
    captured. A referenced file is already linked from the body; this makes
    every file browsable, referenced or not."""
    if not interchange.file_libraries:
        return
    lines = ["# Files", ""]
    for library in interchange.file_libraries:
        stats.libraries += 1
        lines.append(f"## {escape_inline(library.title or library.id)}")
        ordered = [library.files[i] for i in library.file_ids if i in library.files]
        ordered += [f for i, f in library.files.items() if i not in library.file_ids]
        for derived in ordered:
            stats.files += 1
            label = derived.name or derived.title or derived.id
            stored = file_targets.get(derived.id)
            if stored:
                shown = f" — {escape_inline(label)}" if label != stored else ""
                lines.append(f"- [[{stored}]]{shown}")
            else:
                lines.append(f"- {code_span(f'[not captured: {label}]')}")
        lines.append("")
    (vault / "Files.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _note_paths(vault: Path, interchange: Interchange) -> dict[str, Path]:
    """Every note's path by item id, as `write_obsidian_vault` lays them out --
    what a link in an HTML block points at."""
    paths: dict[str, Path] = {}
    for wiki in interchange.wikis:
        wiki_dir = _safe(wiki.title or wiki.label, wiki.id)
        for page in wiki.pages.values():
            paths[page.id] = _note_path(vault, wiki_dir, page, wiki.pages)
    for blog in interchange.blogs:
        blog_dir = vault / _safe(blog.title or blog.handle or blog.id, blog.id)
        for post in blog.posts.values():
            paths[post.id] = blog_dir / (_safe(post.title or post.id, post.id) + ".md")
    for forum in interchange.forums:
        forum_dir = vault / _safe(forum.title or forum.id, forum.id)
        for topic in forum.topics.values():
            paths[topic.id] = forum_dir / (_safe(topic.title or topic.id, topic.id) + ".md")
    return paths


def write_obsidian_vault(
    interchange: Interchange,
    blob_reader: BlobReader,
    out_dir: Path | str,
    *,
    html_mode: str = "markdown",
) -> VaultStats:
    """Write `interchange`'s wikis, blogs and forums as an Obsidian vault
    under `out_dir`. `blob_reader` resolves an asset/attachment `blob_hash`
    to bytes (or `None` if absent). `html_mode` is how bodies are written
    (`_bodies.HTML_MODES`). Returns what was written."""
    mode = html_mode_for("obsidian", html_mode)
    vault = Path(out_dir)
    vault.mkdir(parents=True, exist_ok=True)
    stats = VaultStats(html_mode=mode)
    counts = BlockCounts()
    id_to_note = _note_paths(vault, interchange) if mode != "markdown" else {}

    def context(note: Path) -> _HtmlContext | None:
        if mode == "markdown":
            return None
        return _HtmlContext(mode, counts, note, id_to_note, vault)

    store = _AssetStore(vault / "attachments", blob_reader, stats)

    # Obsidian resolves [[Title]] by note name across the whole vault, so
    # one id->title map spans every container of every app: a post linking
    # to a wiki page resolves the same way a page linking to a page does (a
    # caveat when titles collide).
    #
    # The target is the note's own file name (`_safe`), which is both what
    # Obsidian resolves and something a title cannot use to close the link.
    id_to_title = {
        pid: _safe(p.title or p.label or pid, pid)
        for wiki in interchange.wikis
        for pid, p in wiki.pages.items()
    }
    id_to_title.update(
        {
            pid: _safe(post.title or pid, pid)
            for blog in interchange.blogs
            for pid, post in blog.posts.items()
        }
    )
    id_to_title.update(
        {
            tid: _safe(topic.title or tid, tid)
            for forum in interchange.forums
            for tid, topic in forum.topics.items()
        }
    )

    # Files first, so a body link to one resolves to the copied file rather
    # than the dead deployment URL when the pages are written below.
    file_targets = _store_library_files(interchange, store)

    for wiki in interchange.wikis:
        stats.wikis += 1
        wiki_dir = _safe(wiki.title or wiki.label, wiki.id)
        for page in wiki.pages.values():
            note = _note_path(vault, wiki_dir, page, wiki.pages)
            note.parent.mkdir(parents=True, exist_ok=True)
            body = _rewrite_body(page, store, id_to_title, file_targets, context(note))
            heading = f"# {escape_inline(page.title or page.label or page.id)}\n\n"
            note.write_text(
                _frontmatter(page)
                + heading
                + body
                + _attachments_md(page, store)
                + _versions_note(page)
                + _comments_md(page),
                encoding="utf-8",
            )
            stats.pages += 1

    for blog in interchange.blogs:
        stats.blogs += 1
        blog_dir = vault / _safe(blog.title or blog.handle or blog.id, blog.id)
        blog_dir.mkdir(parents=True, exist_ok=True)
        # Feed order is the blog's order; a post's ordinal in the name keeps
        # a file listing in that order too, which a title alone would not.
        ordered = [blog.posts[pid] for pid in blog.post_ids if pid in blog.posts]
        ordered += [post for pid, post in blog.posts.items() if pid not in blog.post_ids]
        for post in ordered:
            note = blog_dir / (_safe(post.title or post.id, post.id) + ".md")
            body = _rewrite_body(post, store, id_to_title, file_targets, context(note))
            heading = f"# {escape_inline(post.title or post.id)}\n\n"
            note.write_text(
                _frontmatter(post, kind="post") + heading + body + _comments_md(post),
                encoding="utf-8",
            )
            stats.posts += 1

    for forum in interchange.forums:
        stats.forums += 1
        forum_dir = vault / _safe(forum.title or forum.id, forum.id)
        forum_dir.mkdir(parents=True, exist_ok=True)
        ordered_topics = [forum.topics[tid] for tid in forum.topic_ids if tid in forum.topics]
        ordered_topics += [t for tid, t in forum.topics.items() if tid not in forum.topic_ids]
        for topic in ordered_topics:
            note = forum_dir / (_safe(topic.title or topic.id, topic.id) + ".md")
            body = _rewrite_body(topic, store, id_to_title, file_targets, context(note))
            heading = f"# {escape_inline(topic.title or topic.id)}\n\n"
            note.write_text(
                _frontmatter(topic, kind="topic")
                + heading
                + body
                + _attachments_md(topic, store)
                + _replies_md(topic, store, id_to_title, file_targets, context(note)),
                encoding="utf-8",
            )
            stats.topics += 1

    stats.markdown_blocks, stats.html_blocks = counts.markdown, counts.html
    _write_files_index(vault, interchange, file_targets, stats)
    _write_readme(vault, interchange, stats)
    return stats


def _write_readme(vault: Path, interchange: Interchange, stats: VaultStats) -> None:
    lines = [
        "# HCL export → Obsidian vault",
        "",
        "Reconstructed from an interchange package"
        + (f" of {code_span(interchange.base_url)}" if interchange.base_url else "")
        + f": {stats.wikis} wiki(s), {stats.pages} page(s); "
        + f"{stats.blogs} blog(s), {stats.posts} post(s); "
        + f"{stats.forums} forum(s), {stats.topics} topic(s); "
        + f"{stats.libraries} file librar(ies), {stats.files} file(s); "
        + f"{stats.assets_written} attachment(s)/file(s) copied.",
        "",
    ]
    if interchange.wikis:
        lines += ["## Wikis", ""]
        for wiki in interchange.wikis:
            lines.append(f"### {escape_inline(wiki.title or wiki.label)}")
            for root_id in wiki.root_page_ids:
                page = wiki.pages.get(root_id)
                if page:
                    lines.append(f"- [[{_safe(page.title or page.label or root_id, root_id)}]]")
            lines.append("")
    if interchange.blogs:
        lines += ["## Blogs", ""]
        for blog in interchange.blogs:
            lines.append(f"### {escape_inline(blog.title or blog.handle or blog.id)}")
            for pid in blog.post_ids:
                post = blog.posts.get(pid)
                if post:
                    lines.append(f"- [[{_safe(post.title or pid, pid)}]]")
            lines.append("")
    if interchange.forums:
        lines += ["## Forums", ""]
        for forum in interchange.forums:
            lines.append(f"### {escape_inline(forum.title or forum.id)}")
            for tid in forum.topic_ids:
                topic = forum.topics.get(tid)
                if topic:
                    lines.append(f"- [[{_safe(topic.title or tid, tid)}]]")
            lines.append("")
    if interchange.file_libraries:
        lines += ["## Files", "", "- [[Files]] — every library and its documents", ""]
    if stats.html_mode != "markdown":
        lines += [html_mode_note(stats.html_mode, stats.markdown_blocks, stats.html_blocks), ""]
    if stats.assets_missing:
        lines.append(
            f"> {stats.assets_missing} referenced asset(s) were not captured in the "
            "package — shown as visible gaps, not dropped."
        )
    (vault / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def from_source(source, out_dir: Path | str, *, html_mode: str = "markdown") -> VaultStats:
    """Write a vault from anything that answers `get_model()` and
    `get_blob(hash)` -- a `ModelSource` over a package, an archive
    directory, or a zipped archive. The author filter, if any, is the
    source's own.

    Raises `ValueError` when the source holds nothing derivable, rather
    than writing an empty vault that reads as a capture with nothing in it.
    """
    interchange = source.get_model()
    if interchange is None:
        raise ValueError("nothing to ingest: the source holds no derivable content")

    def blob_reader(blob_hash: str) -> bytes | None:
        result = source.get_blob(blob_hash)
        return result[0] if result is not None else None

    return write_obsidian_vault(interchange, blob_reader, out_dir, html_mode=html_mode)


def from_package(
    package_dir: Path | str, out_dir: Path | str, *, author: str | None = None
) -> VaultStats:
    """Convenience: load an interchange package and write it as a vault.
    `author` narrows the vault to that user's involvement (name or user id)."""
    from connections_export.derive.author_filter import filter_by_author  # noqa: PLC0415
    from connections_export.interchange import load_package  # noqa: PLC0415
    from connections_export.interchange.package import open_blob  # noqa: PLC0415

    package_dir = Path(package_dir)
    interchange = load_package(package_dir)
    if author:
        interchange = filter_by_author(interchange, author=author)

    def blob_reader(blob_hash: str) -> bytes | None:
        try:
            return open_blob(package_dir, blob_hash)
        except FileNotFoundError:
            return None

    return write_obsidian_vault(interchange, blob_reader, out_dir)
