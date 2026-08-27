"""One table: for every kind of change, does an update SEE it?

This exists because the same defect kept being found by accident, one
component at a time. The cause was always the same -- a feed that ENUMERATES
things (which wikis exist, which posts, which topics, which replies) fetched
with the default `IF_MISSING` policy, so an update read the archive's copy and
could only ever answer with what was already captured. Every miss reported
success.

Written as a table because the defect is not interesting per component: what
matters is that no component is missing from the list. A case that cannot be
expressed here is a case nobody is checking.

Each row: capture a dataset, change one thing, update, and look for the change
in the derived model. Behavioural on purpose -- a structural check ("every
feed passes a policy") would pass the moment someone passed the wrong one.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import (
    crawl,
    crawl_blogs,
    crawl_files,
    crawl_forums,
    crawl_rich_content,
)
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_files,
    synthesize_forums,
    synthesize_rich_content,
)
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(
    seed=0,
    wiki_count=1,
    depth=1,
    pages_per_level=2,
    comments_per_page=1,
    versions_per_page=1,
    attachments_per_page=1,
)
COMMUNITY = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
#: Later than anything the synthesizer produces, so a changed item is
#: unambiguously newer than the archive's copy.
LATER = "2030-01-01T00:00:00Z"


def _pages(interchange):
    return [page for wiki in interchange.wikis for page in wiki.pages.values()]


# --- the changes, and how to recognise each one afterwards -----------------


def _add_wiki(sets):
    second = copy.deepcopy(sets["wikis"].wikis[0])
    second.label, second.uuid, second.title = "second-wiki", "wiki-second", "A Second Wiki"
    sets["wikis"].wikis.append(second)


def _add_page(sets):
    page = copy.deepcopy(sets["wikis"].wikis[0].pages[-1])
    page.label, page.uuid, page.title = "new-page", "page-new", "A New Page"
    sets["wikis"].wikis[0].pages.append(page)


def _edit_page(sets):
    page = sets["wikis"].wikis[0].pages[0]
    page.body_html, page.modified = "<p>EDITED BODY</p>", LATER


def _rename_wiki(sets):
    sets["wikis"].wikis[0].title = "A Completely New Title"


def _add_wiki_comment(sets):
    page = sets["wikis"].wikis[0].pages[0]
    comment = copy.deepcopy(page.comments[0])
    comment.uuid, comment.content_html = "comment-new", "<p>NEW COMMENT</p>"
    page.comments.append(comment)
    page.modified = LATER


def _add_version(sets):
    page = sets["wikis"].wikis[0].pages[0]
    version = copy.deepcopy(page.versions[0])
    version.uuid, version.version_label = "version-new", "99"
    page.versions.append(version)
    page.modified = LATER


def _add_attachment(sets):
    page = sets["wikis"].wikis[0].pages[0]
    attachment = copy.deepcopy(page.attachments[0])
    attachment.uuid, attachment.filename = "attachment-new", "brand-new.pdf"
    page.attachments.append(attachment)
    page.modified = LATER


def _add_blog(sets):
    blog = copy.deepcopy(sets["blogset"].blogs[0])
    blog.uuid, blog.handle, blog.title = "blog-second", "second", "A Second Blog"
    sets["blogset"].blogs.append(blog)


def _add_post(sets):
    blog = sets["blogset"].blogs[0]
    post = copy.deepcopy(blog.posts[0])
    post.uuid, post.slug, post.title = "post-new", "brand-new", "A Brand New Post"
    post.published = post.updated = LATER
    blog.posts.insert(0, post)


def _edit_post(sets):
    post = sets["blogset"].blogs[0].posts[0]
    post.body_html, post.updated = "<p>EDITED POST</p>", LATER


def _add_blog_comment(sets):
    post = sets["blogset"].blogs[0].posts[0]
    comment = copy.deepcopy(post.comments[0])
    comment.uuid, comment.content_html = "blog-comment-new", "<p>NEW BLOG COMMENT</p>"
    post.comments.append(comment)
    # Deliberately NOT touching the post's date: on the deployment measured,
    # a blog comment moved neither the post nor the dated entries feed.


def _add_forum(sets):
    forum = copy.deepcopy(sets["forumset"].forums[0])
    forum.uuid, forum.title = "forum-second", "A Second Forum"
    sets["forumset"].forums.append(forum)


def _add_topic(sets):
    forum = sets["forumset"].forums[0]
    topic = copy.deepcopy(forum.topics[0])
    topic.uuid, topic.title, topic.published = "topic-new", "A Brand New Topic", LATER
    forum.topics.insert(0, topic)


def _add_reply(sets):
    topic = sets["forumset"].forums[0].topics[0]
    reply = copy.deepcopy(topic.replies[0])
    reply.uuid, reply.content_html, reply.published = "reply-new", "<p>NEW REPLY</p>", LATER
    topic.replies.append(reply)


def _add_file(sets):
    library = sets["fileset"].libraries[0]
    document = copy.deepcopy(library.files[0])
    document.uuid, document.name, document.title = "file-new", "brand-new.txt", "A New File"
    document.published = document.updated = LATER
    library.files.append(document)


def _new_file_version(sets):
    document = sets["fileset"].libraries[0].files[0]
    document.version_label, document.updated = "77", LATER
    document.body = b"NEW BYTES ENTIRELY"


def _add_highlight(sets):
    community = sets["rteset"].communities[0]
    page = copy.deepcopy(community.pages[0])
    page.resource_id, page.title, page.updated = "rte-new", "A Brand New Highlight", LATER
    community.pages.append(page)


def _edit_highlight(sets):
    page = sets["rteset"].communities[0].pages[0]
    page.body_html, page.updated = "<p>EDITED HIGHLIGHT</p>", LATER


#: (name, which crawl, what changed, how to recognise it)
CASES: list[tuple[str, str, Callable, Callable]] = [
    (
        "wiki: a new wiki",
        "wikis",
        _add_wiki,
        lambda i: any(w.label == "second-wiki" for w in i.wikis),
    ),
    (
        "wiki: a wiki renamed",
        "wikis",
        _rename_wiki,
        lambda i: any(w.title == "A Completely New Title" for w in i.wikis),
    ),
    (
        "wiki: a new page",
        "wikis",
        _add_page,
        lambda i: any(p.label == "new-page" for p in _pages(i)),
    ),
    (
        "wiki: an edited page body",
        "wikis",
        _edit_page,
        lambda i: any("EDITED BODY" in (p.content_html or "") for p in _pages(i)),
    ),
    (
        "wiki: a new comment",
        "wikis",
        _add_wiki_comment,
        lambda i: any(
            "NEW COMMENT" in (c.content_html or "") for p in _pages(i) for c in p.comments
        ),
    ),
    (
        "wiki: a new version",
        "wikis",
        _add_version,
        lambda i: any(str(v.version_label) == "99" for p in _pages(i) for v in p.versions),
    ),
    (
        "wiki: a new attachment",
        "wikis",
        _add_attachment,
        lambda i: any(a.filename == "brand-new.pdf" for p in _pages(i) for a in p.attachments),
    ),
    ("blog: a new blog", "blogs", _add_blog, lambda i: any(b.id == "blog-second" for b in i.blogs)),
    (
        "blog: a new post",
        "blogs",
        _add_post,
        lambda i: any(p.title == "A Brand New Post" for b in i.blogs for p in b.posts.values()),
    ),
    (
        "blog: an edited post",
        "blogs",
        _edit_post,
        lambda i: any(
            "EDITED POST" in (p.content_html or "") for b in i.blogs for p in b.posts.values()
        ),
    ),
    (
        "blog: a new comment, post unchanged",
        "blogs",
        _add_blog_comment,
        lambda i: any(
            "NEW BLOG COMMENT" in (c.content_html or "")
            for b in i.blogs
            for p in b.posts.values()
            for c in p.comments
        ),
    ),
    (
        "forum: a new forum",
        "forums",
        _add_forum,
        lambda i: any(f.title == "A Second Forum" for f in i.forums),
    ),
    (
        "forum: a new topic",
        "forums",
        _add_topic,
        lambda i: any(t.title == "A Brand New Topic" for f in i.forums for t in f.topics.values()),
    ),
    (
        "forum: a new reply",
        "forums",
        _add_reply,
        lambda i: any(
            "NEW REPLY" in (r.content_html or "")
            for f in i.forums
            for t in f.topics.values()
            for r in t.replies.values()
        ),
    ),
    (
        "files: a new document",
        "files",
        _add_file,
        lambda i: any(
            f.name == "brand-new.txt" for lib in i.file_libraries for f in lib.files.values()
        ),
    ),
    (
        "files: a new version",
        "files",
        _new_file_version,
        lambda i: any(
            str(f.version_label) == "77" for lib in i.file_libraries for f in lib.files.values()
        ),
    ),
    (
        "highlights: a new page",
        "rich_content",
        _add_highlight,
        lambda i: any(
            p.title == "A Brand New Highlight" for r in i.rich_content for p in r.pages.values()
        ),
    ),
    (
        "highlights: an edited page",
        "rich_content",
        _edit_highlight,
        lambda i: any(
            "EDITED HIGHLIGHT" in (p.content_html or "")
            for r in i.rich_content
            for p in r.pages.values()
        ),
    ),
]


def _fresh_sets():
    return {
        "wikis": synthesize(SEED),
        "blogset": synthesize_blogs(SEED),
        "forumset": synthesize_forums(SEED),
        "fileset": synthesize_files(SEED, community_uuid=COMMUNITY),
        "rteset": synthesize_rich_content(SEED, community_uuid=COMMUNITY),
    }


def _run(which, sets, root, fetch):
    app = make_app(
        sets["wikis"],
        blogset=sets["blogset"],
        forumset=sets["forumset"],
        fileset=sets["fileset"],
        rteset=sets["rteset"],
    )
    common = dict(
        config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
        client=HttpClient(
            transport=httpx.MockTransport(SyncASGIBridge(app).handle_request),
            sleep=lambda _s: None,
        ),
        archive=Archive.open(root),
        emit=lambda _e: None,
    )
    if which == "wikis":
        crawl(**common)
    elif which == "blogs":
        crawl_blogs(**common, blogs_homepage=sets["blogset"].homepage)
    elif which == "forums":
        crawl_forums(**common)
    elif which == "files":
        crawl_files(**common, community_uuid=COMMUNITY)
    else:
        crawl_rich_content(**common, community_uuid=COMMUNITY)


@pytest.mark.parametrize("name,which,change,recognise", CASES, ids=[c[0] for c in CASES])
def test_an_update_sees_the_change(tmp_path, name, which, change, recognise):
    root = tmp_path / "archive"
    sets = _fresh_sets()
    _run(which, sets, root, "resume")

    change(sets)
    _run(which, sets, root, "update")

    assert recognise(derive(Archive.open(root))), f"an update did not see: {name}"


def test_the_table_covers_every_app():
    """A case that is not in the table is a case nobody is checking, so the
    table is checked against the app registry rather than against itself."""
    from connections_export import apps

    covered = {which for _name, which, _c, _r in CASES}
    expected = {"wikis", "blogs", "forums", "files", "rich_content"}
    assert covered == expected
    # ...and the registry has not grown a sixth app that nobody added a row for.
    kinds = {app.kind for app in apps.APPS} - {"ideation_blog"}
    assert len(kinds) == len(expected), f"apps.APPS has {kinds}, table covers {expected}"
