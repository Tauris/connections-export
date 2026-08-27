"""Body assets resolved against the archive. A same-
deployment asset with an archived blob resolves present=True; one
with no blob is surfaced as present=False, not dropped; an external
asset is classified scope="external" and never expected to have a
blob."""

from connections_export.archive.store import Archive
from connections_export.derive.assets import resolve_asset, resolve_body_assets
from connections_export.derive.index import ArchiveIndex
from tests.archive.conftest import FakeResponse

BASE_URL = "https://fake"
PAGE_URL = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/media"


def _index(tmp_path, *, archived_urls: dict[str, bytes]) -> ArchiveIndex:
    archive = Archive.open(tmp_path / "archive")
    for url, content in archived_urls.items():
        archive.write_response(
            FakeResponse(url=url, method="GET", status=200, content=content),
            fetched_at="2026-01-01T00:00:00Z",
        )
    return ArchiveIndex.open(archive)


def test_same_deployment_asset_with_blob_resolves_present(tmp_path):
    img_url = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png"
    index = _index(tmp_path, archived_urls={img_url: b"\x89PNG..."})

    asset = resolve_asset(
        "media/img/diagram.png",
        join_base=PAGE_URL,
        boundary_base=BASE_URL,
        hcl_hosts=(),
        index=index,
    )

    assert asset.present is True
    assert asset.scope == "same"
    assert asset.blob_hash is not None
    assert asset.resolved_url == img_url
    assert asset.original_href == "media/img/diagram.png"


def test_same_deployment_asset_without_blob_is_surfaced_not_present(tmp_path):
    index = _index(tmp_path, archived_urls={})

    asset = resolve_asset(
        "media/img/missing.png",
        join_base=PAGE_URL,
        boundary_base=BASE_URL,
        hcl_hosts=(),
        index=index,
    )

    assert asset.present is False
    assert asset.scope == "same"
    assert asset.blob_hash is None


def test_external_asset_is_classified_external(tmp_path):
    index = _index(tmp_path, archived_urls={})

    asset = resolve_asset(
        "https://cdn.example.com/logo.png",
        join_base=PAGE_URL,
        boundary_base=BASE_URL,
        hcl_hosts=(),
        index=index,
    )

    assert asset.scope == "external"
    assert asset.present is False


def test_resolve_body_assets_scans_every_img_in_document_order(tmp_path):
    same_img = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png"
    body = f'<div><img src="{same_img}"/><img src="https://cdn.example.com/logo.png"/></div>'
    index = _index(tmp_path, archived_urls={same_img: b"\x89PNG..."})

    resolved = resolve_body_assets(
        body, page_url=PAGE_URL, base_url=BASE_URL, hcl_hosts=(), index=index
    )

    assert [a.present for a in resolved] == [True, False]
    assert [a.scope for a in resolved] == ["same", "external"]


def test_resolve_body_assets_on_empty_body_is_empty_list(tmp_path):
    index = _index(tmp_path, archived_urls={})

    assert (
        resolve_body_assets("", page_url=PAGE_URL, base_url=BASE_URL, hcl_hosts=(), index=index)
        == []
    )


def test_hcl_hosts_extend_same_deployment_boundary(tmp_path):
    files_img = "https://host/library/lib1/document/doc1/media/logo.png"
    index = _index(tmp_path, archived_urls={files_img: b"\x89PNG..."})

    asset = resolve_asset(
        files_img,
        join_base=PAGE_URL,
        boundary_base=BASE_URL,
        hcl_hosts=("host",),
        index=index,
    )

    assert asset.scope == "same"
    assert asset.present is True


def test_resolve_body_assets_excludes_avatars_even_when_archived(tmp_path):
    """Privacy: a person's avatar is never surfaced as an asset, even if
    its blob happens to be archived — consent covers the source system,
    not a derived export."""
    avatar_url = "https://fake/profiles/photo.do?userid=jdoe"
    content_url = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png"
    index = _index(
        tmp_path,
        archived_urls={avatar_url: b"AVATAR-BYTES", content_url: b"\x89PNG..."},
    )
    body = (
        f'<p>By <img src="{avatar_url}" class="author-photo"> Jane</p>'
        f'<figure><img src="{content_url}"></figure>'
    )

    resolved = resolve_body_assets(
        body, page_url=PAGE_URL, base_url=BASE_URL, hcl_hosts=(), index=index
    )

    resolved_urls = [a.resolved_url for a in resolved]
    assert content_url in resolved_urls  # ordinary content image kept
    assert all("photo.do" not in url for url in resolved_urls)  # avatar never an asset
