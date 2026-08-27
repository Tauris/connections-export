"""Working out what a run against an existing archive should actually do.

`--into <archive>` is the whole feature from the command line, and it carries
two implications the caller should not have to spell out: the run is an update,
and its cutoff comes from that archive's own provenance.

That second part is what keeps a scheduled re-run correct. A cutoff written
into a script is wrong the first time the schedule slips; a cutoff read from
the archive is right by construction, because it describes what that archive
actually holds rather than what someone expected it to hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config, FetchMode
from connections_export.crawler.provenance import update_cutoff


@dataclass(frozen=True)
class UpdatePlan:
    """What a run resolved to, once the archive it targets has been read."""

    fetch: FetchMode
    output_dir: Path
    #: The cutoff to send to date-filterable feeds, or None when there is
    #: nothing to filter by -- which is not an error, just a slower run.
    since: str | None


def resolve_update_plan(config: Config) -> UpdatePlan:
    """Read `config.into` (if any) and settle the run's mode, target and cutoff.

    With no `into`, this returns exactly what the config already said: every
    existing caller is unaffected.
    """
    if config.into is None:
        return UpdatePlan(
            fetch=config.fetch, output_dir=config.output_dir, since=config.since_override
        )

    target = Path(config.into)
    since = config.since_override
    if since is None:
        # An archive with no finished run offers no cutoff. The run still
        # happens -- it simply cannot filter by date and looks at everything.
        # Slow beats silently partial.
        since = update_cutoff(Archive.open(target))
    return UpdatePlan(fetch="update", output_dir=target, since=since)
