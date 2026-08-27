"""Pydantic models for the archive's on-disk records.

These models define the shape of `manifest.jsonl` lines and `run-*.json`
files. Field order is significant: it is the on-disk field order, kept
stable so that manifest lines are diff-friendly across schema versions.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class Outcome(StrEnum):
    """How a single fetch attempt ended."""

    ok = "ok"
    http_error = "http_error"
    transport_error = "transport_error"
    truncated = "truncated"


class ManifestRecord(BaseModel):
    """One line of `manifest.jsonl`: one HTTP response, or one failure.

    `fetched_at` is always caller-supplied (an injected clock) — the
    archive never reads the wall clock itself, so runs are reproducible.
    """

    schema_version: int = SCHEMA_VERSION
    url: str
    method: str
    status: int | None = None
    request_headers: dict[str, str] = {}
    response_headers: dict[str, str] = {}
    fetched_at: str
    body_hash: str | None = None
    content_type: str | None = None
    discovered_from: str | None = None
    outcome: Outcome
    error: str | None = None
    # Caller-parsed pagination signature for feed responses. The archive
    # never derives these itself (that would mean parsing the body) — it
    # only stores and checks what the caller hands it.
    item_count: int | None = None
    page_size: int | None = None
    has_next: bool | None = None
    #: Which run wrote this line. The manifest is append-only and several runs
    #: share one archive, so without this a record cannot be attributed.
    run_id: str | None = None
    #: The item's OWN last-modified date, as the feed reported it, for records
    #: that correspond to one item. An update compares this against the live
    #: value -- content date against content date -- so a skewed clock between
    #: this machine and the deployment cannot cost content.
    item_date: str | None = None


#: What a run was ASKED to do with one component. `capture` and `skip` describe
#: a fresh run's selection; `update` and `add` describe a run against an
#: existing archive. Recording the selection rather than the outcome is what
#: lets a later run offer "the component you left out" -- a component that was
#: asked for and yielded nothing is still part of this archive's question.
ComponentAction = Literal["capture", "skip", "update", "add"]

#: What a run was. `initial` is a fresh capture; the rest act on an archive
#: that already exists.
RunKind = Literal["initial", "extend", "update", "extend_update"]


class ComponentSelection(BaseModel):
    """One component and what this run was asked to do about it."""

    kind: str
    id: str | None = None
    action: ComponentAction = "capture"


def _generator() -> str:
    from connections_export.interchange.manifest import GENERATOR  # noqa: PLC0415

    return GENERATOR


def _generator_version() -> str:
    from connections_export.sbom import own_version  # noqa: PLC0415

    return own_version()


def _generator_url() -> str:
    from connections_export.sbom import OWN_PACKAGE_URL  # noqa: PLC0415

    return OWN_PACKAGE_URL


class RunMetadata(BaseModel):
    """One `run-<run_id>.json` file: provenance for a single crawl run.

    `hcl_hosts` is the deployment's host set -- additive, default empty, so an
    older on-disk record with no such key still validates and existing
    construction call sites are unaffected.
    """

    schema_version: int = SCHEMA_VERSION
    run_id: str
    base_url: str
    principal: str
    adapter_version: str
    source_system_version: str
    hcl_hosts: list[str] = Field(default_factory=list)
    max_items: int | None = None

    # --- provenance an update needs --------------------
    #
    # Written by every run. Nothing was released before these existed, so they
    # are simply present -- there is no migration path and no fallback dance.

    #: When this run began, caller-supplied like every other timestamp here.
    #: This -- not `completed_at` -- is the anchor for the next update's
    #: cutoff: an item edited mid-crawl, after its own feed page had been read,
    #: would otherwise fall between this run's end and the next run's start and
    #: never be seen again.
    started_at: str | None = None
    #: Absent while the run is in flight, and on a run that was stopped or
    #: died. Only a run with this set may anchor a later update.
    completed_at: str | None = None
    run_kind: RunKind = "initial"
    #: What this run was asked to do, per component. Selection, not outcome.
    components: list[ComponentSelection] = Field(default_factory=list)
    #: The community this run captured for, when it captured for one.
    #:
    #: Not every marker survives the capture that needs it: the wikis LIST
    #: feed is the only place `snx:communityUuid` appears, and a scoped crawl
    #: skips that feed, so a community's own wiki reached the derived model
    #: attributed to nothing. The run knows the answer without asking -- a
    #: community capture crawls that community's components -- so it records
    #: it here, as selection rather than as a guess about a feed nobody has
    #: seen. Additive and optional: an older record without it still
    #: validates.
    community_uuid: str | None = None
    community_title: str | None = None
    #: The author filter this archive was captured under, if any. An archive
    #: made with "only me" cannot later be updated as if it were an everyone
    #: archive without changing the question it answers.
    author_filter: str | None = None
    #: For a person (search-driven) archive: whose content it answers for, and
    #: which search scopes were included.
    person_subject: str | None = None
    search_scopes: list[str] = Field(default_factory=list)
    #: The cutoff an update actually SENT -- not the one it computed or
    #: displayed. Without it nobody can say afterwards which window this
    #: archive covers.
    since_cutoff: str | None = None

    #: Which program wrote this run, which release of it, and where that
    #: release can be obtained. An archive is meant to be opened years later,
    #: often by whoever was handed it rather than whoever made it -- a name
    #: leaves them with a search term, an address does not.
    #:
    #: Defaulted rather than required: a record written before these existed
    #: still validates, because an old archive must not stop opening over a
    #: URL.
    generator: str = Field(default_factory=lambda: _generator())
    generator_version: str = Field(default_factory=lambda: _generator_version())
    generator_url: str = Field(default_factory=lambda: _generator_url())
