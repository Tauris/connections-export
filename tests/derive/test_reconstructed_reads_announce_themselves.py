"""A reconstructed read is a degraded read, and must say so.

`feeds.jsonl` records what each feed walk actually fetched, and `paginate`
replays it. When the file is absent, derive falls back to reconstructing the
page sequence from URLs. That fallback is CORRECT for an archive captured
before the crawler recorded its walks -- and it is silently LOSSY for an
archive that had a recording and lost it, because the two paths disagree about
one thing: a page missing from the manifest mid-walk.

    replay a gap in the ARCHIVE; the later pages were still fetched, keep reading
    reconstruct the end of the feed; stop

Reproduced on the same archive, derived twice, the second time with
`feeds.jsonl` deleted -- which is exactly a zip a human assembled without it:

    replay 20 items
    reconstruct 10 items, exit 0, no warning

Nothing can tell those two absences apart. An old archive carries no marker
saying "I predate this", so refusing to derive would reject every archive that
still derives correctly. What is NOT ambiguous is that the fallback was taken,
and that is what these tests require the index to say -- the same answer
`hit_pagination_cap` already gives for the same kind of shortfall.
"""

from __future__ import annotations

from connections_export.adapters.wikis import parse_wikis_feed
from connections_export.archive.feeds import FEEDS_FILENAME, FeedPage
from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from connections_export.derive.index import ArchiveIndex
from connections_export.paging import feed_identity

BASE = "https://fake/wikis/basic/api/wikis/feed"


def _feed(*, entries: int, total: int, offset: int = 0) -> bytes:
    items = "".join(
        f"<entry><id>urn:lsid:ibm.com:wikis:wiki-{offset + i}</id>"
        f"<title>Wiki {offset + i}</title><td:label>w{offset + i}</td:label></entry>"
        for i in range(entries)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:td="urn:ibm.com/td"'
        ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        f"<opensearch:totalResults>{total}</opensearch:totalResults>"
        f"{items}</feed>"
    ).encode()


class _Response:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.method = "GET"
        self.status = 200
        self.content = content
        self.headers = {"content-type": "application/atom+xml"}
        self.request_headers = {}
        self.elapsed_ms = 1
        self.redirect_location = None


def _archive_with_a_gap(tmp_path) -> Archive:
    """Three pages recorded as walked; page 2's RESPONSE never archived.

    A mid-walk gap is ordinary: one fetch failed while the rest succeeded. The
    recording knows all three pages were walked; the manifest holds two.
    """
    store = Archive.open(tmp_path / "a")
    walked = [
        (1, _feed(entries=2, total=6, offset=0), True),
        (2, None, False),  # recorded as walked, response missing
        (3, _feed(entries=2, total=6, offset=4), True),
    ]
    for number, body, archived in walked:
        url = f"{BASE}?ps=2&page={number}"
        if archived:
            store.write_response(_Response(url, body), fetched_at="2026-08-10T21:14:00Z")
        store.record_feed_page(
            FeedPage(
                feed_id=feed_identity(url),
                page=number,
                url=url,
                item_count=2,
                page_size=2,
                has_next=number < 3,
                total_results=6,
                run_id="r-1",
            )
        )
    return store


def _without_recording(store: Archive) -> Archive:
    (store.root / FEEDS_FILENAME).unlink()
    return store


def test_the_recorded_walk_reads_past_a_missing_page(tmp_path):
    """The baseline the fallback is compared with."""
    store = _archive_with_a_gap(tmp_path)

    items = ArchiveIndex.open(store).paginate(BASE, parse_wikis_feed, page_size=2)

    assert len(items) == 4, "replay stopped at the archive gap instead of reading past it"


def test_losing_the_recording_loses_content(tmp_path):
    """Not a bug being fixed -- a fact being pinned. The reconstruction path
    genuinely cannot know page 3 was ever walked, because the only record of
    that was the file which is gone. The loss is why the fallback has to
    announce itself; it is not something the fallback can avoid."""
    store = _without_recording(_archive_with_a_gap(tmp_path))

    items = ArchiveIndex.open(store).paginate(BASE, parse_wikis_feed, page_size=2)

    assert len(items) == 2, "reconstruction behaviour changed; revisit what the note claims"


def test_a_reconstruction_that_hits_a_gap_says_so(tmp_path):
    """The fix. Silence here is what made the loss above invisible."""
    store = _without_recording(_archive_with_a_gap(tmp_path))
    index = ArchiveIndex.open(store)

    index.paginate(BASE, parse_wikis_feed, page_size=2)

    assert index.reconstructed(BASE)
    assert index.reconstruction_hit_a_gap(BASE), "a lossy read reported itself as a normal one"


def test_a_replayed_feed_does_not_claim_to_be_degraded(tmp_path):
    """The other half: a signal that fires for healthy archives too is one
    people learn to ignore."""
    store = _archive_with_a_gap(tmp_path)
    index = ArchiveIndex.open(store)

    index.paginate(BASE, parse_wikis_feed, page_size=2)

    assert not index.reconstructed(BASE)
    assert not index.reconstruction_hit_a_gap(BASE)


def test_the_answer_is_only_known_after_the_walk(tmp_path):
    """Mirrors `hit_pagination_cap`: which path was taken is a property of a
    walk that has happened, not of the archive at open time."""
    store = _without_recording(_archive_with_a_gap(tmp_path))
    index = ArchiveIndex.open(store)

    assert not index.reconstruction_hit_a_gap(BASE)
    index.paginate(BASE, parse_wikis_feed, page_size=2)
    assert index.reconstruction_hit_a_gap(BASE)


def test_a_complete_legacy_archive_is_reconstructed_but_not_flagged(tmp_path):
    """The discrimination that makes the signal worth having.

    An archive captured before `feeds.jsonl` is reconstructed and complete: the
    walk stops by the shared stop rule, not at a hole, so nothing was lost and
    nothing is reported. Flagging every reconstruction would put a warning on
    every legacy feed and teach people to scroll past it -- which is how a real
    one gets missed.
    """
    store = Archive.open(tmp_path / "b")
    url = f"{BASE}?ps=2&page=1"
    store.write_response(
        _Response(url, _feed(entries=1, total=1)), fetched_at="2026-08-10T00:00:00Z"
    )
    index = ArchiveIndex.open(store)

    items = index.paginate(BASE, parse_wikis_feed, page_size=2)

    assert len(items) == 1
    assert index.reconstructed(BASE), "this archive has no recording to replay"
    assert not index.reconstruction_hit_a_gap(BASE), "a complete read raised the alarm"


# --- the note assembly puts on the model ------------------------------------


def test_the_note_reaches_the_derived_model(tmp_path):
    """The index knowing is not enough -- nobody reads the index. The note is
    where a reader of the archive finds out, the same place the safety cap
    lands."""
    from connections_export.derive.apps._shared import _feed_note

    store = _without_recording(_archive_with_a_gap(tmp_path))
    index = ArchiveIndex.open(store)
    index.paginate(BASE, parse_wikis_feed, page_size=2)

    note = _feed_note(index, "wikis", BASE, page_size=2)

    assert note is not None, "a lossy read produced no note"
    assert "not read back" in note


def test_a_healthy_feed_gets_no_note(tmp_path):
    from connections_export.derive.apps._shared import _feed_note

    store = _archive_with_a_gap(tmp_path)
    index = ArchiveIndex.open(store)
    index.paginate(BASE, parse_wikis_feed, page_size=2)

    assert _feed_note(index, "wikis", BASE, page_size=2) is None


def test_a_fetch_failure_still_outranks_the_reconstruction_note(tmp_path):
    """Ordering matters: a feed that failed to fetch AND was reconstructed
    should report the failure. "We could not fetch this" is the more
    actionable of the two, and burying it under a provenance detail would be
    a regression in what the note is for."""
    from connections_export.derive.apps._shared import _feed_note

    store = Archive.open(tmp_path / "c")
    url = f"{BASE}?ps=2&page=1"
    store.write_failure(
        url=url,
        method="GET",
        fetched_at="2026-08-10T00:00:00Z",
        outcome=Outcome.http_error,
        error="500 Server Error",
    )
    index = ArchiveIndex.open(store)
    index.paginate(BASE, parse_wikis_feed, page_size=2)

    note = _feed_note(index, "wikis", BASE, page_size=2)

    assert note is not None
    assert "failed" in note
