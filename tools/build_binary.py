"""Freeze the CLI into a single-file executable for people without Python.

Usage:
    uv run python tools/build_binary.py [--source DIR] [--dist DIR] [--name NAME]

Used from two places, so the PyInstaller invocation cannot drift between them:
`tools/release_check.py` builds from the exported tree as its last gate, and
`.github/workflows/binaries.yml` builds the per-platform artifacts. The
executable is inherently platform- and architecture-specific -- a Linux build
does not run on macOS, and an arm64 macOS build does not run on Intel -- so
"all platforms" means one matrix cell each, never one universal file.

What needs help from PyInstaller, and why:

  * Package data (`--collect-data connections_export`). The GUI's static
    console, the vendored pdf.js and paged.js, and the force-include'd
    interchange spec are all located with `Path(__file__).parent`. That
    resolves inside the unpacked bundle only if the files travel with it.
  * uvicorn's submodules (`--collect-submodules uvicorn`). It selects its
    event loop and HTTP protocol implementations by string name, which
    static analysis cannot see, so `serve` would die on first request.
  * markdownify and bs4 (`--collect-submodules`). The Obsidian ingester
    imports `markdownify` lazily and only on the `ingest` path, precisely so
    a user without the extra gets a clear message instead of an ImportError
    at startup. That design means a bundle silently missing it still starts,
    still passes `--help`, and only fails when someone tries to ingest -- so
    the build asserts the module is in the bundle rather than trusting it.

Not bundled: Playwright's browsers. The package travels, the ~150 MB Chromium
does not. Everything works except browser-fidelity PDF export, which asks for
`playwright install chromium` when it is missing -- the same as a pip install.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules that must end up in the bundle, with the feature each one carries.
# Checked against PyInstaller's own analysis table, because the failure mode
# is a working executable that breaks in one command.
REQUIRED_MODULES = {
    "markdownify": "ingest --format obsidian",
    "bs4": "ingest --format obsidian (markdownify's parser)",
    "uvicorn": "serve",
    "fastapi": "serve",
    "lxml": "all Atom parsing",
}


#: What SSPI needs at RUNTIME and no analysis can find. pywin32 imports
#: `win32timezone` from inside `pywintypes`, lazily, when a timestamp is
#: converted -- so nothing imports it anywhere a static analysis looks, it is
#: not collected, and the executable is built, verified and shipped without
#: it. The handshake then dies with "No module named win32timezone", which
#: surfaces as every feed failing to authenticate and therefore as a
#: community that appears to hold nothing: a failure landing nowhere near
#: its cause.
SSPI_RUNTIME_IMPORTS = ("win32timezone",)


class BuildError(Exception):
    """The build failed, or produced an executable that is missing something."""


def _run(cmd: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-30:]
        raise BuildError(f"{' '.join(cmd)} exited {proc.returncode}\n" + "\n".join(tail))
    return proc.stdout


def _importable(interpreter: str, module: str) -> bool:
    """Can the build interpreter import `module`? Decides optional inclusion."""
    return (
        subprocess.run(
            [interpreter, "-c", f"import {module}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _license_bundle(source: Path, interpreter: str, also: list[str] | None = None) -> None:
    """Write the SBOM and licence texts into the tree being frozen.

    Before PyInstaller runs, so `--collect-data connections_export` carries
    them into the executable. Someone handed a single file cannot run
    `pip list` against it, and BSD/MIT/Apache-2.0 all require the licence TEXT
    to travel with a binary -- so `connections-export licenses` has to have
    something to read, and this is what puts it there.

    A component with no licence text fails HERE rather than shipping: the
    build refuses, names the component and its project URL, and the licence
    gets researched and recorded. See `connections_export/sbom.py`.
    """
    # `--into` is explicit rather than left to import resolution: the script
    # resolves its default from the `connections_export` it imports, which is
    # this repository when the build runs from here -- so a build of an export
    # tree would silently rewrite the repository's own bundle, and did once.
    cmd = [
        interpreter,
        str(REPO_ROOT / "tools" / "write_license_bundle.py"),
        "--into",
        str(source / "connections_export" / "_licenses"),
        # The NOTICE in an executable describes an executable: these
        # components' code really is inside this one file, where a wheel only
        # requires them and pip installs them alongside.
        "--artifact",
        "executable",
    ]
    if also:
        cmd += ["--also", ",".join(sorted(also))]
    _run(cmd, cwd=source)


def bundled_documents() -> dict[str, str]:
    """The documents the wheel force-includes, `{source path: target path}`.

    Read from `pyproject.toml` rather than listed again here. These are the
    documents the PROGRAM reads -- the console renders the manual, and
    `write_package` copies the interchange spec into every package it writes
    -- so a document that reaches the wheel and not the executable is a
    feature that works one way you can install this and not the other.
    """
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def place_documents(source: Path) -> list[str]:
    """Copy those documents into `source` at the paths the program looks for.

    Before PyInstaller runs, for the same reason as the licence bundle:
    `--collect-data connections_export` carries what is inside the package
    directory, and in a source tree these live under `docs/` instead. Without
    this the executable serves a console with no manual, answers 404 for the
    spec, and raises writing a package.

    Missing means the build fails. Freezing without one succeeds and runs,
    and the gap shows only when someone opens the Manual tab.
    """
    placed = []
    for relative, target in sorted(bundled_documents().items()):
        origin = source / relative
        if not origin.is_file():
            raise BuildError(
                f"{relative} is not in the tree being frozen, so the executable "
                f"would have no {Path(target).name}.\n"
                "    It is force-included into the wheel, so the program expects "
                "to find it and the freeze must place it too."
            )
        destination = source / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        placed.append(target)
    return placed


def _frozen_distributions(source: Path, bundled: set[str], interpreter: str) -> list[str]:
    """Distributions PyInstaller froze that the wheel's own SBOM does not name.

    PyInstaller's analysis reaches further than `Requires-Dist`: on this
    project it draws in packaging, pygments, pypdf, pytest and setuptools.
    Those are genuinely inside the executable, so the bundle inside it has to
    name them -- otherwise it describes a different artifact than the one it
    is in.
    """
    script = (
        "import json,sys\n"
        "from importlib.metadata import packages_distributions\n"
        "from connections_export import sbom\n"
        "doc = json.loads((sbom.bundled_dir() / sbom.SBOM_FILENAME).read_text())\n"
        "named = {c['name'].lower().replace('_','-') for c in doc['components']}\n"
        "named.add(doc['metadata']['component']['name'].lower())\n"
        "top = packages_distributions()\n"
        "modules = sys.stdin.read().split()\n"
        "missing = sorted({d.lower().replace('_','-') for m in modules\n"
        "                  for d in top.get(m, []) } - named)\n"
        "print(json.dumps(missing))\n"
    )
    # The module names go in on stdin, not as arguments. There are thousands
    # of them on Windows once pywin32 is collected, and a command line has a
    # length limit there -- passing them as argv failed the build with
    # "The filename or extension is too long", which names neither the
    # command nor the reason.
    out = _run([interpreter, "-c", script], cwd=source, stdin="\n".join(sorted(bundled)))
    return json.loads(out.strip().splitlines()[-1])


def _required_modules(importable) -> dict[str, str]:
    """Modules the executable must contain, and what each one breaks.

    On Windows that includes native authentication and everything it needs
    at runtime. `auth_mode` defaults to sspi there, so an executable without
    it cannot authenticate against a real deployment at all: it starts,
    passes `--help`, and fails on the first request. Better a build that
    refuses than a binary that ships broken for the platform most of its
    users are on.
    """
    required = dict(REQUIRED_MODULES)
    if sys.platform != "win32":
        return required
    if not importable("requests_negotiate_sspi"):
        raise BuildError(
            "requests_negotiate_sspi is not installed in the build "
            "environment, so this Windows executable could not do native "
            "SSPI auth -- which is the default auth mode.\n"
            "  Install the extra before building: uv sync --all-extras"
        )
    required["requests_negotiate_sspi"] = "--auth sspi (the default on Windows)"
    for module in SSPI_RUNTIME_IMPORTS:
        required[module] = "--auth sspi at run time (pywin32 imports it lazily)"
    return required


def _bundled_modules(work_dir: Path) -> set[str]:
    """Top-level module names PyInstaller recorded in its analysis table."""
    names: set[str] = set()
    for toc in work_dir.rglob("Analysis-*.toc"):
        for line in toc.read_text(errors="ignore").splitlines():
            for chunk in line.split("'"):
                if chunk and (chunk[0].isalpha() or chunk[0] == "_"):
                    names.add(chunk.split(".")[0])
    return names


def build(
    *,
    source: Path,
    dist: Path,
    name: str = "connections-export",
    python: Path | None = None,
) -> tuple[Path, dict[str, str]]:
    """Build the executable from `source`.

    Returns the executable and the modules it was REQUIRED to contain --
    which is not the constant below, because SSPI is included only where it
    exists. Reporting the constant instead is how the Windows build came to
    claim the same contents as every other platform, hiding whether native
    Windows auth had made it in at all.
    """
    interpreter = str(python) if python else sys.executable
    scratch = dist.parent / f"{name}-build"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    # PyInstaller wants a script, not a module, and the console script the
    # wheel installs is generated at install time -- so write the same
    # two lines it would contain.
    entry = scratch / "entry.py"
    entry.write_text(
        "from connections_export.cli import main\n\nraise SystemExit(main())\n",
        encoding="utf-8",
    )

    # The SBOM and the licence texts, into the tree about to be frozen.
    _license_bundle(source, interpreter)
    # ...and the documents the program itself reads, which live under `docs/`
    # in a source tree and inside the package in a wheel.
    for target in place_documents(source):
        print(f"  bundled {target}")

    work = scratch / "work"
    collect_submodules = ["uvicorn", "markdownify", "bs4"]
    # Native Windows SSPI auth is the whole point of the Windows executable,
    # but the module only exists on Windows and only with the `sspi` extra --
    # so collect and require it exactly when the build environment has it,
    # rather than guessing from the platform.
    required = _required_modules(lambda module: _importable(interpreter, module))
    if sys.platform == "win32":
        collect_submodules.append("requests_negotiate_sspi")
    cmd = [
        interpreter,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(scratch),
        "--collect-data",
        "connections_export",
    ]
    for module in collect_submodules:
        cmd += ["--collect-submodules", module]
    if sys.platform == "win32":
        # Named, because they are imported at run time from inside pywin32
        # and no analysis of the source can see them.
        for module in SSPI_RUNTIME_IMPORTS:
            cmd += ["--hidden-import", module]
    cmd.append(str(entry))
    _run(cmd, cwd=source)

    exe = dist / (f"{name}.exe" if os.name == "nt" else name)
    if not exe.exists():
        raise BuildError(f"PyInstaller produced no executable at {exe}")

    bundled = _bundled_modules(work)
    missing = {m: why for m, why in required.items() if m not in bundled}
    if missing:
        raise BuildError(
            "the executable was built without module(s) it needs:\n"
            + "\n".join(f"  {m:<14} breaks: {why}" for m, why in sorted(missing.items()))
        )

    # Two passes, because the inventory has to describe THIS executable and
    # the only way to know what is in it is to build it. The first pass says
    # what PyInstaller actually froze; if that reaches past the wheel's own
    # dependencies -- it does, by five distributions -- the bundle is rewritten
    # to name them and the executable is built again around it. Adding data
    # files does not change module analysis, so the second pass freezes the
    # same set; that is asserted rather than assumed.
    extra = _frozen_distributions(source, bundled, interpreter)
    if extra:
        _license_bundle(source, interpreter, also=extra)
        _run(cmd, cwd=source)  # the PyInstaller command, unchanged
        again = _bundled_modules(work)
        still_missing = _frozen_distributions(source, again, interpreter)
        if still_missing:
            raise BuildError(
                "after rebuilding the licence bundle, PyInstaller still froze "
                "distributions it does not name:\n"
                + "\n".join(f"  {name}" for name in still_missing)
            )

    # Cheapest end-to-end proof that the bundle unpacks and the CLI wires up.
    _run([str(exe), "--help"])
    # ...and that the licence texts came out the other side. A binary whose
    # `licenses` command finds nothing is a compliance failure that no other
    # check here would notice.
    listing = _run([str(exe), "licenses"])
    if "connections-export" not in listing or "components" not in listing:
        raise BuildError(
            "the executable was built without its licence bundle: `licenses` printed nothing usable"
        )
    return exe, required


def _scratch_copy(dist: Path) -> Path:
    """A throwaway copy of this repository, for the build to write into.

    Excludes what a build has no use for and would spend minutes copying:
    the virtualenv, git's own store, caches, and any previous output.
    """
    scratch = dist.parent / "binary-source"
    if scratch.exists():
        shutil.rmtree(scratch)
    skip = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}
    shutil.copytree(
        REPO_ROOT,
        scratch,
        ignore=lambda directory, names: {n for n in names if n in skip},
        symlinks=True,
    )
    print(f"  building from a copy at {scratch}")
    return scratch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="tree to build from (default: a copy of this repository)",
    )
    parser.add_argument("--dist", type=Path, default=REPO_ROOT / "build" / "binary")
    parser.add_argument("--name", default="connections-export")
    args = parser.parse_args()

    # A build WRITES into the tree it is given: the licence bundle in its
    # executable form, and the documents the program reads. Given the
    # repository itself that rewrites tracked files and leaves three
    # untracked ones behind, so building from here copies first. An explicit
    # `--source` is taken at its word -- the caller chose that tree.
    source = args.source.resolve() if args.source else _scratch_copy(args.dist.resolve())

    try:
        exe, required = build(source=source, dist=args.dist.resolve(), name=args.name)
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    size = exe.stat().st_size / (1024 * 1024)
    print(f"{exe}  ({size:.1f} MiB)")
    print(f"verified: runs --help, bundles {', '.join(sorted(required))}")
    if sys.platform != "win32":
        # Not an omission: the package is Windows-only, and so is SSPI.
        print("not included: requests_negotiate_sspi -- Windows-only, N/A on this platform")
    return 0


if __name__ == "__main__":
    sys.exit(main())
