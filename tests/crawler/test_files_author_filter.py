"""Files answers to a person the way the other apps do.

Two faults, both in the product rather than the fake.

`CommunityFile` carries `author_userid` and the adapter parses it, but the
crawl compared the NAME only. Wikis, blogs and forums match through
`Involvement`: name, or user id, or contributor credit. So filtering by a user
id -- which the author picker itself hands you whenever an identity has no
display name -- kept every wiki page and no files at all, silently.

And neither Files nor Highlights emitted an `AuthorFilterSummary`, so the
people who appear in them never reached the picker. An identity that is not in
that list is a filter nobody can construct: the picker exists precisely because
the stored form of a name is not guessable.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_files
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import synthesize, synthesize_files
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.demo import demo_synth_seed
from connections_export.http.client import HttpClient

COMMUNITY = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"


@pytest.fixture
def deployment():
    seed = demo_synth_seed(0)
    return synthesize(seed), synthesize_files(seed, community_uuid=COMMUNITY)


def _run(deployment, root: pathlib.Path, author=None):
    wikis, files = deployment
    events: list = []
    transport = httpx.MockTransport(SyncASGIBridge(make_app(wikis, fileset=files)).handle_request)
    crawl_files(
        config=Config(base_url="https://fake", output_dir=root),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=events.append,
        community_uuid=COMMUNITY,
        author=author,
    )
    return events


def _captured(root):
    return [
        f
        for library in derive(Archive.open(root)).file_libraries
        for f in library.files.values()
        if f.asset and f.asset.present
    ]


def test_filtering_by_a_user_id_keeps_that_person_s_files(tmp_path, deployment):
    from connections_export.fakeserver.model import person_uid

    _wikis, files = deployment
    document = files.libraries[0].files[0]
    # The id the deployment credits this person with -- the fake attaches it
    # when it serves the feed, exactly as a real one does.
    uid = person_uid(document.author)

    _run(deployment, tmp_path / "by-name", author=document.author)
    _run(deployment, tmp_path / "by-uid", author=uid)

    by_name = {f.name for f in _captured(tmp_path / "by-name")}
    by_uid = {f.name for f in _captured(tmp_path / "by-uid")}
    assert by_name, "filtering by name kept nothing, so the fixture proves nothing"
    assert by_uid == by_name, "a user id kept different files than the same person's name"


def test_files_reports_the_people_it_saw(tmp_path, deployment):
    events = _run(deployment, tmp_path / "archive")
    summaries = [e for e in events if type(e).__name__ == "AuthorFilterSummary"]
    assert summaries, "Files never said who is in it"
    identities = summaries[-1].identities
    assert identities
    assert any("Delgado" in i for i in identities), identities


def test_the_summary_counts_what_was_kept(tmp_path, deployment):
    _wikis, files = deployment
    document = files.libraries[0].files[0]
    events = _run(deployment, tmp_path / "archive", author=document.author)
    summary = [e for e in events if type(e).__name__ == "AuthorFilterSummary"][-1]
    assert summary.total > summary.kept > 0
    assert summary.author == document.author


def test_highlights_reports_its_people_too(tmp_path):
    from connections_export.crawler import crawl_rich_content
    from connections_export.fakeserver.synth import synthesize_rich_content

    seed = demo_synth_seed(0)
    wikis = synthesize(seed)
    rte = synthesize_rich_content(seed, community_uuid=COMMUNITY)
    events: list = []
    transport = httpx.MockTransport(SyncASGIBridge(make_app(wikis, rteset=rte)).handle_request)
    crawl_rich_content(
        config=Config(base_url="https://fake", output_dir=tmp_path / "archive"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(tmp_path / "archive"),
        emit=events.append,
        community_uuid=COMMUNITY,
    )
    summaries = [e for e in events if type(e).__name__ == "AuthorFilterSummary"]
    assert summaries, "Highlights never said who is in it"
    assert summaries[-1].identities
