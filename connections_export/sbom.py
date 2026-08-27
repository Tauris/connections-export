"""What is inside the thing you were handed, and under what terms.

Two obligations meet here, and neither is satisfied by the other.

**Naming what ships.** A single-file executable is opaque: someone handed one
cannot run `pip list` against it. So the build writes a CycloneDX SBOM naming
every component, its version and its licence, and bundles it inside.

**Carrying the licence texts.** Almost every licence this project depends on --
BSD, MIT, Apache-2.0 -- requires the licence TEXT to travel with a binary
distribution, not merely the name of the licence. A table of SPDX identifiers
is an inventory, not compliance. So the texts travel too, and
`connections-export licenses --extract DIR` writes them back out.

The vendored JavaScript (pdf.js, paged.js) is here as well. It is not a pip
package, it ships inside the wheel and the executable alike, and an SBOM built
only from installed distributions would omit two things the user is running.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

#: Where the build puts the bundle, relative to the package root, so it travels
#: with `--collect-data connections_export` and resolves the same way under
#: PyInstaller as it does from a wheel.
BUNDLE_DIRNAME = "_licenses"

SBOM_FILENAME = "sbom.cyclonedx.json"
NOTICE_FILENAME = "NOTICE.txt"

#: CycloneDX version this document claims. 1.5 because it is what current
#: consumers (Syft, Dependency-Track, GitHub) all read.
SPEC_VERSION = "1.5"

#: This project's own licence, stated once. `pyproject.toml` carries the same
#: SPDX id; `tests/sbom` asserts they agree, because two spellings of one fact
#: is how they come to disagree.
OWN_LICENSE = "BSD-3-Clause"
OWN_NAME = "connections-export"
#: Where to get the tool. Written into every package and every archive, because
#: those outlive the machine that made them: whoever is handed one years later
#: needs an address, not a search term. Derived from the name rather than
#: written out again, so there is nothing here to fall out of step.
OWN_PACKAGE_URL = f"https://pypi.org/project/{OWN_NAME}/"


#: Researched licences for packages whose own manifest does not carry one.
#: One folder per component: `LICENSE.txt` (the text, as published by the
#: project) and `source.json` (`license`, `source_url`, `retrieved`).
#:
#: Checked in, because it is a claim about someone else's licence: the URL and
#: the date are what make it checkable rather than a guess, and they save the
#: next person doing the same reading.
OVERRIDES_DIR = Path(__file__).resolve().parent.parent / "licenses" / "overrides"


class SbomError(Exception):
    """No SBOM bundle where one was expected, or a licence that could not be
    established -- which is a thing to fix, never a thing to ship past."""


@dataclass(frozen=True)
class Gap:
    """A component whose licence text this build cannot produce.

    `where` is the project's own URL, from its metadata: whoever closes this
    has to go and read a licence, and being told where to look is the
    difference between a task and a puzzle.
    """

    name: str
    version: str
    declared: str
    where: str | None


@dataclass(frozen=True)
class BundleRow:
    """One line of `connections-export licenses`."""

    name: str
    version: str
    license: str
    #: Paths, relative to the bundle, holding this component's licence text.
    texts: tuple[str, ...]


@dataclass(frozen=True)
class Vendored:
    """A component that ships in this repo rather than arriving from pip."""

    name: str
    version: str
    license: str
    #: Where its licence text lives, relative to the package root.
    license_path: str
    homepage: str
    #: Every file this component contributes, relative to the package root.
    #: Named rather than inferred, so a third vendored library cannot arrive
    #: unnoticed: `tests/sbom/test_vendored_assets.py` walks the package for
    #: third-party assets and fails on any that no component claims.
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotShipped:
    """Something the tool uses that this distribution does not contain.

    A NOTICE reads as an account of everything you are running. Where that is
    not true, saying so is the whole value: these components' licences are not
    reproduced here BECAUSE their bytes are not distributed here, and a reader
    who needs them has to be told where they actually come from.
    """

    name: str
    #: What it is and why the tool needs it.
    why: str
    #: How it reaches the user's machine, since it does not arrive with this.
    how: str


#: Used at runtime, never distributed. Data rather than a sentence in the
#: NOTICE, because a second entry would otherwise be added by editing prose --
#: which is how the wording that claimed a wheel "includes" its dependencies
#: came to be wrong in the first place.
NOT_SHIPPED: tuple[NotShipped, ...] = (
    NotShipped(
        name="Chromium",
        why=(
            "Used by the browser-fidelity PDF export, through Playwright. "
            "Playwright is listed above; the browser it drives is not part of "
            "this build"
        ),
        how=(
            "`playwright install chromium`, or point the tool at a browser you "
            "already have. Chromium's own licences are not reproduced here"
        ),
    ),
)

#: Vendored assets, each with the licence file that already sits beside it.
#: Versions are the releases the files were taken from; they change only when
#: the vendored file does.
VENDORED: tuple[Vendored, ...] = (
    Vendored(
        name="pdf.js",
        version="6.2.108",
        license="Apache-2.0",
        license_path="gui/static/vendor/pdf.min.LICENSE",
        homepage="https://mozilla.github.io/pdf.js/",
        # The worker is a second file of one library, not a second library:
        # listing it separately would double-count, and omitting it would
        # leave the file that does the actual rendering unaccounted for.
        files=(
            "gui/static/vendor/pdf.min.mjs",
            "gui/static/vendor/pdf.worker.min.mjs",
            # Our own three-line import shim, not Mozilla's -- but it lives in
            # the vendor directory, so it is claimed here rather than left to
            # look like an unaccounted third-party file.
            "gui/static/vendor/pdfjs-boot.mjs",
        ),
    ),
    Vendored(
        name="paged.js",
        version="0.4.3",
        license="MIT",
        license_path="pdf/vendor/paged.polyfill.LICENSE",
        homepage="https://pagedjs.org/",
        files=("pdf/vendor/paged.polyfill.js",),
    ),
)

#: Filenames inside a `.dist-info` that hold licence text. Checked as a
#: lowercase prefix, because the spelling is not standardised: LICENSE,
#: LICENCE, LICENSE.txt, LICENSE-APACHE, COPYING, NOTICE.
_LICENSE_STEMS = ("license", "licence", "copying", "notice")


def package_root() -> Path:
    return Path(__file__).resolve().parent


def _license_of(dist: metadata.Distribution) -> str:
    """The licence of `dist`, from whichever field carries it.

    Three spellings exist in the wild and a distribution may use any of them:
    PEP 639's `License-Expression`, the older free-text `License`, or only a
    `License::` classifier. Returns `"UNKNOWN"` rather than guessing -- an
    SBOM that invents a licence is worse than one that admits a gap, because
    the gap is actionable and the invention is not.
    """
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression and expression.strip():
        return expression.strip()
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        # "License:: OSI Approved:: BSD License" -> "BSD License"
        return classifiers[0].split("::")[-1].strip()
    free_text = (meta.get("License") or "").strip()
    if free_text and "\n" not in free_text and len(free_text) <= 64:
        return free_text
    if free_text:
        # Some projects paste the entire licence into the field. That is text,
        # not an identifier; the text itself is collected separately.
        return "see licence text"
    return "UNKNOWN"


def _homepage_of(dist: metadata.Distribution) -> str | None:
    meta = dist.metadata
    url = meta.get("Home-page")
    if url:
        return url
    for entry in meta.get_all("Project-URL") or []:
        label, _, value = entry.partition(",")
        if label.strip().lower() in {"homepage", "source", "repository"}:
            return value.strip()
    return None


def _license_files(dist: metadata.Distribution) -> list[Path]:
    """The licence texts `dist` installed, as absolute paths."""
    found: list[Path] = []
    for entry in dist.files or []:
        name = Path(str(entry)).name.lower()
        if not name.startswith(_LICENSE_STEMS):
            continue
        try:
            path = Path(dist.locate_file(entry))
        except Exception:  # noqa: BLE001 - a broken RECORD is not fatal here
            continue
        if path.is_file():
            found.append(path)
    return found


def _override_for(name: str) -> tuple[str, str, str] | None:
    """`(text, spdx id, source url)` researched for `name`, or `None`.

    Raises when an override exists but does not say where it came from: an
    unsourced override is indistinguishable from a guess, and a guess about
    someone else's licence is the one thing this whole module exists to avoid.
    """
    folder = Path(OVERRIDES_DIR) / _safe(name)
    text_path = folder / "LICENSE.txt"
    source_path = folder / "source.json"
    if not text_path.is_file():
        return None
    if not source_path.is_file():
        raise SbomError(
            f"{name}: an override with no source.json. An override is a claim about "
            "someone else's licence; record the source_url it was read from."
        )
    record = json.loads(source_path.read_text(encoding="utf-8"))
    url = (record.get("source_url") or "").strip()
    if not url:
        raise SbomError(
            f"{name}: the override's source.json has no source_url. Record where the "
            "licence text was read from, so the claim can be checked."
        )
    return (
        text_path.read_text(encoding="utf-8", errors="replace"),
        (record.get("license") or "UNKNOWN").strip(),
        url,
    )


def ships_no_code(dist: metadata.Distribution) -> bool:
    """Every file this distribution installs is its own metadata.

    Such a package redistributes nothing, so there is no licence text it
    owes anyone. `pypiwin32` is the case that made this necessary: version
    223 is a 1.6 KB wheel holding only its `dist-info`, whose whole
    substance is `Requires-Dist: pywin32`. It redirects an old name to a
    new one, and the package it points at carries its own licence.

    An override would be the wrong answer for one of these -- it would mean
    recording a licence claim about a package whose author declared none.

    A distribution reporting NO files is not treated as code-free: that is
    far more often a RECORD that could not be read, and excusing it would
    turn every unreadable distribution into an exemption.
    """
    files = list(dist.files or [])
    if not files:
        return False
    return all(".dist-info" in str(entry) or ".egg-info" in str(entry) for entry in files)


def license_gaps(also: frozenset[str] | set[str] = frozenset()) -> list[Gap]:
    """Components whose licence text cannot be produced from the package or
    from recorded research.

    "MIT" in the metadata with no LICENSE file beside it is the common shape
    of this, and the one most easily mistaken for compliance: the identifier
    is an inventory entry, and what the licence asks you to redistribute is
    the text.
    """
    gaps: list[Gap] = []
    for dist in _installed(also):
        name = (dist.metadata["Name"] or "").strip()
        if _license_files(dist) or _override_for(name) or ships_no_code(dist):
            continue
        gaps.append(
            Gap(
                name=name,
                version=dist.version,
                declared=_license_of(dist),
                where=_homepage_of(dist),
            )
        )
    return gaps


def _raise_for_gaps(gaps: list[Gap]) -> None:
    lines = [
        "these components ship no licence text, and none has been researched:",
        "",
    ]
    for gap in gaps:
        lines.append(f"  {gap.name} {gap.version} -- declared {gap.declared}")
        lines.append(f"      look here: {gap.where or 'no project URL in its metadata'}")
    lines += [
        "",
        "Find the licence in the project's own source, then record it under",
        f"  {OVERRIDES_DIR}/<name>/LICENSE.txt   (the text, verbatim)",
        f"  {OVERRIDES_DIR}/<name>/source.json   ({{license, source_url, retrieved}})",
        "",
        "A binary that redistributes this code owes its users that text. Shipping",
        "a note saying the package had none is not the same thing.",
    ]
    raise SbomError("\n".join(lines))


def runtime_closure() -> set[str]:
    """Every distribution this project ships, transitively, lower-cased.

    From `Requires-Dist` rather than from the environment, because the
    environment also holds pytest, ruff and PyInstaller -- none of which is in
    the wheel or the executable, and one of which is GPLv2. An SBOM that named
    GPLv2 tooling as a component of the product would misstate the terms the
    product comes under, which is the one thing an SBOM must not do.

    Extras are included: the Windows executable is built with `sspi` (native
    auth is the point of it) and the frozen build bundles `obsidian`, so their
    dependencies genuinely ship. A dependency whose environment marker
    excludes this platform is skipped -- it is not in this build.
    """
    from packaging.requirements import Requirement  # noqa: PLC0415

    closure: set[str] = set()
    #: (distribution, is this project's own requirement list). The extras
    #: allowance applies ONLY to the first: this project's `sspi` and
    #: `obsidian` extras genuinely ship, while a dependency's own `test` or
    #: `dev` extra does not. Applying it everywhere pulled setuptools'
    #: `pytest; extra == "test"` into the SBOM of a shipped product.
    frontier: list[tuple[str, bool]] = [(OWN_NAME, True)]
    while frontier:
        current, ours = frontier.pop()
        try:
            requires = metadata.requires(current) or []
        except metadata.PackageNotFoundError:
            continue
        for raw in requires:
            requirement = Requirement(raw)
            name = requirement.name.lower().replace("_", "-")
            if name in closure:
                continue
            marker = requirement.marker
            if marker is not None:
                extras = _extras_named(marker) if ours else set()
                if extras:
                    if not any(marker.evaluate({"extra": extra}) for extra in extras):
                        continue
                elif not marker.evaluate():
                    continue
            closure.add(name)
            frontier.append((requirement.name, False))
    return closure


def _extras_named(marker) -> set[str]:
    """The `extra == "..."` values a marker mentions, if any."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, list | tuple):
            for item in node:
                walk(item)
        elif hasattr(node, "value"):
            found.add(str(node.value))

    try:
        walk(marker._markers)  # noqa: SLF001 - packaging exposes no public reader
    except Exception:  # noqa: BLE001 - a marker we cannot read is judged normally
        return set()
    # Only meaningful when the marker actually tests `extra`.
    return found if "extra" in str(marker) else set()


def _installed(also: frozenset[str] | set[str] = frozenset()) -> list[metadata.Distribution]:
    """Every installed distribution this project SHIPS, deduplicated by name --
    a venv can expose the same distribution twice, and it also holds a great
    deal that never leaves the developer's machine."""
    ships = runtime_closure() | {name.lower().replace("_", "-") for name in also}
    seen: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name or name.lower() == OWN_NAME:
            continue
        if name.lower().replace("_", "-") not in ships:
            continue  # development tooling: in this venv, not in the product
        seen.setdefault(name.lower(), dist)
    return [seen[key] for key in sorted(seen)]


def _component(dist: metadata.Distribution) -> dict:
    name = dist.metadata["Name"].strip()
    override = _override_for(name)
    # A researched licence outperforms a bad manifest, which is the whole
    # reason the override exists -- so it wins, and it wins as an SPDX id.
    licence = (
        {"id": override[1]}
        if override and override[1] != "UNKNOWN"
        else {"name": _license_of(dist)}
    )
    component = {
        "type": "library",
        "name": name,
        "version": dist.version,
        "purl": f"pkg:pypi/{name.lower()}@{dist.version}",
        "licenses": [{"license": licence}],
    }
    homepage = _homepage_of(dist)
    if homepage:
        component["externalReferences"] = [{"type": "website", "url": homepage}]
    return component


def _vendored_component(item: Vendored) -> dict:
    return {
        "type": "library",
        "name": item.name,
        "version": item.version,
        "purl": f"pkg:generic/{item.name}@{item.version}",
        "licenses": [{"license": {"id": item.license}}],
        "externalReferences": [{"type": "website", "url": item.homepage}],
        # Said plainly: this one is in the repository, not resolved by pip, so
        # a reader is not left wondering why `pip download` cannot find it,
        # and naming the files says exactly what it covers.
        "description": "vendored in this repository: " + ", ".join(item.files),
    }


def own_version() -> str:
    try:
        return metadata.version(OWN_NAME)
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def build_sbom(*, also: frozenset[str] | set[str] = frozenset()) -> dict:
    """The SBOM for this environment, as a CycloneDX 1.5 document.

    The subject is this tool; the components are what it ships with. Built
    from the environment rather than from a hand-written list, because a
    hand-written list is a thing that goes stale silently.
    """
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": OWN_NAME,
                "version": own_version(),
                "purl": f"pkg:pypi/{OWN_NAME}@{own_version()}",
                "licenses": [{"license": {"id": OWN_LICENSE}}],
            },
        },
        "components": [_component(dist) for dist in _installed(also)]
        + [_vendored_component(item) for item in VENDORED],
    }


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in name)


def collect_license_texts(
    destination: Path,
    *,
    strict: bool = True,
    also: frozenset[str] | set[str] = frozenset(),
) -> dict[str, list[str]]:
    """Write every component's licence text under `destination`.

    Returns `{component name: [paths relative to destination]}`.

    `also` names distributions to include beyond the runtime closure. A frozen
    build needs it: PyInstaller's analysis reaches further than `Requires-Dist`
    -- it drew packaging, pygments, pypdf, pytest and setuptools into the
    executable -- and a bundle inside that executable which named only the
    wheel's dependencies would describe a different artifact than the one it
    is inside.

    `strict` raises `SbomError` when any component has neither a licence file
    of its own nor a researched override. That is the point of the function:
    a bundle that quietly noted "this package shipped no licence" would look
    complete and satisfy nobody -- the binary still redistributes that code.
    Pass `strict=False` only to SEE the gaps while closing them; the build and
    the release gate never do.
    """
    destination = Path(destination)
    if strict:
        gaps = license_gaps(also)
        if gaps:
            _raise_for_gaps(gaps)
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, list[str]] = {}

    own = destination / OWN_NAME / "LICENSE"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(_own_license_text(), encoding="utf-8")
    written[OWN_NAME] = [str(own.relative_to(destination))]

    for dist in _installed(also):
        name = dist.metadata["Name"].strip()
        folder = destination / _safe(name)
        paths: list[str] = []
        for source in _license_files(dist):
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / _safe(source.name)
            target.write_bytes(source.read_bytes())
            paths.append(str(target.relative_to(destination)))
        if not paths:
            # No licence file in the package. Not a stub -- a researched
            # override, or a gap the caller is about to be told about.
            override = _override_for(name)
            if override is not None:
                text, spdx, url = override
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / "LICENSE.txt"
                target.write_text(
                    f"{name} {dist.version} -- {spdx}\n"
                    f"This distribution ships no licence file. The text below was read from\n"
                    f"the project's own source: {url}\n"
                    f"{'-' * 70}\n\n" + text,
                    encoding="utf-8",
                )
                paths.append(str(target.relative_to(destination)))
        written[name] = paths

    root = package_root()
    for item in VENDORED:
        folder = destination / _safe(item.name)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "LICENSE.txt"
        source = root / item.license_path
        target.write_text(
            source.read_text(encoding="utf-8", errors="replace")
            if source.is_file()
            else f"{item.name} {item.version}\nLicence: {item.license}\n",
            encoding="utf-8",
        )
        written[item.name] = [str(target.relative_to(destination))]

    return written


def _own_license_text() -> str:
    """This project's LICENSE, from the repository when it is there and from
    the SPDX id when it is not (an installed wheel keeps it in `.dist-info`)."""
    candidates = [package_root().parent / "LICENSE"]
    try:
        own = metadata.distribution(OWN_NAME)
        candidates += _license_files(own)
    except metadata.PackageNotFoundError:
        pass
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate).read_text(encoding="utf-8", errors="replace")
    return f"{OWN_NAME}\nLicence: {OWN_LICENSE} (BSD 3-Clause License)\n"


#: How each artifact honestly describes its relationship to its components.
#: A wheel does not contain anyio -- pip installs it alongside, with its own
#: licence file. A frozen executable does contain it, in the literal sense of
#: those bytes being inside this one file. The same sentence cannot be true of
#: both, and a compliance document that overstates what it is describing is
#: worse than one that says less.
_ARTIFACT_WORDING = {
    "package": (
        "This is the connections-export Python package.",
        "",
        "It requires the components below, which pip installs alongside it --",
        "they are not part of this package. Their licence texts are reproduced",
        "here so that the list travels with it, each under its own folder.",
    ),
    "executable": (
        "This is the connections-export executable.",
        "",
        "It contains the components below: their code is inside this one file,",
        "put there when it was frozen. Each one's licence text is reproduced",
        "here, under the component's own folder.",
    ),
}


def _notice(document: dict, texts: dict[str, list[str]], artifact: str = "package") -> str:
    wording = _ARTIFACT_WORDING.get(artifact, _ARTIFACT_WORDING["package"])
    lines = [
        f"{OWN_NAME} {document['metadata']['component']['version']}",
        f"Licensed under {OWN_LICENSE}. See {OWN_NAME}/LICENSE.",
        "",
        *wording,
        "",
    ]
    for component in document["components"]:
        entry = component["licenses"][0]["license"]
        licence = entry.get("id") or entry.get("name")
        paths = texts.get(component["name"], [])
        lines.append(f"  {component['name']} {component['version']} -- {licence}")
        for path in paths:
            lines.append(f"      {path}")
    for item in NOT_SHIPPED:
        lines += ["", f"Not included: {item.name}"]
        # Wrapped to match the rest of the file: a NOTICE is read in a
        # terminal and in a text box, neither of which reflows.
        for label, body in (("What it is", item.why), ("How to get it", item.how)):
            lines.append(
                textwrap.fill(
                    f"{label}: {body}.",
                    width=72,
                    initial_indent="  ",
                    subsequent_indent="    ",
                )
            )
    lines.append("")
    lines.append(f"A machine-readable inventory is in {SBOM_FILENAME} (CycloneDX).")
    return "\n".join(lines) + "\n"


def write_license_bundle(
    destination: Path,
    *,
    also: frozenset[str] | set[str] = frozenset(),
    artifact: str = "package",
) -> dict:
    """Write the SBOM, the licence texts and a NOTICE into `destination`.

    This is what the build bundles into the executable and what
    `connections-export licenses` reads back.

    `artifact` decides how the NOTICE describes the relationship: a package
    REQUIRES its components, an executable CONTAINS them. The same sentence
    cannot be true of both.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    document = build_sbom(also=also)
    texts = collect_license_texts(destination, also=also)
    (destination / SBOM_FILENAME).write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (destination / NOTICE_FILENAME).write_text(_notice(document, texts, artifact), encoding="utf-8")

    # Drop what no longer ships. A dependency removed -- or reclassified as
    # build tooling, which is how a GPLv2 PyInstaller notice came to sit in a
    # bundle for a BSD product -- must not leave its licence folder behind for
    # the next executable to carry.
    keep = {_safe(name) for name in texts} | {SBOM_FILENAME, NOTICE_FILENAME}
    for child in destination.iterdir():
        if child.name in keep:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return document


def render_all_texts(source: Path) -> str:
    """Every licence in the bundle, as one text.

    What `connections-export licenses --texts` prints and what the console
    offers as a download -- one renderer, because two would drift and an
    auditor comparing the file with the terminal output is exactly who would
    find it.
    """
    source = Path(source)
    parts = [(source / NOTICE_FILENAME).read_text(encoding="utf-8").rstrip()]
    for row in read_bundle(source):
        for relative in row.texts:
            parts += [
                "",
                "=" * 78,
                f"{row.name} {row.version} -- {row.license}  ({relative})",
                "=" * 78,
                (source / relative).read_text(encoding="utf-8", errors="replace").rstrip(),
            ]
    return "\n".join(parts) + "\n"


def bundled_dir() -> Path:
    """Where the bundle lives in an installed package or a frozen executable."""
    return package_root() / BUNDLE_DIRNAME


def read_bundle(source: Path) -> list[BundleRow]:
    """Every component in the bundle at `source`, for display.

    Raises `SbomError` when there is no bundle there -- which is a real state
    (a source checkout that has never run the build) and one worth naming
    rather than showing an empty table that reads as "no dependencies".
    """
    source = Path(source)
    document_path = source / SBOM_FILENAME
    if not document_path.is_file():
        raise SbomError(f"no SBOM in {source}: expected {SBOM_FILENAME}")
    document = json.loads(document_path.read_text(encoding="utf-8"))
    rows: list[BundleRow] = []
    subject = document.get("metadata", {}).get("component")
    for component in ([subject] if subject else []) + document.get("components", []):
        entry = (component.get("licenses") or [{}])[0].get("license", {})
        folder = source / _safe(component["name"])
        texts = (
            tuple(sorted(str(p.relative_to(source)) for p in folder.iterdir() if p.is_file()))
            if folder.is_dir()
            else ()
        )
        rows.append(
            BundleRow(
                name=component["name"],
                version=component.get("version", ""),
                license=entry.get("id") or entry.get("name") or "UNKNOWN",
                texts=texts,
            )
        )
    return rows
