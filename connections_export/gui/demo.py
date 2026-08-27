"""`run_demo`: the real pipeline, only the server is synthetic.

    synthesize(seed) -> make_app (fakeserver)
        -> crawl(config, real HttpClient via the ASGI bridge, archive=<temp>, emit=emit)
        -> derive(archive)

It emits the crawler's *real* event stream through `emit` -- nothing
here fabricates an event; `emit` is handed straight to `crawl`.
`config.base_url="https://fake"` and `hcl_hosts=["files.example.corp"]`
so the cross-app asset path is exercised for real, exactly as
the design specifies.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import (
    CrawlResult,
    crawl,
    crawl_blogs,
    crawl_files,
    crawl_forums,
    crawl_rich_content,
)
from connections_export.crawler.events import Emit, Event, RunComplete, RunStarted
from connections_export.crawler.report import CrawlReport
from connections_export.derive import Interchange, derive
from connections_export.fakeserver import (
    SynthSeed,
    make_app,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.fakeserver.model import (
    DEMO_CHILD_COMMUNITY_TITLE,
    DEMO_CHILD_COMMUNITY_UUID,
    DEMO_COMMUNITY_TITLE,
    DEMO_COMMUNITY_UUID,
    assign_community,
)
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.fakeserver.synth import (
    impersonated_person,
    synthesize_files,
    synthesize_rich_content,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient, RetryPolicy

#: A deployment host distinct from the demo's `base_url`, exactly as
#: the design specifies -- exercises the crawler's cross-app asset
#: classification path (`assets.classify`, consulting `hcl_hosts`) for
#: real, even though the fake's own cross-app image reference happens
#: to resolve to the base host too (it's a relative href in the
#: synthesized body HTML). `files.example.corp` is an RFC 2606-style
#: placeholder (hygiene guard: any `*.example.*` host is allowed).
DEMO_HCL_HOSTS = ["files.example.corp"]

#: The fixed placeholder deployment root the demo's sample URL chips are
#: built against. Never a real deployment -- `*.example` satisfies the
#: guard's "any host with an `example` label" allowance -- and never used
#: to make a request; it only has to round-trip through
#: `wiki_url.parse_url`.
DEMO_SAMPLE_BASE_URL = "https://demo.connections.example"


#: The demo's blogs and forums carry as many entries as there is written
#: content for: `content.py` holds a body, a comment thread and (for forums) a
#: full reply thread per title, and there are 8 titles in each pool. The
#: `SynthSeed` default of 3 surfaced under half of it, which is also too few
#: to show anything that orients by time -- the month markers in the reader
#: had one or two months to work with. Not raised past 8: the title pools wrap
#: at that point, so a 9th entry repeats the 1st one's title and body.
DEMO_ENTRIES_PER_CONTAINER = 8


def demo_synth_seed(seed: int = 0) -> SynthSeed:
    """THE demo's blog/forum recipe. Every caller that rebuilds the demo
    dataset must use this one.

    Container counts feed the seeded RNG's draw sequence, so changing one
    shifts every uuid generated after it. When the entry count was raised
    here but not in the pickers that rebuild the same sets to answer
    `/api/demo-urls` and `/api/community-components`, those handed out ids
    from a dataset the demo no longer served -- and a community ingest asked
    the fakeserver for a forum uuid that did not exist, which came back as a
    404 from the fake server.
    """
    return SynthSeed(
        seed=seed,
        human_names=True,
        # So a demo can show an update catching a reply added to a topic that
        # did not otherwise change -- the one thing a date-filtered topics
        # feed may miss.
        late_reply=True,
        posts_per_blog=DEMO_ENTRIES_PER_CONTAINER,
        topics_per_forum=DEMO_ENTRIES_PER_CONTAINER,
    )


def demo_sample_urls() -> list[dict]:
    """Sample human page URLs for the Select & Tailor screen's demo
    drag-drop chips, derived from the same fakeserver
    entities `run_demo` actually serves -- never hardcoded -- so the
    chips are guaranteed to point at *real* ids/labels/handles/uuids in
    the demo dataset. One chip per URL kind the setup screen's parser
    (`connections_export.gui.wiki_url.parse_url`) recognises, plus one
    deliberately-unrecognised URL, in exactly the shapes `parse_url`
    accepts (verified by `tests/gui/test_demo_urls.py`'s round-trip):

    - ``wiki-all`` / ``wiki-page``: the human UI's bare (`/wikis/.../wiki/
      {label}`) and hash-route (`#/wiki/{label}/page/{page_label}`) forms.
    - ``blog-all`` / ``blog-post``: `/blogs/{handle}` and
      `/blogs/{handle}/entry/{slug}`.
    - ``forum-all`` / ``forum-thread``: `/forums/html/forum?id={uuid}` and
      `/forums/html/threadTopic?id={topic}&forumUuid={forum}` (the topic
      URL needs the `forumUuid` param too -- `parse_url`'s thread shape
      only reads the forum uuid from that query param, never from `id`).
    - ``unrecognized``: a path `parse_url` matches no shape for at all.

    The first wiki + its first page, the first blog + its first post, and
    the first forum + its first topic -- `human_names=True`, the same
    curated-name switch `run_demo` uses, so the chip labels read like the
    demo's own content."""
    base = DEMO_SAMPLE_BASE_URL

    wikiset = build_prototype_wikiset()
    wiki = wikiset.wikis[0]
    page = wiki.pages[0]

    bf_seed = demo_synth_seed()
    blogset = synthesize_blogs(bf_seed)
    blog = blogset.blogs[0]
    post = blog.posts[0]

    forumset = synthesize_forums(bf_seed)
    forum = forumset.forums[0]
    topic = forum.topics[0]

    return [
        {
            "kind": "wiki-all",
            "label": f"Whole wiki: {wiki.title}",
            "url": f"{base}/wikis/home/wiki/{wiki.label}",
        },
        {
            "kind": "wiki-page",
            "label": f"Single wiki page: {page.title}",
            "url": f"{base}/wikis/home?lang=en_us#/wiki/{wiki.label}/page/{page.label}",
        },
        {
            "kind": "blog-all",
            "label": f"Whole blog: {blog.title}",
            "url": f"{base}/blogs/{blog.handle}",
        },
        {
            "kind": "blog-post",
            "label": f"Single blog post: {post.title}",
            "url": f"{base}/blogs/{blog.handle}/entry/{post.slug}",
        },
        {
            "kind": "forum-all",
            "label": f"Whole forum: {forum.title}",
            "url": f"{base}/forums/html/forum?id={forum.uuid}",
        },
        {
            "kind": "forum-thread",
            "label": f"Single thread: {topic.title}",
            "url": f"{base}/forums/html/threadTopic?id={topic.uuid}&forumUuid={forum.uuid}",
        },
        {
            "kind": "community-all",
            "label": f"Whole community: {DEMO_COMMUNITY_TITLE}",
            # The community start page shape `parse_url`'s `_try_community_shape`
            # recognises: `communities` in the path plus a `communityUuid` query
            # parameter. `assign_community` puts the demo's wikis, one blog, and
            # forums under exactly this uuid.
            "url": (
                f"{base}/communities/service/html/communitystart"
                f"?communityUuid={DEMO_COMMUNITY_UUID}"
            ),
        },
        {
            "kind": "unrecognized",
            "label": "An unrelated page (won't be recognised)",
            "url": f"{base}/something/else",
        },
    ]


def _combined_report(reports: list[CrawlReport]) -> CrawlReport:
    """Merge the three crawls' reports into one so the demo's single
    closing `RunComplete` carries the whole run's totals (counts summed,
    failures summed, orphans/truncation concatenated)."""
    combined = CrawlReport()
    for report in reports:
        for key, value in report.counts.items():
            combined.counts[key] = combined.counts.get(key, 0) + value
        for key, value in report.failures_by_category.items():
            combined.failures_by_category[key] = combined.failures_by_category.get(key, 0) + value
        combined.possibly_truncated.extend(report.possibly_truncated)
        combined.orphans.extend(report.orphans)
        combined.pages_crawled += report.pages_crawled
    return combined


@dataclass
class DemoResult:
    """What a completed demo run threads back to its caller: the
    crawler's own result, the derived model, and where the archive
    landed (Returns/threads the derived model + the
    archive dir)."""

    crawl_result: CrawlResult
    interchange: Interchange
    archive_dir: Path

    @property
    def archive(self):
        """The archive this run wrote, opened."""
        from connections_export.archive.store import Archive  # noqa: PLC0415

        return Archive.open(self.archive_dir)


def _demo_run_clock(seed) -> Callable[[], str]:
    """The clock a demo RUN stamps itself with.

    Positioned inside the dataset's own timeline -- after everything in the
    first wave, before anything in the second -- because that is where a first
    capture happens in the story the demo tells. A wall-clock stamp would put
    the run years after all of its own content, and every cutoff derived from
    it would then exclude the entire dataset.

    Deterministic, like everything else here: same seed, same stamps.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from connections_export.fakeserver.synth import _SECOND_WAVE_AFTER_DAYS  # noqa: PLC0415

    # Halfway into the gap between the waves.
    start = seed.base_timestamp + int(_SECOND_WAVE_AFTER_DAYS * 86400 * 0.5)
    counter = {"n": 0}

    def tick() -> str:
        stamp = start + counter["n"]
        counter["n"] += 1
        return datetime.fromtimestamp(stamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return tick


def run_demo(
    emit: Emit,
    *,
    seed: int = 0,
    archive_dir: str | Path | None = None,
    delay: float = 0.03,
    sleep: Callable[[float], None] | None = None,
    app_filter: str | Iterable[str] | None = None,
    max_entries: int | None = None,
    wiki_labels: list[str] | None = None,
    #: Which community a scoped wiki capture is for. The demo's own wiki has
    #: no other way to say: the marker lives on the wikis list feed and a
    #: scoped crawl skips it.
    community_uuid: str | None = None,
    community_title: str | None = None,
    blog_handles: list[str] | None = None,
    entry_slugs: list[str] | None = None,
    forum_uuids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    stop_event: object | None = None,
    update: bool = False,
    #: How much of the dataset this run's server reveals. `None` (the default)
    #: reveals everything, so an ordinary demo is exactly what it always was.
    #: The extend/update story passes `0` for its first capture and `1` for the
    #: run after "a week has passed" -- holding content back is something that
    #: story opts into, never something every demo pays for.
    wave: int | None = None,
) -> DemoResult:
    # `app_filter` ("wiki"|"blog"|"forum"|None) scopes the demo to one app so a
    # blog chip demos the blog, not the whole set; None = all three (the
    # "Run the demo pipeline" button). `max_entries` caps pages/posts/topics so
    # the Preview limit is honoured. `stop_event` (a threading.Event) lets the
    # Stop button interrupt a demo run between items. All three are honoured
    # here: a demo that ignored them would crawl everything, unstoppably, and
    # so would not demonstrate the controls it is showing off.
    """Run the genuine crawler -> archive -> derive pipeline against
    the in-process fakeserver standing in for a real HCL Connections
    deployment. `emit` receives the crawler's own events, unmodified in
    content -- `delay` only paces *when* each one arrives (a small
    sleep after each `emit` call) so a live viewer can actually watch
    the run happen; pass `delay=0` (as tests do) to run at full speed.

    `archive_dir`, if given, is used (and created) as the archive root;
    otherwise a fresh temporary directory is created and used -- not
    cleaned up afterward, since the whole point is that the caller (or
    a human at the CLI) can inspect the real archive a demo run
    produced.
    """
    sleep = sleep if sleep is not None else time.sleep

    # The curated, believable demo dataset (fixed wikis, pages with their own
    # topic-specific prose, comments, images, attachments) so the demo reads
    # as a genuine deployment. This is the single source of the demo's
    # content. `seed` no longer varies the data -- the curated set is fixed --
    # but is kept for API compatibility (callers/tests still pass it harmlessly).
    wikiset = build_prototype_wikiset()
    # Stage-2 phase 6: the demo now serves all
    # three apps. The curated wikis are unchanged; small, human-readable
    # blog and forum datasets are synthesized alongside them (a couple of
    # blogs with a few posts + comments, a couple of forums with topics
    # and a nested reply thread -- the SynthSeed defaults are already this
    # shape). `human_names=True` draws demo-friendly titles/authors. Same-
    # seed-in => same dataset out, so a demo run stays deterministic.
    bf_seed = demo_synth_seed(seed)
    blogset = synthesize_blogs(bf_seed)
    forumset = synthesize_forums(bf_seed)
    # The community's file library. Unlike the other three sets this one is
    # built FOR a community rather than assigned to one afterwards: a library
    # is addressed by the community uuid and has no identity apart from it,
    # which is why `assign_community` below does not take it.
    fileset = synthesize_files(bf_seed, community_uuid=DEMO_COMMUNITY_UUID)
    # The community's Highlights pages -- the front page people wrote ABOUT
    # everything else. Built for the community, like the file library.
    rteset = synthesize_rich_content(bf_seed, community_uuid=DEMO_COMMUNITY_UUID)
    # The demo's containers all belong to one community, so the community
    # surfaces (the archive list's `community:...` detail, the reader, the
    # PDF) have something to show without a live deployment. A community is
    # only ever a grouping here -- no documents of its own, just the
    # membership markers the containers carry into `derive`.
    assign_community(
        wikiset=wikiset,
        blogset=blogset,
        forumset=forumset,
        child_uuid=DEMO_CHILD_COMMUNITY_UUID,
        child_title=DEMO_CHILD_COMMUNITY_TITLE,
    )
    # Each run builds its own server, so the wave is chosen at construction
    # rather than advanced through a live one -- same result, and it cannot
    # drift between runs.
    app = make_app(
        wikiset,
        blogset=blogset,
        forumset=forumset,
        fileset=fileset,
        rteset=rteset,
        visible_wave=wave,
        # The demo is signed in, as one of the people in its own data. A demo
        # where nobody is you cannot answer "only me", which is the whole
        # reason an author filter exists.
        current_user=impersonated_person(bf_seed),
    )
    client = HttpClient(
        transport=SyncASGIBridge(app),
        retry_policy=RetryPolicy(max_attempts=2),
        sleep=lambda _seconds: None,  # no real delay for the fake's own retries, if any
    )

    root = (
        Path(archive_dir)
        if archive_dir is not None
        # A self-describing default when a caller doesn't pick one: the name
        # plainly announces synthetic data. `hcl-serve`
        # passes an explicit, timestamped dir via `_new_run_archive_dir`.
        else Path(tempfile.mkdtemp(prefix="hcl-DEMO-FAKE-DATA-"))
    )
    archive = Archive.open(root)
    config = Config(
        base_url="https://fake",
        hcl_hosts=list(DEMO_HCL_HOSTS),
        output_dir=root,
        fetch="update" if update else "resume",
    )

    # A demo run stamps its provenance with a clock, and the next update reads
    # its cutoff back from that stamp. Using the WALL clock here would date a
    # run "now" against content dated years ago, so every cutoff would exclude
    # the entire dataset -- and an update would report, perfectly consistently,
    # that nothing had changed. The demo's clock therefore belongs to the
    # demo's own timeline: it sits after the first wave and before the second,
    # which is exactly where a first capture happens in the story being told.
    demo_clock = _demo_run_clock(bf_seed)

    since = None
    if update:
        # The same resolution the CLI performs for `--into`, rather than
        # reaching past it for the cutoff alone. "This run is an update of that
        # archive" is one question with three answers -- mode, target and
        # cutoff -- and one function has always owned it. Reading only the
        # cutoff here is exactly how the CLI and the console drifted apart in
        # the first place.
        from connections_export.crawler.session import resolve_update_plan  # noqa: PLC0415

        since = resolve_update_plan(config.model_copy(update={"into": Path(archive.root)})).since

    def paced_emit(event: Event) -> None:
        emit(event)
        if delay:
            sleep(delay)

    # The demo runs three crawls (wikis, then blogs, then forums) into the
    # SAME archive, but presents them to a live viewer as ONE run: each
    # crawl emits its own RunStarted/RunComplete, and the front-end closes
    # the live console on the first RunComplete it sees -- which would cut
    # the stream off after the wikis, before any blog/forum node appears.
    # So coalesce the lifecycle: keep the first RunStarted, drop every
    # per-crawl RunComplete, and emit a single combined RunComplete after
    # all three finish. Everything between (fetched/page_derived/blog_post
    # _derived/forum_topic_derived/...) streams through unchanged, so the
    # live tree now fills with all three apps before the run closes.
    started = False

    def demo_emit(event: Event) -> None:
        nonlocal started
        if isinstance(event, RunStarted):
            if started:
                return
            started = True
        elif isinstance(event, RunComplete):
            return  # one closing RunComplete is emitted at the end instead
        paced_emit(event)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # A blog/forum URL identifies its target by *handle*/uuid + entry *slug*;
    # the crawler filters by blog uuid + post id. Resolve the human identifiers
    # to the demo dataset's own uuids/ids so a "Whole blog: X"/"Single blog
    # post: Y" chip yields only X / only Y in the archive (mirrors wiki_labels).
    blog_uuids: list[str] | None = None
    entry_ids: list[str] | None = None
    if blog_handles is not None:

        def _blog_for(identifier):
            # A chip gives a handle; a community component gives a uuid.
            return blogset.blog_by_handle(identifier) or next(
                (b for b in blogset.blogs if b.uuid == identifier), None
            )

        scoped_blogs = [b for h in blog_handles if (b := _blog_for(h))]
        blog_uuids = [b.uuid for b in scoped_blogs]
        if entry_slugs is not None:
            entry_ids = [
                post.uuid
                for b in scoped_blogs
                for slug in entry_slugs
                if (post := b.post_by_slug(slug))
            ]

    def _runs(app_name: str) -> bool:
        """Whether `app_name` is in scope for this run.

        `app_filter` is `None` (everything), a single app, or a COLLECTION of
        apps -- the last of which is what a community run needs: selecting one
        forum out of a community must not drag in its wikis and blog, and a
        single-app filter cannot express "forums and blogs but not wikis".
        """
        if app_filter is None:
            return True
        if isinstance(app_filter, str):
            return app_filter == app_name
        return app_name in app_filter

    results: list[CrawlResult] = []
    wiki_result = blog_result = forum_result = files_result = rte_result = None
    if _runs("wiki") and not _stopped():
        wiki_result = crawl(
            config=config,
            clock=demo_clock,
            client=client,
            archive=archive,
            emit=demo_emit,
            # Scope to the specific wiki the URL identified (e.g. a "Whole wiki:
            # Engineering Handbook" chip), so the archive holds only what was
            # imported -- not every demo wiki. None = all wikis (the full demo).
            wiki_labels=wiki_labels,
            max_pages=max_entries,
            stop_event=stop_event,
            # A scoped wiki crawl skips the wikis list feed, which is the only
            # place the community marker appears, so the run records what it
            # was capturing for. Without this the demo's own wiki arrives
            # attributed to nothing and the reader files it under "Not in a
            # community" -- beside the community it plainly belongs to.
            community_uuid=community_uuid,
            community_title=community_title,
        )
        results.append(wiki_result)
    if _runs("blog") and not _stopped():
        blog_result = crawl_blogs(
            config=config,
            clock=demo_clock,
            since=since,
            client=client,
            archive=archive,
            blogs_homepage=blogset.homepage,
            emit=demo_emit,
            max_posts=max_entries,
            blog_uuids=blog_uuids,
            entry_ids=entry_ids,
            stop_event=stop_event,
        )
        results.append(blog_result)
    if _runs("files") and not _stopped():
        files_result = crawl_files(
            config=config,
            clock=demo_clock,
            client=client,
            archive=archive,
            community_uuid=DEMO_COMMUNITY_UUID,
            emit=demo_emit,
            # Preview's stepper bounds documents downloaded, not just items
            # listed: an unbounded preview of a real library would pull down
            # every file in it.
            max_files=max_entries,
            stop_event=stop_event,
        )
        results.append(files_result)
    if _runs("rich_content") and not _stopped():
        rte_result = crawl_rich_content(
            config=config,
            clock=demo_clock,
            client=client,
            archive=archive,
            community_uuid=DEMO_COMMUNITY_UUID,
            emit=demo_emit,
            max_pages_captured=max_entries,
            stop_event=stop_event,
        )
        results.append(rte_result)
    if _runs("forum") and not _stopped():
        forum_result = crawl_forums(
            config=config,
            clock=demo_clock,
            since=since,
            client=client,
            archive=archive,
            emit=demo_emit,
            max_topics=max_entries,
            forum_uuids=forum_uuids,
            topic_ids=topic_ids,
            stop_event=stop_event,
        )
        results.append(forum_result)
    # The single closing event for the whole demo (its report spans whichever
    # apps ran, so the verdict counts are the full run's).
    paced_emit(RunComplete(report=_combined_report([r.report for r in results])))
    interchange = derive(archive)

    # `crawl_result` is whichever crawl actually ran first (wiki for the full
    # demo -- its `pages_crawled` is the wiki count callers/tests key on).
    primary = wiki_result or blog_result or forum_result
    # The same summary a live run writes. Without it a demo archive has no
    # ledger to read back, so it could be captured and never extended -- and
    # the demo exists precisely to show that it can be.
    from connections_export.gui.archives import write_archive_summary  # noqa: PLC0415

    write_archive_summary(archive.root, interchange)

    return DemoResult(crawl_result=primary, interchange=interchange, archive_dir=archive.root)
