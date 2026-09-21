"""A reference ingester that writes a conventional Jekyll Markdown site.

The output is deliberately a site fragment rather than a complete site:
``_posts/`` contains dated Markdown posts and body images are placed below
``assets/images/imported/``.  A site's own ``_config.yml`` remains its
responsibility, including ``url``, ``baseurl`` and layout choices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from connections_export.derive.model import DerivedItem, Interchange
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


def _rewrite_body(
    item: DerivedItem,
    store: _AssetStore,
    id_to_permalink: dict[str, str],
    file_targets: dict[str, str] | None = None,
) -> str:
    from bs4 import BeautifulSoup  # noqa: PLC0415 - optional ingester dependency

    soup = BeautifulSoup(item.content_html or "", "html.parser")
    assets = {}
    for asset in item.assets:
        assets[asset.original_href] = asset
        if asset.resolved_url:
            assets[asset.resolved_url] = asset

    for image in soup.find_all("img"):
        asset = assets.get(image.get("src", ""))
        if asset is None:
            continue
        if asset.present and asset.blob_hash:
            name = store.store(
                blob_hash=asset.blob_hash,
                preferred_name=Path(urlsplit(asset.original_href).path).name or "image",
                content_type=None,
            )
            image["src"] = f"{_ASSET}{name}" if name else f"{_MISSING}{asset.original_href}"
        else:
            image["src"] = f"{_MISSING}{asset.original_href}"
            store._stats.assets_missing += 1

    files = file_targets or {}
    links = {link.original_href: link for link in item.links}
    for anchor in soup.find_all("a"):
        link = links.get(anchor.get("href", ""))
        if not link or link.scope != "in_export":
            continue
        if link.target_page_id in id_to_permalink:
            anchor["href"] = f"{_LINK}{link.target_page_id}"
        elif link.target_file_id and link.target_file_id in files:
            # A body link to a community file, resolved to the copied file.
            anchor["href"] = f"{_FILELINK}{link.target_file_id}"

    markdown = _md()(str(soup), heading_style="ATX", bullets="-")
    asset_pattern = re.escape(_ASSET) + r"([^)]+)"
    markdown = re.sub(
        r"!\[([^\]]*)\]\(" + asset_pattern + r"\)",
        lambda match: (
            f"![{match.group(1)}]({{{{ '{_asset_path(match.group(2))}' | relative_url }}}})"
        ),
        markdown,
    )
    markdown = re.sub(
        r"!\[[^\]]*\]\(" + re.escape(_MISSING) + r"([^)]+)\)",
        r"`[image not captured: \1]`",
        markdown,
    )

    def replace_link(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        permalink = id_to_permalink.get(target, target)
        return f"[{text}]({{{{ '{permalink}' | relative_url }}}})"

    markdown = re.sub(r"\[([^\]]*)\]\(" + re.escape(_LINK) + r"([^)]+)\)", replace_link, markdown)

    def replace_file(match: re.Match[str]) -> str:
        text, fid = match.group(1), match.group(2)
        name = files.get(fid, fid)
        return f"[{text or name}]({{{{ '/assets/files/{name}' | relative_url }}}})"

    return re.sub(r"\[([^\]]*)\]\(" + re.escape(_FILELINK) + r"([^)]+)\)", replace_file, markdown)


def _frontmatter(item: DerivedItem, *, kind: str, date_value: str, tags: list[str]) -> str:
    lines = ["---", f"title: {_yaml_scalar(item.title or item.id)}", f"date: {date_value}"]
    if item.provenance.source_url:
        lines.append(f"source_url: {_yaml_scalar(item.provenance.source_url)}")
    if item.author:
        lines.append(f"author: {_yaml_scalar(item.author)}")
    if tags:
        lines.append("tags: [" + ", ".join(_yaml_scalar(tag) for tag in tags) + "]")
    lines.append(f"kind: {kind}")
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
            body = re.sub(r"<[^>]+>", "", comment.content_html or "").strip()
            when = f" ({comment.created})" if comment.created else ""
            lines.append(f"{'  ' * depth}- **{comment.author or 'Unknown'}**{when}: {body}")
            render(comment.id, depth + 1)

    render(None, 0)
    return "\n".join(lines) + "\n"


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
                lines.append(f"- [{name}]({{{{ '{_asset_path(stored)}' | relative_url }}}})")
                continue
        lines.append(f"- `[not captured: {name}]`")
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
        lines.append(f"## {library.title or library.id}")
        lines.append("")
        ordered = [library.files[i] for i in library.file_ids if i in library.files]
        ordered += [f for i, f in library.files.items() if i not in library.file_ids]
        for derived in ordered:
            stats.files += 1
            label = derived.name or derived.title or derived.id
            stored = file_targets.get(derived.id)
            if stored:
                lines.append(f"- [{label}]({{{{ '/assets/files/{stored}' | relative_url }}}})")
            else:
                lines.append(f"- `[not captured: {label}]`")
        lines.append("")
    (root / "files.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jekyll_site(
    interchange: Interchange, blob_reader: BlobReader, out_dir: Path | str
) -> JekyllStats:
    """Write a generic Jekyll site fragment under ``out_dir``."""
    root = Path(out_dir)
    posts_dir = root / "_posts"
    stats = JekyllStats()
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
        body = _rewrite_body(item, store, id_to_permalink, file_targets).strip()
        content = _frontmatter(item, kind=kind, date_value=date_value, tags=tags)
        content += body + "\n" if body else ""
        content += _attachments(item, store) + _comments(item)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    stats.posts = len(paths)
    _write_files_index(root, interchange, file_targets, stats)
    return stats


def from_source(source, out_dir: Path | str) -> JekyllStats:
    """Write a Jekyll site fragment from any compatible model source."""
    interchange = source.get_model()
    if interchange is None:
        raise ValueError("nothing to ingest: the source holds no derivable content")

    def blob_reader(blob_hash: str) -> bytes | None:
        result = source.get_blob(blob_hash)
        return result[0] if result is not None else None

    return write_jekyll_site(interchange, blob_reader, out_dir)
