"""Crawling a community's file library.

Simpler than the other three by design: one page-walked feed, one folder feed,
one download per file. The parts worth testing are the two decisions a live
capture forced -- content comes from the FLAT feed alone, and the folder feed
is filtered rather than merged.
"""

from __future__ import annotations

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_files
from connections_export.crawler.events import CommunityFileDerived
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_files
from tests.crawler.conftest import make_client

COMMUNITY = "c-1"


def _app_and_library(seed_value: int = 0):
    seed = SynthSeed(seed=seed_value, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    fileset = synthesize_files(seed, community_uuid=COMMUNITY)
    return make_app(synthesize(seed), fileset=fileset), fileset.libraries[0]


def _crawl(tmp_path, **kwargs):
    app, library = _app_and_library()
    archive = Archive.open(tmp_path / "archive")
    events: list = []
    result = crawl_files(
        config=Config(base_url="https://fake"),
        client=make_client(app),
        archive=archive,
        community_uuid=COMMUNITY,
        emit=events.append,
        **kwargs,
    )
    derived = [e for e in events if isinstance(e, CommunityFileDerived)]
    return result, derived, archive, library


def test_every_file_in_the_library_is_captured(tmp_path):
    result, derived, _archive, library = _crawl(tmp_path)

    assert result.ok
    assert len(derived) == len(library.files)


def test_the_bytes_are_fetched_not_just_the_listing(tmp_path):
    """An archive that lists documents it does not contain is not an archive.
    Unlike a wiki image, the bytes ARE the file."""
    _result, derived, archive, _library = _crawl(tmp_path)

    assert all(e.bytes_captured for e in derived)
    assert any("/media/" in url for url in archive.seen_urls())


def test_foldered_and_unfoldered_files_both_arrive(tmp_path):
    """Content comes from the flat feed alone, which a capture verified is
    complete. If folders were the content source, filed files would be missing
    or duplicated."""
    _result, derived, _archive, library = _crawl(tmp_path)

    captured = {e.file_id for e in derived}
    assert captured == {f.uuid for f in library.files}


def test_the_folder_feed_is_never_a_content_source(tmp_path):
    """The collection feed names a file the library does not own. It must not
    be fetched: a community archive containing someone's personal documents
    would be a privacy failure, not a bug."""
    _result, derived, archive, library = _crawl(tmp_path)

    known = {f.uuid for f in library.files}
    assert all(e.file_id in known for e in derived)
    assert not any("personal-file-not-in-this-library" in url for url in archive.seen_urls())


def test_max_files_caps_the_capture(tmp_path):
    _result, derived, _archive, _library = _crawl(tmp_path, max_files=3)

    assert len(derived) == 3


def test_an_author_filter_narrows_it(tmp_path):
    _result, all_derived, _a, _l = _crawl(tmp_path)
    target = all_derived[0].author

    _result2, filtered, _a2, _l2 = _crawl(tmp_path, author=target)

    assert filtered
    assert all(e.author == target for e in filtered)
    assert len(filtered) < len(all_derived)


# --- selecting files the way every other component is selected -------------


def test_files_is_a_component_kind(tmp_path):
    """`--component files:<community-uuid>` -- the same vocabulary the console
    writes into the command it shows. Files are addressed by the COMMUNITY
    uuid, unlike every other component, because that is how the endpoint works."""
    from connections_export.cli import crawl_main

    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    fileset = synthesize_files(seed, community_uuid=COMMUNITY)
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["--component", f"files:{COMMUNITY}", "--base-url", "https://fake"],
        env={},
        client=make_client(make_app(synthesize(seed), fileset=fileset)),
        archive=archive,
    )

    assert exit_code == 0
    assert any("/communitylibrary/" in url for url in archive.seen_urls())
    assert any("/media/" in url for url in archive.seen_urls())


def test_an_unknown_component_kind_names_files_among_the_valid_ones(capsys):
    from connections_export.cli import crawl_main

    crawl_main(["--component", "nonsense:x"], env={})

    assert "files:<id>" in capsys.readouterr().err


def test_a_missing_base_url_is_a_message_not_a_traceback(capsys):
    """`--component` without a URL cannot supply the deployment address."""
    from connections_export.cli import crawl_main

    exit_code = crawl_main(["--component", f"files:{COMMUNITY}"], env={})

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "base_url is not configured" in captured.err
    assert "Traceback" not in captured.err
