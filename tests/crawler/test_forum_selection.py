"""Choosing which forum topics to read, instead of reading them all.

An author-filtered community capture should not have to read every topic and
every reply of every forum just to keep the handful of threads one person is
in. The community-scoped person query answers that narrower question: it
returns root topics, and folds a person's nested replies into the containing
topic.

So Search selects and the crawl fetches. This module is the selecting half,
and these tests pin the parts that decide whether the crawl may trust it:
what counts as a usable answer, what a refusal does, and the fact that an
answer cut short at our own page limit is still used but never silently.
"""

from __future__ import annotations

from connections_export.crawler.forum_selection import (
    forum_topic_id,
    select_community_forum_topics,
)


def _feed(ids: list[str]) -> bytes:
    entries = "".join(
        f"<entry><id>urn:x:{tid}</id><title>{tid}</title>"
        f'<link rel="alternate" type="text/html" href="https://fake/forums/html/topic?id={tid}"/>'
        f'<link rel="via" type="application/atom+xml" '
        f'href="https://fake/forums/atom/topic?topicUuid={tid}"/>'
        "</entry>"
        for tid in ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'
    ).encode()


class _Fetch:
    """A stand-in for the crawl's fetcher: serves `total` distinct topics,
    `page_size` at a time, and `None` for a page the deployment refused (the
    fetcher's own way of saying so, having already archived the failure)."""

    def __init__(self, total: int, page_size: int = 150, refuse_from: int | None = None):
        self.total, self.page_size, self.refuse_from = total, page_size, refuse_from
        self.calls: list[str] = []

    def __call__(self, url):
        self.calls.append(url)
        page = len(self.calls)
        if self.refuse_from is not None and page >= self.refuse_from:
            return None
        served = (page - 1) * self.page_size
        count = max(0, min(self.page_size, self.total - served))
        return _feed([f"t{served + i}" for i in range(count)])


def _select(fetch, **kw):
    return select_community_forum_topics(
        fetch=fetch,
        base_url="https://fake",
        userid="jdoe",
        community_uuid="c-1",
        **kw,
    )


def test_the_query_is_pinned_to_both_the_person_and_the_community():
    """Either pin alone is the wrong question. The person alone spans every
    forum on the deployment they have ever posted in; the community alone is
    everyone's content. The recommendation is the AND of the two."""
    fetch = _Fetch(total=3, page_size=150)
    _select(fetch)
    url = fetch.calls[0]
    assert "personUserId" in url and "jdoe" in url
    assert "community" in url and "c-1" in url
    assert "scope=forums%3Atopic" in url


def test_a_short_page_ends_the_walk_and_the_answer_is_complete():
    fetch = _Fetch(total=200, page_size=150)
    selection = _select(fetch, page_size=150)
    assert len(fetch.calls) == 2
    assert selection.hits == 200
    assert len(selection.topic_ids) == 200
    assert selection.complete is True
    assert selection.usable is True


def test_topic_ids_are_deduplicated_but_keep_their_order():
    """A person with many replies in one thread still gets that thread once,
    so the crawl reads it once."""

    class _Repeats(_Fetch):
        def __call__(self, url):
            self.calls.append(url)
            return _feed(["b", "a", "b", "c"] if len(self.calls) == 1 else [])

    selection = _select(_Repeats(total=0))
    assert selection.topic_ids == ("b", "a", "c")
    assert selection.hits == 4


def test_a_refusal_on_the_first_page_is_not_an_empty_answer():
    """The failure that must never happen is reading a refusal as "this
    person is in no threads" and capturing nothing while reporting success."""
    selection = _select(_Fetch(total=50, refuse_from=1))
    assert selection.usable is False
    assert selection.complete is None
    assert "did not answer" in selection.summary
    assert "reading the forums in full instead" in selection.summary


def test_a_refusal_on_a_later_page_keeps_what_came_before_but_says_so():
    selection = _select(_Fetch(total=400, page_size=150, refuse_from=2), page_size=150)
    assert len(selection.topic_ids) == 150
    assert selection.usable is True
    assert selection.complete is None
    assert "more may exist" in selection.summary


def test_an_answer_cut_short_at_our_own_page_limit_is_used_but_flagged():
    """Falling back to reading every forum in full would be worse in every
    way for a person this prolific -- and would hit its own caps. Use it,
    but never let the truncation be silent."""
    fetch = _Fetch(total=10_000, page_size=150)
    selection = _select(fetch, page_size=150, page_limit=3)
    assert len(fetch.calls) == 3
    assert selection.complete is False
    assert selection.usable is True
    assert "cut short" in selection.summary


def test_hits_that_yield_no_topic_id_leave_the_selection_unusable():
    """An unrecognised permalink shape must fall back to the full walk, not
    seed an empty crawl that reports a clean, successful, empty capture."""

    class _Unrecognised(_Fetch):
        def __call__(self, url):
            self.calls.append(url)
            return (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:x:1</id>'
                b'<link rel="alternate" href="https://fake/somewhere/else"/></entry></feed>'
            )

    selection = _select(_Unrecognised(total=0))
    assert selection.hits == 1
    assert selection.topic_ids == ()
    assert selection.usable is False
    assert selection.samples  # the shape the parser choked on, for the log


def test_no_hits_at_all_is_unusable_so_a_name_typed_by_hand_still_works():
    """The author filter can be a display name; the person query wants a user
    id. A name returns nothing, and nothing must mean "fall back", never
    "this person wrote nothing"."""
    selection = _select(_Fetch(total=0))
    assert selection.hits == 0
    assert selection.usable is False


def test_forum_topic_id_reads_the_shapes_search_actually_returns():
    assert forum_topic_id("https://fake/forums/atom/topic?topicUuid=abc") == "abc"
    assert forum_topic_id("https://fake/forums/html/topic?id=abc") == "abc"
    assert (
        forum_topic_id(
            "https://fake/communities/service/html/communityview?communityUuid=c#topicId=abc"
        )
        == "abc"
    )
    assert forum_topic_id("https://fake/forums/html/threadTopic?id=abc") == "abc"
    assert forum_topic_id("https://fake/blogs/handle/entry/e") is None
    assert forum_topic_id(None) is None
