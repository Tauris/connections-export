"""The applications this tool exports, as data.

The five apps -- wiki, blog (and ideation blog), forum, files, rich content --
cross seven layers. The layers got modules; the apps got `if/elif` chains and
copy-paste, restated in at least eleven places. Two of those restatements had
already fallen out of step with each other by the time this was written:
`cli.COMPONENT_KINDS` rejected a component `console.js` emitted and `--help`
documented, and `interchange/package.py` copied blobs for three apps while
`interchange/manifest.py` counted five.

This module is the single declaration. It holds NO behaviour and imports
nothing above `adapters`, so `cli`, `gui`, `derive` and `interchange` can all
read it without a cycle. The crawl callables live in `crawler/dispatch.py`,
which is what keeps that true.

Adding a sixth app: add an `AppSpec` here, an adapter module, an assembler and
a crawl entry point. Nothing else should need to learn the name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from connections_export.adapters.profiles import (
    BLOGS,
    FILES,
    FORUMS,
    RTE,
    WIKIS,
    AppProfile,
)


@dataclass(frozen=True)
class AppSpec:
    """One application, and the handful of facts every layer needs about it."""

    #: The `--component KIND:ID` vocabulary, and the `kind` community
    #: discovery reports. The user-facing name of this app.
    kind: str
    #: How to say it in a sentence, for messages a person reads.
    label: str
    #: The field on `derive.model.Interchange` this app's containers land in.
    #: `blog` and `ideation_blog` share one -- an ideation blog IS a blog with
    #: a different `kind`, not a separate container type.
    interchange_field: str
    #: Identifies which adapter produced an archive's run metadata.
    adapter_version: str
    #: What this app's crawl entry point calls its item cap. Five different
    #: names for one value, all funnelled into `begin_run(max_items=...)`;
    #: recorded rather than unified because renaming a public keyword argument
    #: would break every existing caller for no gain a reader can see.
    cap_kwarg: str
    #: What this app's crawl entry point calls the ids to capture.
    id_kwarg: str
    #: `"list"`: the crawl takes every id at once. `"each"`: it takes a single
    #: id and must be called once per id. Files and Rich Content are addressed
    #: by community uuid, one community per call.
    id_arity: Literal["list", "each"]
    #: Whether this app's feeds can be asked "what changed since". Wikis
    #: cannot (`crawl` reads each page's entry instead), and Rich Content is
    #: a single layout document, so an update re-reads both in full. Passing
    #: `since` to a crawl that has no such parameter is a TypeError in exactly
    #: the branch a unit test never reaches.
    accepts_since: bool
    #: The order a multi-app capture runs these in -- lower runs first. NOT
    #: the same as declaration order, which is what the console lists and the
    #: CLI's error message enumerates, and which is user-facing.
    #:
    #: Forums first because they are the fastest to produce visible results,
    #: so a person watching a community capture sees the tree fill early
    #: rather than waiting out a wiki. Files and rich content last: files are
    #: the largest download, and rich content is a handful of pages that cost
    #: nothing to defer. Reconstructed from the order the console's
    #: hand-written dispatch chain used before it was replaced.
    crawl_order: int
    profile: AppProfile


WIKI = AppSpec(
    kind="wiki",
    label="wiki",
    interchange_field="wikis",
    adapter_version="wikis-1",
    cap_kwarg="max_pages",
    id_kwarg="wiki_labels",
    id_arity="list",
    accepts_since=False,
    crawl_order=20,
    profile=WIKIS,
)

FORUM = AppSpec(
    kind="forum",
    label="forum",
    interchange_field="forums",
    adapter_version="forums-1",
    cap_kwarg="max_topics",
    id_kwarg="forum_uuids",
    id_arity="list",
    accepts_since=True,
    crawl_order=10,
    profile=FORUMS,
)

BLOG = AppSpec(
    kind="blog",
    label="blog",
    interchange_field="blogs",
    adapter_version="blogs-1",
    cap_kwarg="max_posts",
    id_kwarg="blog_uuids",
    id_arity="list",
    accepts_since=True,
    crawl_order=30,
    profile=BLOGS,
)

IDEATION_BLOG = AppSpec(
    kind="ideation_blog",
    label="ideation blog",
    interchange_field="blogs",
    adapter_version="blogs-1",
    cap_kwarg="max_posts",
    id_kwarg="blog_uuids",
    id_arity="list",
    accepts_since=True,
    crawl_order=31,
    profile=BLOGS,
)

#: `accepts_since=False` while `FILES.since_encoding` is `"rfc3339"`: the
#: deployment's library feed WOULD take a cutoff, and `crawl_files` does not
#: send one, so updating a community's Files re-reads the whole library every
#: time. A missed optimisation, not a correctness bug -- recorded here and
#: pinned by `tests/test_apps_registry.py` so it stays visible rather than
#: becoming folklore. Teaching `crawl_files` a `since` parameter is the fix.
FILES_APP = AppSpec(
    kind="files",
    label="files",
    interchange_field="file_libraries",
    adapter_version="files-1",
    cap_kwarg="max_files",
    id_kwarg="community_uuid",
    id_arity="each",
    accepts_since=False,
    crawl_order=50,
    profile=FILES,
)

RICH_CONTENT = AppSpec(
    kind="rich_content",
    label="rich content",
    interchange_field="rich_content",
    adapter_version="rte-1",
    cap_kwarg="max_pages_captured",
    id_kwarg="community_uuid",
    id_arity="each",
    accepts_since=False,
    crawl_order=40,
    profile=RTE,
)

#: Declaration order is the order the console lists components and the order
#: the CLI's error message enumerates them. Keep it stable: it is user-facing.
APPS: tuple[AppSpec, ...] = (WIKI, FORUM, BLOG, IDEATION_BLOG, FILES_APP, RICH_CONTENT)

BY_KIND: dict[str, AppSpec] = {app.kind: app for app in APPS}

COMPONENT_KINDS: tuple[str, ...] = tuple(app.kind for app in APPS)

#: Deduplicated, order preserved -- `blog` and `ideation_blog` share `blogs`.
INTERCHANGE_FIELDS: tuple[str, ...] = tuple(dict.fromkeys(app.interchange_field for app in APPS))
