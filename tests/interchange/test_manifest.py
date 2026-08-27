"""`CapabilityMap`/`PackageManifest` derived from an
`Interchange` -- never asserted. Every scenario here builds a minimal
model by hand (no crawl/derive needed) and checks what `build_manifest`
concludes from it (PackageManifest & capabilities).
"""

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedPage,
    DerivedVersion,
    DerivedWiki,
    Interchange,
    Provenance,
    ResolvedAsset,
)
from connections_export.interchange.manifest import CapabilityMap, PackageManifest, build_manifest

# --- helpers ----------------------------------------------------------


def _asset(*, present: bool, blob_hash: str | None, scope: str = "same") -> ResolvedAsset:
    return ResolvedAsset(
        original_href="img/x.png",
        resolved_url="https://fake/img/x.png",
        blob_hash=blob_hash,
        present=present,
        scope=scope,
    )


def _page(id: str, **kwargs) -> DerivedPage:
    return DerivedPage(id=id, provenance=Provenance(hcl_id=id), **kwargs)


def _wiki(id: str, pages: dict[str, DerivedPage]) -> DerivedWiki:
    return DerivedWiki(
        id=id, label=f"wiki-{id}", title=f"Wiki {id}", root_page_ids=list(pages), pages=pages
    )


def _interchange(*wikis: DerivedWiki) -> Interchange:
    return Interchange(base_url="https://fake", wikis=list(wikis))


# --- counts -------------------------------------------------------------


def test_counts_reflect_wikis_pages_comments_versions_attachments():
    page1 = _page(
        "p1",
        comments=[DerivedComment(id="c1"), DerivedComment(id="c2")],
        versions=[DerivedVersion(id="v1")],
        attachments=[
            DerivedAttachment(
                id="a1",
                filename="f.bin",
                asset=_asset(present=True, blob_hash="sha256:aaa"),
            )
        ],
    )
    page2 = _page("p2", comments=[DerivedComment(id="c3")])
    interchange = _interchange(_wiki("w1", {"p1": page1, "p2": page2}))

    manifest = build_manifest(interchange)

    assert manifest.counts.wikis == 1
    assert manifest.counts.pages == 2
    assert manifest.counts.comments == 3
    assert manifest.counts.versions == 1
    assert manifest.counts.attachments == 1


def test_blob_count_is_the_deduped_set_of_present_asset_hashes():
    page1 = _page(
        "p1",
        assets=[
            _asset(present=True, blob_hash="sha256:aaa"),
            _asset(present=True, blob_hash="sha256:aaa"),  # same hash, deduped
            _asset(present=True, blob_hash="sha256:bbb"),
        ],
    )
    page2 = _page(
        "p2",
        attachments=[
            DerivedAttachment(
                id="a1", filename="f.bin", asset=_asset(present=True, blob_hash="sha256:bbb")
            ),
            DerivedAttachment(
                id="a2", filename="g.bin", asset=_asset(present=False, blob_hash=None)
            ),
        ],
    )
    interchange = _interchange(_wiki("w1", {"p1": page1, "p2": page2}))

    manifest = build_manifest(interchange)

    assert manifest.counts.blobs == 2  # sha256:aaa, sha256:bbb


def test_multiple_wikis_are_summed():
    interchange = _interchange(
        _wiki("w1", {"p1": _page("p1")}),
        _wiki("w2", {"p2": _page("p2"), "p3": _page("p3")}),
    )

    manifest = build_manifest(interchange)

    assert manifest.counts.wikis == 2
    assert manifest.counts.pages == 3


# --- comment_threading ----------------------------------------------------


def test_comment_threading_is_flat_when_no_comment_has_a_parent():
    page = _page("p1", comments=[DerivedComment(id="c1"), DerivedComment(id="c2")])
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.comment_threading == "flat"


def test_comment_threading_is_present_when_any_comment_has_a_parent():
    page = _page(
        "p1",
        comments=[
            DerivedComment(id="c1"),
            DerivedComment(id="c2", parent_comment_id="c1"),
        ],
    )
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.comment_threading == "present"


def test_comment_threading_is_flat_when_there_are_no_comments_at_all():
    interchange = _interchange(_wiki("w1", {"p1": _page("p1")}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.comment_threading == "flat"


# --- acls -----------------------------------------------------------------


def test_acls_absent_when_no_page_carries_any():
    interchange = _interchange(_wiki("w1", {"p1": _page("p1")}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.acls == "absent"


def test_acls_present_when_any_page_carries_one():
    page = _page("p1", acls=["Everyone"])
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.acls == "present"


# --- versions ---------------------------------------------------------


def test_versions_none_when_no_page_has_any_version():
    interchange = _interchange(_wiki("w1", {"p1": _page("p1")}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.versions == "none"


def test_versions_list_when_versions_exist_but_none_carry_content():
    page = _page("p1", versions=[DerivedVersion(id="v1", content_present=False)])
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.versions == "list"


def test_versions_full_when_at_least_one_version_carries_content():
    page = _page(
        "p1",
        versions=[
            DerivedVersion(id="v1", content_present=False),
            DerivedVersion(id="v2", content_present=True),
        ],
    )
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.versions == "full"


# --- assets / assets_not_present -------------------------------------------


def test_assets_capability_is_always_resolved():
    interchange = _interchange(_wiki("w1", {"p1": _page("p1")}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.assets == "resolved"


def test_assets_not_present_counts_body_and_attachment_assets_not_captured():
    page = _page(
        "p1",
        assets=[
            _asset(present=True, blob_hash="sha256:aaa"),
            _asset(present=False, blob_hash=None),
        ],
        attachments=[
            DerivedAttachment(
                id="a1", filename="f.bin", asset=_asset(present=False, blob_hash=None)
            )
        ],
    )
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.assets_not_present == 2


def test_assets_not_present_is_zero_when_everything_was_captured():
    page = _page("p1", assets=[_asset(present=True, blob_hash="sha256:aaa")])
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)

    assert manifest.capabilities.assets_not_present == 0


# --- schema_version / generator --------------------------------------------


def test_manifest_carries_schema_version_and_a_generator_string():
    interchange = _interchange(_wiki("w1", {"p1": _page("p1")}))

    manifest = build_manifest(interchange)

    assert manifest.schema_version == interchange.schema_version
    assert isinstance(manifest.generator, str) and manifest.generator


def test_manifest_and_capability_map_round_trip_through_json():
    page = _page(
        "p1",
        comments=[DerivedComment(id="c1"), DerivedComment(id="c2", parent_comment_id="c1")],
    )
    interchange = _interchange(_wiki("w1", {"p1": page}))

    manifest = build_manifest(interchange)
    reloaded = PackageManifest.model_validate_json(manifest.model_dump_json())

    assert reloaded == manifest
    assert isinstance(reloaded.capabilities, CapabilityMap)
