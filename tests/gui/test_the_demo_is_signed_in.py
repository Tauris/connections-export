"""The demo is somebody.

"Only me" needs to know who you are, and a demo with no authenticated user
could not answer it: `/api/current-user` returned 503 "the demo has no
authenticated Connections user". So the one identity path a real deployment
always exercises was the one path the demo never did -- `parse_current_user`
had never met a server, only a fixture.

The demo now signs in as one of the people in its own data, and answers the
question the way a deployment does: ask `profileService.do`, parse the
service document. The person is chosen for authoring in every component, so
filtering to them leaves something everywhere rather than an export that
looks broken.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.adapters.directory import parse_current_user, profile_service_url
from connections_export.fakeserver import synthesize_blogs, synthesize_forums
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import person_uid
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.fakeserver.synth import impersonated_person
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL, demo_synth_seed

BASE = "https://fake"


def _client(**kwargs):
    seed = demo_synth_seed(0)
    app = make_app(
        build_prototype_wikiset(),
        blogset=synthesize_blogs(seed),
        forumset=synthesize_forums(seed),
        **kwargs,
    )
    return httpx.Client(transport=httpx.MockTransport(SyncASGIBridge(app).handle_request))


def test_the_fake_answers_who_am_i():
    """The document the adapter reads, from a server rather than a fixture."""
    who = impersonated_person(demo_synth_seed(0))
    with _client(current_user=who) as client:
        response = client.get(profile_service_url(base_url=BASE))

    assert response.status_code == 200
    user = parse_current_user(response.content)
    assert user is not None
    assert user.name == who
    # The id every component's `snx:userid` carries, or filtering by it finds
    # nothing -- which is the failure the shared person id was introduced for.
    assert user.userid == person_uid(who)


def test_a_server_nobody_is_signed_in_to_says_so():
    """401 -- no authenticated user and no id given. A real state, and the
    one a server with nobody configured is in."""
    with _client() as client:
        assert client.get(profile_service_url(base_url=BASE)).status_code == 401


def test_an_id_naming_nobody_is_refused_rather_than_guessed():
    """400 -- no matching user record."""
    with _client(current_user="M. Lindqvist") as client:
        response = client.get(
            profile_service_url(base_url=BASE), params={"userid": "not-a-real-guid"}
        )
    assert response.status_code == 400


@pytest.mark.parametrize("spelling", ["userid", "userId"])
def test_both_spellings_of_the_parameter_work(spelling):
    """The reference documents `userId` in its table and `userid` in its
    worked example, and says to accept either."""
    who = "P. Novak"
    with _client(current_user="M. Lindqvist") as client:
        response = client.get(
            profile_service_url(base_url=BASE), params={spelling: person_uid(who)}
        )
    assert response.status_code == 200
    assert parse_current_user(response.content).name == who


def test_the_console_resolves_the_demo_user():
    from connections_export.gui.support import _demo_current_user

    resolved = _demo_current_user()
    assert resolved is not None
    assert resolved["name"] == impersonated_person(demo_synth_seed(0))
    assert resolved["userid"] == person_uid(resolved["name"])
    assert DEMO_SAMPLE_BASE_URL in resolved["url"]


def test_the_route_no_longer_says_the_demo_has_nobody():
    from fastapi.testclient import TestClient

    from connections_export.gui.app import make_app as make_console

    client = TestClient(make_console(demo=True), base_url="http://127.0.0.1")
    response = client.get("/api/current-user")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == impersonated_person(demo_synth_seed(0))
    assert body["userid"] == person_uid(body["name"])


def test_filtering_the_demo_to_that_person_leaves_content_in_every_component(tmp_path):
    """Which is why this person and not another. An author filter that empties
    a component teaches the wrong thing about what the filter does."""
    from connections_export.archive.store import Archive
    from connections_export.derive import derive
    from connections_export.derive.author_filter import filter_by_author
    from connections_export.gui.demo import run_demo

    root = tmp_path / "archive"
    run_demo(lambda _e: None, archive_dir=root, delay=0)
    who = impersonated_person(demo_synth_seed(0))
    model = filter_by_author(derive(Archive.open(root)), author=who)

    kept = {
        "wiki": [p for w in model.wikis for p in w.pages.values() if p.author == who],
        "blog": [p for b in model.blogs for p in b.posts.values() if p.author == who],
        "forum": [t for f in model.forums for t in f.topics.values() if t.author == who],
        "files": [x for lib in model.file_libraries for x in lib.files.values() if x.author == who],
        "highlights": [p for r in model.rich_content for p in r.pages.values() if p.author == who],
    }
    empty = [component for component, items in kept.items() if not items]
    assert not empty, f"{who} authored nothing in {empty}"
