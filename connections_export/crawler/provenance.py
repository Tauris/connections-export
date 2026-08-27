"""Reading an archive's own record of what it already captured.

An update needs one thing from the archive it is adding to: the moment before
which everything is already known. That moment is not a guess and not the wall
clock -- it is written into the archive by whichever run last finished.
"""

from __future__ import annotations

from connections_export.adapters.since import with_margin
from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive

#: How far back the cutoff is moved from the anchor. See `with_margin` for why
#: a margin exists at all; a minute is enough for clock differences between two
#: machines on the same network and for timestamp rounding between apps.
CUTOFF_MARGIN_SECONDS = 60


def _read_runs(archive: Archive) -> list[RunMetadata]:
    runs: list[RunMetadata] = []
    for entry in archive.source.run_metadata():
        try:
            runs.append(RunMetadata.model_validate_json(entry.data))
        except (OSError, ValueError):
            # An unreadable record is not evidence of a successful run, which
            # is the only thing this module is looking for.
            continue
    return runs


def last_successful_run(archive: Archive) -> RunMetadata | None:
    """The most recent run that actually FINISHED, or None.

    A run that died must never anchor an update. Its feeds were read at wildly
    different moments and everything after the point of death was never looked
    at, so treating its start as "everything before here is captured" would
    lose that window silently and permanently -- the worst failure this feature
    could have, because the archive would look complete.
    """
    finished = [run for run in _read_runs(archive) if run.completed_at and run.started_at]
    if not finished:
        return None
    return max(finished, key=lambda run: run.started_at or "")


def update_cutoff(archive: Archive) -> str | None:
    """The date an update of `archive` should ask for changes since.

    The anchor is the last successful run's START, not its end. An item edited
    while that capture was running -- after its own feed page had already been
    read -- would otherwise fall between that run's end and this one's cutoff,
    and never be seen again.
    """
    run = last_successful_run(archive)
    if run is None or not run.started_at:
        return None
    return with_margin(run.started_at, seconds=CUTOFF_MARGIN_SECONDS)


def components_captured(archive: Archive) -> list[dict]:
    """Every component any finished run was asked to capture, most recent
    first. What "extend" offers is whatever is NOT in here."""
    seen: dict[tuple[str, str | None], dict] = {}
    for run in sorted(_read_runs(archive), key=lambda r: r.started_at or "", reverse=True):
        if not run.completed_at:
            continue
        for component in run.components:
            key = (component.kind, component.id)
            if key not in seen:
                seen[key] = {
                    "kind": component.kind,
                    "id": component.id,
                    "action": component.action,
                    "captured_at": run.started_at,
                }
    return list(seen.values())


#: Hosts that are this tool's own fakeserver rather than a deployment: the one
#: the demo crawls, and the placeholder its chips carry. Neither can be asked
#: for anything, so neither is an answer to "where did this archive come from"
#: while a real one is on record.
FAKE_BASE_URLS = frozenset({"https://fake", "https://demo.connections.example"})


def _is_fake(base_url: str | None) -> bool:
    return (base_url or "").rstrip("/") in FAKE_BASE_URLS


def archive_base_url(archive: Archive) -> str | None:
    """Which deployment this archive was captured from, or `None`.

    An update is an update OF something, and that something knows where it
    came from -- every run records `base_url`. Nothing read it, so extending
    an archive without retyping the address (which is the normal way to do it:
    you pick the archive from a list) left the console with no deployment and
    it fell back to the demo, writing fabricated content into a real archive.

    Prefers a finished run, because an abandoned one may have died before it
    learned anything; falls back to any record rather than answering `None`,
    since a half-finished capture still knows its own address.
    """
    runs = sorted(_read_runs(archive), key=lambda r: r.started_at or "", reverse=True)
    finished = last_successful_run(archive)
    ordered = ([finished] if finished is not None else []) + runs

    # A REAL address first, wherever it appears. An archive that a demo run
    # wrote into -- which is how this bug was reported -- has the fake's host
    # on its most recent finished run, and answering with that would point the
    # next update at a host that does not exist.
    for run in ordered:
        if run.base_url and not _is_fake(run.base_url):
            return run.base_url
    for run in ordered:
        if run.base_url:
            return run.base_url
    return None
