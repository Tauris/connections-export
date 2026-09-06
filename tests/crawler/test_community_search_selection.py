"""An author-filtered community capture reads the threads, not the forums.

Reading a whole community to keep one person's threads is correct and
expensive: every forum's topics feed page-walked, every topic's replies feed
fetched, each thread then kept or pruned on who is in it. In a busy community
that is tens of thousands of entries fetched to keep a few hundred threads.

These tests run the real dispatcher against the fake deployment and assert
the thing that changed: the replies feed of a thread the person is not in is
never fetched at all. Everything else about the capture -- what is kept, what
is reported -- has to come out the same as the full walk, which is the second
half of what is checked here.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.dispatch import run_selection
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.searchfeed import person_userid
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_forums
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"

COMMUNITY = "community-under-test"
#: The person under test. The synthesizer gives every "Replier" a reply in
#: every topic, which would let a crawl that ignored the selection entirely
#: pass every assertion here -- so `_deployment` takes them back out of half
#: the threads, leaving someone who authored some, only replied in others,
#: and is absent from the rest. All three cases matter: Search returns the
#: containing topic for a contribution and never the reply itself.
PERSON = "Replier 0"
STAND_IN = "Replier 1"


def _deployment(**overrides):
    """A fake deployment whose forums all belong to one community."""
    seed = SynthSeed(
        seed=71,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        forum_count=2,
        topics_per_forum=8,
        replies_per_topic=2,
        reply_tree_depth=2,
        **overrides,
    )
    forumset = synthesize_forums(seed)
    topics = [t for f in forumset.forums for t in f.topics]
    for index, topic in enumerate(topics):
        if index % 2:
            continue  # leave the person in every other thread
        if topic.author == PERSON:
            topic.author = STAND_IN
        for reply in topic.replies:
            if reply.author == PERSON:
                reply.author = STAND_IN
    for forum in forumset.forums:
        forum.community_uuid = COMMUNITY
        forum.community_title = "Community Under Test"
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    return make_app(wikiset, forumset=forumset), forumset


def _threads_of(forumset, person: str) -> set[str]:
    """Topics the person wrote or replied in -- what the capture must keep,
    computed from the dataset rather than from the crawl's own output, which
    would be checking the thing under test against itself."""
    out = set()
    for forum in forumset.forums:
        for topic in forum.topics:
            if topic.author == person or any(r.author == person for r in topic.replies):
                out.add(topic.uuid)
    return out


def _capture(tmp_path: Path, app, forumset, *, author, search_userid=None):
    archive = Archive.open(tmp_path / "archive")
    seen: list = []
    run_selection(
        {"forum": [f.uuid for f in forumset.forums]},
        config=Config(base_url=BASE_URL, output_dir=tmp_path / "archive", page_size=10),
        client=make_client(app),
        archive=archive,
        emit=seen.append,
        author=author,
        community_uuid=COMMUNITY,
        search_userid=search_userid,
    )
    return archive, seen


def _person(forumset) -> str:
    """`PERSON`, having first checked the fixture actually poses the problem:
    they are in some threads and out of others, and at least one of the
    threads they are in is somebody else's."""
    topics = [t for f in forumset.forums for t in f.topics]
    mine = _threads_of(forumset, PERSON)
    assert mine, "fixture: the person must be in at least one thread"
    assert len(mine) < len(topics), "fixture: some threads must be somebody else's alone"
    assert any(t.uuid in mine and t.author != PERSON for f in forumset.forums for t in f.topics), (
        "fixture: at least one kept thread must be one the person only replied in"
    )
    return PERSON


def test_the_threads_the_person_is_not_in_are_never_read(tmp_path):
    app, forumset = _deployment()
    person = _person(forumset)
    mine = _threads_of(forumset, person)
    theirs = {t.uuid for f in forumset.forums for t in f.topics} - mine

    archive, seen = _capture(
        tmp_path, app, forumset, author=person, search_userid=person_userid(person)
    )

    derived = {e.topic_id for e in seen if isinstance(e, events.ForumTopicDerived)}
    assert derived == mine

    urls = archive.seen_urls()
    for topic_uuid in theirs:
        assert not any(
            "/forums/atom/replies" in u and f"topicUuid={topic_uuid}" in u for u in urls
        ), f"read the replies of {topic_uuid}, which nobody asked for"
    for topic_uuid in mine:
        assert any("/forums/atom/replies" in u and f"topicUuid={topic_uuid}" in u for u in urls)


def test_the_selection_keeps_exactly_what_the_full_walk_keeps(tmp_path):
    """The saving must be in what is READ, never in what is kept. Same
    person, same community, one capture selected by Search and one reading
    the forums whole -- the kept set has to be identical, or this is a
    smaller capture wearing a faster capture's name."""
    app, forumset = _deployment()
    person = _person(forumset)

    _, selected = _capture(
        tmp_path / "selected",
        app,
        forumset,
        author=person,
        search_userid=person_userid(person),
    )
    _, walked = _capture(tmp_path / "walked", app, forumset, author=person)

    def kept(seen):
        return {e.topic_id for e in seen if isinstance(e, events.ForumTopicDerived)}

    assert kept(selected) == kept(walked)
    assert kept(selected)


def test_the_run_says_search_chose_the_topics(tmp_path):
    app, forumset = _deployment()
    person = _person(forumset)
    _, seen = _capture(tmp_path, app, forumset, author=person, search_userid=person_userid(person))

    selections = [e for e in seen if isinstance(e, events.TopicSelection)]
    assert len(selections) == 1
    selection = selections[0]
    assert selection.used is True
    assert selection.complete is True
    assert selection.selected == len(_threads_of(forumset, person))
    assert selection.ref and "personUserId" in selection.ref


def test_a_person_search_does_not_know_falls_back_to_the_full_walk(tmp_path):
    """The author filter can be a display name, or an id from another
    deployment. Search answers nothing, and nothing must mean "read them
    all", never "this person wrote nothing"."""
    app, forumset = _deployment()
    person = _person(forumset)

    archive, seen = _capture(
        tmp_path, app, forumset, author=person, search_userid="nobody-by-that-id"
    )

    selections = [e for e in seen if isinstance(e, events.TopicSelection)]
    assert len(selections) == 1 and selections[0].used is False
    assert "reading the forums in full instead" in selections[0].detail
    # And the capture is the full-walk capture: everything read, the
    # person's threads kept.
    derived = {e.topic_id for e in seen if isinstance(e, events.ForumTopicDerived)}
    assert derived == _threads_of(forumset, person)
    urls = archive.seen_urls()
    assert any("/forums/atom/topics" in u for u in urls)


def test_without_an_author_filter_nothing_is_selected(tmp_path):
    """ "Capture this community" is not a question Search can narrow: there is
    no person to ask about, and the answer is every topic anyway."""
    app, forumset = _deployment()
    _, seen = _capture(tmp_path, app, forumset, author=None, search_userid="whoever")
    assert not [e for e in seen if isinstance(e, events.TopicSelection)]


def test_the_selected_capture_still_derives_into_the_community(tmp_path):
    """The saving is in the reading, so the exported result must be the same.

    Two things could have gone wrong invisibly. The seeded path skips the
    forums LIST feed, and the community marker lives on a feed -- so "faster"
    could have meant forums that no longer know which community they belong
    to, loose at the top level of the reader. And the two paths archive
    different amounts (the full walk keeps the whole topics feed, seeds keep
    the selected topics), so what a reader is shown could differ even when
    the crawl kept the same threads. Both exports are taken through the
    author filter the archive was captured under, which is what the console
    and the CLI serve, and compared there."""
    from connections_export.derive import derive as run_derive
    from connections_export.derive.author_filter import filter_by_author

    app, forumset = _deployment()
    person = _person(forumset)
    archive, _ = _capture(
        tmp_path / "selected", app, forumset, author=person, search_userid=person_userid(person)
    )
    selected = filter_by_author(run_derive(archive), author=person)

    walked_archive, _ = _capture(tmp_path / "walked", app, forumset, author=person)
    walked = filter_by_author(run_derive(walked_archive), author=person)

    def shape(model):
        return {
            (
                forum.community_uuid,
                forum.title,
                tuple(sorted(forum.topic_ids)),
            )
            for forum in model.forums
        }

    assert shape(selected) == shape(walked)
    assert all(community for community, _, _ in shape(selected))
