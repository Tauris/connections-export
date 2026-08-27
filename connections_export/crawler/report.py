"""Completeness + orphan detection over a crawl run.

Base counts and the truncation signature come straight from
`archive.report` -- the archive already tracks those over the whole
manifest. Orphan detection is the crawler's own: a page whose `parent`
uuid is non-null and resolves to no page in the crawled set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connections_export.adapters.model import NavNode
from connections_export.archive.store import ArchiveReport


@dataclass
class CrawlReport:
    """The end-of-run report `run_complete` carries."""

    counts: dict[str, int] = field(default_factory=dict)
    failures_by_category: dict[str, int] = field(default_factory=dict)
    possibly_truncated: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    pages_crawled: int = 0


def detect_orphans(nodes: list[NavNode]) -> list[str]:
    """Ids of nodes whose non-null `parent` resolves to no node in
    `nodes`. The synthetic nav tree root
    is never a node here (see `NavTree.root_id`); true top-level pages
    have `parent` None/empty and are never orphans."""
    ids = {node.id for node in nodes}
    return [node.id for node in nodes if node.parent and node.parent not in ids]


def build_report(
    archive_report: ArchiveReport, *, orphans: list[str], pages_crawled: int
) -> CrawlReport:
    """Assemble the final `CrawlReport` from the archive's own
    completeness summary plus the crawler's orphan findings."""
    return CrawlReport(
        counts=dict(archive_report.counts),
        failures_by_category=dict(archive_report.failures_by_category),
        possibly_truncated=list(archive_report.possibly_truncated),
        orphans=list(orphans),
        pages_crawled=pages_crawled,
    )
