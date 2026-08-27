"""The interchange model entities construct/validate, and HCL
identifiers (label/source-url/hcl-uuid) live only in `Provenance` -- not
as fields on `DerivedPage`'s own primary shape."""

import pytest
from pydantic import ValidationError

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedPage,
    DerivedVersion,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)

# HCL-specific tokens that must never appear as a *field name* on the
# tool-agnostic entities -- they may appear inside Provenance (the
# sidecar) but nowhere else.
_HCL_TOKENS = ("lsid", "snx", "td_", "tdlabel")


def test_provenance_carries_the_hcl_identifiers():
    prov = Provenance(
        source_url="https://fake/wikis/basic/api/wiki/wiki0/page/p0/entry",
        blob_hash="sha256:deadbeef",
        hcl_id="11111111-1111-4111-8111-111111111111",
        discovered_from="https://fake/wikis/basic/api/wiki/wiki0/nav/feed",
    )
    assert prov.hcl_id == "11111111-1111-4111-8111-111111111111"
    assert prov.blob_hash == "sha256:deadbeef"


def test_provenance_note_is_optional_and_defaults_to_none():
    assert Provenance().note is None


@pytest.mark.parametrize(
    "cls",
    [Provenance, ResolvedAsset, LinkRef, DerivedComment, DerivedVersion, DerivedPage, DerivedWiki],
)
def test_no_hcl_specific_field_names_leak_into_the_model_shape(cls):
    field_names = set(cls.model_fields)
    for token in _HCL_TOKENS:
        assert not any(token in name.lower() for name in field_names), (
            f"{cls.__name__} exposes an HCL-specific field name: {field_names}"
        )


def test_derived_page_id_is_an_opaque_identifier_not_an_hcl_field():
    # DerivedPage.id is documented as "the page uuid, but consumers are
    # told to treat it as an opaque interchange id" -- the field itself
    # carries no HCL-specific name, that's confined to provenance.hcl_id.
    page = DerivedPage(id="p1", provenance=Provenance(hcl_id="p1"))
    assert page.id == "p1"
    assert page.provenance.hcl_id == "p1"
    assert "hcl_id" not in DerivedPage.model_fields


def test_resolved_asset_construction():
    asset = ResolvedAsset(
        original_href="img/diagram.png",
        resolved_url="https://fake/img/diagram.png",
        blob_hash="sha256:abc123",
        present=True,
        scope="same",
    )
    assert asset.present is True
    assert asset.scope == "same"


def test_resolved_asset_scope_is_constrained():
    with pytest.raises(ValidationError):
        ResolvedAsset(original_href="x", resolved_url="x", present=True, scope="not-a-scope")


def test_link_ref_in_export_carries_target_page_id():
    link = LinkRef(
        original_href="p1/entry",
        resolved_url="https://fake/wikis/basic/api/wiki/wiki0/page/p1/entry",
        scope="in_export",
        target_page_id="p1",
    )
    assert link.scope == "in_export"
    assert link.target_page_id == "p1"


def test_link_ref_hcl_deployment_scope_has_no_target_page():
    link = LinkRef(
        original_href="/files/basic/api/library/lib1/document/doc1",
        resolved_url="https://fake/files/basic/api/library/lib1/document/doc1",
        scope="hcl_deployment",
    )
    assert link.scope == "hcl_deployment"
    assert link.target_page_id is None


def test_link_ref_external_keeps_href_verbatim_and_has_no_target_page():
    link = LinkRef(
        original_href="https://example.com", resolved_url="https://example.com", scope="external"
    )
    assert link.target_page_id is None
    assert link.original_href == "https://example.com"


def test_link_ref_scope_is_constrained():
    with pytest.raises(ValidationError):
        LinkRef(original_href="x", scope="not-a-scope")


def test_derived_comment_threading_fields():
    root = DerivedComment(id="c1", author="A", content_html="<p>hi</p>")
    reply = DerivedComment(id="c2", author="B", content_html="<p>re</p>", parent_comment_id="c1")
    assert root.parent_comment_id is None
    assert reply.parent_comment_id == "c1"


def test_derived_version_content_present_flag():
    v = DerivedVersion(id="v1", version_label=1, content_present=False)
    assert v.content_present is False


def test_derived_attachment_wraps_a_resolved_asset():
    attachment = DerivedAttachment(
        id="a1",
        filename="doc.bin",
        content_type="application/octet-stream",
        asset=ResolvedAsset(
            original_href="urn:fake:attachment:a1",
            resolved_url="urn:fake:attachment:a1",
            present=False,
            scope="external",
        ),
    )
    assert attachment.asset.present is False


def test_derived_page_defaults_are_empty_not_missing():
    page = DerivedPage(id="p1")
    assert page.content_html == ""
    assert page.child_ids == []
    assert page.comments == []
    assert page.versions == []
    assert page.attachments == []
    assert page.assets == []
    assert page.links == []
    assert page.tags == []
    assert page.acls == []
    assert page.parent_discrepancy is None
    assert isinstance(page.provenance, Provenance)


def test_derived_page_full_construction_and_ordinal_is_an_int():
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page 1",
        content_html="<p>hi</p>",
        parent_id="root",
        ordinal=2,
        child_ids=["p2", "p3"],
        author="Author",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        tags=["tag1"],
        provenance=Provenance(hcl_id="p1"),
    )
    assert isinstance(page.ordinal, int)
    assert page.child_ids == ["p2", "p3"]


def test_derived_wiki_holds_pages_by_id_and_ordered_roots():
    wiki = DerivedWiki(
        id="w1",
        label="wiki0",
        title="Wiki 0",
        root_page_ids=["p1", "p2"],
        pages={"p1": DerivedPage(id="p1"), "p2": DerivedPage(id="p2")},
    )
    assert wiki.root_page_ids == ["p1", "p2"]
    assert set(wiki.pages) == {"p1", "p2"}


def test_interchange_top_level_construction():
    interchange = Interchange(
        base_url="https://fake",
        source_version="8.0",
        run_id="run-1",
        wikis=[DerivedWiki(id="w1", label="wiki0", title="Wiki 0")],
    )
    # 2, not 1: the v1->v2 break unified four timestamp spellings to
    # created/modified, renamed body_html to content_html, and merged
    # DerivedBlogComment into DerivedComment. Nothing was released, so the
    # rename was free and no migration exists.
    assert interchange.schema_version == 2
    assert interchange.wikis[0].label == "wiki0"


def test_model_rejects_unknown_fields_extra_forbid():
    with pytest.raises(ValidationError):
        DerivedPage(id="p1", not_a_real_field=True)


def test_interchange_round_trips_through_json():
    interchange = Interchange(
        base_url="https://fake",
        wikis=[
            DerivedWiki(
                id="w1",
                label="wiki0",
                title="Wiki 0",
                root_page_ids=["p1"],
                pages={
                    "p1": DerivedPage(
                        id="p1",
                        label="p1",
                        title="Page 1",
                        provenance=Provenance(hcl_id="p1", source_url="https://fake/p1"),
                    )
                },
            )
        ],
    )
    dumped = interchange.model_dump_json()
    reloaded = Interchange.model_validate_json(dumped)
    assert reloaded == interchange


def test_every_container_shares_one_base():
    """Five container types repeated the same five fields, and every consumer
    walked all five shapes by hand."""
    from connections_export.derive import model

    for container in (
        model.DerivedWiki,
        model.DerivedBlog,
        model.DerivedForum,
        model.DerivedFileLibrary,
        model.DerivedRichContent,
    ):
        assert issubclass(container, model.DerivedContainer)


def test_every_item_shares_one_base():
    from connections_export.derive import model

    for item in (
        model.DerivedPage,
        model.DerivedBlogPost,
        model.DerivedForumTopic,
        model.DerivedForumReply,
        model.DerivedFile,
        model.DerivedRichContentPage,
    ):
        assert issubclass(item, model.DerivedItem)


def test_containers_keep_their_domain_names():
    """`wiki.pages` and `forum.topics` are what make the model readable. The
    base gives generic code the SHARED fields, not a generic child name."""
    from connections_export.derive import model

    assert "pages" in model.DerivedWiki.model_fields
    assert "posts" in model.DerivedBlog.model_fields
    assert "topics" in model.DerivedForum.model_fields
    assert "files" in model.DerivedFileLibrary.model_fields


def test_a_comment_is_not_an_item_because_it_carries_no_assets_or_links():
    """A real difference, kept rather than flattened."""
    from connections_export.derive import model

    assert not issubclass(model.DerivedComment, model.DerivedItem)
