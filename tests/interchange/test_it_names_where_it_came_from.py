"""A package outlives the machine that made it, and says where to get the
tool that reads it.

An archive is meant to be opened years later, by someone who was handed it
rather than someone who made it. `interchange.json` is plain JSON and the
format spec travels inside every package, so the content is readable with
no tool at all -- but "readable in principle" is not the same as knowing
that a program exists which does it for you. A generator string that names
the tool without saying where it lives leaves that person with a search
term.

So the package, and the archive it was built from, both carry an address.
"""

from __future__ import annotations

import json

import pytest

from connections_export import sbom
from connections_export.archive.store import Archive
from connections_export.derive.model import Interchange
from connections_export.interchange.package import (
    MANIFEST_FILENAME,
    PROVENANCE_FILENAME,
    SPEC_COPY_FILENAME,
    write_package,
)


@pytest.fixture
def package_dir(tmp_path):
    """The smallest real package: an empty model still has to say where it
    came from, since an empty capture is exactly the one someone would need
    help reading."""
    archive = Archive.open(tmp_path / "archive")
    dest = tmp_path / "package"
    write_package(Interchange(), archive, dest, generated_at="2026-01-01T00:00:00Z")
    return dest


def test_the_package_index_is_derived_from_the_package_name():
    """Two constants would be one constant and a chance to disagree."""
    assert sbom.OWN_PACKAGE_URL == f"https://pypi.org/project/{sbom.OWN_NAME}/"


def test_the_name_is_the_one_the_project_is_published_under():
    """The address is only useful if it is the real one."""
    import tomllib

    from connections_export.interchange.package import REPO_ROOT

    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return  # an installed copy has no source tree to compare against
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    assert sbom.OWN_NAME == declared


def test_the_manifest_says_what_wrote_the_package_and_where_to_get_it(package_dir):
    manifest = json.loads((package_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))

    assert manifest["generator_url"] == sbom.OWN_PACKAGE_URL
    assert manifest["generator_version"]


def test_the_provenance_says_it_too(package_dir):
    """`provenance.json` is the file about where this package came from. The
    tool that made it is part of that answer."""
    provenance = json.loads((package_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8"))

    assert provenance["generator_url"] == sbom.OWN_PACKAGE_URL
    assert provenance["generator"].startswith(sbom.OWN_NAME)
    assert provenance["generator_version"]


def test_the_spec_that_travels_with_it_says_it_in_words(package_dir):
    """The one file in the package written for a person to read. Whoever
    opens it should not have to work out that a program exists."""
    spec = (package_dir / SPEC_COPY_FILENAME).read_text(encoding="utf-8")

    assert sbom.OWN_PACKAGE_URL in spec


# --- the archive, which is the thing people are actually handed -------------


def test_the_run_record_names_the_tool_and_where_to_get_it(tmp_path):
    """A package is the shareable artefact, but what gets zipped and passed
    around is usually the archive itself. Its own provenance record has to
    answer the same question."""
    from connections_export.archive.records import RunMetadata

    run = RunMetadata(
        run_id="run-1",
        base_url="https://fake",
        principal="someone",
        adapter_version="wikis-1",
        source_system_version="8.0",
    )

    assert run.generator_url == sbom.OWN_PACKAGE_URL
    assert run.generator_version


def test_an_older_record_without_them_still_reads(tmp_path):
    """Archives are long-lived by design. A record written before these
    fields existed has to keep validating, or an old archive stops opening
    for the sake of a URL."""
    from connections_export.archive.records import RunMetadata

    run = RunMetadata.model_validate(
        {
            "run_id": "run-1",
            "base_url": "https://fake",
            "principal": "someone",
            "adapter_version": "wikis-1",
            "source_system_version": "8.0",
        }
    )

    assert run.run_id == "run-1"


def test_the_summary_beside_an_archive_says_it_too():
    """The smallest, most readable file in an archive directory, and so the
    first one a curious person opens."""
    from connections_export.derive.model import Interchange
    from connections_export.gui.archives import model_summary

    summary = model_summary(Interchange())

    assert summary["generator_url"] == sbom.OWN_PACKAGE_URL
