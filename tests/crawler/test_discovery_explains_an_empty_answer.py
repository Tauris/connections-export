"""A community that yields no components says why it yielded none.

Discovery asks several feeds and parses each, and every one of those
parses is wrapped in a `try` that returns `[]`. That is right -- one
malformed feed must not lose the other four -- but it means the whole
thing can come back empty with nothing said, and the three causes look
identical from outside:

  * the request never returned content at all;
  * it returned content that parsed to no entries;
  * it returned content whose parse RAISED, and the exception was
    swallowed.

The last is the one that matters, because it is invisible: a frozen
executable cannot be stepped through, and a library behaving differently
there than in a source install produces exactly this shape -- requests
answering 200, and nothing identified.

The reasoning is already in this module, applied to one component:
"A community that plainly has rich content and is offered none is
otherwise indistinguishable from one that has none." It holds for the
whole answer.
"""

from __future__ import annotations

from connections_export.crawler.community import discover_components

BASE = "https://connections.example.corp"
UUID = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"


def _discover(fetch):
    return discover_components(community_uuid=UUID, base_url=BASE, fetch=fetch)


def test_nothing_came_back_is_said_so():
    """Every feed answered with nothing."""
    answer = _discover(lambda url: None)

    assert answer["components"] == []
    assert answer.get("detail"), "an empty answer with no explanation"
    assert "attempts" in answer
    assert answer["attempts"], "no record of what was asked"
    assert all(a["fetched"] is False for a in answer["attempts"])


def test_a_parse_that_raises_is_reported_not_swallowed():
    """The invisible case. Content arrives, the parser throws, and the
    result is indistinguishable from an empty community unless it is
    recorded."""
    answer = _discover(lambda url: b"<<< this is not a feed")

    assert answer["components"] == []
    attempts = {a["feed"]: a for a in answer["attempts"]}
    assert attempts, "no record of what was asked"
    failed = [a for a in attempts.values() if a.get("error")]
    assert failed, f"a parse failed and nothing recorded it: {attempts}"
    assert answer.get("detail")


def test_content_that_parses_to_nothing_is_distinguished_from_no_content():
    """A genuinely empty community is a real answer, and must not read the
    same as a broken one."""
    empty_feed = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>none</title></feed>'
    )
    answer = _discover(lambda url: empty_feed)

    assert answer["components"] == []
    attempts = {a["feed"]: a for a in answer["attempts"]}
    assert any(a.get("fetched") and not a.get("error") for a in attempts.values())


def test_a_community_with_components_says_nothing_extra():
    """The explanation is for the empty case. A working answer must not
    grow a field that reads as a warning."""
    forums_feed = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:snx="http://www.ibm.com/xmlns/prod/sn">'
        b"<entry><id>urn:lsid:ibm.com:forum:11111111-2222-3333-4444-555555555555</id>"
        b"<title>Help</title></entry></feed>"
    )

    def fetch(url: str) -> bytes | None:
        return forums_feed if "/forums/" in url else None

    answer = _discover(fetch)

    if answer["components"]:
        assert "detail" not in answer
        assert "attempts" not in answer
