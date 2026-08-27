"""A package with a poor manifest must not become a silent gap.

Some projects ship no licence file, or declare no licence at all. A bundle
that quietly wrote "this distribution shipped no licence file" would look
complete and satisfy nobody's obligation -- the binary still redistributes
that code, and BSD/MIT/Apache all want the text.

So a gap is fatal, and it is closed by research: go to the project's own
source, find the licence, and record it as an override with the URL it came
from and the date it was read. The override is checked in, so the next build
does not have to do the research again and anyone can check the work.
"""

from __future__ import annotations

import json
from importlib import metadata

import pytest

from connections_export import sbom


class _FakeDist:
    """A distribution with a manifest as bad as the ones this exists for."""

    def __init__(self, name="ghostpkg", version="1.2.3", meta=None, files=()):
        self.version = version
        self._meta = {"Name": name, **(meta or {})}
        self.files = list(files)

    @property
    def metadata(self):
        return _FakeMetadata(self._meta)

    def locate_file(self, entry):  # pragma: no cover - no files to locate
        raise FileNotFoundError(entry)


class _FakeMetadata(dict):
    def get_all(self, key, default=None):
        value = self.get(key)
        if value is None:
            return default
        return value if isinstance(value, list) else [value]


@pytest.fixture
def one_bad_package(monkeypatch):
    dist = _FakeDist(meta={"Project-URL": "Source, https://example.com/ghostpkg"})
    monkeypatch.setattr(sbom, "_installed", lambda also=frozenset(): [dist])
    return dist


def test_a_component_with_no_licence_text_fails_the_bundle(one_bad_package, tmp_path):
    with pytest.raises(sbom.SbomError) as excinfo:
        sbom.write_license_bundle(tmp_path)
    assert "ghostpkg" in str(excinfo.value)


def test_the_failure_says_where_to_look(one_bad_package, tmp_path):
    """The person who hits this has to go and read a licence. Telling them the
    project's own URL is the difference between a task and a puzzle."""
    with pytest.raises(sbom.SbomError) as excinfo:
        sbom.write_license_bundle(tmp_path)
    message = str(excinfo.value)
    assert "https://example.com/ghostpkg" in message
    assert "overrides" in message


def test_a_researched_override_closes_the_gap(one_bad_package, tmp_path, monkeypatch):
    overrides = tmp_path / "overrides"
    (overrides / "ghostpkg").mkdir(parents=True)
    (overrides / "ghostpkg" / "LICENSE.txt").write_text("Ghost License 1.0\n", encoding="utf-8")
    (overrides / "ghostpkg" / "source.json").write_text(
        json.dumps(
            {
                "license": "MIT",
                "source_url": "https://example.com/ghostpkg/blob/main/LICENSE",
                "retrieved": "2026-08-23",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sbom, "OVERRIDES_DIR", overrides)

    document = sbom.write_license_bundle(tmp_path / "bundle")

    ghost = next(c for c in document["components"] if c["name"] == "ghostpkg")
    assert ghost["licenses"][0]["license"]["id"] == "MIT"
    text = (tmp_path / "bundle" / "ghostpkg" / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Ghost License 1.0" in text
    assert "https://example.com/ghostpkg/blob/main/LICENSE" in text


def test_the_override_records_where_it_came_from(one_bad_package, tmp_path, monkeypatch):
    """An override is a claim about someone else's licence. Unsourced, it is
    indistinguishable from a guess."""
    overrides = tmp_path / "overrides"
    (overrides / "ghostpkg").mkdir(parents=True)
    (overrides / "ghostpkg" / "LICENSE.txt").write_text("Ghost License 1.0\n", encoding="utf-8")
    (overrides / "ghostpkg" / "source.json").write_text(
        json.dumps({"license": "MIT"}), encoding="utf-8"
    )
    monkeypatch.setattr(sbom, "OVERRIDES_DIR", overrides)

    with pytest.raises(sbom.SbomError) as excinfo:
        sbom.write_license_bundle(tmp_path / "bundle")
    assert "source_url" in str(excinfo.value)


def test_this_environment_has_no_gaps():
    """The real check, against the real dependency set: every component this
    build ships has a licence text that came from the package or from a
    recorded piece of research."""
    assert sbom.license_gaps() == []


def test_a_declared_licence_with_no_text_is_still_a_gap(monkeypatch, tmp_path):
    """ "MIT" in the metadata and no LICENSE file is the common shape of this
    problem, and the one most easily mistaken for compliance."""
    dist = _FakeDist(meta={"License-Expression": "MIT"})
    monkeypatch.setattr(sbom, "_installed", lambda also=frozenset(): [dist])
    assert [gap.name for gap in sbom.license_gaps()] == ["ghostpkg"]


def test_real_distributions_are_what_the_gap_check_walks():
    """A guard that walks nothing passes vacuously."""
    assert len(sbom._installed()) > 10
    assert all(isinstance(d, metadata.Distribution) for d in sbom._installed())
