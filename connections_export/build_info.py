"""Version and build provenance, for display in the console.

`own_version()` gives the package version — but a real release and a throwaway
test build share the same version, so the version alone cannot tell them apart.
The binaries workflow writes a `_build_stamp.json` data file into the package
recording the commit and git ref it built from (a data file, so PyInstaller's
`--collect-data connections_export` carries it into the executable); this reads
it and turns it into a channel and a human label:

- **release** — built from a `v*` tag, OR installed normally from a wheel (a
  PyPI install has no stamp but is a real release): shown as just `v0.1.6`.
- **test** — built from a branch (a downloadable test build): shown with the
  short commit, `v0.1.6 · test build · 0b83bb3`, so it can never be mistaken
  for an official release.
- **dev** — a source/editable checkout, or a copy that cannot name its own
  version: `v0.1.6 · dev`.

A wheel install carries no `_build_stamp.json` (only the executables do), so a
stamp's absence must NOT mean "dev": that would label every `pip install` of a
release "· dev". An editable checkout is what "dev" means, and PEP 610's
`direct_url.json` is how it is told apart from an installed wheel.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, distribution
from importlib.resources import files

from connections_export.sbom import OWN_NAME, own_version


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


def _is_editable_install() -> bool:
    """True for a source / `pip install -e` checkout, via PEP 610.

    An installed wheel has no `direct_url.json`, or one that is not editable; an
    editable install records `dir_info.editable == true`.
    """
    try:
        raw = distribution(OWN_NAME).read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return False
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    return bool(isinstance(data, dict) and data.get("dir_info", {}).get("editable"))


def build_info(stamp: dict | None = None, *, editable: bool | None = None) -> dict:
    """`{version, commit, ref, channel, label}` for the running build.

    `stamp` and `editable` are injectable for tests; in normal use the stamp is
    read from the CI-written `_build_stamp.json` and `editable` from PEP 610.
    """
    stamp = _read_stamp() if stamp is None else stamp
    version = own_version()
    commit = (stamp.get("commit") or "")[:7]
    ref = stamp.get("ref") or ""
    editable = _is_editable_install() if editable is None else editable

    if commit:
        channel = "release" if ref.startswith("refs/tags/v") else "test"
    elif version.endswith("+unknown") or editable:
        # No stamp AND either no readable version or an editable checkout: dev.
        channel = "dev"
    else:
        # No stamp but a real installed wheel (a PyPI install): a release.
        channel = "release"

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
