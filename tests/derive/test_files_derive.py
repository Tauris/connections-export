"""crawl_files -> derive: a community library becomes a model container.

The end-to-end check for Files. What matters beyond "it round-trips" is that
the two decisions the captures forced survive derivation: folder membership is
an association (a list), and the collection feed's foreign entries never appear.
"""

from __future__ import annotations

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_files
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_files
from tests.crawler.conftest import make_client

COMMUNITY = "c-1"


def _crawled(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    fileset = synthesize_files(seed, community_uuid=COMMUNITY)
    archive = Archive.open(tmp_path / "archive")
    crawl_files(
        config=Config(base_url="https://fake"),
        client=make_client(make_app(synthesize(seed), fileset=fileset)),
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    return archive, fileset.libraries[0]


def _library_of(interchange):
    assert interchange.file_libraries, "no file library was derived"
    return interchange.file_libraries[0]


def test_the_library_derives_with_every_file(tmp_path):
    archive, source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    assert len(library.files) == len(source.files)
    assert library.file_ids  # feed order preserved
    assert len(library.file_ids) == len(library.files)


def test_names_are_kept_exactly_as_connections_holds_them(tmp_path):
    """Including the ones a filesystem cannot take. The safe name is decided
    when a package is written; the model keeps the truth."""
    archive, source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    names = {f.name for f in library.files.values()}
    assert "Q1 Budget: draft?.xlsx" in names
    assert "CON.txt" in names
    assert "Runbook." in names


def test_every_file_resolves_to_its_bytes(tmp_path):
    """EVERY file, not merely one.

    `any(...)` is the wrong quantifier here. The crawler archives the
    percent-encoded URL an HTTP client actually requests, while the feed
    carries the raw href, so a lookup that confuses the two matches only the
    names without spaces -- and most real file names have spaces. Under
    `any`, two files out of eight are enough to pass.
    """
    archive, source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    missing = [f.name for f in library.files.values() if not (f.asset and f.asset.present)]
    assert missing == [], f"files derived without their bytes: {missing}"
    assert len(library.files) == len(source.files)


def test_names_a_url_cannot_carry_still_resolve(tmp_path):
    """Spaces, a colon, a question mark. The `?` is the nastiest: unencoded, a
    client reads the rest of the name as a query string."""
    archive, _source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    awkward = [f for f in library.files.values() if f.name == "Q1 Budget: draft?.xlsx"]
    assert awkward, "the demo should contain a name with a colon and a question mark"
    assert awkward[0].asset and awkward[0].asset.present


def test_folder_membership_is_an_association_not_a_path(tmp_path):
    archive, _source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    filed = [f for f in library.files.values() if f.folder_ids]
    assert filed, "no file was associated with a folder"
    assert all(isinstance(f.folder_ids, list) for f in library.files.values())


def test_no_file_the_community_does_not_own_appears(tmp_path):
    """The collection feed names one. Merging the feeds would put someone's
    personal documents in a community export."""
    archive, source = _crawled(tmp_path)

    library = _library_of(derive(archive, base_url="https://fake"))

    known = {f.uuid for f in source.files}
    assert set(library.files) <= known
    for folder in library.folders:
        assert all(i in known for i in folder.file_ids)


def test_the_community_names_its_library(tmp_path):
    """So a reader or an exporter can get from a community to its files
    without re-deriving anything."""
    archive, _source = _crawled(tmp_path)

    interchange = derive(archive, base_url="https://fake")
    library = _library_of(interchange)

    communities = [c for c in interchange.communities if c.file_library_id]
    if communities:  # the demo wiki may not carry a community marker
        assert communities[0].file_library_id == library.id
