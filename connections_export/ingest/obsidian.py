"""Reference ingester: an interchange package -> an Obsidian vault.

A worked, runnable example of the "Reconstructing content in a target
wiki" algorithm (docs/reference/interchange-format.md §7) and proof the
contract is buildable with **no HCL knowledge** -- it reads only the
normalized model + blobs. It converts each wiki page's `content_html` to
Markdown with `markdownify` and lays the pages out as an Obsidian vault:

- one `.md` per page, the hierarchy mirrored as nested folders (a page's
  children live in a folder named for the page), so `parent_id`/
  `child_ids`/`ordinal` (§7 steps 2-3) drive the tree;
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
model -- against a package that still has the data (§7 step 6). Install
with the `obsidian` extra (`pip install connections-export[obsidian]`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from connections_export.derive.model import DerivedPage, Interchange

#: `blob_hash -> bytes` (or None when a referenced blob isn't present).
#: `interchange.open_blob`-backed in production; injectable for tests.
BlobReader = Callable[[str], bytes | None]

_EMBED = "obsidian-embed:"  # sentinel img src, rewritten to ![[name]] after markdownify
_LINK = "obsidian-link:"  # sentinel a href, rewritten to [[Title|text]]
_MISSING = "obsidian-missing:"  # sentinel for a referenced-but-absent asset

_FORBIDDEN = re.compile(r'[/\\:*?"<>|#\^\[\]]')  # unsafe in a filename / an Obsidian link
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
    assets_written: int = 0
    assets_missing: int = 0
    notes: list[str] = field(default_factory=list)


def _md():
    """`markdownify` lazily, with a clear message when the extra is absent."""
    try:
        from markdownify import markdownify  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "the Obsidian ingester needs `markdownify` — install the extra: "
            "pip install 'connections-export[obsidian]'"
        ) from exc
    return markdownify


def _safe(name: str, fallback: str) -> str:
    cleaned = _FORBIDDEN.sub("-", (name or "").strip()).strip(". ")
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


def _yaml_scalar(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _frontmatter(page: DerivedPage) -> str:
    lines = ["---", f"title: {_yaml_scalar(page.title or page.label or page.id)}"]
    if page.label:
        lines.append(f"aliases: [{_yaml_scalar(page.label)}]")
    if page.author:
        lines.append(f"author: {_yaml_scalar(page.author)}")
    if page.created:
        lines.append(f"created: {_yaml_scalar(page.created)}")
    if page.modified:
        lines.append(f"modified: {_yaml_scalar(page.modified)}")
    if page.tags:
        lines.append("tags: [" + ", ".join(_yaml_scalar(t) for t in page.tags) + "]")
    if page.provenance and page.provenance.hcl_id:
        lines.append(f"hcl_id: {_yaml_scalar(page.provenance.hcl_id)}")
    if page.provenance and page.provenance.source_url:
        lines.append(f"source_url: {_yaml_scalar(page.provenance.source_url)}")
    lines.append("---\n")
    return "\n".join(lines)


def _rewrite_body(page: DerivedPage, store: _AssetStore, id_to_title: dict[str, str]) -> str:
    """Rewrite the page body's `<img>`/`<a>` to sentinels (resolved after
    markdownify), copying present image blobs and mapping in-export links.
    Uses `markdownify`'s own BeautifulSoup so parsing matches the converter."""
    from bs4 import BeautifulSoup  # noqa: PLC0415 - ships with markdownify

    soup = BeautifulSoup(page.content_html or "", "html.parser")

    asset_by_href = {}
    for asset in page.assets:
        asset_by_href[asset.original_href] = asset
        if asset.resolved_url:
            asset_by_href[asset.resolved_url] = asset
    for img in soup.find_all("img"):
        asset = asset_by_href.get(img.get("src", ""))
        if asset is None:
            continue
        if asset.present and asset.blob_hash:
            name = store.store(
                blob_hash=asset.blob_hash,
                preferred_name=Path(urlsplit(asset.original_href).path).name or "image",
                content_type=None,
            )
            # store already counts a present-but-uncaptured blob as missing
            img["src"] = f"{_EMBED}{name}" if name else f"{_MISSING}{asset.original_href}"
        else:
            img["src"] = f"{_MISSING}{asset.original_href}"
            store.note_missing()

    link_by_href = {link.original_href: link for link in page.links}
    for a in soup.find_all("a"):
        link = link_by_href.get(a.get("href", ""))
        if link and link.scope == "in_export" and link.target_page_id in id_to_title:
            a["href"] = f"{_LINK}{link.target_page_id}"
        # hcl_deployment / external links keep their original href (§7 step 5)

    markdown = _md()(str(soup), heading_style="ATX", bullets="-")
    return _apply_sentinels(markdown, id_to_title)


def _apply_sentinels(markdown: str, id_to_title: dict[str, str]) -> str:
    # ![alt](obsidian-embed:NAME) -> ![[NAME]]
    markdown = re.sub(r"!\[[^\]]*\]\(" + re.escape(_EMBED) + r"([^)]+)\)", r"![[\1]]", markdown)
    # ![alt](obsidian-missing:HREF) -> a visible, non-silent gap
    markdown = re.sub(
        r"!\[[^\]]*\]\(" + re.escape(_MISSING) + r"([^)]+)\)",
        r"`[image not captured: \1]`",
        markdown,
    )

    def _link(match: re.Match) -> str:
        text, target = match.group(1), match.group(2)
        title = id_to_title.get(target, target)
        return f"[[{title}|{text}]]" if text and text != title else f"[[{title}]]"

    # [text](obsidian-link:ID) -> [[Title|text]]
    markdown = re.sub(r"\[([^\]]*)\]\(" + re.escape(_LINK) + r"([^)]+)\)", _link, markdown)
    return markdown


def _comments_md(page: DerivedPage) -> str:
    if not page.comments:
        return ""
    children: dict[str | None, list] = {}
    for comment in page.comments:
        children.setdefault(comment.parent_comment_id, []).append(comment)
    strip = re.compile(r"<[^>]+>")

    def render(parent_id: str | None, depth: int, out: list[str]) -> None:
        for comment in children.get(parent_id, []):
            who = comment.author or "Unknown"
            when = f" · {comment.created}" if comment.created else ""
            body = strip.sub("", comment.content_html or "").strip().replace("\n", " ")
            out.append(f"{'    ' * depth}- **{who}**{when}: {body}")
            render(comment.id, depth + 1, out)

    lines: list[str] = []
    render(None, 0, lines)
    # comments whose parent isn't in this page (orphaned reply) stay top-level
    seen = {c.id for c in page.comments}
    for comment in page.comments:
        if comment.parent_comment_id and comment.parent_comment_id not in seen:
            children.setdefault(None, []).append(comment)
    return "\n## Comments\n\n" + "\n".join(lines) + "\n" if lines else ""


def _attachments_md(page: DerivedPage, store: _AssetStore) -> str:
    if not page.attachments:
        return ""
    rows: list[str] = []
    for att in page.attachments:
        name = _safe(att.filename or att.id, "attachment")
        if att.asset.present and att.asset.blob_hash:
            vault_name = store.store(
                blob_hash=att.asset.blob_hash,
                preferred_name=att.filename or att.id,
                content_type=att.content_type,
            )
            rows.append(f"- [[{vault_name}]]" if vault_name else f"- `[not captured: {name}]`")
        else:
            rows.append(f"- `[not captured: {name}]`")
    return "\n## Attachments\n\n" + "\n".join(rows) + "\n" if rows else ""


def _versions_note(page: DerivedPage) -> str:
    if not page.versions:
        return ""
    full = sum(1 for v in page.versions if v.content_present)
    detail = f"{len(page.versions)} revision(s)" + (
        f", {full} with captured content" if full else " (metadata only)"
    )
    return f"\n## History\n\n> {detail}\n"


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


def write_obsidian_vault(
    interchange: Interchange, blob_reader: BlobReader, out_dir: Path | str
) -> VaultStats:
    """Write `interchange`'s wikis as an Obsidian vault under `out_dir`.
    `blob_reader` resolves an asset/attachment `blob_hash` to bytes (or
    `None` if absent). Returns what was written."""
    vault = Path(out_dir)
    vault.mkdir(parents=True, exist_ok=True)
    stats = VaultStats()
    store = _AssetStore(vault / "attachments", blob_reader, stats)

    # Obsidian resolves [[Title]] by note name across the whole vault, so
    # one id->title map spans every wiki (a caveat when titles collide).
    id_to_title = {
        pid: (p.title or p.label or pid)
        for wiki in interchange.wikis
        for pid, p in wiki.pages.items()
    }

    for wiki in interchange.wikis:
        stats.wikis += 1
        wiki_dir = _safe(wiki.title or wiki.label, wiki.id)
        for page in wiki.pages.values():
            note = _note_path(vault, wiki_dir, page, wiki.pages)
            note.parent.mkdir(parents=True, exist_ok=True)
            body = _rewrite_body(page, store, id_to_title)
            heading = f"# {page.title or page.label or page.id}\n\n"
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

    _write_readme(vault, interchange, stats)
    return stats


def _write_readme(vault: Path, interchange: Interchange, stats: VaultStats) -> None:
    lines = [
        "# HCL export → Obsidian vault",
        "",
        "Reconstructed from an interchange package"
        + (f" of `{interchange.base_url}`" if interchange.base_url else "")
        + f": {stats.wikis} wiki(s), {stats.pages} page(s), "
        + f"{stats.assets_written} attachment(s) copied.",
        "",
        "## Wikis",
        "",
    ]
    for wiki in interchange.wikis:
        lines.append(f"### {wiki.title or wiki.label}")
        for root_id in wiki.root_page_ids:
            page = wiki.pages.get(root_id)
            if page:
                lines.append(f"- [[{page.title or page.label or root_id}]]")
        lines.append("")
    if stats.assets_missing:
        lines.append(
            f"> {stats.assets_missing} referenced asset(s) were not captured in the "
            "package — shown as visible gaps, not dropped."
        )
    (vault / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
