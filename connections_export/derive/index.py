"""`ArchiveIndex`: read `manifest.jsonl` + `blobs/` once into an
in-memory index keyed by URL.

Reads the archive's own documented on-disk layout directly
(`Archive`'s module docstring: `<root>/manifest.jsonl`,
`<root>/blobs/<sha256hex>`) rather than adding a new public method to
`connections_export.archive.store.Archive` -- the archive capability is
frozen scope for this change (out of scope: "Do NOT modify
archive/http/adapters/crawler/fakeserver/config code"), and reading
its published layout is exactly what `Archive`'s own docstring commits
to as the interchange contract.

`get(url)` returns the latest `ok`-outcome record's bytes and
content-type; `failure(url)` exposes a URL's latest non-`ok` record so
assembly can tell "never fetched" from "fetched but failed" apart
(project.md principle 2: a failure is never a silent gap). `paginate`
mirrors `connections_export.crawler.crawl.paginate`'s page-walk -- same
`?ps=&page=` URL shape, same "continue while a next link is present OR
the page came back full" stop condition -- but reads pages back out of
the archive instead of fetching them, so assembly recovers every
paginated comments/versions/attachments/wikis page, not just the
first (pagination-awareness).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from connections_export import paging
from connections_export.adapters.atom import feed_next_link, feed_total_results
from connections_export.adapters.errors import AdapterError
from connections_export.archive.blobs import valid_digest
from connections_export.archive.feeds import FEEDS_FILENAME, FeedPage
from connections_export.archive.records import ManifestRecord, Outcome
from connections_export.archive.source import ArchiveSource, ArchiveSourceError
from connections_export.archive.store import MANIFEST_FILENAME

# `add_query` is public and already imported by `crawler.crawl`. This module
# carried a byte-identical private copy justified as "mirrored, not imported:
# that helper is module-private" -- which stopped being true when it was made
# public, and the copy outlived the reason for it.
from connections_export.crawler.engine import add_query as _add_query
from connections_export.paging import feed_identity, feed_item_id, should_stop_paging


@dataclass(frozen=True)
class IndexedResponse:
    """One archived response as assembly needs it: bytes, content
    type, and enough provenance (`body_hash`, `discovered_from`) to
    populate a `Provenance` sidecar without re-reading the manifest."""

    url: str
    content: bytes
    content_type: str | None
    body_hash: str | None
    discovered_from: str | None


def _without_page_size(url: str) -> str:
    split = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if key != "pageSize"
    ]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _page_number(url: str) -> int:
    """The `page` parameter, for ordering one window's pages."""
    try:
        return int(dict(parse_qsl(urlsplit(url).query)).get("page", "0"))
    except ValueError:
        return 0


def _feed_identity(url: str) -> tuple[str, tuple]:
    """A feed's identity, ignoring which page or window is being asked for.

    Two URLs belong to the same logical feed when they differ only in
    pagination (`ps`, `page`, `pageSize`) or in the date window an update
    added (`since`, `sortBy`).
    """
    split = urlsplit(url)
    ignored = {"ps", "page", "pageSize", "since", "sortBy"}
    kept = tuple(
        sorted(
            (k, v) for k, v in parse_qsl(split.query, keep_blank_values=True) if k not in ignored
        )
    )
    return (f"{split.scheme}://{split.netloc}{split.path}", kept)


class ArchiveIndex:
    """A read-only, in-memory index over one archive's manifest +
    blobs. Built once (`ArchiveIndex.open(archive)`); every subsequent
    lookup is a dict access, no re-reading the manifest."""

    def __init__(self, source: ArchiveSource, *, max_pages: int | None = None):
        #: Where this archive's bytes come from -- a directory or a zip. The
        #: index never holds a filesystem path of its own, which is what lets
        #: the same reader work over either.
        self.source = source
        #: How far this index will walk any one feed. Injectable for the same
        #: reason `Fetcher.paginate` takes `max_pages` rather than reading a
        #: global: a test must be able to reach the cap without archiving
        #: 10,000 pages to do it. Defaults to the crawler's cap, read now
        #: rather than at import time so the two cannot drift apart.
        self.max_pages = paging.MAX_PAGES if max_pages is None else max_pages
        self._ok: dict[str, ManifestRecord] = {}
        self._failures: dict[str, ManifestRecord] = {}
        #: Feeds whose walk stopped because it hit `max_pages`, rather than
        #: because the feed ended. A capped read is a TRUNCATED read, and the
        #: crawler refuses to truncate silently (`pagination_cap` warning), so
        #: derive records it too -- `hit_pagination_cap` is how assembly asks.
        self._capped: set[str] = set()
        #: Feeds read by RECONSTRUCTING the page sequence instead of replaying
        #: the crawler's recording. Correct for an archive captured before the
        #: crawler recorded its walks; lossy for one that had a recording and
        #: lost it -- a hand-assembled zip that omitted `feeds.jsonl` is the
        #: realistic way that happens, since nothing in the product writes a
        #: zip. The two paths disagree about a page missing from the manifest
        #: mid-walk: the replay knows the later pages were still fetched and
        #: keeps reading, the reconstruction takes the gap for the end of the
        #: feed and stops. Nothing can tell the two absences apart, so this
        #: records only what IS knowable -- that the fallback was taken.
        #: `reconstructed` is how assembly asks.
        self._reconstructed: set[str] = set()
        #: Feeds whose RECONSTRUCTION stopped because a page was absent from
        #: the manifest. This, not `_reconstructed`, is the harmful case and
        #: the one worth a note: a clean reconstruction stops by the shared
        #: stop rule, having read the whole feed, and loses nothing. Reporting
        #: every reconstruction would fire on every archive captured before
        #: `feeds.jsonl` -- all of which derive correctly -- and a signal that
        #: fires constantly is one people learn to scroll past.
        self._reconstruction_gaps: set[str] = set()
        #: Date-filtered feed pages an UPDATE archived, grouped by the feed
        #: they belong to and the window they asked for. Built once here rather
        #: than scanned per call: `paginate` runs for every feed in the
        #: archive, and re-scanning every archived URL each time is quadratic
        #: in the size of the archive.
        #: What each feed walk actually fetched, as the crawler recorded it
        #: (`archive/feeds.py`). When a feed appears here, `paginate` replays
        #: this rather than re-deriving the page sequence and re-implementing
        #: the crawler's stop condition -- the two implementations drifted four
        #: separate ways before this existed.
        self._recorded: dict[str, list[FeedPage]] = {}
        #: LEGACY. Only consulted for archives captured before `feeds.jsonl`
        #: existed; delete with the fallback in `paginate` once no such archive
        #: is in use.
        self._update_windows: dict[tuple, dict[str, list[str]]] = {}
        #: `ok` records whose `body_hash` is not a digest at all. Archives are
        #: handed on, and a hash like `sha256:../../.ssh/id_rsa` would read a
        #: file off this machine into a page body. Such a record is corrupt:
        #: it is left out -- its URL reads as never archived -- and named here
        #: rather than failing the whole archive over one line.
        self._corrupt: list[str] = []
        self._load()

    @classmethod
    def open(cls, archive, *, max_pages: int | None = None) -> ArchiveIndex:
        """Index `archive`, which may be a writable `Archive` or a read-only
        handle over a zip. Only `.source` is used from here down."""
        return cls(archive.source, max_pages=max_pages)

    def hit_pagination_cap(self, feed_base_url: str) -> bool:
        """Whether walking `feed_base_url` stopped at the safety cap.

        True means the read is SHORT: the archive may hold pages this index
        never returned. Assembly turns that into a `Provenance.note`, so a
        truncated feed is never mistaken for a complete one (project.md
        principle 2: a failure is never a silent gap)."""
        return feed_base_url in self._capped

    def reconstructed(self, feed_base_url: str) -> bool:
        """Whether `feed_base_url` was reconstructed rather than replayed.

        True means the read was DEGRADED: the crawler's own record of this
        walk was unavailable, so the page sequence was inferred from archived
        URLs instead. That inference stops at the first page missing from the
        manifest, where the recording would have read past it -- so a feed with
        a mid-walk gap yields less content here than it would have.

        It is not by itself an error. An archive captured before the crawler
        recorded its walks has no recording to replay and derives correctly
        this way; nothing distinguishes that from a recording that was lost.
        Assembly turns this into a `Provenance.note` for the same reason it
        does for the safety cap (project.md principle 2: a failure is never a
        silent gap), and the note states what happened rather than claiming to
        know which of the two it was."""
        return feed_base_url in self._reconstructed

    def reconstruction_hit_a_gap(self, feed_base_url: str) -> bool:
        """Whether reconstructing `feed_base_url` stopped at a missing page.

        True means the read is SHORT in a way the crawler's own recording
        would have prevented: `_replay` treats a page missing from the
        manifest as a gap in the ARCHIVE and reads on, because the later pages
        were still fetched; reconstruction cannot know that and takes the gap
        for the end of the feed.

        Reaching this state means the recording was unavailable AND the archive
        has a mid-walk hole. Either alone is harmless -- which is why this is
        the condition worth reporting, rather than `reconstructed` on its
        own."""
        return feed_base_url in self._reconstruction_gaps

    def _load(self) -> None:
        self._load_recorded_walks()
        for line in self.source.read_lines(MANIFEST_FILENAME):
            line = line.strip()
            if not line:
                continue
            record = ManifestRecord.model_validate_json(line)
            if record.outcome == Outcome.ok:
                if record.body_hash and valid_digest(record.body_hash) is None:
                    self._corrupt.append(record.url)
                    continue
                self._ok[record.url] = record  # last one wins: the latest ok
                if "since=" in record.url:
                    window = dict(parse_qsl(urlsplit(record.url).query)).get("since", "")
                    by_window = self._update_windows.setdefault(_feed_identity(record.url), {})
                    pages = by_window.setdefault(window, [])
                    if record.url not in pages:
                        pages.append(record.url)
            else:
                self._failures[record.url] = record

    def _load_recorded_walks(self) -> None:
        """Read `feeds.jsonl`: what each feed walk actually consisted of.

        With this, `paginate` is a lookup. Without it -- an archive captured
        before the crawler recorded its walks -- it falls back to reconstructing
        the sequence from URLs, which is what the machinery below still exists
        for. Both paths share `paging.should_stop_paging`, so the fallback
        cannot drift from the crawler the way its predecessor did four separate
        ways.
        """
        for line in self.source.read_lines(FEEDS_FILENAME):
            if not line.strip():
                continue
            page = FeedPage.model_validate_json(line)
            self._recorded.setdefault(page.feed_id, []).append(page)
        for pages in self._recorded.values():
            # Canonical window first, then each cutoff in order, page order
            # within. Load-bearing: `paginate` dedupes by item id, so the
            # canonical copy of anything two windows overlap on is kept.
            pages.sort(key=lambda page: (page.window or "", page.page))

    def get(self, url: str) -> IndexedResponse | None:
        """The latest `ok` response for `url`, or `None` if it was
        never successfully archived."""
        record = self._ok.get(url)
        if record is None and "pageSize=" in url:
            # Archives written before the compatibility alias used only ps.
            record = self._ok.get(_without_page_size(url))
        if record is None:
            return None
        content = b""
        if record.body_hash:
            digest = valid_digest(record.body_hash)  # checked at load: never None here
            payload = self.source.read_blob(digest)
            if payload is None:
                # A manifest entry whose body is absent is a corrupt archive.
                # Deriving an empty body instead would be indistinguishable
                # downstream from a page that genuinely had no content.
                raise ArchiveSourceError(
                    f"{digest} is recorded in the manifest but missing from {self.source.label}"
                )
            content = payload
        return IndexedResponse(
            url=url,
            content=content,
            content_type=record.content_type,
            body_hash=record.body_hash,
            discovered_from=record.discovered_from,
        )

    @property
    def corrupt_urls(self) -> list[str]:
        """URLs whose `ok` record carried a `body_hash` that is not a digest,
        in manifest order. Left out of the index rather than read."""
        return list(self._corrupt)

    def failure(self, url: str) -> ManifestRecord | None:
        """The latest non-`ok` record for `url` (an explicit failure,
        never a gap), or `None` if `url` either succeeded or was never
        touched at all."""
        record = self._failures.get(url)
        if record is None and "pageSize=" in url:
            record = self._failures.get(_without_page_size(url))
        return record

    def ok_urls(self) -> Iterable[str]:
        """Every URL with at least one recorded `ok` response --
        `assemble.py` scans this to locate its entry point (the
        archived `.../wikis/feed?ps=..&page=1` request) without being
        told base_url/auth_root out of band, matching the `derive(archive) -> Interchange`
        signature."""
        return self._ok.keys()

    def has_feed_page(self, feed_base_url: str, *, page_size: int, start_page: int = 1) -> bool:
        """Whether `feed_base_url`'s first page was actually archived (an `ok`
        response), as opposed to never fetched. Lets assembly tell a blog/forum
        whose content really was crawled (present, even if it returned zero
        items) from one that only appeared in a list feed but was scoped out of
        the crawl -- the latter must not become an empty container in the
        model. `start_page` must match the crawler's (0 for blog entry feeds)."""
        return self.get(_add_query(feed_base_url, ps=page_size, page=start_page)) is not None

    def first_page_failure(self, feed_base_url: str, *, page_size: int) -> ManifestRecord | None:
        """The failure record for `feed_base_url`'s first page, if
        fetching it was attempted and failed -- `None` if it actually
        succeeded, or if it was never touched at all. Lets assembly
        distinguish "this page legitimately has no comments/versions/
        attachments" from "the feed fetch itself failed" (project.md
        principle 2: a failure is never a silent gap)."""
        url = _add_query(feed_base_url, ps=page_size, page=1)
        if self.get(url) is not None:
            return None
        return self.failure(url)

    def paginate(
        self,
        feed_base_url: str,
        parser: Callable[[bytes], list],
        *,
        page_size: int,
        start_page: int = 1,
    ) -> list:
        """Every item the crawler read from `feed_base_url`, in feed order.

        When the archive records what the walk consisted of (`feeds.jsonl`),
        this REPLAYS that record: there is no stop condition here at all, which
        is the point. Re-implementing `Fetcher.paginate`'s rule here instead,
        and rebuilding the page sequence by string-matching `since=` in URLs,
        gives two implementations of one rule -- and they drift in ways that
        are individually invisible: whether an empty page is authoritative,
        whether `totalResults` is weighed, whether tuple items are unwrapped
        for dedup, whether the page cap is bound by value. Each divergence
        produces a model that is silently short while the run reports
        success.

        For an archive captured before the crawler recorded its walks, this
        falls back to reconstructing the sequence from URLs -- but through the
        same `paging.should_stop_paging` the crawler uses, so even the fallback
        cannot drift. `start_page` and `page_size` only matter on that path.

        Deduplicated by item id: an update's date-filtered pages overlap the
        canonical ones, and the recorded order puts canonical first, so the
        canonical copy of a shared item wins.

        Bounded by this index's `max_pages` on the fallback path only; reaching
        that bound means the result is TRUNCATED rather than complete, which
        `hit_pagination_cap(feed_base_url)` reports afterwards.
        """
        recorded = self._recorded.get(feed_identity(feed_base_url))
        if recorded:
            return self._replay(recorded, parser)
        # Recorded AFTER the branch is chosen, not before: which path a feed
        # takes is a property of a walk that has happened, exactly as the
        # safety cap is.
        self._reconstructed.add(feed_base_url)
        return self._reconstruct(feed_base_url, parser, page_size=page_size, start_page=start_page)

    def _replay(self, pages: list[FeedPage], parser: Callable[[bytes], list]) -> list:
        """Read back exactly the pages the crawler recorded fetching.

        A page recorded as walked but absent from the manifest is a gap in the
        ARCHIVE, not a signal to stop reading: the remaining pages were still
        fetched and still belong in the model. That is the opposite of the
        reconstruction path, where a missing page is the only way to know the
        sequence ended.
        """
        all_items: list = []
        seen_ids: set = set()
        for recorded in pages:
            response = self.get(recorded.url)
            if response is None:
                continue
            try:
                items = parser(response.content)
            except AdapterError:
                continue
            for item in items:
                item_id = feed_item_id(item)
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    all_items.append(item)
        return all_items

    def _reconstruct(
        self,
        feed_base_url: str,
        parser: Callable[[bytes], list],
        *,
        page_size: int,
        start_page: int,
    ) -> list:
        """LEGACY: rebuild the page sequence from URLs, for archives captured
        before `feeds.jsonl`. Delete once no such archive is in use."""
        all_items: list = []
        seen_ids: set = set()
        for position, sequence in enumerate(
            self._feed_page_sequences(feed_base_url, page_size=page_size, start_page=start_page)
        ):
            gap: list = []
            exhausted = self._walk_pages(
                sequence, parser, all_items, seen_ids, page_size=page_size, gap=gap
            )
            if gap:
                self._reconstruction_gaps.add(feed_base_url)
            # Only the FIRST sequence is the open-ended `range` the cap bounds;
            # running it to the end means the cap stopped us, not the feed.
            # The sequences after it are an update's already-known page URLs, a
            # finite list whose end is simply its end -- reading all of it is
            # completion, not truncation, and must not raise the alarm.
            if position == 0 and exhausted:
                self._capped.add(feed_base_url)
        return all_items

    def _walk_pages(
        self,
        urls,
        parser,
        all_items: list,
        seen_ids: set,
        *,
        page_size: int,
        gap: list | None = None,
    ) -> bool:
        """Read one run of pages until it ends, adding what is new.

        A missing page ends THIS sequence only: the canonical pages and an
        update's date-filtered pages are two separate runs, and stopping the
        first must not stop the second.

        Returns whether `urls` ran out while the walk still wanted more -- i.e.
        nothing here chose to stop. For the capped canonical range that means
        the cap truncated the read, which is the crawler's own `while... else`
        test for the same condition.

        The continuation rule itself is `paging.should_stop_paging` -- the same
        function the crawler stops with, rather than a copy of it here: two
        implementations of one rule drift, and a drifted stop rule silently
        reads a different number of pages than the crawler wrote.
        """
        # Counted PER SEQUENCE, not across them. `all_items` is shared so the
        # caller ends up with one deduplicated result, but each run of pages
        # carries its own `totalResults` -- an update window's total describes
        # that window, not the whole feed. Comparing the accumulated count
        # against it would make the continuation rule inert for every sequence
        # after the first, which is precisely the update path.
        in_sequence: set = set()
        duplicate_pages = 0
        for url in urls:
            response = self.get(url)
            if response is None:
                # Asked for, not archived. The stop rule had said "continue",
                # so this is a hole rather than the end of the feed -- the
                # caller needs to know the difference.
                if gap is not None:
                    gap.append(url)
                break
            try:
                items = parser(response.content)
            except AdapterError:
                break
            new_in_sequence = 0
            for item in items:
                item_id = feed_item_id(item)
                if item_id not in in_sequence:
                    in_sequence.add(item_id)
                    new_in_sequence += 1
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    all_items.append(item)
            try:
                next_link = feed_next_link(response.content)
            except AdapterError:
                next_link = None
            try:
                total_results = feed_total_results(response.content)
            except AdapterError:
                total_results = None
            stop, duplicate_pages = should_stop_paging(
                items_on_page=len(items),
                new_items=new_in_sequence,
                distinct_so_far=len(in_sequence),
                page_size=page_size,
                next_link=next_link,
                total_results=total_results,
                duplicate_pages=duplicate_pages,
            )
            if stop:
                break
        else:
            # The URLs ran out before anything above decided to stop.
            return True
        return False

    def _feed_page_sequences(
        self, feed_base_url: str, *, page_size: int, start_page: int
    ) -> Iterable[Iterable[str]]:
        """Every archived run of pages for one logical feed, in reading order.

        Each run is itself iterable, not necessarily a list: the canonical
        pages are yielded lazily (see below), and only an update's already-known
        page URLs arrive as a concrete sequence.

        The canonical pages first -- `?ps=..&page=n`, as the original capture
        archived them -- and then any page an UPDATE archived for the same
        feed.

        An update asks a date-filtered feed for what changed, so it stores a
        URL carrying `since=` and `sortBy=`. That is a different string, and
        the archive is keyed by URL, so reading only the canonical pages would
        skip every item an update fetched: the run reports success, the archive
        grows, and the derived model does not change. Nothing about that
        failure is visible from the inside, which is what makes it worth
        handling here rather than trusting each caller to remember.

        Later pages come last so the dedup in `paginate` keeps the CANONICAL
        copy of an item the two overlap on, and an update can only ever add.
        """
        # LAZY on purpose. The cap is 10,000 and almost every feed ends
        # after one page, so materialising the whole range would build ten
        # thousand URL strings per feed in order to use one of them. As a
        # list this is slow enough to be felt across a whole test suite.
        yield (
            _add_query(feed_base_url, ps=page_size, page=page)
            for page in range(start_page, self.max_pages + start_page)
        )

        # An update's pages, grouped by the window they asked for, so each
        # window is walked as its own run rather than one flat list where a
        # gap in the middle would truncate the rest. These always carry
        # `since=`, so they can never collide with a canonical page URL.
        windows = self._update_windows.get(_feed_identity(feed_base_url), {})
        # Deterministic order: the same archive must derive the same model
        # every time, and dict iteration order is an implementation detail.
        for window in sorted(windows):
            yield sorted(windows[window], key=_page_number)
