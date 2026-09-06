"""An SBOM of what actually ships, with each component's licence.

Two obligations meet here. A user handed a single-file executable must be able
to find out what is inside it and under which terms, and most of those terms
(BSD, MIT, Apache-2.0) require the licence TEXT to travel with the binary --
not a name in a table. So the SBOM names the components and the licence texts
travel beside it, both readable from the frozen executable itself.
"""

from __future__ import annotations

import json

import pytest

from connections_export import sbom


def test_the_sbom_is_cyclonedx_json():
    doc = sbom.build_sbom()
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"]
    assert doc["components"]


def test_the_tool_itself_is_the_subject_not_a_component():
    doc = sbom.build_sbom()
    assert doc["metadata"]["component"]["name"] == "connections-export"
    assert doc["metadata"]["component"]["licenses"][0]["license"]["id"] == "BSD-3-Clause"
    names = {c["name"] for c in doc["components"]}
    assert "connections-export" not in names


def test_every_component_names_a_version_and_a_purl():
    for component in sbom.build_sbom()["components"]:
        assert component["version"], component["name"]
        assert component["purl"].startswith("pkg:"), component["name"]


def test_a_dependency_that_ships_is_in_it():
    names = {c["name"] for c in sbom.build_sbom()["components"]}
    assert "httpx" in names
    assert "pydantic" in names


def test_the_vendored_javascript_is_a_component_too():
    """pdf.js and paged.js are not pip packages and ship inside the wheel and
    the executable alike. An SBOM that listed only what pip installed would
    omit two things the user is actually running."""
    names = {c["name"] for c in sbom.build_sbom()["components"]}
    assert "pdf.js" in names
    assert "paged.js" in names


def test_a_component_whose_licence_is_unknown_says_so_rather_than_guessing():
    doc = sbom.build_sbom()
    for component in doc["components"]:
        licenses = component.get("licenses") or []
        assert licenses, component["name"]
        entry = licenses[0]["license"]
        assert entry.get("id") or entry.get("name"), component["name"]


def test_licence_texts_are_collected_for_every_component(tmp_path):
    written = sbom.collect_license_texts(tmp_path)
    assert written
    for component in sbom.build_sbom()["components"]:
        assert component["name"] in written, f"no licence text collected for {component['name']}"
    for paths in written.values():
        for path in paths:
            assert (tmp_path / path).read_text(encoding="utf-8", errors="replace").strip()


def test_the_collected_texts_include_the_projects_own_licence(tmp_path):
    written = sbom.collect_license_texts(tmp_path)
    assert "connections-export" in written
    text = (tmp_path / written["connections-export"][0]).read_text(encoding="utf-8")
    assert "BSD 3-Clause" in text


def test_the_index_is_written_beside_the_texts(tmp_path):
    sbom.write_license_bundle(tmp_path)
    index = json.loads((tmp_path / sbom.SBOM_FILENAME).read_text(encoding="utf-8"))
    assert index["bomFormat"] == "CycloneDX"
    assert (tmp_path / "NOTICE.txt").read_text(encoding="utf-8").strip()


def test_a_bundle_reads_back_the_way_the_command_shows_it(tmp_path):
    sbom.write_license_bundle(tmp_path)
    rows = sbom.read_bundle(tmp_path)
    assert rows
    for row in rows:
        assert row.name and row.version and row.license


def test_reading_a_directory_that_holds_no_bundle_is_an_error(tmp_path):
    with pytest.raises(sbom.SbomError):
        sbom.read_bundle(tmp_path)


def test_build_and_test_tooling_is_not_listed_as_something_that_ships():
    """PyInstaller is GPLv2 and pytest, ruff and the rest are development
    tools. None of them is inside the wheel or the executable, and naming
    GPLv2 tooling as a component of the product misstates the terms the
    product comes under -- which is the one thing an SBOM must not do."""
    names = {c["name"].lower() for c in sbom.build_sbom()["components"]}
    for tool in ("pyinstaller", "pytest", "ruff", "pluggy", "iniconfig"):
        assert tool not in names, f"{tool} is build/test tooling, not a shipped component"


def test_what_ships_is_still_listed():
    names = {c["name"].lower() for c in sbom.build_sbom()["components"]}
    for shipped in ("fastapi", "httpx", "lxml", "pydantic", "uvicorn", "playwright"):
        assert shipped in names, f"{shipped} is a declared runtime dependency"


def test_transitive_runtime_dependencies_are_listed_too():
    """`httpx` brings `httpcore`, which brings `h11`. A wheel that pulls them
    in ships them, whether or not this project names them."""
    names = {c["name"].lower() for c in sbom.build_sbom()["components"]}
    assert "httpcore" in names
    assert "h11" in names


def test_the_optional_extras_are_included_because_a_build_can_enable_them():
    """The Windows executable is built with the sspi extra -- that is the
    whole point of the Windows executable -- so its dependencies ship."""
    closure = sbom.runtime_closure()
    assert "markdownify" in closure, "the obsidian extra is bundled into the executable"


def test_a_rebuilt_bundle_drops_what_no_longer_ships(tmp_path):
    """A dependency removed, or reclassified as tooling, must not leave its
    licence folder behind: the executable would go on carrying a notice for
    something it does not contain -- and in one real case that notice was
    GPLv2."""
    stale = tmp_path / "pyinstaller"
    stale.mkdir()
    (stale / "COPYING.txt").write_text("GPLv2", encoding="utf-8")

    sbom.write_license_bundle(tmp_path)

    assert not stale.exists()


def test_rebuilding_keeps_what_still_ships(tmp_path):
    sbom.write_license_bundle(tmp_path)
    first = sorted(p.name for p in tmp_path.iterdir())
    sbom.write_license_bundle(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == first


def test_a_frozen_build_can_add_what_pyinstaller_actually_drew_in(tmp_path):
    """PyInstaller's analysis reaches further than `Requires-Dist`: it froze
    packaging, pygments, pypdf, pytest and setuptools into the executable,
    none of which the wheel ships. The executable's own bundle has to name
    them, or it describes a different artifact than the one it is inside."""
    document = sbom.build_sbom(also={"pytest", "setuptools"})
    names = {c["name"].lower() for c in document["components"]}
    assert "pytest" in names
    assert "setuptools" in names
    # ...and the wheel's bundle still does not.
    assert "pytest" not in {c["name"].lower() for c in sbom.build_sbom()["components"]}


def test_the_added_components_get_their_licence_texts_too(tmp_path):
    written = sbom.collect_license_texts(tmp_path, also={"pytest"})
    assert "pytest" in written
    assert (tmp_path / written["pytest"][0]).read_text(encoding="utf-8").strip()


def test_a_name_that_is_not_installed_is_ignored_rather_than_invented(tmp_path):
    """A module PyInstaller froze that maps to no distribution is not a
    licence we can state. Silence beats a fabricated entry."""
    document = sbom.build_sbom(also={"not-a-real-distribution"})
    assert "not-a-real-distribution" not in {c["name"] for c in document["components"]}


def test_a_package_does_not_claim_to_contain_its_dependencies(tmp_path):
    """ "This distribution includes anyio" is false of a wheel. pip resolves and
    installs anyio alongside it, with its own licence file; nothing is joined
    into anything. Only the frozen executable literally contains them."""
    sbom.write_license_bundle(tmp_path)
    notice = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert "installs alongside it" in notice
    assert "contains the components" not in notice


def test_an_executable_says_it_contains_them_because_it_does(tmp_path):
    sbom.write_license_bundle(tmp_path, artifact="executable")
    notice = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert "inside this one file" in notice
    assert "installs alongside it" not in notice


def test_the_notice_names_which_artifact_it_describes(tmp_path):
    """The two texts ship in different artifacts and differ in what they
    claim, so each has to say which one it is -- otherwise the reader cannot
    tell whether the one in front of them is the right one."""
    sbom.write_license_bundle(tmp_path)
    package = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    sbom.write_license_bundle(tmp_path, artifact="executable")
    executable = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert package != executable
    assert "Python package" in package
    assert "executable" in executable


def test_the_notice_still_reproduces_every_component_and_its_licence(tmp_path):
    """Whatever the wording, the obligation is the same: the list, the terms,
    and where each text is."""
    document = sbom.write_license_bundle(tmp_path)
    notice = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    for component in document["components"]:
        assert component["name"] in notice


def test_the_notice_names_what_the_tool_uses_but_does_not_ship(tmp_path):
    """Playwright's own code is in the artifact; the ~150 MB Chromium it drives
    never is, in a wheel or an executable. A reader would reasonably take a
    NOTICE to cover everything they run, so what it does NOT cover has to be
    said -- Chromium carries its own licences, and they are not reproduced
    here because those bytes are not distributed here."""
    sbom.write_license_bundle(tmp_path)
    notice = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert "Not included" in notice
    assert "Chromium" in notice
    assert "playwright install chromium" in notice


def test_the_executable_says_it_too_because_it_does_not_ship_one_either(tmp_path):
    sbom.write_license_bundle(tmp_path, artifact="executable")
    notice = (tmp_path / sbom.NOTICE_FILENAME).read_text(encoding="utf-8")
    assert "Not included" in notice
    assert "Chromium" in notice


def test_the_browser_is_not_listed_as_a_component_because_it_is_not_shipped():
    """Naming it as a component would claim it is in the artifact and put its
    licence obligations on this distribution. It is neither."""
    names = {c["name"].lower() for c in sbom.build_sbom()["components"]}
    assert "chromium" not in names
    # ...while the client library, whose code IS inside, stays listed.
    assert "playwright" in names


def test_what_is_not_shipped_is_data_rather_than_a_sentence():
    """A second one would otherwise be added by editing prose, which is how
    the first became wrong."""
    assert sbom.NOT_SHIPPED
    for item in sbom.NOT_SHIPPED:
        assert item.name and item.why and item.how


# --- the subject version is stated, not inferred -----------------------
# The SBOM's subject says what this artifact IS. Read off the INSTALLED
# distribution, it says what the build environment happened to have -- which
# during a release is routinely a version behind. Two published releases
# carried an SBOM and a NOTICE naming an older version because of it, visible
# only to somebody who unzipped the wheel to read a document nobody opens
# until they have to.


def test_the_subject_version_can_be_stated_rather_than_looked_up(tmp_path):
    document = sbom.write_license_bundle(tmp_path, version="9.9.9")

    subject = document["metadata"]["component"]
    assert subject["version"] == "9.9.9"
    assert subject["purl"].endswith("@9.9.9")


def test_the_notice_names_the_stated_version(tmp_path):
    """The NOTICE takes its version from the same document, so a bundle
    cannot end up with the two disagreeing."""
    sbom.write_license_bundle(tmp_path, version="9.9.9")

    assert "connections-export 9.9.9" in (tmp_path / "NOTICE.txt").read_text(encoding="utf-8")


def test_without_one_it_still_describes_what_is_installed(tmp_path):
    """At run time that is the right answer -- `connections-export licenses`
    describes the copy doing the describing -- so the default must not
    change."""
    document = sbom.write_license_bundle(tmp_path)

    assert document["metadata"]["component"]["version"] == sbom.own_version()
