"""A second wave of content, so the demo can show an update finding something.

Without this, a demo capture followed by a demo update finds nothing, and the
feature that most needs demonstrating is the one nobody can see work.

The wave a synthesized item belongs to is a pure function of the seed and the
item's own index -- never the wall clock. What the SERVER reveals is separate
state (`visible_wave`), so "a week passes on the live system" is a thing the
demo does deliberately, not something that happens while you watch.
"""

from __future__ import annotations

from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs, synthesize_forums

SEED = SynthSeed(
    seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True, late_reply=True
)


def test_some_content_belongs_to_a_later_wave():
    blogset = synthesize_blogs(SEED)

    waves = {post.wave for blog in blogset.blogs for post in blog.posts}
    assert waves == {0, 1}, "the dataset has no second wave to discover"


def test_the_first_wave_is_the_larger_one():
    """An update should find a handful of new things against a substantial
    archive -- not a dataset that doubles, which would flatter the feature."""
    blogset = synthesize_blogs(SEED)
    posts = [p for blog in blogset.blogs for p in blog.posts]

    later = [p for p in posts if p.wave == 1]
    assert 0 < len(later) < len(posts) / 2


def test_later_content_is_stamped_later():
    """A wave-1 item that looked older than wave 0 would be invisible to a
    date-filtered update -- the demo would prove the opposite of the point."""
    blogset = synthesize_blogs(SEED)
    posts = [p for blog in blogset.blogs for p in blog.posts]

    newest_first_wave = max(p.published for p in posts if p.wave == 0)
    oldest_second_wave = min(p.published for p in posts if p.wave == 1)
    assert oldest_second_wave > newest_first_wave


def test_waves_are_a_pure_function_of_the_seed():
    """Determinism is load-bearing across this whole project; a wave assigned
    from a clock would make every demo assertion a coin toss."""
    first = synthesize_blogs(SEED)
    second = synthesize_blogs(SEED)

    def shape(blogset):
        return [(p.uuid, p.wave, p.published) for b in blogset.blogs for p in b.posts]

    assert shape(first) == shape(second)


def test_forums_have_a_second_wave_too():
    forumset = synthesize_forums(SEED)

    waves = {topic.wave for forum in forumset.forums for topic in forum.topics}
    assert waves == {0, 1}


def test_a_later_reply_lands_on_an_unchanged_first_wave_topic():
    """The comments hole, made visible. A reply added to an otherwise
    untouched topic is exactly what a `since`-filtered topics feed may miss --
    so the demo must contain one, or the re-check option has nothing to catch
    and nothing to demonstrate.
    """
    forumset = synthesize_forums(SEED)

    hidden = [
        (forum, topic)
        for forum in forumset.forums
        for topic in forum.topics
        if topic.wave == 0 and any(reply.wave == 1 for reply in topic.replies)
    ]
    assert hidden, "no late reply on an unchanged topic -- the hole is undemonstrable"
