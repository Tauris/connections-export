"""Several archives' models, combined into one for a single export.

Someone who captured a community in March and again in June, or captured two
communities separately, wants one Hugo site, one Jekyll site or one Obsidian
vault out of them -- not two that link to each other's content on a deployment
that may be gone. This sits one level below the exporters: it takes the
per-archive models and their blob readers and hands back ONE model and ONE
blob reader, so every exporter works on the result exactly as it works on a
single archive.

The rules, in the order they apply:

**Deployments are kept apart.** An item is identified by its Connections id,
and ids are unique within a deployment. Two archives of different deployments
(told apart by their `base_url`) whose ids happen to coincide hold two
different things: the later archive's copy is given a suffixed id
(`<id>~<n>`, `n` its position in the list) everywhere it is referred to, and
the collision is reported. Nothing is merged across deployments.

**Most recent capture wins.** The same container (a wiki, a blog, a forum, a
file library, a Highlights area) or the same item (a page, a post, a topic, a
file, a Highlights page) in several archives appears once. Each copy is dated
by the most specific capture time known for it:

1. the item's own fetch time -- the newest successful fetch of its
   `provenance.source_url` in its archive's manifest -- when the item has one;
2. otherwise its archive's capture time: the start of the archive's latest
   finished run (the moment `crawler.provenance.last_successful_run`
   anchors updates on), else of its latest run of any kind, else its newest
   manifest record (`capture_times`);
3. otherwise nothing -- which sorts before every dated copy.

A container is dated by its archive's capture time. The newest copy wins; on a
tie, the archive listed later wins. An archive updated in June that did not
re-read a page still holds the March copy of it, which is why an item's own
fetch time comes first.

Merging is per item. A wiki present in an older and a newer archive holds the
union of their pages, each page from the newest archive that has it; the
wiki's own title and metadata come from the newest archive holding the wiki.
A page's parent comes from its winning copy, and child lists are rebuilt from
those parents so a page that moved between captures sits in one place only; a
page whose winning parent no archive holds stands at the top level, as an
orphan does in a single export. Comments, replies, versions and attachments
belong to the item they were captured with and travel with the winning copy --
two copies' comments are never mixed.

**Links between archives become internal.** A link one archive could only
record as `hcl_deployment` -- content on the deployment it did not hold --
becomes `in_export` when another archive of the SAME deployment holds the
target. The lookup is the one `derive.crosslink` builds for a single export
(browser URL, wiki entry URL, page label, `/wiki/<w>/page/<p>` path, Files
document id), and the upgrade is its `upgrade_link`, so a link resolves across
archives on exactly the terms it resolves within one.

Nothing disappears silently: `CombineReport` counts every duplicate by kind,
names the winning archive of every duplicated container, counts the links it
made internal and lists every id it had to keep apart.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from connections_export.archive.blobs import valid_digest
from connections_export.derive.crosslink import upgrade_link
from connections_export.derive.links import build_page_lookup
from connections_export.derive.model import (
    DerivedCommunity,
    DerivedItem,
    DerivedPage,
    Interchange,
)

#: `blob_hash -> bytes`, or `None` when no archive holds it -- the reader
#: shape every exporter takes.
BlobReader = Callable[[str], bytes | None]

#: Every container field of the model, the kind a report calls it, the field
#: holding its items, the field ordering them, and the kind of item. Wikis
#: order by their page tree rather than a flat list, handled on their own.
_CONTAINERS = (
    ("wikis", "wiki", "pages", "root_page_ids", "page"),
    ("blogs", "blog", "posts", "post_ids", "post"),
    ("forums", "forum", "topics", "topic_ids", "topic"),
    ("file_libraries", "file library", "files", "file_ids", "file"),
    ("rich_content", "highlights", "pages", "page_ids", "highlights page"),
)

#: Fields that hold one id of a container or an item, and fields that hold a
#: list of them -- what a renamed id is rewritten in.
_ID_FIELDS = frozenset(
    {
        "id",
        "parent_id",
        "resource_id",
        "target_page_id",
        "target_file_id",
        "wiki_id",
        "blog_id",
        "ideation_blog_id",
        "file_library_id",
        "rich_content_id",
    }
)
_ID_LIST_FIELDS = frozenset(
    {
        "child_ids",
        "root_page_ids",
        "post_ids",
        "topic_ids",
        "file_ids",
        "page_ids",
        "folder_ids",
        "forum_ids",
    }
)
#: Dicts keyed by a container's items' ids.
_KEYED_FIELDS = frozenset({"pages", "posts", "topics", "files"})


@dataclass
class CombineInput:
    """One archive going into a combined export.

    `label` names it in the report and in every item's `source_archive`.
    `captured_at` is the archive's capture time and `fetched_at` its per-URL
    fetch times (`capture_times` reads both from an archive); either may be
    empty, and then the rules in the module docstring fall back.
    """

    label: str
    model: Interchange
    blob_reader: BlobReader
    captured_at: str | None = None
    fetched_at: Mapping[str, str] = field(default_factory=dict)


@dataclass
class DuplicateContainer:
    """A container more than one archive held, and which archive's copy won."""

    kind: str
    id: str
    title: str
    winner: str
    #: Every archive that held it, in the order they were given.
    archives: list[str]


@dataclass
class IdCollision:
    """An id two deployments both used, and what this archive's copy became."""

    kind: str
    id: str
    renamed_to: str
    archive: str


@dataclass
class CombineReport:
    archives: list[str]
    captured_at: dict[str, str | None] = field(default_factory=dict)
    #: Duplicates resolved, by kind: `wiki`, `page`, `blog`, `post`, ...
    duplicates: dict[str, int] = field(default_factory=dict)
    containers: list[DuplicateContainer] = field(default_factory=list)
    links_resolved: int = 0
    collisions: list[IdCollision] = field(default_factory=list)
    #: Container or item id -> the label of the archive its copy came from.
    origins: dict[str, str] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def summary(self) -> str:
        """One line: what was combined, and what combining changed."""
        text = (
            f"{len(self.archives)} archives combined, "
            f"{self.duplicates_total} duplicate(s) merged (most recent capture kept), "
            f"{self.links_resolved} cross-archive link(s) resolved"
        )
        if self.collisions:
            text += f", {len(self.collisions)} id(s) from different deployments kept apart"
        return text

    def as_dict(self) -> dict:
        """The report as JSON-ready data, for the console."""
        return {
            "archives": list(self.archives),
            "captured_at": dict(self.captured_at),
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "containers": [vars(container).copy() for container in self.containers],
            "links_resolved": self.links_resolved,
            "collisions": [vars(collision).copy() for collision in self.collisions],
        }


# --- capture times ---------------------------------------------------------------


def _instant(value: str | None) -> datetime | None:
    """`value` as an aware datetime, or `None` when it is not a date. A date
    with no zone is read as UTC, which is how every archive writes them."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def capture_times(path: Path | str) -> tuple[str | None, dict[str, str]]:
    """`(captured_at, fetched_at)` for the archive at `path` (a directory or a
    `.zip`): the archive's capture time, and the newest successful fetch time
    of every URL in its manifest. `(None, {})` for anything that is not an
    archive -- a package records when it was written, not when its content
    was captured, so it has no capture time to offer.
    """
    from connections_export.archive.records import RunMetadata  # noqa: PLC0415
    from connections_export.archive.source import ArchiveSourceError, source_for  # noqa: PLC0415

    try:
        source = source_for(path)
    except ArchiveSourceError:
        return None, {}
    fetched: dict[str, str] = {}
    newest_record: str | None = None
    try:
        for line in source.read_lines("manifest.jsonl"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            when, url = record.get("fetched_at"), record.get("url")
            if not isinstance(when, str) or _instant(when) is None:
                continue
            if newest_record is None or _instant(when) > _instant(newest_record):
                newest_record = when
            if record.get("outcome") == "ok" and isinstance(url, str):
                known = fetched.get(url)
                if known is None or _instant(when) > _instant(known):
                    fetched[url] = when
    except (ArchiveSourceError, OSError):
        pass

    finished: list[str] = []
    started: list[str] = []
    try:
        entries = source.run_metadata()
    except (ArchiveSourceError, OSError):
        entries = []
    for entry in entries:
        try:
            run = RunMetadata.model_validate_json(entry.data)
        except ValueError:
            continue
        if run.started_at and _instant(run.started_at):
            started.append(run.started_at)
            if run.completed_at:
                finished.append(run.started_at)
    for candidates in (finished, started):
        if candidates:
            return max(candidates, key=_instant), fetched
    return newest_record, fetched


# --- deployments and ids ------------------------------------------------------------


def _deployment(model: Interchange) -> str:
    """Which deployment a model came from: its base URL, compared without
    case in the host and without a trailing slash. An archive that does not
    know (`None`) is taken to share a deployment with others that do not."""
    if not model.base_url:
        return ""
    split = urlsplit(model.base_url)
    return f"{(split.hostname or '').lower()}:{split.port or ''}{split.path.rstrip('/')}"


def _owned_ids(model: Interchange) -> dict[str, str]:
    """Every container and item id in `model`, with its kind."""
    ids: dict[str, str] = {}
    for field_name, kind, items_field, _order, item_kind in _CONTAINERS:
        for container in getattr(model, field_name):
            ids.setdefault(container.id, kind)
            for item_id in getattr(container, items_field):
                ids.setdefault(item_id, item_kind)
    return ids


def _renamed(model: Interchange, mapping: dict[str, str]) -> Interchange:
    """`model` with every id in `mapping` replaced -- as an item's own id, a
    key of its container, and every reference to it."""

    def walk(value, key: str | None = None):
        if isinstance(value, dict):
            renamed = {}
            for child_key, child in value.items():
                new_key = mapping.get(child_key, child_key) if key in _KEYED_FIELDS else child_key
                renamed[new_key] = walk(child, child_key)
            return renamed
        if isinstance(value, list):
            if key in _ID_LIST_FIELDS:
                return [
                    mapping.get(item, item) if isinstance(item, str) else item for item in value
                ]
            return [walk(item) for item in value]
        if key in _ID_FIELDS and isinstance(value, str):
            return mapping.get(value, value)
        return value

    return Interchange.model_validate(walk(model.model_dump()))


# --- merging ------------------------------------------------------------------------------

_EARLIEST = datetime.min.replace(tzinfo=UTC)


@dataclass
class _Source:
    index: int
    label: str
    model: Interchange
    deployment: str
    captured: datetime | None
    fetched: Mapping[str, str]

    def rank(self) -> tuple[datetime, int]:
        return (self.captured or _EARLIEST, self.index)

    def item_rank(self, item: DerivedItem) -> tuple[datetime, int]:
        own = _instant(self.fetched.get(item.provenance.source_url or ""))
        return (own or self.captured or _EARLIEST, self.index)


def _ordered_union(lists: Iterable[Iterable[str]], keep: Mapping) -> list[str]:
    seen: list[str] = []
    for ids in lists:
        for item_id in ids:
            if item_id in keep and item_id not in seen:
                seen.append(item_id)
    return seen


def _rebuild_tree(wiki, copies) -> None:
    """Child lists from the winning parents, and the orphans at the top."""
    pages = wiki.pages
    children: dict[str, list[str]] = {}
    for page_id, page in pages.items():
        if page.parent_id in pages:
            children.setdefault(page.parent_id, []).append(page_id)
    for page_id, page in pages.items():
        mine = children.get(page_id, [])
        listed = [child for child in page.child_ids if child in mine]
        rest = sorted((c for c in mine if c not in listed), key=lambda c: pages[c].ordinal)
        pages[page_id] = page.model_copy(update={"child_ids": listed + rest})
    top = {page_id for page_id, page in pages.items() if page.parent_id not in pages}
    roots = _ordered_union((copy.root_page_ids for _s, copy in copies), top)
    roots += sorted((p for p in top if p not in roots), key=lambda p: pages[p].ordinal)
    wiki.root_page_ids = roots


def _merge_containers(sources: list[_Source], report: CombineReport) -> dict[str, list]:
    merged: dict[str, list] = {}
    for field_name, kind, items_field, order_field, item_kind in _CONTAINERS:
        copies_by_id: dict[str, list] = {}
        for source in sources:
            for container in getattr(source.model, field_name):
                copies_by_id.setdefault(container.id, []).append((source, container))
        result = []
        for container_id, copies in copies_by_id.items():
            winner_source, winner = max(copies, key=lambda pair: pair[0].rank())
            report.origins[container_id] = winner_source.label
            if len(copies) > 1:
                report.duplicates[kind] = report.duplicates.get(kind, 0) + 1
                report.containers.append(
                    DuplicateContainer(
                        kind=kind,
                        id=container_id,
                        title=_title(winner),
                        winner=winner_source.label,
                        archives=[source.label for source, _c in copies],
                    )
                )
            # The winner's copy first, so its order leads; then the others in
            # the order they were given.
            ranked = [(winner_source, winner)] + [pair for pair in copies if pair[1] is not winner]
            items: dict[str, tuple] = {}
            for source, copy in ranked:
                for item_id, item in getattr(copy, items_field).items():
                    candidate = (source.item_rank(item), source, item)
                    if item_id in items:
                        report.duplicates[item_kind] = report.duplicates.get(item_kind, 0) + 1
                        if candidate[0] <= items[item_id][0]:
                            continue
                    items[item_id] = candidate
            ordered = _ordered_union(
                [getattr(copy, order_field) for _s, copy in ranked]
                + [getattr(copy, items_field) for _s, copy in ranked],
                items,
            )
            for item_id in ordered:
                report.origins[item_id] = items[item_id][1].label
            container = winner.model_copy(
                update={
                    items_field: {item_id: items[item_id][2] for item_id in ordered},
                    order_field: _ordered_union(
                        [getattr(copy, order_field) for _s, copy in ranked], items
                    ),
                }
            )
            if field_name == "wikis":
                _rebuild_tree(container, ranked)
            elif field_name == "file_libraries":
                _merge_folders(container, ranked)
            result.append(container)
        merged[field_name] = result
    return merged


def _merge_folders(library, ranked) -> None:
    """Folders by id, the newest copy's first. Each lists what any copy of it
    listed and every file whose winning copy names it -- except a file whose
    winning copy names other folders only: that file has moved out."""
    files = library.files
    folders = {}
    listed: dict[str, list[list[str]]] = {}
    for _source, copy in ranked:
        for folder in copy.folders:
            folders.setdefault(folder.id, folder)
            listed.setdefault(folder.id, []).append(folder.file_ids)
    merged = []
    for folder_id, folder in folders.items():
        members = [
            file_id
            for file_id in _ordered_union(
                [*listed[folder_id], [f for f in files if folder_id in files[f].folder_ids]],
                files,
            )
            if not files[file_id].folder_ids or folder_id in files[file_id].folder_ids
        ]
        merged.append(folder.model_copy(update={"file_ids": members}))
    library.folders = merged


def _title(container) -> str:
    return (
        getattr(container, "title", None)
        or getattr(container, "community_title", None)
        or getattr(container, "handle", None)
        or container.id
    )


def _merge_communities(sources: list[_Source]) -> list[DerivedCommunity]:
    """One community per id: the newest copy, with what only an older copy
    recorded (a blog, a library, a logo) filled in -- a community is a
    grouping of containers, and every archive saw some of them."""
    by_id: dict[str, list] = {}
    for source in sources:
        for community in source.model.communities:
            by_id.setdefault(community.id, []).append((source, community))
    result = []
    for copies in by_id.values():
        copies.sort(key=lambda pair: pair[0].rank(), reverse=True)
        merged = copies[0][1].model_copy(deep=True)
        for _source, other in copies[1:]:
            for name in (
                "title",
                "wiki_id",
                "blog_id",
                "ideation_blog_id",
                "file_library_id",
                "rich_content_id",
                "logo",
            ):
                if getattr(merged, name) is None:
                    setattr(merged, name, getattr(other, name))
            merged.forum_ids += [f for f in other.forum_ids if f not in merged.forum_ids]
        result.append(merged)
    return result


def _same_or_none(values: list):
    distinct = {value for value in values}
    return values[0] if len(distinct) == 1 else None


def _resolve_links(model: Interchange, sources: list[_Source], report: CombineReport) -> None:
    """Re-resolve every link that left its archive against the whole combined
    model -- one lookup per deployment, so a link never lands in another."""
    deployment_of = {source.label: source.deployment for source in sources}

    def deployment(entity_id: str) -> str:
        return deployment_of.get(report.origins.get(entity_id, ""), "")

    # `(item, the id whose origin it shares)`: a forum reply came with its
    # topic and has no origin of its own.
    linkable: list[tuple[DerivedItem, str]] = []
    entries: dict[str, list[tuple[str, str, str | None]]] = {}
    for wiki in model.wikis:
        for page in wiki.pages.values():
            linkable.append((page, page.id))
            # The entry URL as well as the browser URL: derive matched a wiki
            # link against the entry URL, so a link can name either.
            for url in (page.alternate_url, page.provenance.source_url):
                if url:
                    entries.setdefault(deployment(page.id), []).append((page.id, url, page.label))
    for blog in model.blogs:
        linkable.extend((post, post.id) for post in blog.posts.values())
    for forum in model.forums:
        for topic in forum.topics.values():
            linkable.append((topic, topic.id))
            linkable.extend((reply, topic.id) for reply in topic.replies.values())
    for area in model.rich_content:
        linkable.extend((page, page.id) for page in area.pages.values())
    for item, owner in linkable:
        # Pages are in already, with their label and entry URL.
        if owner == item.id and item.alternate_url and not isinstance(item, DerivedPage):
            entries.setdefault(deployment(item.id), []).append((item.id, item.alternate_url, None))
    files: dict[str, dict[str, str]] = {}
    for library in model.file_libraries:
        for file_id in library.files:
            files.setdefault(deployment(file_id), {})[file_id] = file_id

    lookups = {key: build_page_lookup(found) for key, found in entries.items()}
    for item, owner in linkable:
        key = deployment(owner)
        for link in item.links:
            if upgrade_link(link, lookup=lookups.get(key, {}), files=files.get(key, {})):
                report.links_resolved += 1


# --- the entry point -------------------------------------------------------------------------


def combine_interchanges(
    inputs: Sequence[CombineInput],
) -> tuple[Interchange, BlobReader, CombineReport]:
    """One model, one blob reader and a report out of several archives'. The
    rules are the module docstring's. The inputs are left as they were."""
    if not inputs:
        raise ValueError("nothing to combine: no archives were given")

    report = CombineReport(
        archives=[entry.label for entry in inputs],
        captured_at={entry.label: entry.captured_at for entry in inputs},
    )
    sources: list[_Source] = []
    claimed: dict[str, str] = {}
    for index, entry in enumerate(inputs):
        deployment = _deployment(entry.model)
        owned = _owned_ids(entry.model)
        clashing = {
            item_id: f"{item_id}~{index + 1}"
            for item_id in owned
            if claimed.get(item_id, deployment) != deployment
        }
        model = _renamed(entry.model, clashing) if clashing else entry.model.model_copy(deep=True)
        for item_id, new_id in clashing.items():
            report.collisions.append(
                IdCollision(kind=owned[item_id], id=item_id, renamed_to=new_id, archive=entry.label)
            )
        for item_id in _owned_ids(model):
            claimed.setdefault(item_id, deployment)
        sources.append(
            _Source(
                index=index,
                label=entry.label,
                model=model,
                deployment=deployment,
                captured=_instant(entry.captured_at),
                fetched=entry.fetched_at or {},
            )
        )

    merged = _merge_containers(sources, report)
    models = [source.model for source in sources]
    combined = Interchange(
        base_url=_same_or_none([model.base_url for model in models]),
        author_filter=_same_or_none([model.author_filter for model in models]),
        source_version=_same_or_none([model.source_version for model in models]),
        run_id=None,
        hcl_hosts=list(dict.fromkeys(host for model in models for host in model.hcl_hosts)),
        communities=_merge_communities(sources),
        **merged,
    )
    _resolve_links(combined, sources, report)

    # Newest first: a blob is content-addressed, so any archive holding the
    # digest holds the same bytes, and the newest is the likeliest to.
    readers = [
        entry.blob_reader
        for _rank, entry in sorted(
            ((source.rank(), inputs[source.index]) for source in sources),
            key=lambda pair: pair[0],
            reverse=True,
        )
    ]

    def read_blob(blob_hash: str) -> bytes | None:
        if valid_digest(blob_hash) is None:
            return None
        for reader in readers:
            data = reader(blob_hash)
            if data is not None:
                return data
        return None

    return combined, read_blob, report


class CombinedSource:
    """A combined model behind the `get_model()`/`get_blob()` pair every
    exporter's `from_source` reads, carrying its `combine_report` so the
    exporter can say which archive each item came from."""

    def __init__(self, inputs: Sequence[CombineInput]):
        self._model, self._read, self.combine_report = combine_interchanges(inputs)

    def get_model(self) -> Interchange:
        return self._model

    def get_blob(self, blob_hash: str) -> tuple[bytes, str] | None:
        data = self._read(blob_hash)
        return (data, "application/octet-stream") if data is not None else None


def input_from_source(label: str, source, path: Path | str | None = None) -> CombineInput:
    """A `CombineInput` from a model source (`gui.model_source.ModelSource`
    or anything with `get_model`/`get_blob`) and, when it is an archive, the
    path its capture times are read from."""
    model = source.get_model()
    if model is None:
        raise ValueError(f"{label}: the archive holds no derivable content")

    def read(blob_hash: str) -> bytes | None:
        result = source.get_blob(blob_hash)
        return result[0] if result is not None else None

    captured_at, fetched_at = capture_times(path) if path is not None else (None, {})
    return CombineInput(
        label=label, model=model, blob_reader=read, captured_at=captured_at, fetched_at=fetched_at
    )


def unique_labels(names: Iterable[str]) -> list[str]:
    """`names`, each made unique by a ` (2)`, ` (3)` ... suffix -- two archives
    called the same in different folders must still be told apart."""
    seen: dict[str, int] = {}
    result = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        result.append(name if count == 1 else f"{name} ({count})")
    return result
