"""`run_demo` must honour the start body: scope to one app, and stay
interruptible via `stop_event`. Without both, the blog chip demos all three
apps and Stop does nothing. The Preview limit caps *body fetches*, not
discovery, so it is verified separately."""

from __future__ import annotations

import tempfile
import threading

from connections_export.gui.demo import run_demo


def _interchange(**kw):
    d = tempfile.mkdtemp()
    return run_demo(lambda _e: None, seed=0, delay=0, archive_dir=d, **kw).interchange


def test_app_filter_wiki_only():
    ix = _interchange(app_filter="wiki")
    assert len(ix.wikis) > 0
    assert ix.blogs == [] and ix.forums == []


def test_app_filter_blog_only():
    ix = _interchange(app_filter="blog")
    assert len(ix.blogs) > 0
    assert ix.wikis == [] and ix.forums == []


def test_app_filter_forum_only():
    ix = _interchange(app_filter="forum")
    assert len(ix.forums) > 0
    assert ix.wikis == [] and ix.blogs == []


def test_app_filter_none_runs_all_three():
    ix = _interchange(app_filter=None)
    assert ix.wikis and ix.blogs and ix.forums


def test_wiki_labels_scopes_to_one_wiki():
    # A "Whole wiki: X" chip identifies a specific wiki_label; the archive must
    # then hold ONLY that wiki, not every demo wiki ("I see all 3 wikis, I
    # should only see one"). None (unset) keeps the whole set.
    all_ix = _interchange(app_filter="wiki")
    assert len(all_ix.wikis) > 1, "demo should have multiple wikis to scope among"
    one_label = all_ix.wikis[0].label
    scoped = _interchange(app_filter="wiki", wiki_labels=[one_label])
    assert [w.label for w in scoped.wikis] == [one_label]


def test_blog_handles_scopes_to_one_blog():
    # A "Whole blog: X" chip must yield ONLY that blog -- not every demo blog,
    # and not an empty second container leaking from the shared list feed
    # (derive drops blogs whose entries feed was never crawled).
    from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs

    all_ix = _interchange(app_filter="blog")
    assert len(all_ix.blogs) > 1, "demo should have multiple blogs to scope among"
    handle = synthesize_blogs(SynthSeed(seed=0, human_names=True)).blogs[0].handle
    scoped = _interchange(app_filter="blog", blog_handles=[handle])
    assert len(scoped.blogs) == 1
    assert scoped.blogs[0].post_ids, "the scoped blog kept its posts"


def test_forum_uuids_scopes_to_one_forum():
    from connections_export.fakeserver.synth import SynthSeed, synthesize_forums

    all_ix = _interchange(app_filter="forum")
    assert len(all_ix.forums) > 1, "demo should have multiple forums to scope among"
    uuid = synthesize_forums(SynthSeed(seed=0, human_names=True)).forums[0].uuid
    scoped = _interchange(app_filter="forum", forum_uuids=[uuid])
    assert len(scoped.forums) == 1
    assert scoped.forums[0].id == uuid


def test_run_demo_accepts_a_stop_event_without_error():
    # stop_event is threaded into each crawl (which honor it between items --
    # covered by the crawler's own stop tests). A run that isn't stopped
    # behaves normally. (Interrupting mid-run is verified end-to-end via the
    # browser: Stop halts a running demo.)
    ev = threading.Event()  # never set
    ix = _interchange(app_filter="wiki", stop_event=ev)
    assert len(ix.wikis) > 0
