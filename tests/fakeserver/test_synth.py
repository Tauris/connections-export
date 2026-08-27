"""The synthesizer builds a wiki tree from a declarative seed,
deterministically, and can reproduce the documented nasty cases."""

import uuid

from connections_export.fakeserver.synth import SynthSeed, synthesize


def _count_tree(wiki, parent_uuid=None):
    kids = wiki.children_of(parent_uuid)
    total = len(kids)
    for kid in kids:
        total += _count_tree(wiki, kid.uuid)
    return total


def test_seed_produces_requested_depth_and_pages_per_level():
    seed = SynthSeed(seed=1, wiki_count=1, depth=3, pages_per_level=2)
    wikiset = synthesize(seed)

    assert len(wikiset.wikis) == 1
    wiki = wikiset.wikis[0]

    top = wiki.top_level_pages()
    assert len(top) == 2  # pages_per_level at depth 1

    # Walk down: each level should have pages_per_level children per
    # parent, for `depth` levels total.
    level = top
    for _ in range(seed.depth - 1):
        next_level = []
        for page in level:
            kids = wiki.children_of(page.uuid)
            assert len(kids) == seed.pages_per_level
            next_level.extend(kids)
        level = next_level
    # The last level's pages have no further children.
    for page in level:
        assert wiki.children_of(page.uuid) == []


def test_every_page_has_an_explicit_ordinal_and_resolvable_parent():
    seed = SynthSeed(seed=2, wiki_count=1, depth=2, pages_per_level=3)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]

    uuids = {p.uuid for p in wiki.pages}
    for page in wiki.pages:
        assert isinstance(page.ordinal, int)
        if page.parent_uuid is not None:
            assert page.parent_uuid in uuids


def test_determinism_same_seed_same_bytes():
    seed = SynthSeed(seed=42, wiki_count=2, depth=2, pages_per_level=2, comments_per_page=2)
    first = synthesize(seed)
    second = synthesize(seed)

    def snapshot(wikiset):
        out = []
        for wiki in wikiset.wikis:
            out.append((wiki.uuid, wiki.label, wiki.title, wiki.created, wiki.modified))
            for page in wiki.pages:
                out.append(
                    (
                        page.uuid,
                        page.label,
                        page.title,
                        page.parent_uuid,
                        page.ordinal,
                        page.created,
                        page.modified,
                        page.version_label,
                        tuple(c.uuid for c in page.comments),
                    )
                )
        return out

    assert snapshot(first) == snapshot(second)
    assert first.base_timestamp_ms == second.base_timestamp_ms


def test_different_seed_produces_different_uuids():
    a = synthesize(SynthSeed(seed=1, wiki_count=1, depth=1, pages_per_level=1))
    b = synthesize(SynthSeed(seed=2, wiki_count=1, depth=1, pages_per_level=1))
    assert a.wikis[0].pages[0].uuid != b.wikis[0].pages[0].uuid


def test_a_files_reported_size_is_the_number_of_bytes_it_serves():
    """The fixture must not claim a size it does not deliver.

    A fixture declaring a real-world size (a 2 MB deck, a 733 KB photo) while
    serving a small stub makes the console honestly report "Size 144.7 KB /
    Captured 92 bytes" -- which reads as catastrophic data loss in the one
    tool whose whole promise is fidelity. `size` is taken from the body, and
    this test keeps it that way.
    """
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.fakeserver.synth import synthesize_files

    fileset = synthesize_files(SynthSeed(seed=3), community_uuid=DEMO_COMMUNITY_UUID)
    files = [f for lib in fileset.libraries for f in lib.files]
    assert files, "the demo community should hold files"
    for f in files:
        assert f.size == len(f.body), f"{f.name} reports {f.size} bytes but serves {len(f.body)}"

    # Two documents can share a name and be different files -- identical
    # bodies would hide whether the writer kept them apart.
    bodies = [f.body for f in files]
    assert len(set(bodies)) == len(bodies), "two files were given identical bytes"


def test_each_app_gets_its_own_id_stream():
    """Two apps must never hand out the same id.

    Every synthesizer seeded its `_IdFactory` from `random.Random(seed.seed)`,
    the same seed for all five, so the wiki's Nth id, the blog's Nth id and the
    file's Nth id came out identical. A demo community run had six ids claimed
    by two apps at once. Real ids do not collide across apps, and anything that
    keys entities by id alone loses one of each colliding pair -- the live
    console dropped two of the community's eight files exactly that way.
    """
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.fakeserver.synth import (
        synthesize_blogs,
        synthesize_files,
        synthesize_forums,
        synthesize_rich_content,
    )

    seed = SynthSeed(seed=7, wiki_count=1, depth=2, pages_per_level=2)
    files = synthesize_files(seed, community_uuid=DEMO_COMMUNITY_UUID)
    rich = synthesize_rich_content(seed, community_uuid=DEMO_COMMUNITY_UUID)
    streams = {
        "wiki": {p.uuid for w in synthesize(seed).wikis for p in w.pages},
        "blog": {e.uuid for b in synthesize_blogs(seed).blogs for e in b.posts},
        "forum": {t.uuid for f in synthesize_forums(seed).forums for t in f.topics},
        "files": {f.uuid for lib in files.libraries for f in lib.files},
        "rich_content": {p.resource_id for c in rich.communities for p in c.pages},
    }
    names = sorted(streams)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            shared = streams[a] & streams[b]
            assert not shared, f"{a} and {b} share ids: {sorted(shared)[:3]}"


def test_the_per_app_id_streams_are_still_deterministic():
    """Salting per app must not cost the fixture its "same seed, same bytes"
    guarantee -- `random.Random` derives its state from a string via SHA-512,
    not `hash`, so the stream is stable across runs and interpreters."""
    seed = SynthSeed(seed=7, wiki_count=1, depth=2, pages_per_level=2)
    first = [p.uuid for w in synthesize(seed).wikis for p in w.pages]
    second = [p.uuid for w in synthesize(seed).wikis for p in w.pages]
    assert first == second


def test_uuids_are_well_formed():
    wikiset = synthesize(SynthSeed(seed=7, wiki_count=1, depth=1, pages_per_level=2))
    for page in wikiset.wikis[0].pages:
        uuid.UUID(page.uuid)  # raises if malformed


def test_comments_per_page_honored():
    seed = SynthSeed(seed=3, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=5)
    wikiset = synthesize(seed)
    page = wikiset.wikis[0].pages[0]
    assert len(page.comments) == 5


def test_nasty_case_deep_nesting():
    seed = SynthSeed(
        seed=4,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        deep_nesting=True,
        deep_nesting_depth=15,
    )
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]

    # Find the longest chain by walking from any leaf back to a root.
    max_depth = 0
    for page in wiki.pages:
        if wiki.children_of(page.uuid) == []:
            depth = 1
            cur = page
            while cur.parent_uuid is not None:
                cur = wiki.page_by_uuid(cur.parent_uuid)
                depth += 1
            max_depth = max(max_depth, depth)
    assert max_depth >= 15


def test_nasty_case_high_comment_count():
    seed = SynthSeed(
        seed=5,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        high_comment_count=True,
        high_comment_count_n=250,
    )
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    assert any(len(p.comments) >= 250 for p in wiki.pages)


def test_nasty_case_unicode_rtl():
    seed = SynthSeed(seed=6, wiki_count=1, depth=1, pages_per_level=2, unicode_rtl=True)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    titles = " ".join(p.title for p in wiki.pages)
    # RTL Hebrew/Arabic range or a non-ASCII codepoint should show up.
    assert any(ord(ch) > 0x2000 for ch in titles)


def test_nasty_case_empty_pages():
    seed = SynthSeed(seed=8, wiki_count=1, depth=1, pages_per_level=2, empty_pages=True)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    assert any(p.body_html == "" for p in wiki.pages)


def test_nasty_case_duplicate_sibling_ordinals():
    seed = SynthSeed(seed=9, wiki_count=1, depth=1, pages_per_level=3, duplicate_ordinals=True)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    ordinals = [p.ordinal for p in wiki.top_level_pages()]
    assert len(ordinals) != len(set(ordinals))
