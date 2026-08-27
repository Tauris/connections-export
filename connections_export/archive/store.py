"""Archive: the single public entry point for the archive capability.

Opens/creates the on-disk layout described in the design:

    <root>/manifest.jsonl append-only, one JSON object per line
    <root>/blobs/<sha256hex> response bodies, content-addressed
    <root>/run-<run_id>.json one per crawl run

`Archive` never parses response bodies. The only "interpreted" data it
accepts is the optional `(item_count, page_size, has_next)` truncation
signature, which the *caller* has already parsed out of a feed body —
the archive just stores and checks that triple.
"""

# Annotations are deferred here because `source` is typed with a name that
# exists only under TYPE_CHECKING. Without this, Python 3.12 evaluates the
# annotation at class-definition time and the import fails -- which 3.14's
# lazy annotations (PEP 649) hide entirely. The project supports 3.12.
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from connections_export.archive.blobs import write_blob
from connections_export.archive.feeds import FEEDS_FILENAME, FeedPage

if TYPE_CHECKING:  # `source` imports MANIFEST_FILENAME from here -- a cycle at runtime
    from connections_export.archive.source import ArchiveSource
from connections_export.archive.records import ManifestRecord, Outcome, RunMetadata
from connections_export.archive.redact import redact_headers

MANIFEST_FILENAME = "manifest.jsonl"


@dataclass
class ArchiveReport:
    """A completeness summary over a run."""

    counts: dict[str, int] = field(default_factory=dict)
    failures_by_category: dict[str, int] = field(default_factory=dict)
    possibly_truncated: list[str] = field(default_factory=list)


def _content_type_of(headers: Mapping[str, str]) -> str | None:
    """Pull the (unparsed-body, header-only) content type out of response
    headers, dropping any `;charset=...` parameter. This reads a header,
    never the body, so it doesn't violate the no-parsing rule."""
    for name, value in headers.items():
        if name.lower() == "content-type":
            return value.split(";", 1)[0].strip()
    return None


def _looks_truncated(record: Any) -> bool:
    """A full page that claims to be the last one.

    `item_count == page_size` with no `rel="next"` is how a deployment
    silently truncates a feed: it hands back exactly what was asked for and
    says there is no more, which is indistinguishable from a feed that
    genuinely ends on an exact boundary. Flagged rather than judged -- the run
    reports it and lets a person decide.
    """
    if not (
        record.item_count is not None
        and record.page_size is not None
        and record.item_count == record.page_size
        and record.has_next is False
    ):
        return False
    # A feed that reported its own total is not ambiguous: if it said how many
    # there are, an exact-boundary final page is a clean end. Only a full page
    # with no next link AND no total is the suspicious shape this looks for.
    # `ManifestRecord` has no total, so records from that side are judged on
    # the original triple alone.
    return getattr(record, "total_results", None) is None


class Archive:
    """A byte-exact, append-only, content-addressed store for HTTP
    responses, plus per-run metadata and completeness reporting."""

    def __init__(self, root: Path):
        self.root = root

    @property
    def source(self) -> ArchiveSource:
        """This archive, as the read paths see it.

        Writing goes through the methods below; reading goes through here, so
        one reader works over a directory or a zip. The local import avoids a
        cycle: `source.py` needs `MANIFEST_FILENAME` from this module.
        """
        from connections_export.archive.source import DirectorySource  # noqa: PLC0415

        return DirectorySource(self.root)

    @classmethod
    def open(cls, root: Path) -> Archive:
        """Open `root` for writing, creating it if it is not there yet.

        A `.zip` is refused here rather than at the first failed write: an
        `Archive` exists to be written into, and a zipped archive is
        read-only. Use `ReadableArchive.open`, which takes either.
        """
        root = Path(root)
        if root.is_file() and root.suffix.lower() == ".zip":
            from connections_export.archive.source import ArchiveSourceError  # noqa: PLC0415

            raise ArchiveSourceError(f"{root} is a zipped archive, which is read-only")
        root.mkdir(parents=True, exist_ok=True)
        return cls(root)

    @property
    def _manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    def _append_record(self, record: ManifestRecord) -> ManifestRecord:
        with self._manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json())
            fh.write("\n")
        return record

    # --- feed walks -----------------------------------------------------
    #
    # `manifest.jsonl` records one HTTP response, which is the right shape for
    # "what did we fetch and did it work" and the wrong shape for "what did
    # this feed walk consist of". It is keyed by URL, and an update fetches the
    # same logical feed under a different URL (`?since=...`), so a reader could
    # only guess which pages belonged together from the string. `feeds.jsonl`
    # is that missing fact, written by the paginator as it walks.

    @property
    def _feeds_path(self) -> Path:
        return self.root / FEEDS_FILENAME

    def record_feed_page(self, page: FeedPage) -> None:
        """Append one page of one feed walk."""
        with self._feeds_path.open("a", encoding="utf-8") as fh:
            fh.write(page.model_dump_json())
            fh.write("\n")

    def _all_feed_pages(self) -> list[FeedPage]:
        path = self._feeds_path
        if not path.exists():
            return []
        pages: list[FeedPage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                pages.append(FeedPage.model_validate_json(line))
        return pages

    def feed_pages(self, feed_id: str) -> list[FeedPage]:
        """Every recorded page of `feed_id`, in reading order.

        Canonical pages first, then each date window in cutoff order, page
        order within each. That order is load-bearing: a reader dedupes by item
        id, so the CANONICAL copy of anything two windows overlap on is the one
        kept, and an update can only ever add.
        """
        matching = [page for page in self._all_feed_pages() if page.feed_id == feed_id]
        # `window is None` sorts first: "" precedes any real cutoff string.
        return sorted(matching, key=lambda page: (page.window or "", page.page))

    def write_response(
        self,
        response: Any,
        *,
        fetched_at: str,
        request_headers: Mapping[str, str] | None = None,
        discovered_from: str | None = None,
        item_count: int | None = None,
        page_size: int | None = None,
        has_next: bool | None = None,
        run_id: str | None = None,
        item_date: str | None = None,
    ) -> ManifestRecord:
        """Record a completed HTTP response (success or HTTP-error status).

        `response` needs only `.url`, `.method`, `.status`, `.headers`,
        `.content` — the minimal shape a real HTTP response and the
        tests' `FakeResponse` both satisfy.
        """
        body_digest = write_blob(self.root, response.content)
        outcome = Outcome.ok if response.status < 400 else Outcome.http_error

        record = ManifestRecord(
            url=response.url,
            method=response.method,
            status=response.status,
            request_headers=redact_headers(request_headers or {}),
            response_headers=redact_headers(response.headers or {}),
            fetched_at=fetched_at,
            body_hash=f"sha256:{body_digest}",
            content_type=_content_type_of(response.headers or {}),
            discovered_from=discovered_from,
            outcome=outcome,
            item_count=item_count,
            page_size=page_size,
            has_next=has_next,
            run_id=run_id,
            item_date=item_date,
        )
        return self._append_record(record)

    def write_failure(
        self,
        *,
        url: str,
        method: str,
        fetched_at: str,
        outcome: Outcome,
        error: str,
        request_headers: Mapping[str, str] | None = None,
        discovered_from: str | None = None,
    ) -> ManifestRecord:
        """Record a failed fetch: no response body was captured (a
        transport failure, or a transfer cut short before completion).
        The record exists so the failure is never mistaken for a gap.
        """
        record = ManifestRecord(
            url=url,
            method=method,
            status=None,
            request_headers=redact_headers(request_headers or {}),
            response_headers={},
            fetched_at=fetched_at,
            body_hash=None,
            content_type=None,
            discovered_from=discovered_from,
            outcome=outcome,
            error=error,
        )
        return self._append_record(record)

    def report(self) -> ArchiveReport:
        """Summarize the manifest: counts by outcome, failures grouped by
        category (the only categorization the archive has is `outcome`
        itself, since it never parses bodies), and URLs whose recorded
        `(item_count, page_size, has_next)` signature suggests silent
        pagination truncation.
        """
        counts: dict[str, int] = {}
        failures_by_category: dict[str, int] = {}
        possibly_truncated: list[str] = []

        for record in self._read_records():
            outcome_name = record.outcome.value
            counts[outcome_name] = counts.get(outcome_name, 0) + 1
            if record.outcome != Outcome.ok:
                failures_by_category[outcome_name] = failures_by_category.get(outcome_name, 0) + 1
            if _looks_truncated(record):
                possibly_truncated.append(record.url)

        # The same signature, from the feed walks themselves. `ManifestRecord`
        # has carried these three fields since the archive was designed and
        # this detector has read them the whole time -- wired out to a
        # `truncation` warning, a nonzero exit and a GUI event -- but no writer
        # ever populated them, so in production it could not fire. The
        # paginator holds all three facts at the moment it decides to stop, and
        # now records them on the `FeedPage`.
        for page in self._all_feed_pages():
            if _looks_truncated(page) and page.url not in possibly_truncated:
                possibly_truncated.append(page.url)

        return ArchiveReport(
            counts=counts,
            failures_by_category=failures_by_category,
            possibly_truncated=possibly_truncated,
        )

    def seen_urls(self) -> set[str]:
        """URLs already recorded with an `ok` outcome. Failure records
        (including `http_error` and `truncated`) do not count as seen, so
        a resumed run retries them."""
        return {record.url for record in self._read_records() if record.outcome == Outcome.ok}

    def _run_metadata_path(self, run_id: str) -> Path:
        return self.root / f"run-{run_id}.json"

    def run_metadata_exists(self, run_id: str) -> bool:
        """Whether `run-<run_id>.json` is already taken."""
        return self._run_metadata_path(run_id).exists()

    def write_run_metadata(self, run: RunMetadata) -> Path:
        """Write per-run provenance to `run-<run_id>.json`."""
        path = self._run_metadata_path(run.run_id)
        path.write_text(run.model_dump_json(), encoding="utf-8")
        return path

    def read_run_metadata(self, run_id: str) -> RunMetadata:
        """Read back a previously written run metadata record."""
        path = self._run_metadata_path(run_id)
        return RunMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def close(self) -> None:
        """No persistent handles are held open between writes, so there is
        nothing to flush; kept for API symmetry and future extension."""

    def _read_records(self) -> list[ManifestRecord]:
        path = self._manifest_path
        if not path.exists():
            return []
        records = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                records.append(ManifestRecord.model_validate(json.loads(line)))
        return records
