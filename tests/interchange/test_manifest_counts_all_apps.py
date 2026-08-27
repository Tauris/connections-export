"""`manifest.json` must account for every app the package carries.

An ingester reads the manifest to decide what it is dealing with before
touching `interchange.json`. Anything the manifest does not count is invisible
at that stage -- so a package can contain a community's whole file library and
its front page, and announce neither.

Files were added as a fourth app without being counted here; Rich Content is
the fifth. This pins both, and the blob accounting that goes with them.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedFile,
    DerivedFileLibrary,
    DerivedRichContent,
    DerivedRichContentPage,
    Interchange,
    ResolvedAsset,
)
from connections_export.interchange.manifest import build_manifest


def _asset(digest: str, present: bool = True) -> ResolvedAsset:
    return ResolvedAsset(
        original_href=f"https://fake/{digest}",
        resolved_url=f"https://fake/{digest}",
        present=present,
        scope="same",
        blob_hash=f"sha256:{digest}" if present else None,
    )


def _model() -> Interchange:
    return Interchange(
        file_libraries=[
            DerivedFileLibrary(
                id="L",
                title="Files",
                file_ids=["f1", "f2"],
                files={
                    "f1": DerivedFile(id="f1", name="a.pdf", asset=_asset("aaa")),
                    "f2": DerivedFile(id="f2", name="b.pdf", asset=_asset("bbb", present=False)),
                },
            )
        ],
        rich_content=[
            DerivedRichContent(
                id="C",
                title="Highlights",
                page_ids=["p1"],
                pages={
                    "p1": DerivedRichContentPage(
                        id="p1",
                        resource_id="p1",
                        title="Welcome",
                        content_html="<p>hi</p>",
                        assets=[_asset("ccc")],
                    )
                },
                placed=3,
                initialized=1,
            )
        ],
    )


def test_file_libraries_and_files_are_counted():
    counts = build_manifest(_model()).counts

    assert counts.file_libraries == 1
    assert counts.files == 2


def test_rich_content_pages_are_counted():
    counts = build_manifest(_model()).counts

    assert counts.rich_content == 1
    assert counts.rich_content_pages == 1


def test_a_files_blob_is_counted_as_a_blob():
    """`write_package` copies these bytes. A blob count that ignored files
    would understate the package's size by the whole library."""
    counts = build_manifest(_model()).counts

    assert counts.blobs == 2  # the captured file + the rich content image


def test_a_file_that_was_never_captured_is_not_counted_as_present():
    manifest = build_manifest(_model())

    assert manifest.capabilities.assets_not_present >= 1


def test_an_empty_model_counts_zero_rather_than_omitting():
    counts = build_manifest(Interchange()).counts

    assert counts.file_libraries == 0
    assert counts.files == 0
    assert counts.rich_content == 0
    assert counts.rich_content_pages == 0
