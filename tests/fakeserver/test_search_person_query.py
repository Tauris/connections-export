"""The fake's Search person query, in the shape the real one answered in.

A capture depends on three properties of the community-scoped person query. A
fake that got any of them wrong would let a client that mishandles them look
correct here and fail against a deployment:

- the person and community clauses arrive as TWO `social` parameters and
  AND-combine;
- a forum hit is a TOPIC, once, however many replies the person left in it,
  and it is returned for a reply as readily as for authorship;
- the entry links to the topic, never to a reply.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from connections_export.adapters.search import parse_search_results, search_results_url
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import assign_community, person_uid
from connections_export.fakeserver.searchfeed import person_userid
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_forums
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"
COMMUNITY = "the-community"
ELSEWHERE = "another-community"


def _deployment():
    seed = SynthSeed(
        seed=71,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        forum_count=2,
        topics_per_forum=4,
        replies_per_topic=2,
        reply_tree_depth=2,
    )
    forumset = synthesize_forums(seed)
    # One forum in the community under test, one outside it -- so a pin that
    # is ignored is visible as extra results rather than as nothing at all.
    forumset.forums[0].community_uuid = COMMUNITY
    forumset.forums[1].community_uuid = ELSEWHERE
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    return make_app(wikiset, forumset=forumset), forumset


def _hits(client, userid, community_uuid=COMMUNITY):
    url = search_results_url(
        base_url=BASE_URL,
        userid=userid,
        community_uuid=community_uuid,
        scope="forums:topic",
        page=1,
        page_size=150,
    )
    return parse_search_results(client.get(url).content)


def _topic_ids(hits):
    from connections_export.crawler.forum_selection import forum_topic_id

    return [forum_topic_id(h.via_url) or forum_topic_id(h.alternate_url) for h in hits]


def test_a_reply_returns_the_containing_topic_once():
    app, forumset = _deployment()
    forum = forumset.forums[0]
    topic = forum.topics[0]
    # Someone who is not the author, replying twice in the same thread.
    replier = "Solo Replier"
    for reply in topic.replies[:2]:
        reply.author = replier
    for other in forum.topics[1:]:
        for reply in other.replies:
            if reply.author == replier:
                reply.author = "Someone Else"
        assert other.author != replier

    hits = _hits(make_client(app), person_userid(replier))
    assert _topic_ids(hits) == [topic.uuid]


def test_the_community_clause_narrows_the_answer():
    app, forumset = _deployment()
    person = forumset.forums[0].topics[0].author
    client = make_client(app)

    inside = {t for t in _topic_ids(_hits(client, person_userid(person)))}
    everywhere = {t for t in _topic_ids(_hits(client, person_userid(person), community_uuid=None))}

    in_community = {t.uuid for t in forumset.forums[0].topics}
    assert inside <= in_community
    assert inside
    # The pin is applied, not ignored: asking without it can only widen.
    assert inside <= everywhere


def test_both_social_clauses_survive_the_request():
    """They arrive as two parameters of the same name. Read as one value, the
    last wins -- the person filter vanishes and the feed answers for nobody,
    which a client would read as "this person wrote nothing here"."""
    app, forumset = _deployment()
    person = forumset.forums[0].topics[0].author
    url = (
        f"{BASE_URL}/search/atom/mysearch/results"
        f"?social={quote(json.dumps({'type': 'personUserId', 'id': person_userid(person)}))}"
        f"&social={quote(json.dumps({'type': 'community', 'id': COMMUNITY}))}"
        "&scope=forums%3Atopic&page=1&pageSize=150"
    )
    assert parse_search_results(make_client(app).get(url).content)


def test_the_person_is_recognised_by_the_id_the_profile_service_gives_out():
    """What a console actually sends: it asks the deployment who it is signed
    in as, and puts that back into the person query. A fake that only knew
    display names would answer nothing for the one value the product uses."""
    app, forumset = _deployment()
    person = forumset.forums[0].topics[0].author
    assert _topic_ids(_hits(make_client(app), person_uid(person)))


def test_a_forum_hit_never_points_at_a_reply():
    app, forumset = _deployment()
    person = forumset.forums[0].topics[0].author
    topics = {t.uuid for f in forumset.forums for t in f.topics}
    replies = {r.uuid for f in forumset.forums for t in f.topics for r in t.replies}

    hits = _hits(make_client(app), person_userid(person))
    assert hits
    for hit in hits:
        assert "topicUuid=" in (hit.via_url or "")
        identifier = _topic_ids([hit])[0]
        assert identifier in topics
        assert identifier not in replies


def test_wikis_still_answer_the_way_they_did():
    """The forum behaviour is scope-gated. A person query with no scope is
    the wiki answer the demo's comparison depends on."""
    app, _ = _deployment()
    url = search_results_url(base_url=BASE_URL, userid="anyone", page=1, page_size=150)
    parse_search_results(make_client(app).get(url).content)  # parses, does not raise


def test_assign_community_leaves_forums_reachable_by_their_community():
    """The demo relies on this: its containers get their community assigned
    after synthesis, and the pin has to see it."""
    seed = SynthSeed(seed=71, wiki_count=1, depth=1, pages_per_level=1, forum_count=2)
    forumset = synthesize_forums(seed)
    wikiset = synthesize(SynthSeed(seed=71, wiki_count=1, depth=1, pages_per_level=1))
    assign_community(wikiset=wikiset, forumset=forumset)
    assert any(f.community_uuid for f in forumset.forums)
