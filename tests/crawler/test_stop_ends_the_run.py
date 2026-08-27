"""Stop stops the run, not just the container it is in the middle of.

pressing Stop did nothing visible and the ingest carried
on.

Each app checked the stop event in its INNER loop only -- the pages of a
wiki, the posts of a blog. So Stop ended the current container and the crawl
moved straight on to the next one, and the next. With one container that is
indistinguishable from stopping; across a whole deployment's worth it is
indistinguishable from a button that does nothing.

Stop is pressed MID-run here, the way a person presses it: the event fires as
soon as the first container's content starts arriving. Setting it before the
run proves nothing -- the first feed is stopped and there is no second
container to wrongly continue to.
"""

from __future__ import annotations

import pathlib
import threading

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl, crawl_blogs, crawl_forums
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=3, depth=1, pages_per_level=3)


@pytest.fixture
def deployment():
    return synthesize(SEED), synthesize_blogs(SEED), synthesize_forums(SEED)


def _client(deployment):
    wikis, blogs, forums = deployment
    app = make_app(wikis, blogset=blogs, forumset=forums)
    return HttpClient(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request), sleep=lambda _s: None
    )


def _fetched(events) -> list[str]:
    return [e.url for e in events if type(e).__name__ == "Fetched"]


def _stop_once_underway(stop: threading.Event, events: list, marker: str):
    """Press Stop as soon as the run is inside the first container."""

    def emit(event):
        events.append(event)
        if not stop.is_set() and type(event).__name__ == "Fetched" and marker in event.url:
            stop.set()

    return emit


def test_a_stopped_wiki_run_does_not_move_on_to_the_next_wiki(deployment, tmp_path: pathlib.Path):
    wikis = deployment[0]
    assert len(wikis.wikis) > 1, "the fixture needs more than one wiki to prove anything"
    stop = threading.Event()
    events: list = []

    crawl(
        config=Config(base_url="https://fake", output_dir=tmp_path / "a"),
        client=_client(deployment),
        archive=Archive.open(tmp_path / "a"),
        emit=_stop_once_underway(stop, events, "/page/"),
        stop_event=stop,
    )

    touched = {
        wiki.label for wiki in wikis.wikis if any(f"/{wiki.label}/" in u for u in _fetched(events))
    }
    assert len(touched) <= 1, f"kept going through {sorted(touched)}"


def test_a_stopped_blog_run_does_not_move_on_to_the_next_blog(deployment, tmp_path: pathlib.Path):
    blogs = deployment[1]
    assert len(blogs.blogs) > 1
    stop = threading.Event()
    events: list = []

    crawl_blogs(
        config=Config(base_url="https://fake", output_dir=tmp_path / "b"),
        client=_client(deployment),
        archive=Archive.open(tmp_path / "b"),
        emit=_stop_once_underway(stop, events, "entrycomments"),
        blogs_homepage=blogs.homepage,
        stop_event=stop,
    )

    touched = {
        blog.handle
        for blog in blogs.blogs
        if any(f"/{blog.handle}/" in u for u in _fetched(events))
    }
    assert len(touched) <= 1, f"kept going through {sorted(touched)}"


def test_a_stopped_forum_run_does_not_move_on_to_the_next_forum(deployment, tmp_path: pathlib.Path):
    forums = deployment[2]
    assert len(forums.forums) > 1
    stop = threading.Event()
    events: list = []

    crawl_forums(
        config=Config(base_url="https://fake", output_dir=tmp_path / "c"),
        client=_client(deployment),
        archive=Archive.open(tmp_path / "c"),
        emit=_stop_once_underway(stop, events, "/replies"),
        stop_event=stop,
    )

    touched = {
        forum.uuid for forum in forums.forums if any(forum.uuid in u for u in _fetched(events))
    }
    assert len(touched) <= 1, f"kept going through {sorted(touched)}"
