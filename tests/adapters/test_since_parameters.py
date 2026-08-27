"""Asking each feed for "what changed since", in the format it accepts.

The three apps that support this do not agree on how to say it, and the
difference is not cosmetic -- a date sent in the wrong format is either
rejected or, far worse, silently ignored, and an update that silently gets the
whole feed back looks like it worked.

* blog entries take `since` as RFC 3339
  * forum topics take `since` as epoch MILLISECONDS
  * wikis honour neither -- see the separate test that pins that

The UI never shows this split; the adapters own it.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from connections_export.adapters.blogs import entries_feed_url
from connections_export.adapters.forums import topics_url
from connections_export.adapters.search import search_results_url

CUTOFF = "2026-08-10T21:13:00Z"


def _query(url: str) -> dict:
    return parse_qs(urlsplit(url).query)


# --- blogs: RFC 3339 --------------------------------------------------------


def test_a_blog_entries_feed_takes_the_cutoff_as_rfc3339():
    """With milliseconds, as HCL's own example carries
    (`2009-01-04T20:32:31.171Z`) -- the same instant, in the shape the
    documented parameter is shown in."""
    url = entries_feed_url(base_url="https://fake", blog_uuid="b1", since=CUTOFF)

    assert _query(url)["since"] == ["2026-08-10T21:13:00.000Z"]


def test_a_blog_feed_without_a_cutoff_is_unchanged():
    """Every existing caller passes no cutoff and must get exactly the URL it
    always got -- an archive keyed by URL would otherwise miss its own
    records."""
    assert entries_feed_url(base_url="https://fake", blog_uuid="b1") == (
        "https://fake/blogs/roller-ui/rendering/feed/b1/entries/atom"
    )


def test_a_blog_feed_sorts_by_modification_when_filtering():
    """Verified accepted alongside `since`. Without it the server may answer in
    publication order, where "changed since" and "published since" quietly
    differ for an edited old post."""
    url = entries_feed_url(base_url="https://fake", blog_uuid="b1", since=CUTOFF)

    assert _query(url)["sortBy"] == ["modified"]


# --- forums: epoch milliseconds --------------------------------------------


def test_a_forum_topics_feed_takes_the_cutoff_as_epoch_milliseconds():
    url = topics_url(base_url="https://fake", forum_uuid="f1", since=CUTOFF)

    assert _query(url)["since"] == ["1786396380000"]


def test_the_forum_cutoff_is_milliseconds_not_seconds():
    """A seconds value would be read as 1970 and return the entire forum --
    an update that appears to work and re-downloads everything."""
    value = int(
        _query(topics_url(base_url="https://fake", forum_uuid="f1", since=CUTOFF))["since"][0]
    )

    assert value > 1_000_000_000_000


def test_a_forum_feed_without_a_cutoff_is_unchanged():
    assert topics_url(base_url="https://fake", forum_uuid="f1") == (
        "https://fake/forums/atom/topics?forumUuid=f1"
    )


@pytest.mark.parametrize(
    "stamp",
    ["2026-08-10T21:13:00Z", "2026-08-10T21:13:00+00:00", "2026-08-10T23:13:00+02:00"],
)
def test_the_forum_cutoff_accepts_the_timestamp_shapes_the_archive_writes(stamp):
    """Provenance stamps arrive in more than one shape; all must convert."""
    assert _query(topics_url(base_url="https://fake", forum_uuid="f1", since=stamp))["since"]


def test_an_unparseable_cutoff_is_refused_rather_than_silently_dropped():
    """Dropping it would send an unfiltered feed request that looks identical
    to a successful update returning everything."""
    with pytest.raises(ValueError):
        topics_url(base_url="https://fake", forum_uuid="f1", since="last tuesday")


# --- search: no `since` exists, so order by date and stop -------------------


def test_the_person_search_can_ask_for_newest_first():
    """Search has no `since`. It has `sortKey=date`, which is enough: page
    newest-first and stop at the cutoff. No date parameter needs to exist."""
    url = search_results_url(base_url="https://fake", userid="u1", newest_first=True)

    assert _query(url)["sortKey"] == ["date"]


def test_the_person_search_is_unchanged_when_not_asking_for_order():
    url = search_results_url(base_url="https://fake", userid="u1")

    assert "sortKey" not in _query(url)


# --- wikis: no server-side filter exists ------------------------------------


def test_a_wiki_feed_offers_no_cutoff_parameter():
    """Live-verified: the wiki page feed ignores both `since` and
    `sortBy=modified` -- a far-future cutoff still returned all 110 pages.

    So `wikis` deliberately has no `since` argument to pass. Offering one that
    the server ignores would be worse than having none: the caller would
    believe it had filtered, and an unfiltered feed looks identical to a
    correctly-filtered empty one. Wikis compare each page's own `atom:updated`
    instead, which is what `CachePolicy.IF_CHANGED` is for.
    """
    import inspect

    from connections_export.adapters import wikis

    for builder in (wikis.wiki_feed_url, wikis.nav_feed_url, wikis.page_entry_url):
        assert "since" not in inspect.signature(builder).parameters, builder.__name__


# --- the safety margin ------------------------------------------------------


def test_the_cutoff_is_moved_back_by_a_minute():
    """The server evaluates `since` against ITS clock; the anchor came from
    ours. The margin covers that difference and edits in flight at the
    boundary -- it can only re-check a minute of unchanged items, never skip
    one."""
    from connections_export.adapters.since import with_margin

    assert with_margin("2026-08-10T21:14:00Z") == "2026-08-10T21:13:00Z"


def test_the_margin_is_adjustable_for_a_deployment_that_needs_more():
    from connections_export.adapters.since import with_margin

    assert with_margin("2026-08-10T21:14:00Z", seconds=300) == "2026-08-10T21:09:00Z"


def test_each_app_encodes_a_cutoff_the_way_its_deployment_expects():
    """`AppProfile` recorded this divergence as data and argued in its own
    docstring that a single constant would mis-encode one app or the other --
    and then four call sites picked the encoding by hand and never read the
    field. Only the fake server consulted the profile at all. Reading it is
    what makes the data load-bearing rather than decorative."""
    from connections_export.adapters.profiles import BLOGS, FORUMS
    from connections_export.adapters.since import encode_for

    cutoff = "2026-08-10T21:13:00Z"

    from connections_export.adapters.since import as_epoch_millis, as_rfc3339

    assert encode_for(BLOGS, cutoff) == as_rfc3339(cutoff)
    assert encode_for(FORUMS, cutoff) == as_epoch_millis(cutoff)
    assert encode_for(BLOGS, cutoff) != encode_for(FORUMS, cutoff)


def test_an_app_whose_feeds_take_no_cutoff_refuses_rather_than_passes_through():
    """A feed that ignores `since` returns everything, which reads as
    "everything changed"; or returns nothing, which reads as "nothing changed"
    for content that did move. Both are worse than an error."""
    import pytest

    from connections_export.adapters.profiles import RTE, WIKIS
    from connections_export.adapters.since import encode_for

    for profile in (WIKIS, RTE):
        with pytest.raises(ValueError, match="cannot be filtered by date"):
            encode_for(profile, "2026-08-10T21:13:00Z")


def test_the_real_client_reads_the_profile_not_a_hand_picked_encoder():
    """The deletion, asserted. Four call sites chose `as_rfc3339` or
    `as_epoch_millis` directly; if one comes back, the profile stops being the
    single source of truth for a divergence it exists to record."""
    import re
    from pathlib import Path

    from connections_export import adapters

    root = Path(adapters.__file__).parent.parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name in ("since.py",) or "fakeserver" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for call in ("as_rfc3339(", "as_epoch_millis("):
            if re.search(rf"(?<![\w.]){re.escape(call)}", source):
                offenders.append(f"{path.name}:{call}")

    assert not offenders, f"the cutoff encoding is being chosen by hand again: {offenders}"
