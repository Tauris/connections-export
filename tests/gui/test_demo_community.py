"""The demo dataset's community.

The fakeserver had no notion of a community at all, so `run_demo`'s
interchange never contained one and the community surfaces (the archive
list's `community:...` detail, the reader, the PDF) had nothing to be
designed against without a live deployment.

A community here is exactly what it is on the real system as far as this
tool is concerned: a grouping that says which wiki, blog, and forum belong
together. The derive layer already assembled communities from the
`snx:communityUuid` marker on a container's feed -- the demo simply never
emitted one.
"""

from __future__ import annotations

from connections_export.gui.demo import run_demo


def _interchange(tmp_path):
    return run_demo(
        (lambda _event: None), seed=0, delay=0, archive_dir=tmp_path / "archive"
    ).interchange


def test_demo_interchange_has_a_parent_and_a_child_community(tmp_path):
    """The demo holds a SET of communities, because that is now a shape the
    exporter has to get right and one community cannot exercise it.

    One level of nesting, which is what the real deployment does. This asserted "exactly one" when
    the demo emitted
    its first community at all; the shape it describes has changed on purpose.
    """
    from connections_export.fakeserver.model import (
        DEMO_CHILD_COMMUNITY_TITLE,
        DEMO_COMMUNITY_TITLE,
    )

    communities = _interchange(tmp_path).communities

    titles = {community.title for community in communities}
    assert titles == {DEMO_COMMUNITY_TITLE, DEMO_CHILD_COMMUNITY_TITLE}


def test_the_child_community_holds_content_of_its_own(tmp_path):
    """A child with nothing in it could not show what capturing a set means,
    and every test below it would describe a fiction."""
    from connections_export.fakeserver.model import DEMO_CHILD_COMMUNITY_TITLE

    child = next(
        c for c in _interchange(tmp_path).communities if c.title == DEMO_CHILD_COMMUNITY_TITLE
    )

    assert child.forum_ids or child.wiki_id or child.blog_id, "the child community is empty"


def test_demo_community_has_a_human_title_not_a_bare_uuid(tmp_path):
    """The console renders `community.title || community.id`, so an
    untitled community shows up as a raw uuid -- unusable for design work."""
    community = _interchange(tmp_path).communities[0]

    assert community.title
    assert community.title != community.id


def test_demo_community_owns_the_wiki_blog_and_forum(tmp_path):
    community = _interchange(tmp_path).communities[0]

    assert community.wiki_id, "the community's wiki is missing"
    assert community.blog_id, "the community's blog is missing"
    assert community.forum_ids, "the community's forum is missing"


def test_demo_community_members_are_real_containers_in_the_same_interchange(tmp_path):
    """The ids must resolve against the interchange's own containers -- a
    community pointing at ids nothing else knows about would render as
    empty sections rather than as the demo's content."""
    interchange = _interchange(tmp_path)
    community = interchange.communities[0]

    assert community.wiki_id in {wiki.id for wiki in interchange.wikis}
    assert community.blog_id in {blog.id for blog in interchange.blogs}
    assert set(community.forum_ids) <= {forum.id for forum in interchange.forums}


def test_only_one_blog_joins_the_community_and_the_rest_stay_standalone(tmp_path):
    """`DerivedCommunity` holds a single `blog_id` (plus a single
    `ideation_blog_id`), mirroring the real system. Putting every demo blog in
    the community would silently drop all but the last, so exactly one joins
    -- which also gives the demo a blog inside the community and one outside."""
    interchange = _interchange(tmp_path)
    community = interchange.communities[0]

    in_community = [blog for blog in interchange.blogs if blog.community_uuid == community.id]
    assert len(in_community) == 1
    assert in_community[0].id == community.blog_id
    assert any(blog.community_uuid is None for blog in interchange.blogs), (
        "the demo should keep a standalone blog for contrast"
    )


def test_only_one_wiki_joins_the_community_and_the_rest_stay_standalone(tmp_path):
    """A community holds at most one wiki, so `DerivedCommunity.wiki_id` is a
    single field. Stamping every demo wiki would have meant all but one were
    silently dropped -- and would have shown a shape the source system cannot
    produce."""
    interchange = _interchange(tmp_path)
    community = interchange.communities[0]

    in_community = [w for w in interchange.wikis if w.community_uuid == community.id]
    assert len(in_community) == 1
    assert in_community[0].id == community.wiki_id
    assert any(w.community_uuid is None for w in interchange.wikis), (
        "the demo should keep a standalone wiki for contrast"
    )


def test_the_demo_serves_as_many_entries_as_there_is_written_content_for(tmp_path):
    """`content.py` holds a body, a comment thread and a reply thread per
    title, 8 titles per pool. The SynthSeed default of 3 surfaced under half
    of it -- and gave anything that orients by time one or two months to work
    with."""
    interchange = _interchange(tmp_path)

    for blog in interchange.blogs:
        assert len(blog.posts) == 8, f"{blog.title} has {len(blog.posts)} posts"
    for forum in interchange.forums:
        assert len(forum.topics) == 8, f"{forum.title} has {len(forum.topics)} topics"


def test_no_demo_entry_repeats_another_entrys_title(tmp_path):
    """The title pools hold 8 each and wrap past that, so a 9th entry would
    repeat the 1st one's title and body."""
    interchange = _interchange(tmp_path)

    titles = [p.title for b in interchange.blogs for p in b.posts.values()]
    titles += [t.title for f in interchange.forums for t in f.topics.values()]

    assert len(set(titles)) == len(titles), "an entry repeats another's title"


def test_demo_entries_span_enough_months_to_orient_by(tmp_path):
    interchange = _interchange(tmp_path)

    for blog in interchange.blogs:
        months = {p.created[:7] for p in blog.posts.values()}
        assert len(months) >= 3, f"{blog.title} spans only {sorted(months)}"


def test_the_ids_the_pickers_hand_out_exist_in_what_the_demo_serves(tmp_path):
    """Container counts feed the seeded RNG's draw sequence, so changing one
    shifts every uuid generated after it. Raising the entry count in
    `run_demo` without raising it in the pickers that rebuild the same sets
    left them handing out ids from a dataset the demo no longer served -- a
    community ingest then asked the fakeserver for a forum uuid that did not
    exist and got a 404."""
    import asyncio

    import httpx

    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.gui import app as app_module
    from connections_export.gui import make_app
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    app_module.ARCHIVES_BASE = tmp_path
    app = make_app(demo=True, demo_delay=0)

    async def components():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.get(
                "/api/community-components",
                params={"community_uuid": DEMO_COMMUNITY_UUID, "base_url": DEMO_SAMPLE_BASE_URL},
            )
            return response.json()["components"]

    served = _interchange(tmp_path)
    known = (
        {wiki.label for wiki in served.wikis}
        | {wiki.id for wiki in served.wikis}
        | {blog.id for blog in served.blogs}
        | {blog.handle for blog in served.blogs if blog.handle}
        | {forum.id for forum in served.forums}
        # A files component is addressed by the COMMUNITY uuid rather than a
        # container id -- that is how the library endpoint works -- so the id
        # to check it against is the library's own community, not its id.
        | {lib.community_uuid for lib in served.file_libraries if lib.community_uuid}
    )

    offered = {(c["kind"], c["id"]) for c in asyncio.run(components())}
    missing = [pair for pair in offered if pair[1] not in known]

    assert not missing, f"the picker offers ids the demo does not serve: {missing}"


def test_demo_chip_urls_point_at_entities_the_fakeserver_serves():
    """Same failure mode by a different route: the chips are built by
    rebuilding the sets too, so they drift the moment the recipe changes.

    Compared against the FAKESERVER's own identifiers, which is what a chip
    URL is resolved against -- the derived model re-keys a blog by uuid,
    so it is the wrong thing to check a `/blogs/{handle}` URL against.
    """
    from connections_export.fakeserver.prototype import build_prototype_wikiset
    from connections_export.fakeserver.synth import synthesize_blogs, synthesize_forums
    from connections_export.gui.demo import demo_sample_urls, demo_synth_seed

    seed = demo_synth_seed()
    labels = {wiki.label for wiki in build_prototype_wikiset().wikis}
    handles = {blog.handle for blog in synthesize_blogs(seed).blogs}
    forums = {forum.uuid for forum in synthesize_forums(seed).forums}

    checks = {
        "wiki-all": lambda url: any("/wiki/" + label in url for label in labels),
        "blog-all": lambda url: any("/blogs/" + handle in url for handle in handles),
        "forum-all": lambda url: any(uuid in url for uuid in forums),
    }
    for chip in demo_sample_urls():
        check = checks.get(chip["kind"])
        if check:
            assert check(chip["url"]), f"{chip['kind']} chip points nowhere: {chip['url']}"
