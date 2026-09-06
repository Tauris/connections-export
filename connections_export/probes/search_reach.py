"""How far a person-scoped Search query reaches, asked on its own.

Whether a person-scoped query returns every thread a person is in is a
question about recall, and recall is a comparison: answering it means
reading a forum in full to have something to compare against.

"Was the answer cut short at the page limit we impose" is not a comparison.
It is about the query's own response: how many pages came back, and whether
the last was full. Reading a forum to find that out would be paying an
evening for a number the query itself carries.

So it is asked directly. One paginated feed, read to its end, nothing
written.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchReachVerdict:
    """What the query returned, and whether that was all of it.

    `complete` is `None` when the deployment did not answer -- a refusal is
    not a short page, and must not be read as "that was everything".
    """

    total: int
    pages_read: int
    last_page: int
    page_size: int
    page_limit: int
    complete: bool | None
    summary: str


def probe_search_reach(
    *,
    client,
    base_url: str,
    userid: str,
    community_uuid: str | None = None,
    scope: str = "forums:topic",
    page_size: int = 150,
    page_limit: int = 40,
) -> SearchReachVerdict:
    """Walk the person query's pages and report where it stopped. Reads only."""
    from connections_export.adapters.search import (  # noqa: PLC0415
        parse_search_results,
        search_results_url,
    )

    total = pages_read = last_page = 0
    for page in range(1, page_limit + 1):
        url = search_results_url(
            base_url=base_url,
            userid=userid,
            community_uuid=community_uuid,
            scope=scope,
            page=page,
            page_size=page_size,
        )
        result = client.get(url)
        status = getattr(result, "status", None)
        if not hasattr(result, "content") or (status is not None and status >= 400):
            # A refusal has a body too, and an error page parses as no
            # entries -- which is indistinguishable from a short page unless
            # the status is checked before the bytes are believed.
            return SearchReachVerdict(
                total=total,
                pages_read=pages_read,
                last_page=last_page,
                page_size=page_size,
                page_limit=page_limit,
                complete=None,
                summary=(
                    f"the deployment answered {status} on page {page} — "
                    f"{total} result(s) were read before that, and how many more "
                    "there are is unknown"
                ),
            )
        try:
            batch = parse_search_results(result.content)
        except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
            return SearchReachVerdict(
                total=total,
                pages_read=pages_read,
                last_page=last_page,
                page_size=page_size,
                page_limit=page_limit,
                complete=None,
                summary=f"page {page} did not parse as a Search feed: {exc}",
            )
        total += len(batch)
        pages_read, last_page = page, len(batch)
        if len(batch) < page_size:
            return SearchReachVerdict(
                total=total,
                pages_read=pages_read,
                last_page=last_page,
                page_size=page_size,
                page_limit=page_limit,
                complete=True,
                summary=(
                    f"{total} result(s) over {pages_read} page(s); the last came back "
                    f"short ({last_page} of {page_size}), so that was the whole answer"
                ),
            )

    return SearchReachVerdict(
        total=total,
        pages_read=pages_read,
        last_page=last_page,
        page_size=page_size,
        page_limit=page_limit,
        complete=False,
        summary=(
            f"{total} result(s) over {pages_read} page(s), every one of them full — "
            f"the answer was cut short by the {page_limit}-page limit asked for here, "
            "not by the deployment. How much more there is, is unknown."
        ),
    )
