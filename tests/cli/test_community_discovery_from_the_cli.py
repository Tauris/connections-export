"""`crawl <community-url>` from the CLI discovers what the console discovers.

The console lists a community's components; the CLI, over the same
authenticated connection, reported "no wiki, blog or forum" for every
community. The cause was in the CLI's discovery `fetch`: `client.get`
returns the tool's `Fetched`, whose field is `.status`, and the shim read
`.status_code` -- a raw-httpx name that does not exist on `Fetched` -- so
`getattr(..., None)` was None for every feed, every feed looked unreadable,
and nothing was found.

Nobody had run this path: everyone used the console, whose lookup reads
`.status`. This drives `_discover_community` with a client that returns real
`Fetched` objects, so the wrong attribute name yields an empty community and
the right one yields the forum.
"""

from __future__ import annotations

from connections_export.adapters.forums import forums_list_url
from connections_export.cli import _discover_community
from connections_export.config import Config
from connections_export.http.results import Fetched

BASE = "https://fake"

# A minimal forums list feed the real parser accepts: one forum.
_FORUMS_FEED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom" '
    b'xmlns:snx="http://www.ibm.com/xmlns/prod/sn">'
    b"<title>Forums</title>"
    b"<entry>"
    b"<id>urn:lsid:ibm.com:forum:11111111-1111-1111-1111-111111111111</id>"
    b"<title>Team Forum</title>"
    b'<link rel="self" type="application/atom+xml" '
    b'href="https://fake/forums/atom/topics?forumUuid=11111111-1111-1111-1111-111111111111"/>'
    b"<snx:communityUuid>c-1</snx:communityUuid>"
    b"</entry>"
    b"</feed>"
)


class _Client:
    """Returns a real `Fetched` for the forums list, 404 otherwise -- so the
    only way any component is found is by reading `.status` correctly."""

    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str):
        self.urls.append(url)
        if url.startswith(forums_list_url(base_url=BASE)):
            return Fetched(url=url, method="GET", status=200, headers={}, content=_FORUMS_FEED)
        return Fetched(url=url, method="GET", status=404, headers={}, content=b"")


def test_the_cli_reads_a_feeds_status_and_finds_the_component():
    client = _Client()

    result = _discover_community(Config(base_url=BASE), client, "c-1")

    kinds = {c["kind"] for c in result.get("components", [])}
    assert "forum" in kinds, (
        "the CLI found no forum in a community whose forums feed answered 200 -- "
        "the .status_code/.status regression is back"
    )
    assert client.urls, "no request was even made"


def test_a_forbidden_feed_is_not_treated_as_content():
    """The other half of the same read: a 403 must be None, not passed to the
    parser as if it were a feed. A guard that read the wrong attribute got
    both directions wrong at once."""

    class _Forbidden:
        def get(self, url: str):
            return Fetched(url=url, method="GET", status=403, headers={}, content=b"nope")

    result = _discover_community(Config(base_url=BASE), _Forbidden(), "c-1")
    assert result.get("components") == []
