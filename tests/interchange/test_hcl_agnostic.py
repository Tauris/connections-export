"""The package is HCL-agnostic: HCL-specific
tokens (`td:`, `lsid`, `snx:`) may appear only inside `provenance`
values in the serialized `interchange.json` -- never in a structural
field. `Provenance.note` is the one field allowed to quote such a
token verbatim, mirroring what `derive/assemble.py`
actually writes there on a hierarchy discrepancy (e.g. "page entry
td:parentUuid=... disagrees with the navigation feed's parent").
"""

import json
from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange, Provenance
from connections_export.interchange.package import write_package

_TOKENS = ("td:", "lsid", "snx:")


def _find_token_leaks(obj, *, in_provenance: bool, path: str = "$") -> list[tuple[str, str]]:
    """Walk a JSON-decoded structure, returning `(path, token)` for
    every occurrence of an HCL-specific token found in a string value
    that is NOT nested under a `"provenance"` key."""
    leaks: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_in_provenance = in_provenance or key == "provenance"
            leaks.extend(
                _find_token_leaks(value, in_provenance=child_in_provenance, path=f"{path}.{key}")
            )
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            leaks.extend(
                _find_token_leaks(item, in_provenance=in_provenance, path=f"{path}[{idx}]")
            )
    elif isinstance(obj, str) and not in_provenance:
        for token in _TOKENS:
            if token in obj:
                leaks.append((path, token))
    return leaks


def _write_and_load_json(tmp_path: Path, interchange: Interchange) -> dict:
    archive = Archive.open(tmp_path / "archive")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at="2026-07-20T00:00:00Z")
    return json.loads((dest / "interchange.json").read_text(encoding="utf-8"))


def test_hcl_tokens_confined_to_provenance_pass(tmp_path):
    """A realistic case, mirroring `derive/assemble.py`'s own
    hierarchy-discrepancy note: HCL-shaped tokens inside
    `provenance.note`/`provenance.hcl_id` are fine and must not trip
    the checker."""
    page = DerivedPage(
        id="p1",
        label="p1",
        title="A perfectly ordinary title",
        content_html="<p>ordinary content, no HCL-isms here</p>",
        provenance=Provenance(
            hcl_id="urn:lsid:ibm.com:td:11111111-1111-4111-8111-111111111111",
            source_url="https://fake/wikis/basic/api/wiki/wiki0/page/p1/entry",
            note=(
                "page entry td:parentUuid='root-uuid' disagrees with the "
                "navigation feed's parent=None; snx:rank scheme mismatch too"
            ),
        ),
    )
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    interchange = Interchange(base_url="https://fake", wikis=[wiki])

    dumped = _write_and_load_json(tmp_path, interchange)

    leaks = _find_token_leaks(dumped, in_provenance=False)
    assert leaks == []


def test_checker_catches_a_token_leaking_outside_provenance(tmp_path):
    """Proves the checker actually bites: a token placed on a
    structural (non-provenance) field must be flagged -- otherwise the
    "passes" test above would be meaningless."""
    page = DerivedPage(
        id="p1",
        label="p1",
        title="td:leaked-into-title",  # deliberately placed on a structural field
        provenance=Provenance(hcl_id="p1"),
    )
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    interchange = Interchange(base_url="https://fake", wikis=[wiki])

    dumped = _write_and_load_json(tmp_path, interchange)

    leaks = _find_token_leaks(dumped, in_provenance=False)
    assert any(token == "td:" for _path, token in leaks)


def test_checker_still_flags_a_leak_nested_inside_a_list():
    """The path-tracking walk must not lose the `in_provenance` flag
    across list boundaries -- a token on a page inside `wikis[0].pages`
    (not under any `provenance` key) must still be caught."""
    data = {
        "wikis": [
            {
                "pages": {
                    "p1": {
                        "title": "lsid leaked here",
                        "provenance": {"note": "lsid safe here"},
                    }
                }
            }
        ]
    }

    leaks = _find_token_leaks(data, in_provenance=False)

    paths = {path for path, _token in leaks}
    assert "$.wikis[0].pages.p1.title" in paths
    assert not any("provenance" in path for path in paths)
