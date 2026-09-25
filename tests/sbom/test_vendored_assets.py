"""Third-party JavaScript is not in the SBOM by accident.

pdf.js and paged.js arrive by being committed to this repository, not by pip.
Nothing about a Python packaging tool notices them, so nothing about a Python
packaging tool would notice a third one being added -- and it would ship, in
the wheel and the executable both, with its licence obligations unmet.

This walks the package for third-party assets and asserts each one belongs to
a declared component.
"""

from __future__ import annotations

from connections_export import sbom

#: Extensions that could carry someone else's code or design.
THIRD_PARTY_SUFFIXES = {".js", ".mjs", ".css", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".map"}

#: Files this project wrote. Everything else under those extensions has to
#: belong to a component in `sbom.VENDORED`.
OURS = {
    "connections_export/gui/static/console.js",
    "connections_export/gui/static/console.css",
    # The Hugo starter site's stylesheet, written for it (`ingest.hugo_starter`).
    "connections_export/ingest/hugo_starter_site/static/css/site.css",
}


def _assets() -> list[str]:
    root = sbom.package_root()
    return sorted(
        str(path.relative_to(root.parent)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in THIRD_PARTY_SUFFIXES
        and "__pycache__" not in path.parts
        and sbom.BUNDLE_DIRNAME not in path.parts
    )


def test_every_third_party_asset_belongs_to_a_declared_component():
    declared = {f"connections_export/{path}" for item in sbom.VENDORED for path in item.files}
    unaccounted = [asset for asset in _assets() if asset not in OURS and asset not in declared]
    assert not unaccounted, (
        "third-party assets with no component in sbom.VENDORED: "
        f"{unaccounted}. Add one, with its licence text beside the file."
    )


def test_the_walk_actually_finds_the_vendored_javascript():
    """A guard that enumerates nothing passes vacuously."""
    assets = _assets()
    assert "connections_export/gui/static/vendor/pdf.min.mjs" in assets
    assert "connections_export/pdf/vendor/paged.polyfill.js" in assets


def test_every_declared_file_actually_exists():
    """The other direction: a component naming a file that has been removed
    would keep a licence obligation alive for code that no longer ships."""
    root = sbom.package_root()
    for item in sbom.VENDORED:
        for path in item.files:
            assert (root / path).is_file(), f"{item.name} names a missing file: {path}"
        assert (root / item.license_path).is_file(), item.name


def test_the_worker_is_covered_by_the_same_component_as_the_library():
    """pdf.worker.min.mjs is a second file of one library, not a second
    library. Listing it separately would double-count; omitting it would leave
    the file that does the actual rendering unaccounted for."""
    pdfjs = next(item for item in sbom.VENDORED if item.name == "pdf.js")
    assert "gui/static/vendor/pdf.worker.min.mjs" in pdfjs.files
    assert "gui/static/vendor/pdf.min.mjs" in pdfjs.files


def test_the_declared_version_matches_what_the_vendored_licence_states():
    """A declared version has to be read, not invented. The licence file
    beside each asset states it; that is the source of truth."""
    root = sbom.package_root()
    for item in sbom.VENDORED:
        text = (root / item.license_path).read_text(encoding="utf-8", errors="replace")
        assert item.version in text, (
            f"{item.name} is declared as {item.version}, which its own licence "
            f"file ({item.license_path}) does not mention"
        )


def test_the_bundle_carries_the_javascript_licences(tmp_path):
    written = sbom.collect_license_texts(tmp_path)
    for item in sbom.VENDORED:
        assert item.name in written
        text = (tmp_path / written[item.name][0]).read_text(encoding="utf-8")
        assert text.strip()


#: What a licence's actual text must contain, by SPDX id. Short, distinctive
#: phrases from the licence bodies themselves -- not their titles, which a
#: note ABOUT the licence would also contain.
LICENCE_BODY_MARKERS = {
    "Apache-2.0": ("TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",),
    "MIT": ("Permission is hereby granted, free of charge",),
    "BSD-3-Clause": ("Redistribution and use in source and binary forms",),
}


def test_each_vendored_licence_file_holds_the_licence_and_not_a_note_about_it():
    """The pdf.js file was sixteen lines of our own vendoring notes saying
    "Licensed under the Apache License, Version 2.0" and pointing at a "full
    text" that was generated from that very file. The bundle therefore shipped
    no Apache text at all, while the page showed a row claiming it did.

    A title is not a licence. This looks for the body."""
    root = sbom.package_root()
    for item in sbom.VENDORED:
        text = (root / item.license_path).read_text(encoding="utf-8", errors="replace")
        markers = LICENCE_BODY_MARKERS.get(item.license)
        assert markers, f"no body marker known for {item.license} ({item.name})"
        for marker in markers:
            assert marker in text, (
                f"{item.name}'s licence file does not contain the {item.license} text -- "
                f"expected to find {marker!r}"
            )


def test_a_vendored_licence_file_is_long_enough_to_be_one():
    """A blunt second check: no real licence is a handful of lines, and the
    failure mode here was exactly a short file that read like one."""
    root = sbom.package_root()
    for item in sbom.VENDORED:
        lines = (
            (root / item.license_path).read_text(encoding="utf-8", errors="replace").splitlines()
        )
        assert len(lines) > 20, f"{item.name}'s licence file is {len(lines)} lines"


def test_the_bundled_copy_carries_the_body_too():
    """What ends up in the distribution, not just what sits in the repo."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path

        written = sbom.collect_license_texts(Path(tmp))
        for item in sbom.VENDORED:
            text = (Path(tmp) / written[item.name][0]).read_text(encoding="utf-8")
            for marker in LICENCE_BODY_MARKERS[item.license]:
                assert marker in text, f"{item.name}: the bundled copy lost the licence body"
