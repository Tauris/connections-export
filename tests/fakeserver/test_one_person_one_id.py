"""In the demo, a person has one user id, and nobody shares one.

The blog synthesizer gave every post in a blog the id of the BLOG
(`blogger0`), while giving the posts different author names -- so the author
list offered "T. Bianchi (uid: blogger0)" and "S. Nakamura (uid: blogger0)",
two people wearing one id. Elsewhere a name was used as its own id.

On a deployment `snx:userid` is what identifies a person, and it is the thing
the author filter matches on. A fake that models it as a per-container label
teaches a shape the real system does not have, and it is exactly the shape
that makes the author picker read as nonsense.
"""

from __future__ import annotations

import collections

from connections_export.fakeserver.synth import (
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.gui.demo import demo_synth_seed

SEED = demo_synth_seed(0)


def _pairs():
    """(name, userid) for everyone the demo dataset credits."""
    pairs: list[tuple[str, str]] = []
    for blog in synthesize_blogs(SEED).blogs:
        for post in blog.posts:
            pairs.append((post.author, post.author_userid))
            for comment in post.comments:
                pairs.append((comment.author, comment.author_userid))
    for wiki in synthesize(SEED).wikis:
        for page in wiki.pages:
            pairs.append((page.author, getattr(page, "author_userid", None)))
    for forum in synthesize_forums(SEED).forums:
        for topic in forum.topics:
            pairs.append((topic.author, getattr(topic, "author_userid", None)))
    return [(name, uid) for name, uid in pairs if name and uid]


def test_nobody_shares_a_user_id():
    holders = collections.defaultdict(set)
    for name, uid in _pairs():
        holders[uid].add(name)
    shared = {uid: names for uid, names in holders.items() if len(names) > 1}
    assert not shared, f"one id worn by several people: {shared}"


def test_a_person_keeps_the_same_id_everywhere():
    ids = collections.defaultdict(set)
    for name, uid in _pairs():
        ids[name].add(uid)
    split = {name: uids for name, uids in ids.items() if len(uids) > 1}
    assert not split, f"one person with several ids: {split}"


def test_an_id_is_not_just_the_name_again():
    """ "M. Lindqvist (uid: M. Lindqvist)" tells a reader nothing, and is not
    what a deployment stores."""
    assert not [(name, uid) for name, uid in _pairs() if name == uid]


def test_the_dataset_still_credits_several_people():
    """A guard that passes because everything is one person proves nothing."""
    assert len({name for name, _ in _pairs()}) >= 4


# --- across every component, as the deployment serves it -------------------
#
# The checks above read the dataclasses, and most of them carry no user id at
# all -- it is the feed emitter that attaches one. So the real check is what
# comes out of the fake and back through the adapters that read a deployment.


def _emitted_pairs():
    """(name, userid) for every author the fake SERVES, per component."""
    import httpx

    from connections_export.adapters import files as files_adapter
    from connections_export.adapters import rte as rte_adapter
    from connections_export.adapters.blogs import parse_entries_feed
    from connections_export.adapters.forums import parse_topics_feed
    from connections_export.adapters.wikis import parse_page_entry
    from connections_export.fakeserver.app import make_app
    from connections_export.fakeserver.synth import (
        synthesize_files,
        synthesize_rich_content,
    )
    from connections_export.gui.bridge import SyncASGIBridge

    community = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
    wikis = synthesize(SEED)
    blogs = synthesize_blogs(SEED)
    forums = synthesize_forums(SEED)
    app = make_app(
        wikis,
        blogset=blogs,
        forumset=forums,
        fileset=synthesize_files(SEED, community_uuid=community),
        rteset=synthesize_rich_content(SEED, community_uuid=community),
    )
    client = httpx.Client(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request),
        base_url="https://fake",
    )
    found: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)

    def note(component, name, uid):
        if name and uid:
            found[component].add((name, uid))

    library = client.get(f"/files/basic/api/communitylibrary/{community}/feed?ps=500&page=1")
    for document in files_adapter.parse_library_feed(library.content):
        note("files", document.author, document.author_userid)

    widgets = client.get(f"/communities/service/atom/community/widgets?communityUuid={community}")
    for widget in rte_adapter.parse_widget_layout(widgets.content, community_uuid=community):
        page = client.get(
            rte_adapter.rich_content_page_url(
                base_url="https://fake", community_uuid=community, resource_id=widget.resource_id
            ).replace("https://fake", "")
        )
        if page.status_code < 400:
            parsed = rte_adapter.parse_page_entry(page.content, resource_id=widget.resource_id)
            note("highlights", parsed.author, parsed.author_userid)

    blog = blogs.blogs[0]
    entries = client.get(f"/blogs/{blog.handle}/feed/entries/atom?ps=500&page=1")
    for entry in parse_entries_feed(entries.content):
        note("blogs", entry.author, entry.author_userid)

    forum = forums.forums[0]
    topics = client.get(f"/forums/atom/topics?forumUuid={forum.uuid}&ps=500&page=1")
    for topic in parse_topics_feed(topics.content):
        note("forums", topic.author, topic.author_userid)

    wiki = wikis.wikis[0]
    for page_model in wiki.pages[:3]:
        entry = client.get(f"/wikis/basic/api/wiki/{wiki.label}/page/{page_model.label}/entry")
        if entry.status_code < 400:
            parsed = parse_page_entry(entry.content)
            note("wikis", parsed.author, parsed.author_userid)

    return found


def test_every_component_credits_authors_with_an_id():
    """A component that emits no id leaves its people out of the author
    picker entirely -- they cannot be offered, so they cannot be filtered
    for."""
    found = _emitted_pairs()
    missing = [
        component for component in ("files", "blogs", "forums", "wikis") if not found[component]
    ]
    assert not missing, f"components serving authors with no user id: {missing}"


def test_one_person_keeps_one_id_across_components():
    """The point of the whole exercise: the same human, credited the same way,
    wherever they appear."""
    found = _emitted_pairs()
    ids = collections.defaultdict(set)
    for pairs in found.values():
        for name, uid in pairs:
            ids[name].add(uid)
    split = {name: uids for name, uids in ids.items() if len(uids) > 1}
    assert not split, f"one person credited with several ids across components: {split}"


def test_the_id_is_opaque_rather_than_the_name_again():
    """the published API reference: content feeds carry the directory
    GUID, and the display name is the thing NOT to match on. An id derived
    visibly from the name teaches the opposite."""
    found = _emitted_pairs()
    for pairs in found.values():
        for name, uid in pairs:
            assert uid != name
            assert name.split()[-1].casefold() not in uid.casefold(), (name, uid)
