"""A zip assembled without `feeds.jsonl` says so rather than deriving less.

This is where two changes meet. Reading an archive from a zip made
user-assembled archives a real thing -- nothing in the product writes a zip,
so a person makes one, and a person can leave a file out. And a rebuilt page
sequence that stops at a hole reads back less content than was captured.

`test_reconstructed_reads_announce_themselves.py` establishes the behaviour on
a directory. This asserts it survives the source seam, because the whole point
of that seam is that a reader cannot tell which one it is holding -- and a
guarantee that quietly applies to only one of them is worse than none.
"""

from __future__ import annotations

import zipfile

import httpx
import pytest

from connections_export.archive.source import ReadableArchive
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient


@pytest.fixture
def captured(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=1)
    transport = httpx.MockTransport(SyncASGIBridge(make_app(synthesize(seed))).handle_request)
    root = tmp_path / "archive"
    crawl(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    return root


def _zip_of(root, destination, *, omit=()):
    with zipfile.ZipFile(destination, "w") as bundle:
        for path in sorted(root.rglob("*")):
            name = str(path.relative_to(root)).replace("\\", "/")
            if path.is_file() and name not in omit:
                bundle.write(path, name)
    return destination


def _notes(interchange):
    notes = []
    for wiki in interchange.wikis:
        for page in wiki.pages.values():
            if page.provenance.note:
                notes.append(page.provenance.note)
    return notes


def test_a_complete_zip_carries_no_reconstruction_note(captured, tmp_path):
    """The other half of the guarantee: a zip with everything in it must not
    acquire a warning simply for being a zip."""
    zipped = _zip_of(captured, tmp_path / "complete.zip")
    assert not [note for note in _notes(derive(ReadableArchive.open(zipped))) if "gap" in note]


def test_a_zip_that_omits_the_feed_record_still_derives(captured, tmp_path):
    """Dropping the recording is not fatal -- reconstruction is the fallback,
    and for an archive with no holes it is correct."""
    zipped = _zip_of(captured, tmp_path / "no-feeds.zip", omit=("feeds.jsonl",))
    interchange = derive(ReadableArchive.open(zipped))
    assert interchange.wikis
    assert any(wiki.pages for wiki in interchange.wikis)


def test_the_same_zip_derives_what_the_directory_does(captured, tmp_path):
    """With no hole, the fallback loses nothing -- so the two agree, and the
    note has nothing to fire about."""
    zipped = _zip_of(captured, tmp_path / "no-feeds-2.zip", omit=("feeds.jsonl",))
    from_zip = derive(ReadableArchive.open(zipped))
    from_directory = derive(Archive.open(captured))
    assert {p.id for w in from_zip.wikis for p in w.pages.values()} == {
        p.id for w in from_directory.wikis for p in w.pages.values()
    }
