"""How far a person-scoped Search query actually reaches.

Whether a person-scoped query returns every thread a person is in is a
question about *recall*, and recall needs something to compare against: a
whole forum read in full, thousands of topics and replies, an evening's
work.

"Was the answer cut short at the page limit we impose" needs none of that.
It is a question about the query's own response: how many pages it
returned, and whether the last one was full. No baseline, no crawl, no
bodies, no assets. It is one paginated feed read to its end.

So it is asked directly. This is what the leftover doubt costs: minutes,
against an evening.
"""

from __future__ import annotations

from connections_export.probes import probe_search_reach


class _Response:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status = status


def _feed(n: int) -> bytes:
    entries = "".join(
        f"<entry><id>urn:x:{i}</id><title>t{i}</title>"
        f'<link rel="alternate" type="text/html" href="https://fake/forums/html/topic?id=t{i}"/>'
        "</entry>"
        for i in range(n)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'
    ).encode()


class _Client:
    """Answers with full pages until `total` is exhausted."""

    def __init__(self, total: int, page_size: int = 150):
        self.total, self.page_size, self.calls = total, page_size, []

    def get(self, url):
        self.calls.append(url)
        served = (len(self.calls) - 1) * self.page_size
        return _Response(_feed(max(0, min(self.page_size, self.total - served))))


def test_a_short_page_means_the_whole_answer_was_seen():
    verdict = probe_search_reach(
        client=_Client(total=320), base_url="https://fake", userid="u", community_uuid="c"
    )

    assert verdict.total == 320
    assert verdict.complete is True
    assert verdict.pages_read == 3


def test_filling_every_page_means_the_answer_was_cut_short():
    """The limit is ours, so reaching it says nothing about how much there
    was -- only that we stopped."""
    verdict = probe_search_reach(
        client=_Client(total=100_000),
        base_url="https://fake",
        userid="u",
        community_uuid="c",
        page_limit=4,
        page_size=150,
    )

    assert verdict.complete is False
    assert verdict.total == 600
    assert "cut short" in verdict.summary.lower()


def test_it_reads_nothing_but_the_search_feed():
    """No baseline crawl: that is the whole point of asking separately."""
    client = _Client(total=10)
    probe_search_reach(client=client, base_url="https://fake", userid="u", community_uuid="c")

    assert len(client.calls) == 1
    assert all("/search/atom/" in url for url in client.calls)


def test_a_refusal_is_reported_rather_than_counted_as_an_answer():
    class _Refuses:
        def get(self, url):
            return _Response(b"<html>denied</html>", status=403)

    verdict = probe_search_reach(
        client=_Refuses(), base_url="https://fake", userid="u", community_uuid="c"
    )

    assert verdict.complete is None, "a refusal is not a complete answer"
    assert "403" in verdict.summary
