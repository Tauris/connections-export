"""The full pipeline proof for the `interchange` capability --
synthesize -> crawl (real client + fake app via the ASGI bridge) ->
derive -> write_package -> load_package -> assert model equality, every
present referenced blob on disk, and manifest counts matching the
model. Run under both a plain seed and the nasty-case seed (deep
nesting, unicode/RTL, empty page, duplicate ordinals, high comment
count), mirroring `tests/derive/test_roundtrip.py`'s pattern.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.interchange.manifest import build_manifest
from connections_export.interchange.package import load_package, open_blob, write_package
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"
GENERATED_AT = "2026-07-20T12:00:00Z"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url=BASE_URL, output_dir=tmp_path / "archive", **overrides)


def _derive_from_fresh_crawl(tmp_path: Path, wikiset, *, page_size: int | None = None):
    app = make_app(wikiset, default_page_size=page_size)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    overrides = {} if page_size is None else {"page_size": page_size}
    result = crawl(config=_config(tmp_path, **overrides), client=client, archive=archive)
    assert result.ok is True

    return archive, derive(archive)


def _referenced_present_blob_hashes(interchange) -> set[str]:
    hashes: set[str] = set()
    for wiki in interchange.wikis:
        for page in wiki.pages.values():
            for asset in page.assets:
                if asset.present and asset.blob_hash:
                    hashes.add(asset.blob_hash)
            for attachment in page.attachments:
                if attachment.asset.present and attachment.asset.blob_hash:
                    hashes.add(attachment.asset.blob_hash)
    return hashes


def _assert_full_pipeline(tmp_path: Path, wikiset, *, page_size: int | None = None) -> None:
    archive, interchange = _derive_from_fresh_crawl(tmp_path, wikiset, page_size=page_size)

    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    # Model equality: what's loaded back equals what was written.
    loaded = load_package(dest)
    assert loaded == interchange

    # Every present referenced blob exists in blobs/, and its bytes are
    # readable both directly and through open_blob.
    referenced_hashes = _referenced_present_blob_hashes(interchange)
    for blob_hash in referenced_hashes:
        filename = blob_hash.split(":", 1)[-1]
        blob_path = dest / "blobs" / filename
        assert blob_path.is_file(), f"blob {blob_hash} referenced but missing from blobs/"
        assert open_blob(dest, blob_hash) == blob_path.read_bytes()

    # Manifest counts match the model.
    manifest_on_disk_path = dest / "manifest.json"
    assert manifest_on_disk_path.is_file()
    from connections_export.interchange.manifest import PackageManifest

    manifest = PackageManifest.model_validate_json(
        manifest_on_disk_path.read_text(encoding="utf-8")
    )
    expected_manifest = build_manifest(interchange)
    assert manifest == expected_manifest

    total_pages = sum(len(wiki.pages) for wiki in interchange.wikis)
    total_comments = sum(
        len(page.comments) for wiki in interchange.wikis for page in wiki.pages.values()
    )
    total_versions = sum(
        len(page.versions) for wiki in interchange.wikis for page in wiki.pages.values()
    )
    total_attachments = sum(
        len(page.attachments) for wiki in interchange.wikis for page in wiki.pages.values()
    )
    assert manifest.counts.wikis == len(interchange.wikis)
    assert manifest.counts.pages == total_pages
    assert manifest.counts.comments == total_comments
    assert manifest.counts.versions == total_versions
    assert manifest.counts.attachments == total_attachments
    assert manifest.counts.blobs == len(referenced_hashes)


def test_full_pipeline_normal_seed(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=300,
            wiki_count=2,
            depth=2,
            pages_per_level=2,
            comments_per_page=3,
            versions_per_page=2,
            attachments_per_page=2,
            tags_per_page=1,
        )
    )

    _assert_full_pipeline(tmp_path, wikiset)


def test_full_pipeline_nasty_case_seed(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=301,
            wiki_count=1,
            depth=2,
            pages_per_level=2,
            comments_per_page=1,
            versions_per_page=1,
            attachments_per_page=1,
            tags_per_page=1,
            deep_nesting=True,
            deep_nesting_depth=8,
            high_comment_count=True,
            high_comment_count_n=60,
            unicode_rtl=True,
            empty_pages=True,
            duplicate_ordinals=True,
        )
    )

    _assert_full_pipeline(tmp_path, wikiset, page_size=10)
