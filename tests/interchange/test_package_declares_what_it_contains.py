"""A package that names a blob must contain it.

`manifest.json` is a promise about `blobs/`. It was made by one walk over the
model and kept by a second, and the two covered different apps: rich-content
images and a community's file bytes were counted and never copied, and forum
attachments were missed by both -- symmetrically, so a manifest-vs-package
check could not see them either. One walk, used by both, is the only thing
that makes the promise structural rather than a coincidence.
"""

from __future__ import annotations

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedFile,
    DerivedFileLibrary,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedRichContent,
    DerivedRichContentPage,
    Interchange,
    ResolvedAsset,
)
from connections_export.derive.traverse import iter_all_assets
from connections_export.interchange.manifest import build_manifest
from connections_export.interchange.package import write_package


def _asset(tag: str) -> ResolvedAsset:
    return ResolvedAsset(
        original_href=f"{tag}.bin",
        resolved_url=f"http://example.invalid/{tag}.bin",
        blob_hash=f"sha256:{tag}",
        present=True,
        scope="same",
    )


def _every_app_interchange() -> Interchange:
    """One item per app, each carrying exactly one distinctly-tagged blob."""
    reply = DerivedForumReply(
        id="r1", attachments=[DerivedAttachment(id="ra", asset=_asset("replyattach"))]
    )
    topic = DerivedForumTopic(
        id="t1",
        assets=[_asset("topicbody")],
        attachments=[DerivedAttachment(id="ta", asset=_asset("topicattach"))],
        reply_ids=["r1"],
        replies={"r1": reply},
    )
    return Interchange(
        forums=[DerivedForum(id="f1", topic_ids=["t1"], topics={"t1": topic})],
        file_libraries=[
            DerivedFileLibrary(
                id="lib1",
                file_ids=["fi1"],
                files={"fi1": DerivedFile(id="fi1", asset=_asset("filebytes"))},
            )
        ],
        rich_content=[
            DerivedRichContent(
                id="rc1",
                page_ids=["p1"],
                pages={
                    "p1": DerivedRichContentPage(
                        id="p1", resource_id="res1", assets=[_asset("rcimage")]
                    )
                },
            )
        ],
    )


def test_every_apps_blobs_are_reachable_from_one_walk():
    found = {asset.blob_hash for asset in iter_all_assets(_every_app_interchange())}

    assert found == {
        "sha256:topicbody",
        "sha256:topicattach",
        "sha256:replyattach",
        "sha256:filebytes",
        "sha256:rcimage",
    }


def test_the_manifest_count_matches_what_the_package_copies(tmp_path):
    """The promise itself: `manifest.blobs` is the number of files in `blobs/`."""
    archive = Archive.open(tmp_path / "archive")
    interchange = _every_app_interchange()
    for asset in iter_all_assets(interchange):
        # The archive must really hold the bytes, or copying is a no-op.
        asset.blob_hash = write_blob(archive.root, asset.original_href.encode())

    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at="2026-01-01T00:00:00Z")

    manifest = build_manifest(interchange)
    copied = sorted(p.name for p in (dest / "blobs").iterdir())

    assert manifest.counts.blobs == len(copied)
    assert len(copied) == 5
