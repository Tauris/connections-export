"""Version and build provenance, for display in the console.

`own_version()` gives the package version — but a real release and a throwaway
test build share the same version, so the version alone cannot tell them apart.
The binaries workflow writes a `_build_stamp.json` data file into the package
recording the commit and git ref it built from (a data file, so PyInstaller's
`--collect-data connections_export` carries it into the executable); this reads
it and turns it into a channel and a human label:

- **release** — built from a `v*` tag: shown as just `v0.1.6`.
- **test** — built from a branch (a downloadable test build): shown with the
  short commit, `v0.1.6 · test build · 0b83bb3`, so it can never be mistaken
  for an official release.
- **dev** — a source checkout with no stamp: `v0.1.6 · dev`.
"""

from __future__ import annotations

import json
from importlib.resources import files

from connections_export.sbom import own_version


def _read_stamp() -> dict:
    """The build stamp the CI wrote, or `{}` from a source checkout."""
    try:
        raw = files("connections_export").joinpath("_build_stamp.json").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ModuleNotFoundError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "commit": str(data.get("commit") or "").strip(),
        "ref": str(data.get("ref") or "").strip(),
    }


def build_info(stamp: dict | None = None) -> dict:
    """`{version, commit, ref, channel, label}` for the running build.

    `stamp` is injectable for tests; in normal use it is read from the
    CI-written `_build_stamp` module (absent → a dev checkout).
    """
    stamp = _read_stamp() if stamp is None else stamp
    version = own_version()
    commit = (stamp.get("commit") or "")[:7]
    ref = stamp.get("ref") or ""

    if not commit:
        channel = "dev"
    elif ref.startswith("refs/tags/v"):
        channel = "release"
    else:
        channel = "test"

    if channel == "release":
        label = f"v{version}"
    elif channel == "test":
        label = f"v{version} · test build · {commit}"
    else:
        label = f"v{version} · dev"

    return {
        "version": version,
        "commit": commit,
        "ref": ref,
        "channel": channel,
        "label": label,
    }
