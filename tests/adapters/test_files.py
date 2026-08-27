"""The Files adapter, round-tripped through the fake server.

Endpoints are against a live deployment; the XML shape carrying the
fields is not documented. So the fake serves exactly what the adapter reads: if the
real shape differs, both change together and the mismatch is one edit rather
than a hunt.
"""

from __future__ import annotations

from lxml import etree

from connections_export.adapters.files import (
    community_collection_url,
    community_introspection_url,
    community_library_url,
    folders_limited_to,
    parse_collection_feed,
    parse_library_feed,
)
from connections_export.adapters.model import CommunityFile, CommunityFolder
from connections_export.fakeserver.atom import (
    community_collection_feed,
    community_library_feed,
)
from connections_export.fakeserver.synth import SynthSeed, synthesize_files

BASE = "https://fake"


def _library():
    return synthesize_files(SynthSeed(seed=0, human_names=True), community_uuid="c-1").libraries[0]


def test_the_urls_take_a_community_uuid_not_a_library_id():
    """The documented shape is `/library/{libraryId}/feed` and needs a lookup
    that does not exist -- the generic libraries feed is global. These
    community-scoped endpoints are what a live capture found."""
    assert community_library_url(base_url=BASE, community_uuid="c-1").endswith(
        "/files/basic/api/communitylibrary/c-1/feed"
    )
    assert community_collection_url(base_url=BASE, community_uuid="c-1").endswith(
        "/files/basic/api/communitycollection/c-1/feed"
    )
    assert community_introspection_url(base_url=BASE, community_uuid="c-1").endswith(
        "/files/basic/api/community/c-1/introspection"
    )


def test_a_library_feed_round_trips():
    library = _library()

    files = parse_library_feed(community_library_feed(library, base_url=BASE))

    assert len(files) == len(library.files)
    first = files[0]
    assert first.name == library.files[0].name
    assert first.size == library.files[0].size
    assert first.version_label == library.files[0].version_label
    assert first.content_type == library.files[0].content_type
    assert first.download_url and "/media/" in first.download_url


def test_file_change_date_prefers_content_modification_over_feed_updated():
    library = _library()
    root = etree.fromstring(community_library_feed(library, base_url=BASE))
    entry = root.xpath("//*[local-name()='entry']")[0]
    updated = entry.xpath("./*[local-name()='updated']")[0]
    updated.text = "2099-01-01T00:00:00Z"
    data = etree.tostring(root)
    files = parse_library_feed(data)
    assert files[0].updated == library.files[0].updated


def test_the_flat_feed_carries_foldered_files_too():
    """Verified on a live deployment: the flat feed is everything, not "the
    files outside folders". It is why the crawler takes content from here
    alone."""
    files = parse_library_feed(community_library_feed(_library(), base_url=BASE))

    assert any(f.filed_in_folder for f in files)
    assert any(not f.filed_in_folder for f in files)


def test_collections_name_files_the_library_does_not_have():
    """A capture found 22 collection entries against 19 library files, the
    three extras being personal documents. The fake reproduces that."""
    library = _library()

    folders = parse_collection_feed(community_collection_feed(library, base_url=BASE))

    all_ids = {i for f in folders for i in f.file_ids}
    library_ids = {f.uuid for f in library.files}
    assert all_ids - library_ids, "the fake should plant a file the library lacks"


def test_filtering_keeps_community_files_and_drops_the_rest():
    """The consequence that matters: merging the two feeds would put someone's
    private documents in a community archive."""
    library = _library()
    files = parse_library_feed(community_library_feed(library, base_url=BASE))
    folders = parse_collection_feed(community_collection_feed(library, base_url=BASE))

    kept = folders_limited_to(folders, files)

    known = {f.id for f in files}
    assert all(i in known for folder in kept for i in folder.file_ids)


def test_a_folder_left_empty_by_filtering_is_dropped():
    """An empty folder here means every file it named was somebody else's."""
    kept = folders_limited_to(
        [CommunityFolder(id="F", name="Theirs", file_ids=["private"])],
        [CommunityFile(id="ours")],
    )

    assert kept == []
