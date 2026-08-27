"""`connections-export licenses` -- what is inside this build, and its terms.

The point of the command is the frozen executable: someone handed a single
file cannot run `pip list` against it, and BSD/MIT/Apache-2.0 all require the
licence text to travel with a binary. So the text has to come back OUT of the
binary, which means a command that reads the bundle inside it.
"""

from __future__ import annotations

import json

from connections_export import sbom
from connections_export.cli import licenses_main


def test_it_lists_every_component_with_its_licence(tmp_path, capsys):
    sbom.write_license_bundle(tmp_path)
    assert licenses_main(["--bundle", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "httpx" in out
    assert "BSD-3-Clause" in out
    assert "pdf.js" in out


def test_extract_writes_the_texts_somewhere_the_user_chose(tmp_path, capsys):
    bundle = tmp_path / "bundle"
    sbom.write_license_bundle(bundle)
    out_dir = tmp_path / "extracted"
    assert licenses_main(["--bundle", str(bundle), "--extract", str(out_dir)]) == 0
    assert (out_dir / sbom.NOTICE_FILENAME).is_file()
    assert (out_dir / sbom.SBOM_FILENAME).is_file()
    assert "BSD 3-Clause" in (out_dir / "connections-export" / "LICENSE").read_text(
        encoding="utf-8"
    )


def test_sbom_prints_the_machine_readable_document(tmp_path, capsys):
    sbom.write_license_bundle(tmp_path)
    assert licenses_main(["--bundle", str(tmp_path), "--sbom"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["bomFormat"] == "CycloneDX"


def test_a_build_with_no_bundle_says_so_rather_than_showing_an_empty_table(tmp_path, capsys):
    assert licenses_main(["--bundle", str(tmp_path)]) == 2
    assert "no SBOM" in capsys.readouterr().err


def test_it_is_wired_into_the_cli():
    from connections_export.cli import _SUBCOMMANDS

    assert "licenses" in _SUBCOMMANDS
