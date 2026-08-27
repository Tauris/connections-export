"""Shared crawl mechanics, factored out of `crawl.py` so the Wikis
traversal and the Stage-2 Blogs/Forums traversals (`crawl_blogs`,
`crawl_forums`) run on one implementation instead of three copies.

Everything here is app-agnostic: fetch-and-archive (`Fetcher.get_content`),
non-fatal parsing (`Fetcher.parse_or_none`), feed page-walking
(`Fetcher.paginate`), retry observation, issue tracking, cache lookup,
and the per-run scaffolding (`begin_run`/`finish_run`) every entry point
brackets its traversal with. The app-specific parts -- which URLs to
build, which parsers to run, which derived events to emit -- stay in
`crawl.py`.

`crawl`'s observable behavior (archived bytes + event stream) is
unchanged by this extraction: the logic below is transplanted verbatim
from the former inline closures, and `crawl.py` still owns `MAX_PAGES`
(passed in per call, so `test_pagination`'s monkeypatch keeps working)
and `ADAPTER_VERSION`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from connections_export.adapters.atom import feed_next_link, feed_total_results
from connections_export.adapters.errors import AdapterError
from connections_export.archive.blobs import read_blob
from connections_export.archive.feeds import FeedPage
from connections_export.archive.records import ManifestRecord, Outcome, RunMetadata
from connections_export.archive.store import Archive
from connections_export.config import Config, ConfigError
from connections_export.crawler import assets, events, report
from connections_export.crawler.events import Emit, ResourceKind, safe_emit
from connections_export.crawler.report import CrawlReport
from connections_export.http.client import HttpClient
from connections_export.http.results import FailureKind, Fetched
from connections_export.paging import (
    feed_identity,
    feed_item_id,
    feed_window,
    should_stop_paging,
)


class CrawlError(Exception):
    """A crawl could not even start (e.g. no `base_url` configured).
    Distinct from a per-resource failure, which is always recorded and
    never raised -- this is for setup problems, not fetch problems."""


def add_query(url: str, **params: object) -> str:
    """Add/override query parameters on `url`, preserving whatever is
    already there (the design, "`add_query` preserves existing query
    params (e.g. `?category=version` on artifact feeds). For HCL's
    mixed-generation APIs, `ps` is mirrored as `pageSize` too."""
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in params.items()})
    if "ps" in params:
        query["pageSize"] = str(params["ps"])
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _free_run_id(archive: Archive, preferred: str) -> str:
    """`preferred`, or the first free variation of it.

    A run id names a file (`run-<id>.json`) and is derived from the clock to
    the second -- and a community capture runs its components one after
    another, several of which land in the same second. They shared the
    filename: a later component wrote its START record (no `completed_at`)
    over an earlier component's FINISHED one, and an archive that had an
    anchor for the next update quietly lost it.
    """
    if not archive.run_metadata_exists(preferred):
        return preferred
    for suffix in range(2, 1000):
        candidate = f"{preferred}-{suffix}"
        if not archive.run_metadata_exists(candidate):
            return candidate
    return preferred


def default_clock() -> str:
    """When a run happened. A date, readable by `adapters.since.parse_cutoff`.

    Kept separate from the run id on purpose. A run id becomes a filename
    (`run-<id>.json`), where a colon is not legal on Windows, so the id is
    stamped `%H-%M-%SZ` by `run_id_for`. One string cannot do both jobs: a
    stamp shaped for a filename is not parseable as a date, and every update
    reads this one back to compute its cutoff.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_id_for(stamp: str) -> str:
    """A run id from a run's stamp: the same instant, safe as a filename."""
    return stamp.replace(":", "-")


@dataclass
class CrawlResult:
    """What a completed (or gracefully aborted-early) crawl returns."""

    run_id: str
    ok: bool
    report: CrawlReport


class IssueTracker:
    """Tracks whether *anything* worth flagging happened this run --
    any failure or warning -- independent of what the caller's `emit`
    does with the events (Nonzero exit on any failure or
    warning)."""

    def __init__(self) -> None:
        self.had_issue = False

    def mark(self) -> None:
        self.had_issue = True


class RetryObserver:
    """Emit retry events for actual retry backoff, not normal throttling."""

    def __init__(self, client: HttpClient, emit: Emit) -> None:
        self._client = client
        self._emit = emit
        self._original_callback = client._retry_callback  # noqa: SLF001
        self._current_url: str | None = None
        self._attempts: dict[str, int] = {}
        client.set_retry_callback(self._on_retry)

    def _on_retry(self, seconds: float) -> None:
        url = self._current_url
        if url is not None:
            self._attempts[url] = self._attempts.get(url, 0) + 1
            safe_emit(
                self._emit,
                events.Retried(
                    url=url,
                    attempt=self._attempts[url],
                    reason=f"retrying after a {seconds:.2f}s wait",
                ),
            )

    def set_current(self, url: str | None) -> None:
        self._current_url = url

    def restore(self) -> None:
        self._client.set_retry_callback(self._original_callback)


class CachePolicy(StrEnum):
    """Whether one request may be answered from the archive.

    Only consulted in `update` mode. Under `resume` the archive always wins
    (that is what finishing an interrupted crawl means) and under `refresh` it
    never does (that is what refetching everything means) -- a policy that
    could override either would make those two modes lie about themselves.

    `IF_MISSING` is the default and reads exactly like `resume`. The two that
    matter are the ends:

    * `ALWAYS` for the documents that REVEAL change -- feeds, search result
      pages, the rich content layout. Serving these from the archive would
      make every update find nothing at all.
    * `IF_CHANGED` for the expensive parts of an item -- body, comments,
      versions, assets -- refetched only when the item's own date has moved.
    """

    IF_MISSING = "if_missing"
    ALWAYS = "always"
    IF_CHANGED = "if_changed"


def read_ok_records(archive: Archive) -> dict[str, ManifestRecord]:
    """The latest `ok`-outcome manifest record per URL -- read once at
    the start of a run for resumability lookups."""
    path = archive.root / "manifest.jsonl"
    if not path.exists():
        return {}
    records: dict[str, ManifestRecord] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = ManifestRecord.model_validate_json(line)
            if record.outcome == Outcome.ok:
                records[record.url] = record
    return records


class Fetcher:
    """The app-agnostic fetch/parse/page-walk core shared by every
    traversal. Holds the injected dependencies (`config`, `client`,
    `archive`, `emit`, `clock`) plus the per-run mutable state
    (`observer`, `issues`, `cached_records`) the former `crawl`
    closures captured -- the methods below are those closures,
    transplanted verbatim so archived bytes and emitted events are
    byte-identical to before the extraction."""

    def __init__(
        self,
        *,
        config: Config,
        client: HttpClient,
        archive: Archive,
        emit: Emit,
        clock: Callable[[], str],
        observer: RetryObserver,
        issues: IssueTracker,
        cached_records: dict[str, ManifestRecord],
    ) -> None:
        self.config = config
        self.client = client
        self.archive = archive
        self.emit = emit
        self.clock = clock
        self.observer = observer
        self.issues = issues
        self.cached_records = cached_records
        #: Which run this fetcher writes for; stamped onto every record so an
        #: append-only manifest shared by several runs stays attributable.
        self.run_id: str | None = None
        #: Item dates learned during this run, by URL.
        self._archived_item_dates: dict[str, str] = {}

    def remember_item_date(self, url: str, item_date: str | None) -> None:
        """Record what the archive believes an item's date to be.

        Used by tests and by traversals that learn an item's archived date from
        somewhere other than the manifest record itself.
        """
        if item_date:
            self._archived_item_dates[url] = item_date

    def archived_item_date(self, url: str) -> str | None:
        """What date the archive holds for `url`, if it holds one.

        Public because a traversal legitimately needs it: the wiki crawl
        compares the index's date against this one to decide whether a page is
        worth opening at all. `remember_item_date` above is its other half,
        and a pair where one side is public and the other is not is a seam
        that gets reached through instead of used.
        """
        remembered = self._archived_item_dates.get(url)
        if remembered:
            return remembered
        cached = self.cached_records.get(url)
        return cached.item_date if cached is not None else None

    #: The name this had while it was private. Kept so nothing that reached
    #: for it breaks on the way past.
    _archived_item_date = archived_item_date

    def _may_use_cache(self, url: str, policy: CachePolicy, item_date: str | None) -> bool:
        """Whether the archived copy of `url` still answers the question."""
        mode = getattr(self.config, "fetch", "resume")
        if mode == "refresh":
            return False
        if mode != "update":
            # `resume`: the archive always wins. A policy must not turn
            # finishing an interrupted crawl into a partial re-crawl.
            return True
        if policy is CachePolicy.ALWAYS:
            return False
        if policy is CachePolicy.IF_MISSING:
            return True
        # IF_CHANGED: compare the item's own date on both sides. Content date
        # against content date -- neither machine's clock is involved, so skew
        # between them cannot cost content. Either side being unknown is not
        # evidence of "unchanged", so it means fetch.
        archived = self._archived_item_date(url)
        if not archived or not item_date:
            return False
        return item_date <= archived

    def get_content(
        self,
        url: str,
        kind: ResourceKind,
        discovered_from: str | None,
        *,
        policy: CachePolicy = CachePolicy.IF_MISSING,
        item_date: str | None = None,
    ) -> bytes | None:
        cached = self.cached_records.get(url)
        if not self._may_use_cache(url, policy, item_date):
            cached = None
        if cached is not None and cached.body_hash:
            digest = cached.body_hash.split(":", 1)[-1]
            body = read_blob(self.archive.root, digest)
            safe_emit(
                self.emit, events.Discovered(url=url, kind=kind, discovered_from=discovered_from)
            )
            safe_emit(
                self.emit,
                events.Fetched(
                    url=url, kind=kind, status=None, num_bytes=len(body), from_cache=True
                ),
            )
            return body

        safe_emit(self.emit, events.Discovered(url=url, kind=kind, discovered_from=discovered_from))
        safe_emit(self.emit, events.Fetching(url=url, kind=kind))
        self.observer.set_current(url)
        try:
            result = self.client.get(url)
        finally:
            self.observer.set_current(None)
        fetched_at = self.clock()

        if isinstance(result, Fetched):
            self.archive.write_response(
                result,
                fetched_at=fetched_at,
                discovered_from=discovered_from,
                run_id=self.run_id,
                # Recorded so the NEXT update has something to compare against.
                # Fetching without recording the date would make the same item
                # look changed on every future run, forever.
                item_date=item_date,
            )
            safe_emit(
                self.emit,
                events.Fetched(
                    url=url,
                    kind=kind,
                    status=result.status,
                    num_bytes=len(result.content),
                    from_cache=False,
                ),
            )
            if result.status >= 400:
                self.issues.mark()
                safe_emit(
                    self.emit,
                    events.Failed(url=url, kind="http_error", error=f"HTTP {result.status}"),
                )
                return None
            return result.content

        if result.error == "stopped":
            # Concluded, not abandoned silently: everything discovered has to
            # reach one of Fetched / Failed / Stopped, or the counters cannot
            # be trusted to show a real loss.
            safe_emit(self.emit, events.Stopped(url=url, kind=kind))
            return None

        failure_kind = {
            FailureKind.timeout: "timeout",
            FailureKind.transport: "transport",
            FailureKind.http_error: "http_error",
        }.get(result.kind, "transport")
        self.archive.write_failure(
            url=url,
            method=result.method,
            fetched_at=fetched_at,
            outcome=Outcome.transport_error,
            error=result.error,
            discovered_from=discovered_from,
        )
        self.issues.mark()
        safe_emit(self.emit, events.Failed(url=url, kind=failure_kind, error=result.error))
        return None

    def parse_or_none(self, parser: Callable[[bytes], object], content: bytes, *, url: str):
        try:
            return parser(content)
        except AdapterError as exc:
            self.issues.mark()
            safe_emit(self.emit, events.Failed(url=url, kind="unparsed", error=str(exc)))
            return None

    def paginate(
        self,
        feed_base_url: str,
        parser: Callable[[bytes], list],
        kind: ResourceKind,
        discovered_from: str | None,
        *,
        max_pages: int,
        start_page: int = 1,
        stop_event: threading.Event | None = None,
        policy: CachePolicy = CachePolicy.IF_MISSING,
        item_date: str | None = None,
    ) -> list:
        """Walk `feed_base_url` to exhaustion (crawler --
        page-walking), archiving every page via `get_content`. The
        stop condition is deliberately conservative: continue while
        either a `rel="next"` link is present OR the page came back
        full (`len(items) == config.page_size`); stop only when both
        say "no more" -- `rel="next"` is not documented for Wikis,
        so it is never trusted alone. `max_pages` bounds the walk; if
        it is hit, that is surfaced as a warning (never a silent
        truncation, never an infinite loop). `max_pages` is passed in
        (not read from a module global here) so a caller in `crawl.py`
        supplies its own monkeypatchable `MAX_PAGES` at call time.

        `start_page` controls the first page number fetched. Use 0 for
        HCL Connections blog entry feeds, which are 0-indexed: page 0 and
        page 1 return disjoint entries, so page 0 is the real first page. All
        other feeds use the default `start_page=1`.

        Deduplication by id (via `paging.feed_item_id`) ensures that if a server
        was silently fixed and page 0 == page 1, no entries are doubled."""
        all_items: list = []
        seen_ids: set = set()
        duplicate_pages = 0
        page = start_page
        while page <= max_pages + start_page - 1:
            if stop_event is not None and stop_event.is_set():
                break
            url = add_query(feed_base_url, ps=self.config.page_size, page=page)
            content = self.get_content(
                url, kind, discovered_from, policy=policy, item_date=item_date
            )
            if content is None:
                break
            items = self.parse_or_none(parser, content, url=url)
            if items is None:
                break
            # Deduplicate: if the server was fixed and page 0 == page 1,
            # items will already be in seen_ids and are silently dropped.
            new_items = []
            for item in items:
                item_id = feed_item_id(item)
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    new_items.append(item)
            all_items.extend(new_items)

            next_link = feed_next_link(content)
            total_results = feed_total_results(content)

            # Record what this page of the walk actually was, before deciding
            # whether to continue. Derive replays this instead of
            # re-implementing the rule below -- the two implementations drifted
            # four separate ways before this existed. It also carries the
            # truncation signature, which `Archive.report` has read since the
            # archive was designed and which no writer ever populated.
            self.archive.record_feed_page(
                FeedPage(
                    feed_id=feed_identity(url),
                    page=page,
                    window=feed_window(url),
                    url=url,
                    item_count=len(items),
                    page_size=self.config.page_size,
                    has_next=next_link is not None,
                    total_results=total_results,
                    run_id=self.run_id,
                )
            )

            # `all_items` holds only NEW items, so `distinct_so_far` is a
            # distinct count -- the only kind that can be compared against the
            # server's own total without a repeated page making us think we are
            # done.
            stop, duplicate_pages = should_stop_paging(
                items_on_page=len(items),
                new_items=len(new_items),
                distinct_so_far=len(all_items),
                page_size=self.config.page_size,
                next_link=next_link,
                total_results=total_results,
                duplicate_pages=duplicate_pages,
            )
            if stop:
                break
            page += 1
        else:
            self.issues.mark()
            safe_emit(
                self.emit,
                events.Warning(
                    kind="pagination_cap",
                    detail=f"pagination exceeded the {max_pages}-page safety cap",
                    ref=feed_base_url,
                ),
            )
        return all_items


class AssetCapture:
    """The same-origin media rule,
    shared by every traversal (`crawl`, `crawl_blogs`, `crawl_forums`):
    resolve a (possibly relative) asset href against a caller-supplied
    `base`, classify it same-deployment vs. external
    (`assets.classify`), and either fetch+archive it (`Fetcher.get_content`)
    or record-and-skip it (an `external_asset_skipped` `Warning`,
    never fetched).

    Free-standing rather than nested inside the wiki traversal, so
    `crawl_blogs`/`crawl_forums` can scan their own bodies (blog posts
    and comments, forum topics and replies) through the identical
    dedup/classify/fetch-or-skip path instead of duplicating it.

    Dedup is by resolved absolute URL, scoped to one instance -- a
    caller constructs exactly one `AssetCapture` per crawl run (mirrors
    the former `processed_assets` set's per-`crawl`-call lifetime).
    """

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        base_url: str,
        hcl_hosts: Iterable[str] = (),
        enabled: bool = True,
    ) -> None:
        self._fetcher = fetcher
        self._base_url = base_url
        self._hcl_hosts = list(hcl_hosts)
        self._enabled = enabled
        self._processed: set[str] = set()

    def handle(
        self, raw_href: str, *, base: str, discovered_from: str, kind: ResourceKind = "asset"
    ) -> None:
        """Resolve, dedup, classify, and fetch-or-skip one asset href
        (e.g. an `<img src>` found in a body, or an attachment's
        enclosure href)."""
        if not self._enabled:
            return
        resolved = assets.resolve(raw_href, base_url=base)
        if resolved in self._processed:
            return
        self._processed.add(resolved)
        classification = assets.classify(
            resolved, base_url=self._base_url, hcl_hosts=self._hcl_hosts
        )
        if classification == "same":
            self._fetcher.get_content(resolved, kind, discovered_from)
        else:
            self._fetcher.issues.mark()
            safe_emit(
                self._fetcher.emit,
                events.Warning(
                    kind="external_asset_skipped",
                    detail="referenced asset is outside the deployment",
                    ref=resolved,
                ),
            )

    def scan_body(self, body_html: bytes | str, *, base: str, discovered_from: str) -> None:
        """Scan `body_html` for same-origin assets and `handle` each one.

        Two kinds, one path. `<img src>` is the embedded case this has always
        captured. `<a href>` to a Files DOCUMENT is the linked case: an export
        has to stand on its own, and a link stands only while the deployment
        is reachable. People link liberally, often to a file in another
        community entirely, and that file is exactly as absent from a
        self-contained archive as one in the same community.

        Only recognisable Files documents, and of those only the shape that
        names the BYTES (`assets.scan_file_media_links`). A link naming a
        document by id points at a viewer PAGE: fetching it would archive HTML
        under the name of a file, which is worse than not capturing it -- the
        archive would claim to hold something it does not. Those are left for
        resolution, and everything else stays recorded-not-fetched, because
        following every in-deployment link is how a user-level export becomes
        a deployment-wide crawl by accident.

        Never a reason to fail the containing page/post/topic.
        """
        for href in assets.scan(body_html):
            self.handle(href, base=base, discovered_from=discovered_from)
        for href in assets.scan_file_media_links(body_html):
            self.handle(href, base=base, discovered_from=discovered_from)


@dataclass
class RunSession:
    """The per-run scaffolding every crawl entry point shares: a wired
    `Fetcher`, its `IssueTracker`/`RetryObserver`, and the resolved
    run identity (`run_id`, `base_url`, `auth_root`). Created by
    `begin_run`, closed by `finish_run`."""

    fetcher: Fetcher
    issues: IssueTracker
    observer: RetryObserver
    run_id: str
    base_url: str
    auth_root: str
    config: Config
    archive: Archive
    emit: Emit
    clock: Callable[[], str]
    #: This run's provenance as written at start-up, without `completed_at`.
    #: `finish_run` re-writes it with the completion stamp; a run that dies
    #: leaves the incomplete record behind, which is exactly what stops a later
    #: update from anchoring on it.
    run_metadata: RunMetadata | None = None


def begin_run(
    *,
    config: Config,
    client: HttpClient,
    archive: Archive,
    emit: Emit,
    clock: Callable[[], str],
    run_id: str | None,
    adapter_version: str,
    max_items: int | None = None,
    run_kind: str = "initial",
    components: list | None = None,
    community_uuid: str | None = None,
    community_title: str | None = None,
    author_filter: str | None = None,
    since_cutoff: str | None = None,
) -> RunSession:
    """Common run start-up shared by `crawl`/`crawl_blogs`/`crawl_forums`
    (crawler -- write it at run start): resolve `base_url`
    (raising `CrawlError` if unset), install the retry observer, load
    the resumability cache, write run provenance, and emit `RunStarted`.
    Returns a `RunSession` the caller drives its traversal with."""
    try:
        base_url = config.require_base_url()
    except ConfigError as exc:
        raise CrawlError(str(exc)) from exc

    observer = RetryObserver(client, emit)
    issues = IssueTracker()
    cached_records = {} if config.fetch == "refresh" else read_ok_records(archive)
    # One reading of the clock, used for both: the run's identity and the
    # moment it began. Two readings would make `started_at` a tick later than
    # the id, and the anchor for the next update is meant to be the earliest
    # moment this run could have seen anything.
    started_at = clock()
    run_id = run_id or _free_run_id(archive, run_id_for(started_at))

    run_metadata = RunMetadata(
        run_id=run_id,
        base_url=base_url,
        principal=getattr(config, "principal", None) or "unknown",
        adapter_version=adapter_version,
        source_system_version=config.source_version,
        hcl_hosts=list(config.hcl_hosts),
        max_items=max_items,
        started_at=started_at,
        run_kind=run_kind,
        components=list(components or []),
        community_uuid=community_uuid,
        community_title=community_title,
        author_filter=author_filter,
        since_cutoff=since_cutoff,
    )
    # Written now, without `completed_at`. That absence is the record that this
    # run is in flight -- and if it dies here, the absence is what stops a
    # later update from trusting it as an anchor.
    archive.write_run_metadata(run_metadata)
    safe_emit(
        emit,
        events.RunStarted(run_id=run_id, base_url=base_url, source_version=config.source_version),
    )

    fetcher = Fetcher(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        clock=clock,
        observer=observer,
        issues=issues,
        cached_records=cached_records,
    )
    fetcher.run_id = run_id
    return RunSession(
        run_metadata=run_metadata,
        fetcher=fetcher,
        issues=issues,
        observer=observer,
        run_id=run_id,
        base_url=base_url,
        auth_root=config.auth_root,
        config=config,
        archive=archive,
        emit=emit,
        clock=clock,
    )


def finish_run(session: RunSession, *, orphans: list[str], pages_crawled: int) -> CrawlResult:
    """Common run wind-down shared by every entry point: surface the
    archive's truncation signatures as warnings, assemble the
    `CrawlReport`, emit `RunComplete`, restore the observer, and return
    the `CrawlResult` (`ok` iff no failure/warning was ever flagged)."""
    archive_report = session.archive.report()
    for truncated_url in archive_report.possibly_truncated:
        session.issues.mark()
        safe_emit(
            session.emit,
            events.Warning(
                kind="truncation",
                detail="feed response looks possibly truncated",
                ref=truncated_url,
            ),
        )
    crawl_report = report.build_report(archive_report, orphans=orphans, pages_crawled=pages_crawled)
    # Only now may this run anchor a later update: its feeds have all been
    # read, so "everything before it started is captured" is finally true.
    if session.run_metadata is not None:
        session.archive.write_run_metadata(
            session.run_metadata.model_copy(update={"completed_at": session.fetcher.clock()})
        )
    safe_emit(session.emit, events.RunComplete(report=crawl_report))
    session.observer.restore()
    return CrawlResult(run_id=session.run_id, ok=not session.issues.had_issue, report=crawl_report)
