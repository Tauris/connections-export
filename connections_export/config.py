"""Layered configuration for the export tooling.

`Config` (pydantic v2) holds everything a capability needs to talk to a
Connections deployment, except secrets — see below. `base_url` defaults
to `None`, never a real or example server, so the package ships
server-agnostic and a capability that needs it fails with a clear error
(`Config.require_base_url`) rather than silently hitting a placeholder.

`load_config` resolves the layers in precedence order, highest wins,
merged per key:

    CLI flags > environment (CONNECTIONS_EXPORT_*) > config file (TOML) > defaults

Each layer contributes only the keys it actually sets; a key untouched
by a higher layer falls through to whatever the next layer below it
set, all the way down to the `Config` field defaults.

Secrets (bearer token, basic username/password) are never `Config`
fields — `model_config = ConfigDict(extra="forbid")` guarantees a
constructor call can't smuggle one in as a field. They are resolved on
demand from environment or an interactive prompt by `resolve_token`/
`resolve_basic`, and are never logged or rendered: `Config.__repr__`
only ever walks the model's own (secret-free) fields.
"""

from __future__ import annotations

import getpass
import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AuthMode = Literal["sspi", "kerberos", "basic", "paste_token"]
AuthRoot = Literal["basic", "basic/anonymous", "form", "oauth"]
VersionPolicy = Literal["list", "full", "none"]
#: How a run treats what the archive already holds.
#:
#: `resume` — skip any URL already archived ok. The historical default, and
#: what makes an interrupted crawl cheap to finish.
#: `update` — always refetch the documents that REVEAL change (feeds, search
#: result pages, the rich content layout), and refetch an item's
#: expensive parts (body, comments, versions, assets) only when the
#: item's own date says it moved. This is "bring the archive up to
#: date" and is the only mode that can add to a finished capture
#: without re-downloading it.
#: `refresh` — refetch everything, ignoring what is on disk.
FetchMode = Literal["resume", "update", "refresh"]

CONFIG_FILE_NAME = "connections-export.toml"


class ConfigError(Exception):
    """A configuration problem a capability should report clearly
    (e.g. a required value was never set by any layer), as opposed to
    a pydantic `ValidationError` (a value was set but is malformed)."""


class Config(BaseModel):
    """Resolved configuration for one run. Never constructed with a
    secret: there is no field for one to occupy."""

    model_config = ConfigDict(extra="forbid")

    #: The floor for `min_interval`. Not advertised as a target: it exists so a
    #: fast local or test deployment is not made artificially slow, not so an
    #: export can be hurried through a production system.
    MIN_REQUEST_INTERVAL: ClassVar[float] = 0.2

    base_url: str | None = None
    auth_mode: AuthMode = "sspi"
    auth_root: AuthRoot = "basic"
    source_version: str = "8.0"
    hcl_hosts: list[str] = Field(default_factory=list)
    #: Seconds to wait between requests to the deployment. Defaults to a
    #: deliberate 1s: an export walks thousands of feeds, and a tool that goes
    #: as fast as the server allows is one a Connections admin notices for the
    #: wrong reasons. A large export is better run overnight at 3s than pushed
    #: through at midday. Values below MIN_REQUEST_INTERVAL are raised to it.
    min_interval: float = 1.0
    page_size: int = 500
    output_dir: Path = Path("archive")
    #: See `FetchMode`. Replaces the older `refresh` boolean, which could only
    #: say "skip what I have" or "refetch the world" -- neither of which is an
    #: update.
    fetch: FetchMode = "resume"
    #: An existing archive directory this run adds to. When set, the run is an
    #: extend/update of that archive rather than a fresh capture, and the
    #: cutoff for date-filtered feeds is computed from its own provenance.
    into: Path | None = None
    #: During an `update`, ask for each item's comments/replies even when the
    #: item itself did not change.
    #:
    #: A `since`-filtered feed reports items that MOVED. A new comment on an
    #: otherwise untouched post may not move it, so an update that trusts the
    #: entry feed alone loses that comment. For wikis the same question applies
    #: to a page's `modified` date, and whether a comment bumps it is not
    #: something a deployment guarantees.
    #:
    #: Closing the hole costs one request per item -- the expensive half of an
    #: otherwise cheap update -- so it is a choice made where the cost is
    #: visible, not a silent default in either direction. Off by default keeps
    #: an update cheap; the console explains the trade at the point of
    #: choosing.
    recheck_comments: bool = False
    #: Overrides the cutoff an update would compute for itself. Absent by
    #: default, so a scripted monthly re-run stays correct without anyone
    #: editing a date. Earlier re-checks more, never less.
    since_override: str | None = None
    versions: VersionPolicy = "list"
    #: Keep feeds, entries, comments, and hierarchy while skipping referenced
    #: images/attachments for comparison or preview crawls.
    capture_assets: bool = True
    #: Overrides for the PDF design tokens, by token name without the
    #: `--pdf-` prefix: `{"body_size": "11pt", "font": "Georgia, serif"}`.
    #: A small, named surface, so nobody has to depend on our rule structure
    #: to change a size. `connections-export style --dump` lists what exists.
    pdf_style: dict[str, str] = Field(default_factory=dict)
    #: Running header/footer settings by name -- `footer_left`, `footer_right`,
    #: `header_left`, `header_right`, `mark_size`, `mark_color`. Values may
    #: contain `{page}`, `{pages}`, `{title}`, `{date}`, `{section}` and literal
    #: text. See `connections_export.pdf.marks`.
    pdf_marks: dict[str, str] = Field(default_factory=dict)
    #: A stylesheet appended after everything else, including the captured
    #: page's own CSS -- the escape hatch for anything the tokens do not cover.
    pdf_css: Path | None = None
    #: "Capture everything I authored": when set, the derived/served/exported
    #: model is narrowed to this user (matched by author name, snx:userid, or
    #: contributor), each kept entity with its full chain. The raw archive stays
    #: complete -- only the model is filtered. None = no filter.
    filter_author: str | None = None

    @field_validator("min_interval")
    @classmethod
    def _not_below_the_floor(cls, value: float) -> float:
        """Raise a too-small interval to the floor rather than rejecting it.

        Someone asking for 0 wants "as fast as possible", which is a reasonable
        thing to want and an unreasonable thing to do to a shared deployment.
        Clamping honours the intent as far as it goes; refusing would just move
        the argument to the command line.
        """
        return max(float(value), cls.MIN_REQUEST_INTERVAL)

    def require_base_url(self) -> str:
        """Return `base_url`, or raise a clear `ConfigError` if no
        layer ever set it — the "not configured" error the spec
        requires from capabilities that need it."""
        if not self.base_url:
            raise ConfigError(
                "base_url is not configured. Set it via --base-url, the "
                "CONNECTIONS_EXPORT_BASE_URL environment variable, or base_url in "
                "connections-export.toml (copy connections-export.example.toml to get started)."
            )
        return self.base_url

    def __repr__(self) -> str:
        # Deliberately walks only the model's own declared fields —
        # there is no secret field to accidentally include, and this
        # stays true even if someone adds a field later without
        # updating this method, because model_dump never sees
        # anything that wasn't declared above.
        fields = ", ".join(f"{name}={value!r}" for name, value in self.model_dump().items())
        return f"Config({fields})"

    def __str__(self) -> str:
        return repr(self)


# --- File discovery -------------------------------------------------


def discover_config_file(
    *,
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Find the user's config file, in precedence order:

    1. `explicit` (the CLI `--config` flag)
    2. `$CONNECTIONS_EXPORT_CONFIG` (an explicit override named in the environment)
    3. `./connections-export.toml`
    4. `$XDG_CONFIG_HOME/connections-export/config.toml`, falling back to
       `~/.config/connections-export/config.toml` when `XDG_CONFIG_HOME` is unset

    Returns `None` if none of the standard locations (3, 4) exist.
    An explicit override (1 or 2) that names a nonexistent file is an
    error, not a silent fall-through — the user pointed at it on purpose.
    """
    env = env if env is not None else os.environ

    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"--config points to {path}, which does not exist")
        return path

    env_override = env.get("CONNECTIONS_EXPORT_CONFIG")
    if env_override:
        path = Path(env_override)
        if not path.is_file():
            raise FileNotFoundError(
                f"CONNECTIONS_EXPORT_CONFIG points to {path}, which does not exist"
            )
        return path

    cwd_candidate = Path.cwd() / CONFIG_FILE_NAME
    if cwd_candidate.is_file():
        return cwd_candidate

    xdg_home = env.get("XDG_CONFIG_HOME")
    if xdg_home:
        config_root = Path(xdg_home)
    else:
        # `Path.home` consults `$HOME` on POSIX but not on Windows (which
        # instead reads `%USERPROFILE%` / `%HOMEDRIVE%+%HOMEPATH%`), so on
        # Windows an explicit `$HOME` override would otherwise be silently
        # ignored. Check the real process `$HOME` explicitly first (many
        # cross-platform CLI tools honor it on Windows too, e.g. git/ssh),
        # falling back to `Path.home` for the native-Windows case.
        home_override = os.environ.get("HOME")
        config_root = Path(home_override) if home_override else Path.home()
        config_root = config_root / ".config"
    xdg_candidate = config_root / "connections-export" / "config.toml"
    if xdg_candidate.is_file():
        return xdg_candidate

    return None


def _load_file_layer(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


# --- Environment layer ------------------------------------------------

_ENV_FIELDS: dict[str, str] = {
    "base_url": "CONNECTIONS_EXPORT_BASE_URL",
    "auth_mode": "CONNECTIONS_EXPORT_AUTH_MODE",
    "auth_root": "CONNECTIONS_EXPORT_AUTH_ROOT",
    "source_version": "CONNECTIONS_EXPORT_SOURCE_VERSION",
    "hcl_hosts": "CONNECTIONS_EXPORT_HCL_HOSTS",
    "min_interval": "CONNECTIONS_EXPORT_MIN_INTERVAL",
    "page_size": "CONNECTIONS_EXPORT_PAGE_SIZE",
    "output_dir": "CONNECTIONS_EXPORT_OUTPUT_DIR",
    "fetch": "CONNECTIONS_EXPORT_FETCH",
    "into": "CONNECTIONS_EXPORT_INTO",
    "since_override": "CONNECTIONS_EXPORT_SINCE",
    "versions": "CONNECTIONS_EXPORT_VERSIONS",
}

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def parse_env_layer(env: Mapping[str, str]) -> dict:
    """Extract and coerce the `CONNECTIONS_EXPORT_*` variables present in `env`
    into a partial `Config`-shaped dict — only keys actually set (and
    non-empty) are present, so this can be merged straight into the
    precedence chain."""
    layer: dict = {}
    for field, var in _ENV_FIELDS.items():
        raw = env.get(var)
        if not raw:
            continue
        if field == "hcl_hosts":
            layer[field] = [host.strip() for host in raw.split(",") if host.strip()]
        elif field == "min_interval":
            try:
                layer[field] = float(raw)
            except ValueError as exc:
                raise ValueError(f"{var}: cannot parse {raw!r} as a float") from exc
        elif field == "page_size":
            try:
                layer[field] = int(raw)
            except ValueError as exc:
                raise ValueError(f"{var}: cannot parse {raw!r} as an int") from exc
        elif field == "fetch":
            lowered = raw.strip().lower()
            if lowered not in ("resume", "update", "refresh"):
                raise ValueError(f"{var}: {raw!r} is not a fetch mode (resume, update, refresh)")
            layer[field] = lowered
        elif field == "into":
            layer[field] = Path(raw)
        elif field == "output_dir":
            layer[field] = Path(raw)
        else:
            layer[field] = raw
    return layer


# --- Loading ------------------------------------------------------------


def load_config(
    cli_overrides: Mapping[str, object] | None = None,
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Resolve a `Config` from all four layers, highest wins, merged
    per key: CLI > env > config file > defaults.

    `cli_overrides` is whatever `cli.py` parsed from flags; keys with a
    value of `None` (argparse's default for a flag the user didn't
    pass) are treated as unset, not as an override to `None`.

    `config_path` is the CLI `--config` flag's value, if any — it is
    the top of the file-discovery precedence (see
    `discover_config_file`). `env` defaults to `os.environ`.
    """
    env = env if env is not None else os.environ
    cli_layer = {k: v for k, v in (cli_overrides or {}).items() if v is not None}

    file_path = discover_config_file(explicit=config_path, env=env)
    file_layer = _load_file_layer(file_path) if file_path is not None else {}
    env_layer = parse_env_layer(env)

    merged: dict = {}
    merged.update(file_layer)
    merged.update(env_layer)
    merged.update(cli_layer)

    return Config(**merged)


# --- Secrets --------------------------------------------------------------
#
# Never a Config field, never logged. sspi/kerberos need no stored
# secret (they use the OS session). paste_token reads CONNECTIONS_EXPORT_TOKEN.
# basic reads CONNECTIONS_EXPORT_USER/CONNECTIONS_EXPORT_PASSWORD from the environment,
# or prompts — guarded so it can never block a test: it only prompts
# when stdin is actually a tty, which pytest's captured stdin is not.


def resolve_token(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve a paste-token auth credential from `CONNECTIONS_EXPORT_TOKEN`.
    Returns `None` if unset (or empty) — no prompt, there is nothing
    sensible to prompt for a bearer token with."""
    env = env if env is not None else os.environ
    return env.get("CONNECTIONS_EXPORT_TOKEN") or None


def resolve_basic(
    env: Mapping[str, str] | None = None,
    *,
    allow_prompt: bool = True,
    input_func: Callable[[str], str] = input,
    getpass_func: Callable[[str], str] = getpass.getpass,
) -> tuple[str, str] | None:
    """Resolve `(username, password)` for basic auth: from
    `CONNECTIONS_EXPORT_USER`/`CONNECTIONS_EXPORT_PASSWORD`, else an interactive
    prompt (guarded — see module docstring), else `None`.

    `allow_prompt=False` and the tty guard both exist independently so
    callers (and tests) have two ways to guarantee no blocking I/O.
    """
    env = env if env is not None else os.environ
    username = env.get("CONNECTIONS_EXPORT_USER")
    password = env.get("CONNECTIONS_EXPORT_PASSWORD")
    if username and password:
        return username, password

    if not allow_prompt or not sys.stdin.isatty():
        return None

    username = username or input_func("HCL Connections username: ")
    password = password or getpass_func("HCL Connections password: ")
    if not username or not password:
        return None
    return username, password
