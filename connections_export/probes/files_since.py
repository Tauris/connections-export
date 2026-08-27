"""Does the Files library feed's `since=` filter on modification or creation?

`profiles.FILES.since_encoding` says the community library feed accepts an
RFC 3339 `since=`. That is documented but unconfirmed, and the difference
between the two readings is the difference between an optimisation and silent
data loss:

* filtered on **modification**: sending `since` skips feed pages for documents
  that have not changed. Safe.
* filtered on **creation**: a document written two years ago and edited
  yesterday would not come back, and an update would lose the edit while
  reporting success. Never send it.

So the crawler does not send it, and this asks the question instead.

**How one request can be made to answer it.** Asking for "everything since X"
and getting an edited old document back is not enough on its own: a deployment
that IGNORES an unknown parameter returns that document too. The question
needs two subjects, chosen from the library's own contents:

* a **candidate** -- created before the cutoff, modified after it. Comes back
  under "modification", stays away under "creation".
* a **control** -- created AND modified before the cutoff. Comes back under
  neither, so if it comes back the parameter was ignored.

Their presence and absence separate all three readings, which no single
document can do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connections_export.adapters.since import parse_cutoff


@dataclass(frozen=True)
class FilesSincePlan:
    """The two subjects and the cutoff that tells them apart."""

    cutoff: str
    candidate_id: str
    candidate_name: str | None
    control_id: str
    control_name: str | None
    #: Why no plan could be made, when there is none.
    obstacle: str | None = None


@dataclass(frozen=True)
class FilesSinceVerdict:
    """What the deployment did, and what to do about it."""

    #: "modification" | "creation" | "ignored" | "inconclusive" | "incoherent"
    behaviour: str
    detail: str
    safe_to_send: bool = False
    plan: FilesSincePlan | None = None
    returned: list[str] = field(default_factory=list)


def _midpoint(earlier: str, later: str) -> str:
    """An instant strictly between two timestamps, as RFC 3339.

    The midpoint rather than either end, so a boundary that is inclusive on one
    deployment and exclusive on another cannot change the answer.
    """
    start, end = parse_cutoff(earlier), parse_cutoff(later)
    middle = start + (end - start) / 2
    return middle.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_files_since_probe(files: list) -> FilesSincePlan | None:
    """Pick a candidate, a control, and the cutoff between them.

    `None` when the library holds no such pair -- which is a real outcome, not
    a failure: a library where nothing has ever been edited after being
    uploaded cannot answer this question, and the caller is told what to change
    rather than given a guess.
    """
    edited = [
        f
        for f in files
        if f.published and f.updated and parse_cutoff(f.updated) > parse_cutoff(f.published)
    ]
    best: FilesSincePlan | None = None
    best_controls = 0
    for candidate in edited:
        cutoff = _midpoint(candidate.published, candidate.updated)
        moment = parse_cutoff(cutoff)
        controls = [
            f
            for f in files
            if f.id != candidate.id and f.updated and parse_cutoff(f.updated) < moment
        ]
        if len(controls) > best_controls:
            best_controls = len(controls)
            best = FilesSincePlan(
                cutoff=cutoff,
                candidate_id=candidate.id,
                candidate_name=candidate.name or candidate.title,
                control_id=controls[0].id,
                control_name=controls[0].name or controls[0].title,
            )
    return best


def interpret_files_since(plan: FilesSincePlan, returned_ids: list[str]) -> FilesSinceVerdict:
    """The truth table. Both subjects are needed; neither alone decides."""
    candidate_back = plan.candidate_id in returned_ids
    control_back = plan.control_id in returned_ids

    if candidate_back and not control_back:
        return FilesSinceVerdict(
            behaviour="modification",
            detail=(
                "The feed returned the edited-old document and withheld the untouched one, "
                "so `since` filters on MODIFICATION. Sending it is safe and would skip feed "
                "pages for unchanged documents."
            ),
            safe_to_send=True,
            plan=plan,
            returned=returned_ids,
        )
    if not candidate_back and not control_back:
        return FilesSinceVerdict(
            behaviour="creation",
            detail=(
                "The feed withheld both, including the document edited AFTER the cutoff, so "
                "`since` filters on CREATION. It must never be sent: an update would silently "
                "lose edits to older documents."
            ),
            plan=plan,
            returned=returned_ids,
        )
    if candidate_back and control_back:
        return FilesSinceVerdict(
            behaviour="ignored",
            detail=(
                "The feed returned both, including one untouched since long before the cutoff, "
                "so `since` was IGNORED. Sending it is harmless and buys nothing."
            ),
            plan=plan,
            returned=returned_ids,
        )
    return FilesSinceVerdict(
        behaviour="incoherent",
        detail=(
            "The feed withheld the document edited after the cutoff and returned the one "
            "untouched before it, which fits no reading of `since`. Do not send it, and record "
            "what this deployment did."
        ),
        plan=plan,
        returned=returned_ids,
    )


def probe_files_since(*, client, base_url: str, community_uuid: str) -> FilesSinceVerdict:
    """Two GETs against a real deployment. Reads only; writes nothing."""
    from connections_export.adapters.files import (  # noqa: PLC0415
        community_library_url,
        parse_library_feed,
    )
    from connections_export.crawler.engine import add_query  # noqa: PLC0415

    feed_url = community_library_url(base_url=base_url, community_uuid=community_uuid)

    def read(url: str) -> tuple[list | None, str | None]:
        """`(files, why not)`. A probe reports what happened; it never raises.

        A refusal has a body too -- an error page parses as nothing -- so the
        status is checked before the bytes are believed.
        """
        result = client.get(url)
        status = getattr(result, "status", None)
        if not hasattr(result, "content"):
            return None, f"the request did not complete ({result})"
        if status is not None and status >= 400:
            return None, f"the deployment answered {status}"
        try:
            return parse_library_feed(result.content), None
        except Exception as exc:  # noqa: BLE001 - an unreadable answer is an answer
            return None, f"the answer did not parse as a library feed ({exc})"

    files, why = read(add_query(feed_url, ps=500))
    if files is None:
        return FilesSinceVerdict(
            behaviour="inconclusive",
            detail=(
                f"Could not read that community's library: {why}. Check the community UUID and "
                "that you can open its Files list in a browser as this user."
            ),
        )
    if not files:
        return FilesSinceVerdict(
            behaviour="inconclusive",
            detail="That community's library holds no documents, so there is nothing to ask about.",
        )

    plan = plan_files_since_probe(files)
    if plan is None:
        return FilesSinceVerdict(
            behaviour="inconclusive",
            detail=(
                f"Read {len(files)} document(s), but none is both created before and modified "
                "after a moment that another document predates entirely -- so no cutoff can "
                "tell the two readings apart. Edit any document that was uploaded a while ago "
                "(a trivial change is enough), then run this again."
            ),
        )

    filtered, why = read(add_query(feed_url, ps=500, since=plan.cutoff, sortBy="modified"))
    if filtered is None:
        return FilesSinceVerdict(
            behaviour="inconclusive",
            detail=(
                f"The unfiltered read worked, but asking with `since={plan.cutoff}` failed: "
                f"{why}. A deployment that REFUSES the parameter is itself worth recording."
            ),
            plan=plan,
        )
    return interpret_files_since(plan, [f.id for f in filtered])
