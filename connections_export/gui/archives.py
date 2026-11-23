"""Discovery + path-safety for run archives under a base dir (the console's
`ARCHIVES_BASE`). Pure and DOM-free so it unit-tests without a server."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from connections_export.archive.source import ArchiveSourceError, source_for
from connections_export.archive.store import Archive
from connections_export.derive import DeriveError, derive

#: Matches the ``<YYYYMMDD>-<HHMMSS>`` stamp `_new_run_archive_dir` (app.py)
#: bakes into every archive dir name. Everything after the stamp is the
#: "tail": either a bare unique suffix (older archives, dash-free) or
#: ``<slug>-<unique>`` -- the name of what was captured, plus four characters
#: to keep two runs of the same thing on the same second apart.
_DEMO_RE = re.compile(r"^DEMO-FAKE-DATA-(\d{8})-(\d{6})(?:-(.*))?$")
#: Current shape: no host. Everyone saves from one deployment, so repeating it
#: in every directory name spent length the captured thing's name needs; the
#: host is recorded in the run's manifest and summary instead.
_REAL_RE = re.compile(r"^export-(\d{8})-(\d{6})(?:-(.*))?$")
#: Archives written before the host came out of the name. Tried second, so a
#: current name -- whose first field is a date, not a host -- never matches it.
_REAL_HOSTED_RE = re.compile(r"^export-(.+?)-(\d{8})-(\d{6})(?:-(.*))?$")

#: How much of a captured thing's name goes into the directory name. Long
#: enough to recognise a community or forum at a glance, short enough that the
#: directory name stays readable in a terminal and under path limits.
SLUG_MAX = 20


def slugify(text: str | None, limit: int = SLUG_MAX) -> str:
    """A short, filesystem-safe form of a captured thing's name.

    Lowercase, words joined by single hyphens, trimmed at a word boundary
    where possible so a truncated slug does not end mid-word.
    """
    if not text:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    # Prefer a word boundary, unless that would leave almost nothing.
    if "-" in cut and cut.rindex("-") >= limit // 2:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-")


def _slug_of(tail: str | None) -> str:
    """The captured thing's name out of a directory name's tail.

    A tail with no hyphen is an older archive's bare unique suffix, which
    names nothing.
    """
    if not tail or "-" not in tail:
        return ""
    return tail.rsplit("-", 1)[0]


def _format_stamp(date_part: str, time_part: str) -> str | None:
    try:
        dt = datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def _display_name_for(archive_dir: Path, summary: dict | None) -> str:
    """What to call this archive.

    A stored `display_name` wins: the user renamed it, and their name for a
    thing beats one derived from a directory. The directory name stays the
    archive's id -- `resolve_archive`, "extend & update" and every `into:`
    reference address an archive by it, so a rename moves nothing on disk.
    """
    stored = (summary or {}).get("display_name")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return _display_name(archive_dir.name)


def _display_name(name: str) -> str:
    """Humanize a raw archive dir name for display. Falls back to the raw
    `name` when it doesn't match either the demo or real naming scheme
    (e.g. a hand-made or foreign-tool directory dropped into the base)."""
    m = _DEMO_RE.match(name)
    if m:
        date_part, time_part, tail = m.groups()
        stamp = _format_stamp(date_part, time_part)
        if stamp:
            slug = _slug_of(tail)
            return f"Demo (fake data) · {slug} · {stamp}" if slug else f"Demo (fake data) · {stamp}"
    m = _REAL_RE.match(name)
    if m:
        date_part, time_part, tail = m.groups()
        stamp = _format_stamp(date_part, time_part)
        if stamp:
            # What was captured leads: it is what somebody is looking for in a
            # list of runs. The time follows it.
            slug = _slug_of(tail)
            return f"{slug} · {stamp}" if slug else stamp
    m = _REAL_HOSTED_RE.match(name)
    if m:
        host, date_part, time_part, tail = m.groups()
        stamp = _format_stamp(date_part, time_part)
        if stamp:
            slug = _slug_of(tail)
            return f"{slug} · {host} · {stamp}" if slug else f"{host} · {stamp}"
    return name


def _item_count(archive_dir: Path) -> int:
    """Cheap size hint for a run archive: the number of non-empty lines in
    its `manifest.jsonl` (one line per captured item). Deliberately not the
    full derived model -- that's too slow to compute for a whole list.

    Reads through the source so a zipped archive lists with a real count
    rather than a placeholder: for a zip this is one central-directory seek
    and one member read, no extraction.
    """
    try:
        source = source_for(archive_dir)
    except ArchiveSourceError:
        return 0
    return sum(1 for line in source.read_lines("manifest.jsonl") if line.strip())


@dataclass(frozen=True)
class ArchiveInfo:
    name: str
    path: Path
    is_demo: bool
    mtime: float
    #: Signature of what this archive captured (`archive_target_key`), or
    #: `None` when it has no usable persisted summary to read it from.
    target_key: str | None
    display_name: str
    item_count: int
    repair_needed: bool = False
    repair_reason: str | None = None
    repair_keys: list[str] = field(default_factory=list)
    #: Known problems an older release left in this archive, each with what
    #: went wrong and what to do (see `archive_issues`).
    issues: list[dict] = field(default_factory=list)
    #: The archives whose selections a completed run here repeated.
    captured_again_from: frozenset[str] = frozenset()
    #: The newer archive that captured this one again, once one has.
    captured_again_as: str | None = None
    #: The runs this archive holds, by id -- how a zip is recognised as a
    #: zipped copy of the folder beside it (`with_zip_twins`).
    run_ids: frozenset[str] = frozenset()
    #: For a zip: the folder beside it that it is a zipped copy of.
    copy_of: str | None = None


#: Source hosts that mark a run as demo data (the fake server's own hosts).
#: Stored as bare hostnames and joined to the ``export-`` archive-dir prefix
#: at comparison time, never written out as one fused literal: the prefix
#: would run into the leading ``example`` label and the hygiene guard
#: (tests/test_hygiene.py) would stop reading these as the RFC 2606
#: placeholders they are.
DEMO_SOURCE_HOSTS = ("example.corp", "demo.connections.example")

_SUMMARY_CACHE: dict[tuple[str, float], dict] = {}
SUMMARY_FILENAME = "archive-summary.json"


def model_summary(model, *, captured_at: str | None = None) -> dict:
    """Build the small, list-safe description persisted beside a run.

    `captured_at` stamps every group. Per group rather than per archive
    because components can arrive in different visits -- an archive-level date
    would be right for the last one and wrong for all the others, and the
    cutoff for updating a component has to be about that component.
    """
    groups = [
        {
            "kind": "wiki",
            # The id it is ADDRESSED by. Matching holdings against what the
            # deployment offers has to key on this: a renamed blog is the same
            # blog, and matching on titles would offer it as something new.
            "id": wiki.label or wiki.id,
            "title": wiki.title or wiki.label,
            "count": len(wiki.pages),
            "captured_at": captured_at,
        }
        for wiki in model.wikis
    ]
    groups.extend(
        {
            "kind": "blog" if blog.kind == "blog" else "ideation_blog",
            "id": blog.id,
            "title": blog.title or blog.handle or blog.id,
            "count": len(blog.posts),
            "captured_at": captured_at,
        }
        for blog in model.blogs
    )
    groups.extend(
        {
            "kind": "forum",
            "id": forum.id,
            "title": forum.title or forum.id,
            "count": len(forum.topics),
            "captured_at": captured_at,
        }
        for forum in model.forums
    )
    groups.extend(
        {
            "kind": "files",
            # Files and Rich Content are addressed by the COMMUNITY uuid.
            "id": library.community_uuid or library.id,
            "title": library.title or "Files",
            "count": len(library.file_ids),
            "captured_at": captured_at,
        }
        for library in model.file_libraries
    )
    groups.extend(
        {
            "kind": "rich_content",
            "id": highlights.community_uuid or highlights.id,
            "title": highlights.title or "Highlights",
            "count": len(highlights.page_ids),
            "captured_at": captured_at,
        }
        for highlights in model.rich_content
    )
    from connections_export.sbom import OWN_PACKAGE_URL, OWN_REPO_URL  # noqa: PLC0415

    return {
        "status": "ok",
        # The smallest readable file in an archive directory, and so the first
        # one a stranger opens. It says what will read the rest of it -- and
        # both where to get that tool (PyPI) and where its source lives (repo).
        "generator_url": OWN_PACKAGE_URL,
        "generator_repo_url": OWN_REPO_URL,
        "base_url": model.base_url,
        "source_version": model.source_version,
        "hcl_hosts": model.hcl_hosts,
        "groups": groups,
        "communities": [
            # The id as well as the title: a later visit has to be able to ask
            # the deployment what this community contains NOW, and a title
            # cannot address anything.
            {"id": community.id, "title": community.title or community.id}
            for community in model.communities
        ],
    }


#: How each app answers "what changed since". The asymmetry is the deployment's,
#: not ours, and it is large enough that a user has to see it before choosing --
#: which is why it travels as data rather than living in the front end.
#:
#: `since` -- the server can be asked directly. A handful of requests.
#: `rescan` -- no date filter exists; the listing is re-read and each item's own
#: date compared. Cost grows with the size of the container.
#: `reread` -- small enough to fetch whole every time.
UPDATE_METHODS = {
    "wiki": "rescan",
    "blog": "since",
    "ideation_blog": "since",
    "forum": "since",
    "files": "rescan",
    "rich_content": "reread",
}

#: Roughly how many requests an update of one component costs BEFORE anything
#: turns out to have changed -- the floor a screen can quote honestly. Feed
#: pages are ~500 items, so a rescan is dominated by the per-item date check
#: rather than by pagination.
_FLOOR_PER_ITEM = {"rescan": 1.0}
_FLOOR_FIXED = {"since": 4, "reread": 4, "rescan": 2}


def update_floor(kind: str, count: int) -> int:
    """The request floor for updating one component holding `count` items."""
    method = UPDATE_METHODS.get(kind, "since")
    per_item = _FLOOR_PER_ITEM.get(method, 0.0)
    return int(_FLOOR_FIXED.get(method, 4) + per_item * max(0, count))


def archive_ledger(archive_dir: Path) -> dict:
    """What an existing archive holds, and what updating it would take.

    Read from the archive's own summary and run records -- never by deriving
    the model, which is far too slow to render a list of archives with.
    """
    from connections_export.archive.store import Archive  # noqa: PLC0415
    from connections_export.crawler.provenance import (  # noqa: PLC0415
        last_successful_run,
        update_cutoff,
    )

    archive_dir = Path(archive_dir)
    summary = read_archive_summary(archive_dir) or {}
    rows = []
    for group in summary.get("groups", []):
        kind = group.get("kind", "")
        count = int(group.get("count") or 0)
        method = UPDATE_METHODS.get(kind, "since")
        rows.append(
            {
                "kind": kind,
                "id": group.get("id"),
                "title": group.get("title"),
                "count": count,
                "in_archive": True,
                "captured_at": group.get("captured_at"),
                "update_method": method,
                "floor_requests": update_floor(kind, count),
            }
        )

    anchor = None
    since = None
    try:
        store = Archive.open(archive_dir)
        run = last_successful_run(store)
        anchor = run.started_at if run else None
        since = update_cutoff(store)
    except Exception:  # noqa: BLE001 - a ledger must degrade, never raise
        pass

    return {
        "archive": archive_dir.name,
        # The humanized name, so the identity strip can lead with what a
        # person calls this archive while the directory name stays a fact.
        "display_name": _display_name_for(archive_dir, summary),
        "rows": rows,
        # The moment the cutoff is derived from, kept beside it so the screen
        # can explain the minute's difference rather than just assert a date.
        "anchor": anchor,
        "since": since,
        "base_url": summary.get("base_url"),
        # Carried so the caller can ask the deployment what this community
        # holds now -- which is the only way to know what is missing.
        "communities": summary.get("communities") or [],
    }


#: What an older forum reader missed, said to the person who has to decide
#: whether it matters to them.
FORUM_ATTACHMENTS_ISSUE = {
    "key": "forum-attachments",
    "title": "Some forum attachments are missing",
    "explanation": (
        "When this archive was made, some attachments were skipped by mistake: "
        "documents uploaded to forum topics and replies (a Word or PDF file, say). "
        "They do not show up as broken links -- they are missing entirely, and "
        "nothing in the archive mentions them. Only these attachments are affected: "
        "pages, posts, replies, comments and the images in their text are complete. "
        "Repair adds the missing attachments to this archive. It needs the server the "
        "archive was captured from to be available, reads only what is missing, and "
        "changes nothing that is already there."
    ),
    "remedy": "repair",
}


def _runs(archive_dir: Path) -> list:
    from connections_export.archive.records import RunMetadata  # noqa: PLC0415

    runs = []
    try:
        for entry in source_for(archive_dir).run_metadata():
            try:
                runs.append(RunMetadata.model_validate_json(entry.data))
            except (OSError, ValueError):
                continue
    except (ArchiveSourceError, OSError, ValueError):
        return []
    return runs


def archive_issues(archive_dir: Path) -> tuple[list[dict], frozenset[str]]:
    """Known problems in this archive, and the archives it captured again.

    Read from run metadata alone: each run records the version of the reader
    that made it. A forum captured by the `forums-1` reader can lack uploaded
    documents (`FORUM_ATTACHMENTS_ISSUE`); the remedy is a new archive, so
    nothing here ever changes the old one.
    """
    runs = _runs(archive_dir)
    issues = []
    if forum_attachments_repair_needed(runs):
        issues.append(dict(FORUM_ATTACHMENTS_ISSUE))
    again = frozenset(
        run.captured_again_from for run in runs if run.captured_again_from and run.completed_at
    )
    return issues, again


def with_recaptures(infos: list[ArchiveInfo]) -> list[ArchiveInfo]:
    """Mark each archive a newer copy has replaced -- a Capture again, or the
    folder a zip was unzipped into and repaired. Its repair is no longer
    offered (that would make a second copy), but its issues are kept: it is
    still not repaired itself, and says so beside the copy's name."""
    newest: dict[str, str] = {}
    for info in sorted(infos, key=lambda i: (i.mtime, i.name)):
        for source in info.captured_again_from:
            newest[source] = info.name
    return [
        replace(
            info,
            captured_again_as=newest[info.name],
            # Its newer copy answers for it; repairing the old one as well
            # would make a second copy of the same thing.
            repair_needed=False,
            repair_reason=None,
            repair_keys=[],
        )
        if info.name in newest
        else info
        for info in infos
    ]


def with_zip_twins(infos: list[ArchiveInfo]) -> list[ArchiveInfo]:
    """Recognise a zip that is a zipped copy of the folder beside it.

    "Save as .zip" writes ``X.zip`` next to ``X``; the folder may have been
    updated since, so the zip is a copy when every run it holds is in the
    folder too. The folder is the archive to repair -- repairing the zip as
    well would unzip a third copy of the same thing -- so the zip is not
    offered the repair, keeps its issues, and names the folder."""
    folders = {info.name: info for info in infos if info.path.is_dir()}
    out = []
    for info in infos:
        twin = folders.get(info.path.stem) if is_zip_archive(info.path) else None
        if twin is not None and info.run_ids and info.run_ids <= twin.run_ids:
            info = replace(
                info, copy_of=twin.name, repair_needed=False, repair_reason=None, repair_keys=[]
            )
        out.append(info)
    return out


def archive_repair_status(archive_dir: Path) -> dict:
    """Which known repairs this archive needs, from its run metadata alone.

    `repair_keys` names them (``blog-comments``, ``forum-attachments``) for
    `crawl --repair`, which does exactly those and nothing else;
    `repair_reason` says why, for the person deciding.
    """
    blog = _blog_repair_status(archive_dir)
    keys, reasons = [], []
    if blog["repair_needed"]:
        keys.append("blog-comments")
        reasons.append(blog["repair_reason"])
    if forum_attachments_repair_needed(_runs(archive_dir)):
        keys.append("forum-attachments")
        reasons.append("some forum attachments were skipped by mistake when it was made")
    return {
        "repair_needed": bool(keys),
        "repair_reason": "; ".join(reasons) or None,
        "repair_keys": keys,
    }


#: The first release whose recorded repair can be trusted: before it, a repair
#: that was stopped part-way was still recorded as made.
TRUSTED_REPAIR_FROM = (1, 0, 1, 1, 5)  # 1.0.1b5


def _version_key(version: str | None) -> tuple | None:
    """`1.0.1b5` -> (1, 0, 1, 1, 5); a final release ranks above its betas."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?(?:[.+-].*)?", version or "")
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[stage]
    return (int(major), int(minor), int(patch), rank, int(number or 0))


def forum_attachments_repair_needed(runs: list) -> bool:
    """A forum captured by the `forums-1` reader, and no trusted repair since.

    A repair is recorded (`RunMetadata.repairs`), not inferred from a newer
    reader: an update by a fixed release re-reads only changed threads. Only a
    repair recorded by `TRUSTED_REPAIR_FROM` or later counts -- an earlier one
    may have been stopped and recorded anyway, and repeating a repair that had
    finished costs nothing, since it fetches only what is missing."""
    from connections_export.crawler.repair import FORUM_ATTACHMENTS  # noqa: PLC0415

    if not any(run.adapter_version == "forums-1" for run in runs):
        return False

    def trusted(run) -> bool:
        key = _version_key(getattr(run, "generator_version", None))
        return key is not None and key >= TRUSTED_REPAIR_FROM

    return not any(
        run.completed_at and FORUM_ATTACHMENTS in (run.repairs or []) and trusted(run)
        for run in runs
    )


def _blog_repair_status(archive_dir: Path) -> dict:
    """Detect whether the latest component run predates a known repair.

    This reads only run metadata. An old blog capture is repairable because
    the blog adapter version records whether its comment pagination fix was
    present; no model derivation or network request is needed to list it.
    """
    try:
        source = source_for(archive_dir)
        runs = []
        for entry in source.run_metadata():
            try:
                from connections_export.archive.records import RunMetadata  # noqa: PLC0415

                runs.append(RunMetadata.model_validate_json(entry.data))
            except (OSError, ValueError):
                continue

        # The blog adapter stamps every blog run with its version, so the run
        # metadata -- not the append-only manifest -- is the authority on
        # whether the pagination fix was in force. Key off the run, not its
        # components: a blog run records an empty component list, and the run's
        # `adapter_version` and completion are what matter. The LATEST blog run
        # decides, so a completed repair supersedes both the old capture and any
        # earlier interrupted attempt -- which is why a repaired archive must
        # stop offering a repair here rather than fall through to the manifest
        # heuristic below, where the old run's failed page-1 comment requests
        # live forever and would keep proposing a repair that already ran.
        def _run_key(run) -> tuple[str, str]:
            return (getattr(run, "started_at", "") or "", getattr(run, "run_id", "") or "")

        blog_runs = [run for run in runs if run.adapter_version in {"blogs-1", "blogs-2"}]
        if blog_runs:
            latest_blog = max(blog_runs, key=_run_key)
            if latest_blog.adapter_version == "blogs-1":
                return {
                    "repair_needed": True,
                    "repair_reason": "blog comments may be missing from this older capture",
                }
            if not latest_blog.completed_at:
                return {
                    "repair_needed": True,
                    "repair_reason": "the previous blog comment repair did not finish",
                }
            # The most recent blog run is a completed `blogs-2` capture/repair:
            # this archive is current. Do not consult the manifest heuristic.
            return {"repair_needed": False, "repair_reason": None}

        # No blog run metadata at all -- an archive from before run metadata
        # existed can still be recognized from the append-only request manifest.
        # This is deliberately conservative: uncertainty becomes an offered
        # repair, never a claim of completeness. (A repair writes a `blogs-2`
        # run, so a legacy archive that has been repaired is caught above and
        # never reaches here.)
        records = [json.loads(line) for line in source.read_lines("manifest.jsonl") if line.strip()]
        entry_seen = any(
            "/blogs/" in record.get("url", "") and "/entries/atom" in record.get("url", "")
            for record in records
        )
        comment_records = [
            record for record in records if "/entrycomments/" in record.get("url", "")
        ]
        page_zero_seen = any(
            parse_qs(urlsplit(record.get("url", "")).query).get("page") == ["0"]
            for record in comment_records
        )
        comment_failed = any(record.get("outcome") != "ok" for record in comment_records)
        if entry_seen and (not comment_records or comment_failed or not page_zero_seen):
            return {
                "repair_needed": True,
                "repair_reason": (
                    "blog comments may be missing or incomplete in this legacy capture"
                ),
            }
    except (ArchiveSourceError, OSError, ValueError):
        pass
    return {"repair_needed": False, "repair_reason": None}


def write_archive_summary(archive_dir: Path, model) -> None:
    """Persist list metadata once a run has produced its derived model."""
    from connections_export.archive.store import Archive  # noqa: PLC0415
    from connections_export.crawler.provenance import last_successful_run  # noqa: PLC0415

    captured_at = None
    try:
        run = last_successful_run(Archive.open(archive_dir))
        captured_at = run.started_at if run else None
    except Exception:  # noqa: BLE001 - a summary must never fail a run
        captured_at = None
    summary = model_summary(model, captured_at=captured_at)
    path = archive_dir / SUMMARY_FILENAME
    path.write_text(json.dumps(summary, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    _SUMMARY_CACHE.pop((str(archive_dir), archive_dir.stat().st_mtime), None)


def read_archive_summary(archive_dir: Path) -> dict | None:
    """The persisted sidecar, from a directory or from inside a zip."""
    try:
        return source_for(archive_dir).read_json(SUMMARY_FILENAME)
    except ArchiveSourceError:
        return None


def summarize_archive(archive_dir: Path) -> dict:
    """Return display metadata for one archive without changing it.

    The derived model is cached by archive modification time because the
    archive list is refreshed whenever the Overview is shown. A failed or
    partial archive is represented as data so the UI can explain it instead
    of showing an apparently valid but unusable entry.
    """
    persisted = read_archive_summary(archive_dir)
    if persisted is not None:
        return persisted
    try:
        key = (str(archive_dir), archive_dir.stat().st_mtime)
    except OSError:
        return {"status": "unavailable", "detail": "Archive is no longer available."}
    cached = _SUMMARY_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        model = derive(Archive.open(archive_dir))
    except (DeriveError, OSError, ValueError) as exc:
        result = {"status": "unavailable", "detail": str(exc) or "No browsable content."}
    else:
        result = model_summary(model)
    _SUMMARY_CACHE[key] = result
    return result


def archive_target_key(summary: dict | None) -> str | None:
    """A stable signature for *what* an archive captured, or `None` when that
    cannot be known.

    Directory names carry only `export-<host>-<stamp>-<suffix>` -- the
    deployment and when the run happened, never which community, forum, or
    wiki was captured. So repeated attempts at one target are recognised from
    the run's persisted summary instead: its base URL plus the titles of the
    groups and communities it holds.

    Item counts are deliberately excluded: a later attempt at the same target
    usually captured more, and including counts would make every attempt look
    like a different target. A summary that is missing or not `ok` yields
    `None` -- an archive whose content is unknown must never be grouped, and
    therefore never selected for deletion on our say-so.
    """
    if not summary or summary.get("status") != "ok":
        return None
    groups = sorted(
        f"{group.get('kind')}:{group.get('title')}" for group in summary.get("groups") or []
    )
    communities = sorted(
        f"community:{community.get('title')}" for community in summary.get("communities") or []
    )
    if not groups and not communities:
        return None  # nothing captured -- not a target anyone re-attempts
    return "|".join([summary.get("base_url") or "", *groups, *communities])


def superseded_names(infos: list[ArchiveInfo]) -> set[str]:
    """Every archive that a later attempt at the same target has replaced.

    The newest run for each target is always kept. Archives whose content is
    unknown (no usable summary) and demo archives are never included -- demo
    runs have their own bulk delete, and sweeping them in here would make
    "delete superseded" quietly remove the demo data too.
    """
    by_target: dict[str, list[ArchiveInfo]] = {}
    for info in infos:
        if info.is_demo or info.target_key is None:
            continue
        by_target.setdefault(info.target_key, []).append(info)
    superseded: set[str] = set()
    for group in by_target.values():
        if len(group) < 2:
            continue
        newest = max(group, key=lambda info: info.mtime)
        superseded.update(info.name for info in group if info.name != newest.name)
    return superseded


def is_demo_archive(name: str) -> bool:
    """Whether an archive of that name holds demo data.

    Read off the name, which is where the crawl wrote the answer: a demo run
    lands in `DEMO-FAKE-DATA-...`, and a run against the demo's synthetic
    deployment in `export-<demo host>-...`.

    Deliberately NOT "was the server started with --demo". That flag says the
    server has no deployment configured, which is a different question: a real
    URL dropped into a `serve --demo` console runs a real import against a real
    system, and the result is not demo data.
    """
    return name.startswith("DEMO-FAKE-DATA") or any(
        name.startswith(f"export-{host}-") for host in DEMO_SOURCE_HOSTS
    )


#: The folder-name endings the developer-format exports are written under
#: (`gui/routes/ingest.py`), beside the archive they came from -- which puts
#: them in the archives folder whenever the archive is there.
EXPORT_FOLDER_SUFFIXES = ("-hugo-content", "-jekyll-site", "-obsidian-vault", "-pdf")

#: What an archive or a package holds at its root, whatever its name.
_ARCHIVE_MARKERS = ("manifest.jsonl", "interchange.json")


def _is_export(child: Path) -> bool:
    """A Hugo content folder, Jekyll site or Obsidian vault this tool wrote.

    Recognised by the name the export gives it, and only when nothing in it
    makes it an archive: a folder that holds one stays an archive, whatever
    someone named it."""
    return child.name.endswith(EXPORT_FOLDER_SUFFIXES) and not any(
        (child / marker).exists() for marker in _ARCHIVE_MARKERS
    )


def _is_archive_candidate(child: Path) -> bool:
    """A direct child that could be an archive: a directory, or a `.zip`.

    A zip moves cleanly where a directory of thousands of small files does
    not, which is the whole reason it is readable at all -- so someone handed
    one has only to drop it here. Whether it really holds an archive is
    settled by reading it, not by its name -- except for the exports this tool
    writes beside the archives, which are not archives at all.
    """
    if child.is_dir():
        return not _is_export(child)
    return child.is_file() and child.suffix.lower() == ".zip"


def is_zip_archive(path: Path) -> bool:
    """A zipped archive: it opens and reads, and never takes a write."""
    return path.is_file() and path.suffix.lower() == ".zip"


def unzip_archive(zip_path: Path) -> Path:
    """Unpack a zipped archive into a new folder beside it, and return it.

    The folder is a normal, writable archive -- it can be updated, extended
    and repaired -- and the zip is left exactly as it is. Only the archive
    inside the zip is unpacked (at its root or under its one folder, as
    `ZipSource` finds it), into a name not yet taken, and nothing is written
    outside that folder whatever the zip's entries claim.
    """
    import shutil  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    from connections_export.archive.source import ZipSource  # noqa: PLC0415

    source = ZipSource(zip_path)
    prefix = source._resolve_prefix()
    stem = zip_path.stem
    target = zip_path.with_name(stem)
    counter = 2
    while target.exists():
        target = zip_path.with_name(f"{stem}-{counter}")
        counter += 1
    staging = zip_path.with_name(f".{target.name}.unzipping")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as handle:
            for info in handle.infolist():
                if not info.filename.startswith(prefix) or info.is_dir():
                    continue
                relative = Path(info.filename[len(prefix) :])
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    continue  # never outside the folder
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info) as member, destination.open("wb") as out:
                    shutil.copyfileobj(member, out)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def archive_base_url_of(path: Path) -> str | None:
    """The deployment an archive -- a folder or a zip -- was captured from."""
    from connections_export.crawler.provenance import FAKE_BASE_URLS  # noqa: PLC0415

    urls = [run.base_url for run in _runs(path) if run.base_url]
    real = [url for url in urls if url.rstrip("/") not in FAKE_BASE_URLS]
    return (real or urls or [None])[0]


def writable_archive(archive_dir: Path) -> bool:
    """Whether this archive can be written into. A zip cannot."""
    return not (archive_dir.is_file() and archive_dir.suffix.lower() == ".zip")


def list_archives(base: Path) -> list[ArchiveInfo]:
    """Every direct child of `base` that is an archive -- a directory or a
    `.zip` -- newest first. Demo runs (the fake-data prefix or the demo
    source host names) are flagged, not hidden."""
    if not base.is_dir():
        return []
    infos = []
    for child in base.iterdir():
        if not _is_archive_candidate(child):
            continue
        # Read only the persisted sidecar -- never derive. Deriving here would
        # make listing 1200 archives unusably slow. Read once: both the target
        # key and the display name come out of it.
        summary = read_archive_summary(child)
        infos.append(
            ArchiveInfo(
                name=child.name,
                path=child,
                is_demo=is_demo_archive(child.name),
                mtime=child.stat().st_mtime,
                target_key=archive_target_key(summary),
                display_name=_display_name_for(child, summary),
                item_count=_item_count(child),
                **archive_repair_status(child),
            )
        )
        issues, again = archive_issues(child)
        # A demo archive's content is invented; it has nothing to recover.
        infos[-1] = replace(
            infos[-1],
            issues=[] if infos[-1].is_demo else issues,
            captured_again_from=again,
            run_ids=frozenset(run.run_id for run in _runs(child)),
        )
    infos = with_zip_twins(with_recaptures(infos))
    # Name breaks the tie, because mtime alone does not: two archives written
    # in the same second -- a batch, or one run started right after another --
    # carry the same mtime, and `iterdir` order is then whatever the
    # filesystem feels like. The list would reorder itself between two
    # listings of an unchanged directory. Name is stable, and an archive name
    # carries its own timestamp, so it agrees with mtime rather than fighting
    # it.
    return sorted(infos, key=lambda i: (i.mtime, i.name), reverse=True)


def resolve_archive(base: Path, name: str) -> Path | None:
    """The archive ``base/name`` -- a directory or a `.zip` -- iff `name` is a
    plain direct child of `base` (no traversal, no absolute, no nesting, no
    symlink escape); otherwise ``None``, which the caller treats as 'not
    found'. Zips pass exactly the same guards as directories."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    candidate = base / name
    try:
        resolved = candidate.resolve()
        base_resolved = base.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved.parent != base_resolved or not _is_archive_candidate(resolved):
        return None
    return candidate
