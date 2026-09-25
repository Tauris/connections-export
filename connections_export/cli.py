"""The `connections-export` CLI: one command with subcommands (`crawl`, `serve`,
`capture`, `sanitize`) dispatched by `main` at the bottom of this file,
so the installed namespace stays clean. `crawl` and `serve` are real;
`capture`/`sanitize` are thin stubs that resolve+print config until their
capabilities land. Secrets are shown as `***`, never their value.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from connections_export import apps
from connections_export.archive.store import Archive
from connections_export.config import (
    Config,
    ConfigError,
    load_config,
    resolve_basic,
    resolve_token,
)
from connections_export.crawler import CrawlResult
from connections_export.crawler.events import Emit
from connections_export.gui import make_app
from connections_export.http import AuthError
from connections_export.http.auth import (
    AuthStrategy,
    BasicAuth,
    KerberosAuth,
    PasteTokenAuth,
    SspiAuth,
)
from connections_export.http.client import HttpClient


def _build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        epilog=(
            "Every setting above can also come from the environment "
            "(CONNECTIONS_EXPORT_BASE_URL and friends) or a connections-export.toml "
            "file, so a configured machine needs no flags at all. Precedence: "
            "these flags > environment > config file > defaults. Secrets are read "
            "only from the environment or a prompt, never from a file. "
            "No deployment to point at yet? Add --demo, or run "
            "`connections-export serve` for the console."
        ),
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="Path to a TOML config file (overrides discovery).",
    )
    parser.add_argument(
        "--base-url",
        dest="base_url",
        default=None,
        help="Your HCL Connections base URL, e.g. https://connections.example.corp.",
    )
    parser.add_argument(
        "--auth-mode",
        dest="auth_mode",
        default=None,
        choices=["sspi", "kerberos", "basic", "paste_token"],
        help="How to authenticate. sspi/kerberos use your OS session; basic and "
        "paste_token read credentials from the environment, never from a file.",
    )
    parser.add_argument(
        "--auth-root",
        dest="auth_root",
        default=None,
        choices=["basic", "basic/anonymous", "form", "oauth"],
        help="The URL prefix the deployment serves authenticated APIs under.",
    )
    parser.add_argument(
        "--source-version",
        dest="source_version",
        default=None,
        help="The deployment's Connections version, e.g. 8.0. Selects API shapes.",
    )
    parser.add_argument(
        "--delay",
        dest="min_interval",
        type=float,
        default=None,
        help="Seconds between requests to the deployment (default: 1). A large "
        "export is better run overnight at 3 than hurried through at midday -- "
        "it is thousands of requests either way, and a slower one is a tool "
        "nobody has to notice.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Where the archive is written (and read back from).",
    )
    parser.add_argument(
        "--author",
        dest="filter_author",
        default=None,
        help="Only expose content this user authored/participated in (name or "
        "user id) — each kept entity with its full chain. The archive stays "
        "complete; only the derived/served/exported model is filtered.",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="How to reach the deployment: a proxy URL, or `direct` to use none. Unset, "
        "decided the way a browser decides -- environment, then the system's PAC or "
        "auto-detection, then its proxy setting. `probe proxy` shows the result.",
    )
    parser.add_argument(
        "--search-userid",
        default=None,
        help="A user id the deployment's Search API knows. With --author and a "
        "community URL, the community's forums are chosen by asking Search "
        "which threads this person is in, instead of reading every topic and "
        "every reply to find out. Each chosen thread is still read in full and "
        "still passes --author. Omit it and the forums are read whole, which "
        "is slower and always correct.",
    )
    return parser


def _parse_with(
    prog: str,
    argv: Sequence[str] | None,
    env: Mapping[str, str] | None,
    add_args: Callable[[argparse.ArgumentParser], None] | None = None,
) -> tuple[Config, argparse.Namespace]:
    """Resolve config, letting a command contribute its own flags first.

    `serve` already grew `--demo`/`--host`/`--port` by rebuilding the shared
    parser by hand; this is the same thing without the copy, and it hands back
    the parsed namespace so the caller can read whatever it added.
    """
    parser = _build_parser(prog)
    if add_args is not None:
        add_args(parser)
    args = parser.parse_args(argv)
    cli_overrides = {
        "base_url": args.base_url,
        "auth_mode": args.auth_mode,
        "auth_root": args.auth_root,
        "source_version": args.source_version,
        "output_dir": args.output_dir,
        "filter_author": args.filter_author,
        "min_interval": args.min_interval,
        "proxy": getattr(args, "proxy", None),
        # Present only on the crawl parser; `getattr` keeps the other
        # subcommands, which reuse this helper, from having to declare them.
        "into": Path(args.into) if getattr(args, "into", None) else None,
        "since_override": getattr(args, "since", None),
        "recheck_comments": getattr(args, "recheck_comments", False) or None,
    }
    cli_overrides = {key: value for key, value in cli_overrides.items() if value is not None}
    return load_config(cli_overrides, config_path=args.config_path, env=env), args


def _resolve_config(prog: str, argv: Sequence[str] | None, env: Mapping[str, str] | None) -> Config:
    return _parse_with(prog, argv, env)[0]


def _run_demo_into(archive_dir, emit: Emit | None):
    """Drive the in-process fakeserver through the real pipeline into
    `archive_dir`. The synthetic deployment ships inside the package -- and so
    inside the frozen executable -- so this needs no network and no config."""
    from connections_export.gui.demo import run_demo  # noqa: PLC0415

    return run_demo(
        emit if emit is not None else (lambda _event: None),
        archive_dir=archive_dir,
        delay=0.0,
        sleep=lambda _seconds: None,
    )


def _print_resolved(config: Config, env: Mapping[str, str] | None) -> None:
    print(repr(config))
    token = resolve_token(env)
    basic = resolve_basic(env, allow_prompt=False)
    if token:
        print("token: ***")
    elif basic:
        print("basic credentials: ***")
    else:
        print("secrets: not set")


def _main(prog: str, argv: Sequence[str] | None, env: Mapping[str, str] | None) -> Config:
    config = _resolve_config(prog, argv, env)
    _print_resolved(config, env)
    return config


def _resolve_auth_strategy(config: Config, env: Mapping[str, str] | None) -> AuthStrategy | None:
    if config.auth_mode == "basic":
        credentials = resolve_basic(env, allow_prompt=False)
        if credentials is None:
            return None
        username, password = credentials
        # Bound to the deployment: the password is never sent to another host.
        return BasicAuth(username=username, password=password, base_url=config.base_url or "")
    if config.auth_mode == "paste_token":
        token = resolve_token(env)
        return PasteTokenAuth(ltpa_token=token, base_url=config.base_url or "") if token else None
    # The deployment's other hosts may receive the sign-in when it is
    # redirected there -- an organisation's login server on another domain.
    sign_in_hosts = tuple(config.hcl_hosts)
    if config.auth_mode == "kerberos":
        return KerberosAuth(base_url=config.require_base_url(), sign_in_hosts=sign_in_hosts)
    # config.auth_mode == "sspi", the default
    return SspiAuth(base_url=config.require_base_url(), sign_in_hosts=sign_in_hosts)


def _build_default_client(
    config: Config,
    env: Mapping[str, str] | None,
    *,
    min_interval: float | None = None,
    timeout: float | None = None,
) -> HttpClient:
    """A real `HttpClient` for a real deployment -- never exercised by
    offline tests, which always inject their own `client`.

    `AuthError` propagates: every command that builds a client turns it into a
    message. Authentication failing is an ordinary thing to get wrong -- a
    missing optional package, no domain to authenticate against, credentials
    not set -- and it should read as instructions, not as a crash.

    `min_interval` and `timeout` override the crawl's defaults for a client
    that is not crawling: a read-only lookup makes a handful of requests and
    has no business being paced like a walk over thousands of feeds, and may
    reasonably wait longer for each one. `Config` refuses a pacing below its
    floor, which is right for a crawl; this is the seam for the other case.
    """
    from connections_export.http.proxy import resolve_proxy  # noqa: PLC0415

    # Decided once, for the deployment, the way a browser would -- and
    # printed to the log when it is anything but direct-by-default, so a
    # request that goes somewhere unexpected is visible from the first line.
    decision = resolve_proxy(config.base_url or "https://localhost", explicit=config.proxy, env=env)
    if decision.source not in ("default", "loopback"):
        print(f"connections-export: proxy — {decision.detail} [{decision.source}]", flush=True)
    client = HttpClient(
        min_interval=config.min_interval if min_interval is None else min_interval,
        proxy=decision,
        **({"timeout": timeout} if timeout is not None else {}),
    )
    auth = _resolve_auth_strategy(config, env)
    if auth is not None:
        auth.prepare(client)
    return client


def _print_crawl_report(result: CrawlResult) -> None:
    print(f"run {result.run_id}: {'OK' if result.ok else 'ISSUES'}")
    print(f"  pages crawled: {result.report.pages_crawled}")
    print(f"  counts: {result.report.counts}")
    if result.report.failures_by_category:
        print(f"  failures: {result.report.failures_by_category}")
    if result.report.orphans:
        print(f"  orphans: {result.report.orphans}")
    if result.report.possibly_truncated:
        print(f"  possibly truncated: {result.report.possibly_truncated}")


#: How the console names a selected component, and therefore how a generated
#: command carries it: `wiki:<label>`, `forum:<uuid>`, `blog:<uuid>`,
#: `ideation_blog:<uuid>`. Mirroring the console's own vocabulary means the
#: command it shows is a serialization of its state, not a translation of it.
#: Every part of a target the CLI can capture, from the one place that
#: declares them. This was a hand-written tuple that omitted `rich_content`
#: while `--help` documented it and the console emitted it, so a copied
#: command was rejected.
COMPONENT_KINDS = apps.COMPONENT_KINDS


def _split_components(values: Sequence[str] | None) -> dict[str, list[str]]:
    """`["wiki:eng", "forum:abc"]` -> `{"wiki": ["eng"], "forum": ["abc"]}`."""
    grouped: dict[str, list[str]] = {kind: [] for kind in COMPONENT_KINDS}
    for value in values or []:
        kind, _, ident = value.partition(":")
        if kind not in grouped or not ident:
            raise ConfigError(
                f"unrecognised --component {value!r}: expected one of "
                + ", ".join(f"{kind}:<id>" for kind in COMPONENT_KINDS)
            )
        grouped[kind].append(ident)
    return grouped


def _targets_from_urls(urls: Sequence[str]) -> tuple[dict[str, list[str]], dict]:
    """Turn dropped-URL-equivalents into a component selection plus the
    identity they carry (base_url, auth_root, community).

    The console resolves a URL exactly this way, via the same parser, which is
    what makes a URL on the command line the equivalent of one dropped there.
    """
    # `parse_url`, not `parse_wiki_url`: the latter only knows wiki shapes.
    # This is the function `/api/identify` uses, which is what makes a URL here
    # mean the same thing as a URL dropped into the console.
    from connections_export.gui.wiki_url import parse_url  # noqa: PLC0415

    grouped: dict[str, list[str]] = {kind: [] for kind in COMPONENT_KINDS}
    identity: dict = {
        "base_url": None,
        "auth_root": None,
        "community_uuid": None,
        "community_title": None,
        "single": {},
    }
    for url in urls:
        parsed = parse_url(url)
        if not parsed.ok:
            raise ConfigError(f"could not identify {url!r}: {parsed.reason}")
        identity["base_url"] = identity["base_url"] or parsed.base_url
        identity["auth_root"] = identity["auth_root"] or parsed.auth_root
        identity["community_uuid"] = identity["community_uuid"] or parsed.community_uuid
        if parsed.app == "wiki" and parsed.wiki_label:
            grouped["wiki"].append(parsed.wiki_label)
            if parsed.scope == "single" and parsed.page_label:
                identity["single"].setdefault("pages", []).append(parsed.page_label)
        elif parsed.app == "blog" and parsed.blog_handle:
            grouped["blog"].append(parsed.blog_handle)
            if parsed.scope == "single" and parsed.entry_slug:
                identity["single"].setdefault("entries", []).append(parsed.entry_slug)
        elif parsed.app == "forum" and parsed.forum_uuid:
            grouped["forum"].append(parsed.forum_uuid)
            if parsed.scope == "single" and parsed.topic_id:
                identity["single"].setdefault("topics", []).append(parsed.topic_id)
        elif parsed.app == "community" or parsed.community_uuid:
            # Deliberately adds nothing. Files ARE addressed by the community
            # uuid, so a shortcut here is tempting -- but it would select them
            # for a community that has none, turning "this community is empty"
            # into a crawl that 404s. Discovery asks the library feed whether it
            # exists; that is its job.
            # A community is a target in its own right: it has a name, that
            # name decides the archive name, and it appears in the derived
            # model. What to capture inside it still comes from --component,
            # which is what the console fills in.
            continue
        else:
            raise ConfigError(f"{url!r} identifies no wiki, blog, forum or community to capture")
    return grouped, identity


def _add_crawl_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "url",
        nargs="*",
        help="What to capture, as a URL -- the same URL you would drop into the "
        "console. A wiki, blog or forum URL captures that container (or the "
        "single page/post/thread it names); a community URL identifies the "
        "community, whose parts you then choose with --component.",
    )
    parser.add_argument(
        "--component",
        action="append",
        dest="components",
        metavar="KIND:ID",
        help="A part of the target to capture: wiki:<label>, forum:<uuid>, "
        "blog:<uuid>, ideation_blog:<uuid>, files:<community-uuid> for a "
        "community's Files section, or rich_content:<community-uuid> for the "
        "pages on its Highlights area. Repeatable. The console shows the exact "
        "command for whatever you have selected -- copy it rather than "
        "assembling these by hand.",
    )
    parser.add_argument(
        "--into",
        metavar="ARCHIVE_DIR",
        help="Add to an existing archive instead of capturing a fresh one. "
        "Implies an update: the cutoff for date-filtered feeds is read from "
        "that archive's own provenance, so a scheduled re-run needs no date "
        "editing. Nothing already captured is removed, and a re-captured item "
        "replaces its own earlier copy rather than duplicating it.",
    )
    parser.add_argument(
        "--since",
        metavar="TIMESTAMP",
        help="Override the cutoff an update computes for itself (RFC 3339, "
        "e.g. 2026-08-01T00:00:00Z). Earlier re-checks more, never less.",
    )
    parser.add_argument(
        "--recheck-comments",
        action="store_true",
        help="During an update, ask for each item's comments and replies even "
        "when the item itself did not change. A date-filtered feed reports "
        "items that MOVED, and a new comment on an untouched post may not move "
        "it -- so without this an update can miss new discussion on old "
        "content. Costs about one extra request per item.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="With --into, re-read the components and author scope recorded in the "
        "archive, without a date cutoff, to recover content missed by an older release.",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=None,
        help="Cap pages/posts/topics per container (default: no cap). For a "
        "Files component it caps the DOCUMENTS DOWNLOADED; the library is "
        "still described in full, so a file that is not fetched shows as "
        "listed-but-missing rather than vanishing from the archive.",
    )
    parser.add_argument(
        "--target-label",
        default=None,
        help="Name for the archive directory (the console uses the community, "
        "wiki, blog or forum name).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Crawl the built-in synthetic deployment instead of a real one. "
        "Needs no URL, no credentials and no network -- everything it reads "
        "ships inside this program. Note it covers wikis, blogs and forums.",
    )


def _legacy_repair_scope(archive_dir: Path) -> tuple[dict[str, list[str]], str | None]:
    """Recover component IDs from pre-provenance manifest URLs."""
    selected = {
        kind: [] for kind in ("wiki", "blog", "ideation_blog", "forum", "files", "rich_content")
    }
    community_uuid = None
    manifest = archive_dir / "manifest.jsonl"
    if not manifest.is_file():
        return selected, community_uuid
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            url = json.loads(line).get("url", "")
        except (TypeError, ValueError):
            continue
        split = urlsplit(url)
        query = parse_qs(split.query)
        community_uuid = community_uuid or (query.get("communityUuid") or [None])[0]
        match = re.search(r"/blogs/roller-ui/rendering/feed/([^/]+)/entries/atom", split.path)
        if match and match.group(1) not in selected["blog"]:
            selected["blog"].append(match.group(1))
        match = re.search(r"/blogs/([^/]+)/feed/entrycomments/", split.path)
        if match and match.group(1) not in selected["blog"]:
            selected["blog"].append(match.group(1))
        forum_uuid = (query.get("forumUuid") or [None])[0]
        if forum_uuid and forum_uuid not in selected["forum"]:
            selected["forum"].append(forum_uuid)
        wiki = re.search(r"/api/wiki/([^/]+)/feed", split.path)
        if wiki and wiki.group(1) not in selected["wiki"]:
            selected["wiki"].append(wiki.group(1))
        files = re.search(r"/community/([^/]+)/", split.path)
        if "/files/" in split.path and files and files.group(1) not in selected["files"]:
            selected["files"].append(files.group(1))
    return selected, community_uuid


def crawl_main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    client: HttpClient | None = None,
    archive: Archive | None = None,
    emit: Emit | None = None,
    stop_event: Any = None,
) -> int:
    """Run a real crawl (thin; testable with injected
    deps). `client`/`archive`/`emit` are injected by tests (and could
    be by any other caller); production use leaves them unset and gets
    a real `HttpClient` and an `Archive` rooted at `config.output_dir`.

    Scope: **wikis, all of them**. `crawl` can be scoped (`wiki_labels`,
    `max_pages`) and `crawl_blogs`/`crawl_forums` exist, but nothing here
    reaches them -- the console is what drives a scoped or multi-app capture.
    Anything claiming otherwise in the docs is wrong, not this.

    Returns a process exit code: 0 if the run completed with no
    failure or warning, nonzero otherwise (Nonzero exit on
    any failure or warning).
    """
    config, args = _parse_with("connections-export crawl", argv, env, _add_crawl_args)
    # `--into` says "add to this archive": the run becomes an update, writes
    # where that archive lives, and takes its cutoff from that archive's own
    # provenance -- so a scheduled re-run stays correct with no date in the
    # script.
    from connections_export.crawler.session import resolve_update_plan  # noqa: PLC0415

    plan = resolve_update_plan(config)
    config = config.model_copy(update={"fetch": plan.fetch, "output_dir": plan.output_dir})
    if args.demo:
        # Naming both a URL and the demo is a contradiction, and one of them
        # would have to be ignored silently. Which is exactly the failure this
        # switch is not allowed to have: a flag that overrides the address in
        # front of it and says nothing.
        if args.url:
            print(
                "connections-export crawl: --demo captures the built-in synthetic "
                "deployment, so it cannot also capture a URL.\n"
                "  Drop the --demo to capture the URL, or the URL to capture the demo.",
                file=sys.stderr,
            )
            return 2
        result = _run_demo_into(config.output_dir, emit)
        _print_crawl_report(result.crawl_result)
        print(f"  archive: {result.archive_dir}")
        return 0

    if args.repair:
        if config.into is None or args.url or args.components:
            print(
                "connections-export crawl: --repair requires --into and cannot be combined "
                "with URLs or --component.",
                file=sys.stderr,
            )
            return 2
        from connections_export.crawler.provenance import (  # noqa: PLC0415
            archive_base_url,
            components_captured,
            last_successful_run,
        )

        target = Archive.open(config.into)
        source_run = last_successful_run(target)
        captured = components_captured(target)
        base_url = archive_base_url(target)
        summary: dict = {}
        if target.root.is_dir():
            summary_path = target.root / "archive-summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    summary = {}
        legacy_selected, legacy_community = _legacy_repair_scope(target.root)
        if not captured:
            captured = [
                {"kind": kind, "id": ident, "action": "capture"}
                for kind, ids in legacy_selected.items()
                for ident in ids
            ]
        # This repair targets the known blog-comment pagination defect. Do not
        # re-run unrelated legacy forum/wiki components: one stale 404 there
        # would mark the whole command as failed and hide comments recovered
        # successfully from the blog.
        captured = [
            component for component in captured if component["kind"] in {"blog", "ideation_blog"}
        ]
        if not captured:
            for group in summary.get("groups", []):
                if group.get("kind") and group.get("id"):
                    captured.append({"kind": group["kind"], "id": group["id"], "action": "capture"})
        base_url = base_url or summary.get("base_url")
        if not source_run and not legacy_community:
            legacy_community = (summary.get("communities") or [{}])[0].get("id")
        if not captured or not base_url:
            print(
                "connections-export crawl: cannot repair this archive; its completed "
                "run provenance does not identify a source and captured components.",
                file=sys.stderr,
            )
            return 2
        selected = {
            kind: [] for kind in ("wiki", "blog", "ideation_blog", "forum", "files", "rich_content")
        }
        for component in captured:
            if component["action"] != "skip" and component["id"]:
                selected[component["kind"]].append(component["id"])
        from_urls = {}
        identity = {
            "base_url": base_url,
            "auth_root": None,
            "community_uuid": source_run.community_uuid if source_run else legacy_community,
            "community_title": source_run.community_title if source_run else None,
            "single": {},
        }
        config = config.model_copy(
            update={
                "base_url": base_url,
                "fetch": "update",
                "recheck_comments": True,
                "filter_author": source_run.author_filter if source_run else None,
            }
        )
        plan = plan.__class__(fetch="update", output_dir=plan.output_dir, since=None)
        print(
            "connections-export crawl: repairing "
            + ", ".join(f"{len(ids)} {kind}" for kind, ids in selected.items() if ids)
            + (
                f" for author {source_run.author_filter}"
                if source_run and source_run.author_filter
                else ""
            )
        )
    else:
        try:
            selected = _split_components(args.components)
            from_urls, identity = _targets_from_urls(args.url)
        except ConfigError as error:
            print(f"connections-export crawl: {error}", file=sys.stderr)
            return 2
    for kind, idents in from_urls.items():
        for ident in idents:
            if ident not in selected[kind]:
                selected[kind].append(ident)

    if not any(selected.values()) and not identity["community_uuid"]:
        print(
            "connections-export crawl: nothing to capture.\n"
            "  Give the URL of a wiki, blog or forum -- the same URL you would drop\n"
            "  into the console -- or name parts with --component wiki:<label> etc.\n"
            "  For a community, pass its URL and the --component list the console\n"
            "  shows for your selection. `connections-export serve` is where you\n"
            "  choose visually; it prints the matching command to copy.\n"
            "  To see it all work with no deployment: --demo.",
            file=sys.stderr,
        )
        return 2

    if identity["base_url"]:
        config = config.model_copy(update={"base_url": identity["base_url"]})
    if identity["auth_root"]:
        config = config.model_copy(update={"auth_root": identity["auth_root"]})
    try:
        # `--component` without a URL cannot supply the deployment address, and
        # a missing one is a thing to say, not a stack trace.
        config.require_base_url()
    except ConfigError as error:
        print(f"connections-export crawl: {error}", file=sys.stderr)
        return 2

    if archive is None:
        archive = Archive.open(config.output_dir)
    if client is None:
        try:
            client = _build_default_client(config, env)
        except AuthError as error:
            print(f"connections-export crawl: {error}", file=sys.stderr)
            return 3

    if identity["community_uuid"] and not any(selected.values()):
        # A community URL on its own: work out what it holds, the same way the
        # console does -- same function, so the two cannot disagree about what
        # a community contains.
        discovered = _discover_community(config, client, identity["community_uuid"])
        for component in discovered.get("components", []):
            kind, ident = component.get("kind"), component.get("id")
            if kind in selected and ident and ident not in selected[kind]:
                selected[kind].append(ident)
        if discovered.get("community"):
            identity["community_title"] = discovered["community"]
            if not args.target_label:
                args.target_label = discovered["community"]
        if not any(selected.values()):
            print(
                "connections-export crawl: that community reports no wiki, blog "
                "or forum to capture.\n"
                "  If you expected some, check the URL and that you can reach "
                "them signed in as this user.",
                file=sys.stderr,
            )
            return 1
        print(
            "connections-export crawl: community holds "
            + ", ".join(f"{len(ids)} {kind}" for kind, ids in selected.items() if ids)
        )

    return _run_selected_crawls(
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        selected=selected,
        identity=identity,
        max_entries=args.max_entries,
        author=config.filter_author,
        search_userid=(args.search_userid or "").strip() or None,
        # `plan.since` is the whole point of `--into`: the cutoff read from the
        # target archive's own provenance. It was computed above and never
        # passed, so every `--into` run re-crawled everything unfiltered while
        # reporting success. Every test asserted against the plan object, which
        # is how the feature stayed inert and green.
        since=plan.since,
        repair_comments_only=args.repair,
        stop_event=stop_event,
    )


def _discover_community(config: Config, client, community_uuid: str) -> dict:
    """What a community holds, via the shared discovery the console also uses."""
    from connections_export.crawler.community import discover_components  # noqa: PLC0415

    base_url = config.require_base_url()

    def fetch(url: str) -> bytes | None:
        try:
            response = client.get(url)
        except Exception:  # noqa: BLE001 - a failed probe is "not found", not fatal
            return None
        # `client` is the tool's HttpClient, whose reply is a `Fetched` with
        # `.status` -- NOT a raw httpx response with `.status_code`. Reading
        # the wrong name made `getattr(..., None)` return None for every feed,
        # so every feed looked unreadable and discovery reported "no wiki,
        # blog or forum" for every community over a perfectly good connection.
        # The console never hit this because its lookup reads `.status`.
        status = getattr(response, "status", None)
        content = getattr(response, "content", None)
        return content if status is not None and status < 400 else None

    def redirect_location(url: str) -> str | None:
        try:
            response = client.get(url)
            headers = getattr(response, "headers", None) or {}
            return headers.get("location", "")
        except Exception:  # noqa: BLE001
            return None

    return discover_components(
        community_uuid=community_uuid,
        base_url=base_url,
        fetch=fetch,
        redirect_location=redirect_location,
    )


def _run_selected_crawls(
    *,
    config,
    client,
    archive,
    emit,
    selected,
    identity,
    max_entries,
    author,
    since=None,
    search_userid=None,
    repair_comments_only=False,
    stop_event=None,
) -> int:
    """Run one crawl per app for whatever was selected, into one archive.

    The dispatch itself lives in `crawler.dispatch.run_selection`, shared with
    the console -- it was written out by hand here and twice more in
    `gui/app.py`, with three different coverages. This one had no rich-content
    branch, so a component the console offered did nothing when the copied
    command was run.
    """
    from connections_export.crawler.dispatch import run_selection  # noqa: PLC0415

    results = run_selection(
        selected,
        config=config,
        client=client,
        archive=archive,
        emit=emit,
        max_entries=max_entries,
        author=author,
        since=since,
        single=identity.get("single") or {},
        # The console has always passed these and this path never did, so a
        # community captured from the CLI produced wikis that did not record
        # which community they were for -- and, now, forums that could not be
        # selected by the person query. Same selection, same run, whichever
        # front end asked.
        community_uuid=identity.get("community_uuid") or None,
        community_title=identity.get("community_title") or None,
        search_userid=search_userid,
        repair_comments_only=repair_comments_only,
        stop_event=stop_event,
    )
    for result in results:
        _print_crawl_report(result)
    return 0 if all(result.ok for result in results) else 1


def _content_source(path: str, *, author: str | None):
    """A `ModelSource` for whatever `path` is: a package (`interchange.json`
    inside), an archive directory (`manifest.jsonl` inside) or a zipped
    archive. Returns `(source, kind)`, or raises `ValueError` saying what
    was found and what would have been accepted.

    The distinction is not the user's problem. The console writes archives;
    a package is a separate, deliberate step; and a person who has one of
    them should not need to know which flag the other one wanted, or meet
    a missing-file traceback naming `interchange.json` for guessing wrong.
    A path is a path; which kind it is, this can tell.
    """
    from connections_export.gui.model_source import ModelSource  # noqa: PLC0415

    target = Path(path)
    if target.is_file() and target.suffix.lower() == ".zip":
        return ModelSource.from_archive(target, author=author), "zipped archive"
    if not target.is_dir():
        raise ValueError(f"{target} is not a directory or a .zip archive")
    if (target / "interchange.json").is_file():
        return ModelSource.from_package(target, author=author), "package"
    if (target / "manifest.jsonl").is_file():
        return ModelSource.from_archive(target, author=author), "archive"
    raise ValueError(
        f"{target} is neither a package (no interchange.json) nor an archive "
        "(no manifest.jsonl). Point this at the directory a capture wrote -- "
        "the one holding manifest.jsonl and blobs/ -- or at a .zip of one."
    )


def ingest_main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """`connections-export ingest --format obsidian --archive DIR --output VAULT`:
    a reference ingester that reconstructs captured content into a target
    (docs/reference/interchange-format.md §7). The built-in formats are
    `obsidian`, `jekyll` and `hugo`; all use `markdownify`, a base dependency,
    so a plain install runs them. `--html` chooses how page bodies are written
    (`ingest._bodies`).

    Takes what a capture actually produces -- an archive directory, or a zip
    of one -- as readily as a written package. `--package` and `--archive`
    are both accepted and both auto-detected: either flag with either kind
    of directory works, and says which it found.
    """
    parser = argparse.ArgumentParser(prog="connections-export ingest")
    parser.add_argument("--format", choices=["obsidian", "jekyll", "hugo"], default="obsidian")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--archive",
        action="append",
        help="An archive a capture wrote. Repeat for a combined Hugo site.",
    )
    source.add_argument(
        "--package", action="append", help="A written interchange package directory."
    )
    parser.add_argument("--output", required=True, help="Target vault/site directory to write.")
    parser.add_argument(
        "--site", action="store_true", help="Add a small built-in theme to a Hugo export."
    )
    parser.add_argument(
        "--html",
        dest="html_mode",
        choices=["markdown", "mixed", "html", "raw"],
        default=None,
        help=(
            "How page content is written. markdown: everything converted to Markdown. "
            "mixed: Markdown where that loses nothing, cleaned HTML where it would. "
            "html: cleaned HTML (scripts and event handlers removed). raw: the HTML "
            "exactly as captured, NOT cleaned -- whatever it contains is then your "
            "responsibility. Default: markdown for obsidian, mixed for jekyll and hugo."
        ),
    )
    parser.add_argument(
        "--author",
        dest="filter_author",
        default=None,
        help="Only ingest content this user authored/participated in (name or user id).",
    )
    args = parser.parse_args(argv)
    output = args.output
    if args.site and args.format != "hugo":
        parser.error("--site is only supported with --format hugo")

    from connections_export.archive.source import ArchiveSourceError  # noqa: PLC0415
    from connections_export.derive import DeriveError  # noqa: PLC0415
    from connections_export.ingest import from_source_for_format  # noqa: PLC0415

    try:
        source_paths = args.archive or args.package
        sources = [_content_source(path, author=args.filter_author)[0] for path in source_paths]
        theme_source_url = None
        kind = "archive" if args.archive else "package"
        if len(sources) == 1:
            stats = from_source_for_format(
                sources[0], output, args.format, html_mode=args.html_mode
            )
            if args.format == "hugo":
                theme_source_url = sources[0].get_model().base_url
        elif args.format != "hugo":
            raise ValueError("multiple inputs are only supported with --format hugo")
        else:
            from connections_export.ingest.hugo import write_hugo_content  # noqa: PLC0415
            models = [source.get_model() for source in sources]
            merged = models[0].model_copy(update={
                field: sum((getattr(model, field) for model in models), [])
                for field in (
                    "wikis", "blogs", "forums", "file_libraries", "rich_content", "communities"
                )
            })
            def blob_reader(blob_hash):
                for source in sources:
                    result = source.get_blob(blob_hash)
                    if result is not None:
                        return result[0]
                return None
            theme_source_url = merged.base_url
            stats = write_hugo_content(
                merged, blob_reader, output, html_mode=args.html_mode or "mixed"
            )
        if args.site:
            from connections_export.ingest.hugo import write_hugo_theme  # noqa: PLC0415

            write_hugo_theme(output, theme_source_url)
    except (ValueError, DeriveError, ArchiveSourceError) as error:
        print(f"connections-export ingest: {error}", file=sys.stderr)
        return 1
    if args.format == "jekyll":
        print(
            f"connections-export ingest: from the {kind}, {stats.posts} post(s), "
            f"{stats.assets_written} asset(s) → {output}"
        )
    elif args.format == "hugo":
        print(
            f"connections-export ingest: from the {kind}, {stats.pages} page(s) in "
            f"{stats.wikis} wiki(s), {stats.posts} post(s) in {stats.blogs} blog(s), "
            f"{stats.topics} topic(s) in {stats.forums} forum(s), "
            f"{stats.files} file(s), {stats.highlight_pages} Highlights page(s), "
            f"{stats.assets_written} file(s) copied → {output}"
        )
    else:
        print(
            f"connections-export ingest: from the {kind}, {stats.pages} page(s) in "
            f"{stats.wikis} wiki(s), {stats.posts} post(s) in {stats.blogs} blog(s), "
            f"{stats.topics} topic(s) in {stats.forums} forum(s), "
            f"{stats.assets_written} attachment(s) → {args.output}"
        )
    if stats.html_mode != "markdown":
        print(
            f"  page content ({stats.html_mode}): {stats.markdown_blocks} block(s) as "
            f"Markdown, {stats.html_blocks} kept as HTML."
        )
    if stats.html_mode == "raw":
        print("  raw HTML was NOT cleaned: whoever publishes it is responsible for its content.")
    if stats.assets_missing:
        print(f"  {stats.assets_missing} referenced asset(s) not captured (shown as visible gaps).")
    return 0


def package_main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """`connections-export package --archive DIR --output DIR`: write the
    portable interchange package for a capture -- `interchange.json`, its
    `blobs/`, the capability manifest, and the format's own reference
    document -- so it can be handed to any ingester, including one written
    by someone who has never seen this tool.

    The manual has always said every capture produces a package. Until this
    command, nothing did: the package writer existed and no command called
    it, and the reader, the PDF and the ingester all worked from the archive
    directly. This makes the sentence true.
    """
    parser = argparse.ArgumentParser(prog="connections-export package")
    parser.add_argument(
        "--archive",
        required=True,
        help="An archive a capture wrote (the directory holding manifest.jsonl), or a .zip of one.",
    )
    parser.add_argument("--output", required=True, help="Directory to write the package into.")
    parser.add_argument(
        "--author",
        dest="filter_author",
        default=None,
        help="Only include content this user authored/participated in (name or user id).",
    )
    args = parser.parse_args(argv)

    import datetime as _dt  # noqa: PLC0415

    from connections_export.archive.source import (  # noqa: PLC0415
        ArchiveSourceError,
        ReadableArchive,
    )
    from connections_export.derive import DeriveError  # noqa: PLC0415
    from connections_export.derive.traverse import iter_all_assets  # noqa: PLC0415
    from connections_export.interchange.package import write_package  # noqa: PLC0415

    try:
        source, kind = _content_source(args.archive, author=args.filter_author)
        if kind == "package":
            raise ValueError(f"{args.archive} is already a package")
        if kind == "zipped archive":
            # The package writer copies blobs out of an archive DIRECTORY.
            # Saying so beats a traceback from inside a zip reader.
            raise ValueError("unzip the archive first; a package is written from a directory")
        model = source.get_model()
        if model is None:
            raise ValueError("nothing to package: the archive holds no derivable content")
        # Wall clock, stated once here and passed in: the writer takes it as
        # an argument so a test around it can be deterministic.
        generated_at = _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()
        write_package(
            model, ReadableArchive.open(args.archive), Path(args.output), generated_at=generated_at
        )
    except (ValueError, DeriveError, ArchiveSourceError) as error:
        print(f"connections-export package: {error}", file=sys.stderr)
        return 1
    blobs = sum(1 for asset in iter_all_assets(model) if asset.present and asset.blob_hash)
    print(
        f"connections-export package: {len(model.wikis)} wiki(s), {len(model.blogs)} blog(s), "
        f"{len(model.forums)} forum(s), {blobs} blob(s) → {args.output}"
    )
    print("  interchange.json, manifest.json, provenance.json, blobs/, and INTERCHANGE.md")
    return 0


def _load_model_source(args: argparse.Namespace, env: Mapping[str, str] | None):
    """A `ModelSource` for the `pdf` command's chosen input: an explicit
    `--package` or `--archive` dir, else the configured `output_dir`
    (treated as an archive) -- so `connections-export pdf` after a crawl "just
    works" with no extra flags."""
    from connections_export.gui.model_source import ModelSource  # noqa: PLC0415

    author = getattr(args, "filter_author", None)
    if args.package is not None:
        return ModelSource.from_package(args.package, author=author)
    if args.archive is not None:
        return ModelSource.from_archive(args.archive, author=author)
    config = load_config({}, config_path=args.config_path, env=env)
    return ModelSource.from_archive(config.output_dir, author=author or config.filter_author)


def _archive_model(args: argparse.Namespace, env: Mapping[str, str] | None):
    """The lossless path: a derived model + blob lookup from a package,
    an archive, or the configured output_dir. `(None, None)` when nothing
    is derivable."""
    from connections_export.archive.source import ArchiveSourceError  # noqa: PLC0415
    from connections_export.derive import DeriveError  # noqa: PLC0415

    try:
        source = _load_model_source(args, env)
        model = source.get_model()
    except (DeriveError, ArchiveSourceError):
        # "this is not an archive" and "this archive holds nothing yet" are
        # different failures that mean the same thing here: no model.
        return None, None
    if model is None:
        return None, None

    def blob_bytes(digest: str) -> bytes | None:
        result = source.get_blob(digest)
        return result[0] if result is not None else None

    return model, blob_bytes


def _quick_model(args: argparse.Namespace, env: Mapping[str, str] | None, client):
    """The no-archive path (`--quick`): fetch the content list directly
    into a model. `client` is injected by tests; production builds a real
    one (whose auth is still a first-contact stub -- caught here so the
    command reports the gap instead of crashing). Blobs aren't resolved
    (nothing is archived), so the blob lookup is empty."""
    config = load_config(
        {"base_url": args.base_url, "auth_mode": args.auth_mode},
        config_path=args.config_path,
        env=env,
    )
    if not config.base_url:
        print(
            "connections-export pdf --quick: no base_url "
            "(set --base-url or CONNECTIONS_EXPORT_BASE_URL)",
            file=__import__("sys").stderr,
        )
        return None, None
    if client is None:
        try:
            client = _build_default_client(config, env)
        except Exception as exc:  # sspi/kerberos are first-contact stubs
            print(
                f"connections-export pdf --quick: auth not available yet ({exc})",
                file=__import__("sys").stderr,
            )
            return None, None

    from connections_export.quick import quick_blog_model  # noqa: PLC0415

    model = quick_blog_model(
        config, client, homepage=args.blogs_homepage, with_comments=args.with_comments
    )
    return model, (lambda _digest: None)


def pdf_main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    render: Callable[..., bytes] | None = None,
    client: HttpClient | None = None,
) -> int:
    """`connections-export pdf`: render a reconstructed export to a PDF. Two ways
    to get the model:

    - **from an archive/package** (default): a `--package`, an `--archive`,
      or the configured `output_dir` -- the lossless, archive-first path.
    - **`--quick`** (no archive): fetch the content's list directly and
      render it -- so a browser PDF of a blog needs only the list of
      entries, not a full import (the import is not a gatekeeper). A
      *preview/export*, not a recoverable capture (no blobs/provenance).
      `--app blog` (+ `--blogs-homepage`, `--with-comments`) for now.

    `render`/`client` are injected by tests (no real browser, no real
    socket); production leaves them unset and gets Chromium + a real
    `HttpClient`. Returns nonzero with a clear message when no model is
    available or no usable browser is present.
    """
    parser = argparse.ArgumentParser(prog="connections-export pdf")
    parser.add_argument("--config", dest="config_path", default=None)
    parser.add_argument(
        "--css",
        dest="css_path",
        default=None,
        help="A stylesheet appended after everything else, including the "
        "captured pages' own CSS. `connections-export style --dump` writes the "
        "default stylesheet out to start from.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Render the built-in synthetic deployment. Needs no archive, no "
        "base_url and no network -- the demo data ships inside this program. "
        "The one external requirement is a Chromium-based browser (Edge, "
        "Chrome, or `playwright install chromium`).",
    )
    parser.add_argument("--package", default=None, help="A written interchange package dir.")
    parser.add_argument(
        "--archive",
        default=None,
        help="A raw archive -- a directory or a .zip (derived on the fly).",
    )
    parser.add_argument("--output", default="wiki-export.pdf", help="Output PDF path.")
    parser.add_argument(
        "--author",
        dest="filter_author",
        default=None,
        help="Only render content this user authored/participated in (name or user id).",
    )
    parser.add_argument(
        "--fidelity",
        choices=["portable", "browser"],
        default="portable",
        help="portable = our print HTML (Path A); browser = per-page browser fidelity (Path B).",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Fetch the content list directly and render it -- no archive/import.",
    )
    parser.add_argument("--app", choices=["blog"], default="blog", help="--quick: which app.")
    parser.add_argument("--base-url", dest="base_url", default=None)
    parser.add_argument("--auth-mode", dest="auth_mode", default=None)
    parser.add_argument("--blogs-homepage", default="homepage")
    parser.add_argument(
        "--with-comments", action="store_true", help="--quick: also fetch comments."
    )
    parser.add_argument(
        "--no-external-images",
        dest="external_images",
        action="store_false",
        help="Leave images from other websites out of the PDF (their addresses stay in the "
        'text). By default they are fetched now, embedded, marked "External image E1..." '
        "and listed with their original addresses on an External content page; whether "
        "you may reproduce them is for you to judge.",
    )
    args = parser.parse_args(argv)

    if args.demo and not (args.package or args.archive):
        # Crawl the synthetic deployment into a scratch archive, then take the
        # ordinary archive path over it -- same derive, same blob resolution,
        # no second rendering path to keep in step with the real one.
        import tempfile  # noqa: PLC0415

        args.archive = tempfile.mkdtemp(prefix="connections-export-demo-")
        _run_demo_into(args.archive, None)

    if args.quick:
        model, blob_bytes = _quick_model(args, env, client)
    else:
        model, blob_bytes = _archive_model(args, env)
    if model is None:
        print("connections-export pdf: no model available to render", file=__import__("sys").stderr)
        return 1

    if render is None:
        render = _default_pdf_render(args.fidelity)
        if render is None:
            print(
                "connections-export pdf: no usable browser found. Install Microsoft Edge or "
                "Google Chrome, or run `playwright install chromium`, and retry.",
                file=__import__("sys").stderr,
            )
            return 2

    from pathlib import Path as _Path  # noqa: PLC0415

    style_kwargs = _pdf_style_kwargs(args, env)
    if style_kwargs is None:
        return 2
    from connections_export.pdf.external import prepare_external_images  # noqa: PLC0415

    render_kwargs = {
        **style_kwargs,
        "external_images": prepare_external_images(model, include=args.external_images),
    }
    try:
        # A renderer (browser fidelity, an injected one) need not accept
        # every option; it gets the ones it does.
        pdf_bytes = render(model, blob_bytes, **_accepted_kwargs(render, render_kwargs))
    except ValueError as error:  # an unknown token name
        print(f"connections-export pdf: {error}", file=sys.stderr)
        return 2
    _Path(args.output).write_bytes(pdf_bytes)
    print(
        f"connections-export pdf: wrote {args.output} "
        f"({len(pdf_bytes)} bytes, {args.fidelity} fidelity)"
    )
    return 0


def _accepted_kwargs(func: Callable[..., bytes], kwargs: dict) -> dict:
    """The subset of `kwargs` that `func` takes (all of them if it takes **kwargs)."""
    import inspect  # noqa: PLC0415

    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return kwargs
    return {name: value for name, value in kwargs.items() if name in parameters}


def _pdf_style_kwargs(args: argparse.Namespace, env: Mapping[str, str] | None) -> dict | None:
    """`style_overrides`/`extra_css` for `render_pdf`, or None after reporting.

    Config supplies the durable choice; `--css` overrides per run. Both are
    read here rather than inside the renderer so a missing file is reported as
    a command error, before a browser is launched.
    """
    from connections_export.pdf.marks import resolve as resolve_marks  # noqa: PLC0415

    config = load_config({}, config_path=getattr(args, "config_path", None), env=env)
    kwargs: dict = {}
    if config.pdf_style:
        kwargs["style_overrides"] = dict(config.pdf_style)
    if config.pdf_marks:
        kwargs["marks"] = resolve_marks(dict(config.pdf_marks))
    kwargs["small_image_px"] = config.pdf_small_image_px
    css_path = getattr(args, "css_path", None) or config.pdf_css
    if css_path:
        path = Path(css_path)
        if not path.is_file():
            print(f"connections-export pdf: no such stylesheet: {path}", file=sys.stderr)
            return None
        kwargs["extra_css"] = path.read_text(encoding="utf-8")
    return kwargs


def _default_pdf_render(fidelity: str) -> Callable[..., bytes] | None:
    """The real PDF renderer for `fidelity`, or `None` when no usable
    Chromium is present. Imported lazily so the browser probe (and the
    heavy playwright import) only run when a PDF is actually requested."""
    from connections_export.pdf import CHROMIUM_AVAILABLE  # noqa: PLC0415

    if not CHROMIUM_AVAILABLE:
        return None
    if fidelity == "browser":
        from connections_export.pdf import render_pdf_browser  # noqa: PLC0415

        return render_pdf_browser
    from connections_export.pdf import render_pdf  # noqa: PLC0415

    return render_pdf


def serve_main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., Any] | None = None,
) -> int:
    """`hcl-serve [--demo/--no-demo] [--host] [--port]`: builds a
    `Config`, an app via `make_app(demo=...)`, and launches uvicorn on
    it.

    `--demo` defaults to on when no real `base_url` is configured
    (`--demo` (default on when no real `base_url` is
    configured)) -- attaching to a real, non-demo crawl needs working
    auth first. The seam exists via `make_app(demo=False)`, but demo is the
    tested, always-available path today.

    `run` is injected by tests so no real socket is ever bound
    (production leaves it unset and gets `uvicorn.run`).
    """
    parser = _build_parser("connections-export serve")
    # Accepted, and doing nothing, so an existing command or script keeps
    # working. The demo is a deployment at its own address, reached by
    # reading that address; there is no mode for a switch to set. A switch
    # that decided which deployment every later request read, whatever URL
    # was in front of it, could only disagree with the URL -- and would
    # win.
    parser.add_argument(
        "--demo", dest="demo", action="store_true", default=None, help=argparse.SUPPRESS
    )
    parser.add_argument("--no-demo", dest="demo", action="store_false", help=argparse.SUPPRESS)
    parser.add_argument("--host", dest="host", default="127.0.0.1")
    parser.add_argument("--port", dest="port", type=int, default=8000)
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the console in a browser once it is listening. On by default "
        "for a bare `connections-export`; off here, because `serve` is also what "
        "runs on a machine nobody is sitting at.",
    )
    args = parser.parse_args(argv)

    cli_overrides = {
        "base_url": args.base_url,
        "auth_mode": args.auth_mode,
        "auth_root": args.auth_root,
        "source_version": args.source_version,
        "output_dir": args.output_dir,
        "filter_author": args.filter_author,
    }
    config = load_config(cli_overrides, config_path=args.config_path, env=env)

    host, port = args.host, args.port
    # Pass the bound host so LocalGuardMiddleware still admits requests
    # addressed to it (the localhost defaults are already allowlisted).
    app = make_app(bound_host=host, author_filter=config.filter_author)
    if run is None:
        # Real launch path only: if the requested port is busy, pick the
        # next free one so a common conflict (e.g. another dev server on
        # 8000) doesn't crash the demo, and print where to open it. When
        # `run` is injected (tests/callers), pass the requested port as-is.
        port = _free_port(host, port)
        print(f"connections-export: serving the console at http://{host}:{port}", flush=True)
        import uvicorn  # noqa: PLC0415

        run = uvicorn.run
    _warn_if_exposed(host, port, "connections-export serve")
    if args.open_browser:
        # The port actually BOUND, not the one asked for: a busy 8000 moves us
        # elsewhere, and opening the requested one would point a browser at
        # somebody else's server or at nothing.
        _open_browser_when_listening(host, port)
    run(app, host=host, port=port, timeout_graceful_shutdown=1)
    return 0


def _is_loopback_host(host: str) -> bool:
    """Does binding `host` keep the console on this machine?

    Only a loopback address or `localhost` does. A wildcard (`0.0.0.0`,
    `::`, empty), a LAN address or any other name may be reachable from
    elsewhere -- and link-local, which `http.proxy.is_loopback` treats as
    this machine for routing, is very much on the network for listening.
    """
    import ipaddress  # noqa: PLC0415

    name = host.strip().strip("[]").lower()
    if name in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _warn_if_exposed(host: str, port: int, prog: str) -> None:
    """Say it plainly when the console listens beyond this machine.

    The console has no login. Its Host check stops a browser page from
    elsewhere, not a client that sets the header itself, so whoever can reach
    the port can do what the console does -- with the credentials of the
    person who started it.
    """
    if _is_loopback_host(host):
        return
    print(
        f"{prog}: WARNING -- listening on {host}:{port}, which is not this machine only.\n"
        "  The console has NO authentication. Anyone on the network who can reach this port\n"
        "  can use it: start a capture with YOUR credentials, read every archive it can see,\n"
        "  and delete archive folders. Use --host 127.0.0.1 unless you mean this.",
        file=sys.stderr,
        flush=True,
    )


def _open_browser_when_listening(host: str, port: int) -> None:
    """Open the console, once there is something there to open.

    `run` blocks, so the browser has to be launched from a thread that waits
    for the port to answer. Opening immediately shows a connection error for as
    long as startup takes, which reads as a broken install.
    """
    import threading  # noqa: PLC0415

    url = f"http://{host}:{port}/"

    def wait_then_open() -> None:
        import socket  # noqa: PLC0415
        import time  # noqa: PLC0415

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            # Never came up. Say nothing: the server's own output is the place
            # a failure to start belongs, and a browser that did not open is
            # not itself an error worth a second message.
            return
        webbrowser.open(url)

    threading.Thread(target=wait_then_open, daemon=True).start()


def open_main(
    argv: Sequence[str] | None = None,
    *,
    run: Callable[..., Any] | None = None,
) -> int:
    """`connections-export open <path> [--host] [--port]`: launch the local
    console pointed at an existing archive (or interchange package) so it can
    be browsed and exported to PDF later, with no crawl. For people who use
    the terminal; the console's 'open an archive' UI covers everyone else."""
    parser = argparse.ArgumentParser(prog="connections-export open")
    parser.add_argument(
        "path",
        help="Path to a run archive (a directory or a .zip) or an interchange package directory.",
    )
    parser.add_argument("--host", dest="host", default="127.0.0.1")
    parser.add_argument("--port", dest="port", type=int, default=8000)
    parser.add_argument(
        "--author",
        dest="filter_author",
        default=None,
        help="Only browse content this user authored/participated in (name or user id).",
    )
    args = parser.parse_args(argv)

    target = Path(args.path)
    zipped = target.is_file() and target.suffix.lower() == ".zip"
    if not target.is_dir() and not zipped:
        print(
            f"connections-export open: not an archive directory or .zip: {target}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    from connections_export.archive.source import ArchiveSourceError  # noqa: PLC0415
    from connections_export.derive import DeriveError  # noqa: PLC0415

    # A package has a top-level interchange.json; otherwise treat it as a raw archive.
    try:
        if not zipped and (target / "interchange.json").is_file():
            app = make_app(
                demo=False,
                package_dir=target,
                bound_host=args.host,
                author_filter=args.filter_author,
            )
        else:
            app = make_app(
                demo=False,
                archive_dir=target,
                bound_host=args.host,
                author_filter=args.filter_author,
            )
    except (DeriveError, ArchiveSourceError):
        # make_app derives eagerly; an empty/partial archive raises here
        # instead of leaving it to the API layer (which serves a pending
        # response for the same case -- see `_archive_model` above). Report
        # it cleanly rather than let the traceback surface to a terminal user.
        print(
            "connections-export open: that archive has nothing to browse yet "
            "— has an import finished?",
            file=sys.stderr,
            flush=True,
        )
        return 1

    host, port = args.host, args.port
    if run is None:
        port = _free_port(host, port)
        print(f"connections-export: serving {target} at http://{host}:{port}", flush=True)
        import uvicorn  # noqa: PLC0415

        run = uvicorn.run
    _warn_if_exposed(host, port, "connections-export open")
    run(app, host=host, port=port, timeout_graceful_shutdown=1)
    return 0


def _free_port(host: str, preferred: int) -> int:
    """Return `preferred` if it can be bound, else the next free port
    above it (scanning a small range). Best-effort — falls back to
    `preferred` so the launcher surfaces the bind error itself if the
    whole range is busy.

    The probe asks for an ordinary, exclusive binding and sets no options.
    `SO_REUSEADDR` in particular must NOT be set: on Unix it permits
    rebinding a socket in TIME_WAIT, but on Windows it permits binding a
    port another socket is *actively* using — so the probe reported a busy
    port as free, and uvicorn then failed to bind it for real. A bare
    `connections-export` on a machine with anything else on 8000 answered
    with a bind error, and the way out of that is `serve --port 8137`,
    which is not something a first minute should require knowing.
    """
    import socket  # noqa: PLC0415

    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    return preferred


# --- single entry point ------------------------------------------------
def _print_comparison(
    cmp,
    *,
    search_returned_unfiltered: int | None = None,
    truncation_note: str | None = None,
) -> None:
    def row(label: str, value: int, note: str = "") -> None:
        print(f"  {label:<22}{value:>4}  {note}".rstrip())

    print(f"Author-filter comparison for: {cmp.author}\n")
    row("naive scan", cmp.naive_count, "authored/participated-in items")
    if search_returned_unfiltered is not None:
        # Before narrowing, because this is the number a page ceiling shows
        # up in: "returned everything" and "returned the first N" are told
        # apart here and nowhere else.
        row("search returned", search_returned_unfiltered, "for the whole community")
        row("  ...in this forum", cmp.search_returned, "narrowed, see the note below")
    else:
        row("search returned", cmp.search_returned, "the superset")
    row("search authored", cmp.search_authored, "re-filtered to authorship")
    row("agreed", len(cmp.agreed))
    row("MISSED by search", len(cmp.missed_by_search), "search never returned it")
    row("extra from search", len(cmp.extra_from_search))
    row("superset noise", len(cmp.superset_noise), "returned, not authored")
    if search_returned_unfiltered is not None:
        print(
            "\n  Search is scoped to the community, which holds other forums, so its\n"
            "  results are narrowed to this one -- by topic id, and the ids come from\n"
            "  the crawl above. So `extra from search` and `superset noise` are held\n"
            "  at zero by that narrowing rather than measured, and `...in this forum`\n"
            "  is the size of the overlap. `MISSED by search` is unaffected: narrowing\n"
            "  what Search returned cannot turn a miss into a hit."
        )
    # Before the verdict, because it decides whether there is one to read: an
    # answer cut short at our own page limit makes "search agrees" a statement
    # about what we asked for rather than about what exists.
    if truncation_note:
        print("\n  INCOMPLETE — Search's answer was cut short\n")
        print(truncation_note)

    verdict = "AGREES" if cmp.search_agrees else "DISAGREES"
    tail = "" if cmp.search_agrees else "  -- do not trust search alone yet"
    if truncation_note and cmp.search_agrees:
        tail = "  -- but only across the part of its answer that was read"
    print(f"\n  => search {verdict} with the naive scan{tail}")
    if cmp.missed_by_search:
        print("\n  missed by search (naive kept, search never returned):")
        for title in cmp.missed_by_search[:20]:
            print(f"    - {title}")
        if len(cmp.missed_by_search) > 20:
            print(f"    ... and {len(cmp.missed_by_search) - 20} more")


def _comparison_progress():
    """An `emit` that shows the baseline crawl is alive.

    The comparison reads a whole forum before it can compare anything, at a
    request a second, and it writes into a temporary directory that is
    deleted -- so with events discarded there is no output and no archive
    growing on disk. An hour of silence is indistinguishable from a hang, and
    whoever is running this is usually not the person who wrote it.

    Counted rather than echoed: a line per request would be thousands of
    lines, which hides the progress as effectively as printing nothing.
    """
    import time  # noqa: PLC0415

    state = {"fetched": 0, "items": 0, "last": 0.0}

    def emit(event) -> None:
        name = type(event).__name__
        if name == "Fetched":
            state["fetched"] += 1
        elif name in ("ForumTopicDerived", "BlogPostDerived", "PageDerived"):
            state["items"] += 1
        else:
            return
        now = time.monotonic()
        if now - state["last"] < 2.0:
            return
        state["last"] = now
        print(
            f"\r  {state['fetched']} request(s), {state['items']} item(s) read",
            end="",
            file=sys.stderr,
            flush=True,
        )

    return emit


def _search_pagination_note(
    *, pages_read: int, last_batch: int, page_size: int, page_limit: int
) -> str | None:
    """A warning when Search's answer was cut short by OUR page limit.

    The walk stops on a short page -- that was all of it -- or on running
    out of pages, which means only that we stopped looking. The two endings
    produce identical output otherwise, and the second one invalidates the
    conclusion the whole comparison exists to reach: `MISSED by search = 0`
    then says nothing was missed among what we asked for, which is not the
    question.

    It is an ordinary outcome at real sizes. A forum of 2,000 topics sits in
    a community holding others, and the person query is scoped to the
    community.
    """
    if pages_read < page_limit or last_batch < page_size:
        return None
    seen = pages_read * page_size
    return (
        f"  Search filled every one of the {page_limit} pages this asks for "
        f"({seen:,} results)\n"
        f"  and was still returning a full page. Its answer was cut short HERE, not\n"
        f"  by the deployment -- so anything past {seen:,} was never looked at, and\n"
        f"  `MISSED by search` cannot be read as complete recall. Narrow the query\n"
        f"  (a smaller community, or a scope with fewer items) and run it again."
    )


def _search_topic_id(result) -> str | None:
    for url in (result.via_url, result.alternate_url):
        if not url:
            continue
        split = urlsplit(url)
        query = parse_qs(split.query)
        for name in ("topicUuid", "topicId"):
            values = query.get(name)
            if values and values[0]:
                return values[0]
        if "threadTopic" in split.path:
            values = query.get("id")
            if values and values[0]:
                return values[0]
    return None


def _filter_search_results_to_topics(results, topic_ids: set[str]):
    return [result for result in results if _search_topic_id(result) in topic_ids]


def compare_author_main(
    argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None
) -> int:
    """`connections-export compare-author --author X --demo`: run the naive
    author scan and the Search person query over the same data and diff them,
    so the Search API's person filter can be trust-checked before it's relied
    on to drive collection. `--demo` uses the in-process
    dataset; a live target crawl+search is a follow-up."""
    parser = argparse.ArgumentParser(prog="connections-export compare-author")
    parser.add_argument("--author", required=True, help="Person (name or user id) to compare.")
    parser.add_argument(
        "--demo", action="store_true", help="Compare over the in-process demo dataset."
    )
    parser.add_argument("--base-url", help="Live HCL deployment URL.")
    parser.add_argument(
        "--auth-mode", choices=["sspi", "kerberos", "basic", "paste_token"], default="sspi"
    )
    parser.add_argument("--app", choices=["blog", "forum"], required=False, default="forum")
    parser.add_argument(
        "--community", dest="community_uuid", help="Community UUID for Search scoping."
    )
    parser.add_argument("--forum-uuid", help="Forum UUID to crawl for the naive baseline.")
    parser.add_argument(
        "--blogs-homepage", default="Blogs", help="Blogs list-feed homepage handle."
    )
    parser.add_argument(
        "--with-assets",
        action="store_true",
        help="Include images and attachments (comparison skips them by default).",
    )
    args = parser.parse_args(argv)
    if not args.demo and not args.base_url:
        print(
            "provide --demo or --base-url for a live comparison.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    from connections_export.adapters.search import parse_search_results  # noqa: PLC0415
    from connections_export.compare.author_compare import compare_author  # noqa: PLC0415

    # Set for both paths: the demo neither narrows nor paginates, and the
    # live path assigns these only when it does.
    unfiltered: int | None = None
    truncation: str | None = None
    if args.demo:
        import tempfile  # noqa: PLC0415

        from connections_export.fakeserver.prototype import (  # noqa: PLC0415
            build_prototype_wikiset,
        )
        from connections_export.fakeserver.searchfeed import (  # noqa: PLC0415
            search_results_feed,
        )
        from connections_export.gui.demo import run_demo  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            interchange = run_demo(
                (lambda _e: None), archive_dir=Path(tmp) / "a", delay=0
            ).interchange
        feed = search_results_feed(
            build_prototype_wikiset(),
            userid=args.author,
            base_url="https://fake",
            auth_root="basic",
        )
        comparison = compare_author(interchange, parse_search_results(feed), author=args.author)
    else:
        import tempfile  # noqa: PLC0415

        from connections_export.adapters.search import search_results_url  # noqa: PLC0415
        from connections_export.crawler import crawl_blogs, crawl_forums  # noqa: PLC0415
        from connections_export.derive import derive  # noqa: PLC0415
        from connections_export.http.results import Fetched  # noqa: PLC0415

        config = Config(
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            capture_assets=args.with_assets,
        )
        try:
            client = _build_default_client(config, env={})
        except AuthError as error:
            print(f"connections-export compare-author: {error}", file=sys.stderr)
            return 3
        progress = _comparison_progress()
        with tempfile.TemporaryDirectory() as tmp:
            archive = Archive.open(Path(tmp) / "archive")
            print(
                f"reading {args.app}s directly — this is the baseline, and it reads "
                "everything before it can compare anything",
                flush=True,
            )
            if args.app == "forum":
                crawl_forums(
                    config=config,
                    client=client,
                    archive=archive,
                    forum_uuids=[args.forum_uuid] if args.forum_uuid else None,
                    emit=progress,
                )
            else:
                crawl_blogs(
                    config=config,
                    client=client,
                    archive=archive,
                    blogs_homepage=args.blogs_homepage,
                    emit=progress,
                )
            print(file=sys.stderr)
            interchange = derive(archive, base_url=args.base_url)
            search_results = []
            print("asking the deployment's own Search for the same thing", flush=True)
            page_limit, page_size = 40, 150
            pages_read, last_batch = 0, 0
            for page in range(1, page_limit + 1):
                search_url = search_results_url(
                    base_url=args.base_url,
                    userid=args.author,
                    community_uuid=args.community_uuid,
                    scope="blogs:entry" if args.app == "blog" else "forums:topic",
                    page=page,
                    page_size=page_size,
                )
                response = client.get(search_url)
                if not isinstance(response, Fetched) or response.status >= 400:
                    if page == 1:
                        print(
                            f"Search request failed: {getattr(response, 'status', None)}",
                            file=sys.stderr,
                        )
                        return 1
                    break
                batch = parse_search_results(response.content)
                search_results.extend(batch)
                pages_read, last_batch = page, len(batch)
                print(f"  search page {page}: {len(batch)} hit(s)", flush=True)
                if len(batch) < page_size:
                    break
            if args.app == "forum" and args.forum_uuid:
                # Kept, because it is the only place a page ceiling shows: a
                # Search that stopped at its limit and one that returned
                # everything are indistinguishable after narrowing.
                unfiltered = len(search_results)
                forum_topic_ids = {
                    topic.id for forum in interchange.forums for topic in forum.topics.values()
                }
                search_results = _filter_search_results_to_topics(search_results, forum_topic_ids)
            truncation = _search_pagination_note(
                pages_read=pages_read,
                last_batch=last_batch,
                page_size=page_size,
                page_limit=page_limit,
            )
            comparison = compare_author(interchange, search_results, author=args.author)
    _print_comparison(comparison, search_returned_unfiltered=unfiltered, truncation_note=truncation)
    return 0


# The installed namespace holds ONE command, `connections-export`, with
# subcommands, rather than four separate `hcl-*` scripts.


def style_main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """`connections-export style`: show or write out the PDF stylesheet.

    Exists because "you can supply your own CSS" is only useful if you can see
    what you are overriding. Guessing our class names from a rendered PDF is
    not a reasonable thing to ask of anyone, so the program hands you its own
    stylesheet, tokens first, to edit and pass back with `pdf --css`.
    """
    from connections_export.pdf.html import _STYLE, style_token_names  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="connections-export style",
        description="The PDF stylesheet and the settings that change it.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Write the default stylesheet (tokens first) to --output or stdout.",
    )
    parser.add_argument(
        "--tokens",
        action="store_true",
        help="List the token names settable under [pdf_style] in the config file.",
    )
    parser.add_argument(
        "--marks",
        action="store_true",
        help="Write the running header/footer settings, with their placeholders "
        "explained, as a TOML block to paste into connections-export.toml. Same "
        "idea as --dump: see what you have before changing it.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write to this file.")
    args = parser.parse_args(argv)

    if args.marks:
        from connections_export.pdf.marks import dump_marks  # noqa: PLC0415

        text = dump_marks()
        if args.output:
            args.output.write_text(text, encoding="utf-8")
            print(f"wrote {args.output}")
        else:
            print(text, end="")
        return 0

    if args.tokens:
        print("Settable under [pdf_style] in connections-export.toml, or via --css:")
        for name in style_token_names():
            print(f"  {name.replace('-', '_')}")
        print('\nExample:\n  [pdf_style]\n  body_size = "11pt"\n  font = "Georgia, serif"')
        # The header and footer are configured separately and were easy to
        # miss entirely, so this names them where someone is already looking.
        print("\nThe running header and footer are separate:\n  connections-export style --marks")
        return 0

    if not args.dump:
        parser.print_help()
        return 0

    header = (
        "/* connections-export: the default PDF stylesheet.\n"
        " *\n"
        " * Edit and pass back with `connections-export pdf --css this-file.css`,\n"
        " * or set `pdf_css` in connections-export.toml. It is appended AFTER the\n"
        " * captured pages' own CSS, so what you write here wins.\n"
        " *\n"
        " * For a size or a colour you usually want only the :root block below --\n"
        " * those tokens are the supported surface and will keep working. The\n"
        " * rules under it are ours to change.\n"
        " */\n"
    )
    text = header + _STYLE.lstrip("\n")
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"connections-export style: wrote {args.output} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


def licenses_main(argv: Sequence[str] | None = None) -> int:
    """`connections-export licenses [--sbom] [--extract DIR] [--bundle DIR]`.

    What is inside this build and under what terms. The point of it is the
    frozen executable: someone handed a single file cannot run `pip list`
    against it, and BSD, MIT and Apache-2.0 all require the licence TEXT to
    travel with a binary rather than the name of the licence. So the text has
    to come back out, which means a command that reads the bundle inside.
    """
    from connections_export import sbom  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="connections-export licenses",
        description="List the components in this build with their licences.",
    )
    parser.add_argument(
        "--sbom",
        action="store_true",
        help="Print the CycloneDX SBOM instead of the table.",
    )
    parser.add_argument(
        "--extract",
        metavar="DIR",
        default=None,
        help="Write every licence text, the NOTICE and the SBOM into DIR.",
    )
    parser.add_argument(
        "--texts",
        action="store_true",
        help="Print every licence text to stdout (pipe it, or redirect to a file).",
    )
    parser.add_argument(
        "--bundle",
        metavar="DIR",
        default=None,
        help="Read the bundle from DIR (default: the one inside this build).",
    )
    args = parser.parse_args(argv)

    source = Path(args.bundle) if args.bundle else sbom.bundled_dir()
    try:
        rows = sbom.read_bundle(source)
    except sbom.SbomError as exc:
        print(
            f"connections-export licenses: {exc}\n"
            "  A build produces this bundle; a source checkout can write one with\n"
            "  `uv run python tools/write_license_bundle.py`.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    if args.sbom:
        print((source / sbom.SBOM_FILENAME).read_text(encoding="utf-8").rstrip())
        return 0

    if args.texts:
        # Everything, to stdout: the frozen executable has no file browser
        # inside it, and redirecting one stream is the lowest-ceremony way to
        # get the texts out of it. The console offers the same bytes as a
        # download, from this same renderer.
        print(sbom.render_all_texts(source).rstrip())
        return 0

    if args.extract:
        target = Path(args.extract)
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            destination = target / item.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.read_bytes())
            copied += 1
        print(f"{copied} files written to {target}")
        return 0

    width = max(len(row.name) for row in rows)
    for row in rows:
        print(f"{row.name:<{width}}  {row.version:<14}  {row.license}")
    print()
    print(f"{len(rows)} components. Licence texts: connections-export licenses --extract DIR")
    return 0


def probe_main(argv: Sequence[str] | None = None) -> int:
    """`connections-export probe files-since --community <uuid>`.

    Ask the deployment the one question this project cannot answer offline.

    Everything else here is tested against the fake server, which serves our
    documented UNDERSTANDING of Connections -- which is exactly why it cannot
    settle a question about whether that understanding is right. `files-since`
    is such a question, and the crawler takes the conservative side until it
    is answered.

    Reads two feed pages and writes nothing: no archive, no file, no change to
    the deployment.
    """
    from connections_export.probes import (  # noqa: PLC0415
        probe_files_since,
        probe_search_reach,
    )

    parser = argparse.ArgumentParser(
        prog="connections-export probe",
        description="Ask a live deployment a question that cannot be answered offline.",
    )
    parser.add_argument(
        "question",
        choices=["files-since", "search-reach", "proxy"],
        help="which question to ask",
    )
    parser.add_argument(
        "--community",
        metavar="UUID",
        help="the community to ask about (required for files-since; scopes search-reach)",
    )
    parser.add_argument(
        "--author",
        metavar="USERID",
        help="search-reach: whose items to ask Search for",
    )
    parser.add_argument(
        "--scope",
        default="forums:topic",
        help="search-reach: the Search scope to ask under (default: forums:topic)",
    )
    parser.add_argument("--base-url", default=None, help="deployment root (default: configured)")
    parser.add_argument("--auth", default=None, help="auth mode (default: configured)")
    parser.add_argument(
        "--proxy", default=None, help="proxy: a proxy URL, or `direct` (default: decided)"
    )
    args = parser.parse_args(list(argv or []))

    try:
        config = load_config(
            {
                key: value
                for key, value in (
                    ("base_url", args.base_url),
                    ("auth_mode", args.auth),
                    ("proxy", args.proxy),
                )
                if value
            }
        )
    except ConfigError as exc:
        print(f"connections-export probe: {exc}", file=sys.stderr)
        return 2
    if args.question == "proxy":
        # Answered first, and without a deployment being configured: the
        # question is what THIS MACHINE does, and the address is only an
        # example to ask about. Every line is flushed as it is made, so a
        # PAC evaluation that takes its time is visibly taking its time
        # rather than a command that printed nothing.
        import os  # noqa: PLC0415

        from connections_export.http.proxy import (  # noqa: PLC0415
            PAC_TIMEOUT_SECONDS,
            explain,
            pac_configured,
            resolve_proxy,
        )

        deployment = config.base_url or "https://connections.example.com/"
        print("connections-export probe proxy", flush=True)
        print(f"  platform: {sys.platform}", flush=True)
        print(
            f"  PAC / auto-detect: {pac_configured() or 'none configured, or not Windows'}",
            flush=True,
        )
        if not config.base_url:
            print(
                f"  (no deployment configured; asking about {deployment} as an example -- "
                "pass --base-url for the real one)",
                flush=True,
            )
        for url in (deployment, "http://127.0.0.1:8000/"):
            print(f"  asking about {url} ...", flush=True)
            decision = resolve_proxy(url, explicit=config.proxy, env=os.environ)
            print(f"  {explain(decision)}", flush=True)
        print(
            "  order of authority: --proxy / config `proxy`; then (Windows) the PAC "
            f"script up to {PAC_TIMEOUT_SECONDS:.0f} s and the system proxy setting; then "
            "HTTPS_PROXY & NO_PROXY; then direct. Loopback is always direct; an "
            "undetermined PAC is surfaced, not assumed direct.",
            flush=True,
        )
        return 0
    if not config.base_url:
        print(
            "connections-export probe: no deployment configured. Pass --base-url, or set one in "
            "connections-export.toml / CONNECTIONS_EXPORT_BASE_URL.",
            file=sys.stderr,
        )
        return 2

    # Before a client is built, let alone a request made: a forgotten flag
    # surfacing as an authentication stack trace tells the reader about the
    # wrong problem, and costs nothing to check here.
    if args.question == "search-reach" and not args.author:
        print(
            "connections-export probe search-reach: --author is required — the "
            "question is how far one person's query reaches.",
            file=sys.stderr,
        )
        return 2
    if args.question == "files-since" and not args.community:
        print(
            "connections-export probe files-since: --community is required — the "
            "question is about one community's file library.",
            file=sys.stderr,
        )
        return 2

    import os  # noqa: PLC0415

    try:
        client = _build_default_client(config, env=os.environ)
    except AuthError as error:
        # Every other command turns this into something to act on. This one is
        # run by whoever has the deployment rather than by whoever wrote it,
        # which makes a stack trace worth even less than usual.
        print(f"connections-export probe: {error}", file=sys.stderr)
        return 3

    if args.question == "search-reach":
        reach = probe_search_reach(
            client=client,
            base_url=config.base_url,
            userid=args.author,
            community_uuid=args.community,
            scope=args.scope,
        )
        print("question:  how far does one person's Search query reach before we stop reading?")
        print(f"asked of:  {config.base_url}  (source_version {config.source_version})")
        print(f"  scope:           {args.scope}")
        print(f"  community:       {args.community or '(none — the whole deployment)'}")
        print(f"  results read:    {reach.total:,} over {reach.pages_read} page(s)")
        print(f"  last page:       {reach.last_page} of {reach.page_size}")
        print(f"verdict:   {reach.summary}")
        if reach.complete is False:
            # The one outcome that invalidates a recall result taken with the
            # same limit, so it is said as a conclusion and not left implied.
            print(
                "\n  A recall comparison run with this limit cannot be read as "
                "complete:\n  it only saw the part of the answer above."
            )
        return 0 if reach.complete else 1

    verdict = probe_files_since(
        client=client, base_url=config.base_url, community_uuid=args.community
    )

    print("question:  does the Files library feed's `since=` filter on modification or creation?")
    # Printed because a verdict without the deployment it came from is not
    # evidence: the first answer came back with "version not exposed by the
    # probe", which was the probe's omission, not the deployment's.
    print(f"asked of:  {config.base_url}  (source_version {config.source_version})")
    if verdict.plan is not None:
        print(f"cutoff:    {verdict.plan.cutoff}")
        print(
            f"  edited after it:   {verdict.plan.candidate_name or verdict.plan.candidate_id} "
            f"({verdict.plan.candidate_id})"
        )
        print(
            f"  untouched before:  {verdict.plan.control_name or verdict.plan.control_id} "
            f"({verdict.plan.control_id})"
        )
        print(f"  the feed returned: {len(verdict.returned)} document(s)")
    print(f"verdict:   {verdict.behaviour}")
    print(f"           {verdict.detail}")
    if verdict.behaviour == "inconclusive":
        return 1
    print()
    print(
        "Please record this in docs/reference/architecture.md (section 14, "
        '"Files still sends no cutoff") and open-questions.md, with the deployment '
        "version -- a verified answer is worth more than the code change it unblocks."
    )
    return 0


_SUBCOMMANDS: dict[str, Callable[..., object]] = {
    "crawl": crawl_main,
    "serve": serve_main,
    "open": open_main,
    "pdf": pdf_main,
    "style": style_main,
    "ingest": ingest_main,
    "package": package_main,
    "compare-author": compare_author_main,
    "licenses": licenses_main,
    "probe": probe_main,
}

_MAIN_USAGE = (
    "usage: connections-export <command> [options]\n"
    "\n"
    "commands:\n"
    "  crawl           run an export crawl of a configured deployment\n"
    "  serve           launch the local web console (--demo for a synthetic run)\n"
    "  open            open an existing archive/package to browse or export\n"
    "  pdf             render the reconstructed wiki to a PDF\n"
    "  ingest          reconstruct an archive or package into a target (obsidian, jekyll, hugo)\n"
    "  package         write a capture's portable interchange package\n"
    "  style           show or dump the PDF stylesheet, and list its settings\n"
    "  licenses        what is in this build, with licence texts to extract\n"
    "  probe           ask a live deployment a question it alone can answer\n"
    "  compare-author  check an author filter against the deployment's search\n"
    "\n"
    "Run `connections-export <command> --help` for a command's options."
)


def _serve_for_default_launch(argv: list[str]) -> int:
    """Indirection so a test can watch the bare launch without binding a port."""
    return serve_main(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """The `connections-export` entry point. Dispatches `connections-export <command> …`
    to the per-capability mains, returning a process exit code."""
    import sys  # noqa: PLC0415

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(_MAIN_USAGE)
        return 0
    if args and args[0] in ("--version", "-V"):
        from connections_export.build_info import build_info  # noqa: PLC0415

        # The same string the console shows at the sidebar foot: the version,
        # and for a test build or a dev checkout, which it is.
        print(build_info()["label"])
        return 0
    if not args:
        # Bare `connections-export` shows the thing rather than describing
        # it. A usage message is the right answer to a wrong command, not to
        # a bare one.
        #
        # It does NOT force the demo. It used to, which meant a console
        # started this way read the synthetic deployment whatever URL was
        # dropped on it -- so a real community came back empty, and the
        # console said the community held nothing rather than that it had
        # not looked. The demo is on the setup screen either way, as URLs to
        # try.
        return _serve_for_default_launch(["--open"])
    handler = _SUBCOMMANDS.get(args[0])
    if handler is None:
        print(f"connections-export: unknown command {args[0]!r}\n", file=sys.stderr)
        print(_MAIN_USAGE, file=sys.stderr)
        return 2
    result = handler(args[1:])
    return result if isinstance(result, int) else 0
