"""#19/#20/#21: blog/forum parity in the LIVE INGEST console -- static/
structural backstop over the *served* `console.html`/`console.js`, in the
style of `test_reader_blogs_forums_static.py` (no `node` dependency).

Before this fix, only a wiki `page` node got the ingest drawer's inline
content preview + "Open reader" button; a blog `post` or forum `topic`
node showed neither, the drawer's `url` provenance row rendered the
literal string "null" for every live node (blog posts included) because
`it.url` is always `null` on a live tree node, and the ingest
topbar showed a hardcoded, always-stale fake path regardless of what the
run actually writes to.
"""

from __future__ import annotations

import re

from tests._hostcheck import find_disallowed_hosts
from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()


def _function_body(name: str, js: str = JS) -> str:
    match = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{(.*?)\n  \}", js, re.S)
    assert match, f"could not locate {name}'s body"
    return match.group(1)


# --- every app the server derives must reach the live console ---------------


def test_the_console_handles_every_derived_event_the_server_emits():
    """A `*_derived` event with no case in `handleLiveEvent` is an app that is
    crawled, archived and derived but invisible while it happens.

    That is exactly what Files was: its 8 files reached the archive and showed
    up in the verdict breakdown (which reads the derived model), while the live
    console drew no group, no nodes and no count for them, because
    `community_file_derived` was never added to the switch. Blog and forum got
    their cases when they landed; files did not. Structural rather than
    per-app, so a sixth app cannot repeat it quietly.
    """
    import inspect

    from connections_export.gui import events as gui_events

    emitted = set(re.findall(r'"type": "(\w+_derived)"', inspect.getsource(gui_events)))
    assert emitted, "no *_derived events found in the serializer -- check the regex"

    missing = sorted(t for t in emitted if f'case "{t}":' not in JS)
    assert not missing, f"the server emits {missing} but the live console ignores them"


def test_every_content_bearing_kind_reaches_the_drawer():
    """Clicking a node must show you what the archive holds for it.

    Highlights had a live tree node and a reader view, and nothing in between:
    `hasContent` listed page/post/topic only, so the content pane and the
    "Open reader" button were hidden for a highlight, and `livePageContentHtml`
    had no branch for one -- even though `findRealEntityByLiveNode` already
    mapped it. Files had a node and no representation at all.
    """
    mapper = _function_body("findRealEntityByLiveNode")
    assert 'node.kind === "rich_content"' in mapper
    assert 'node.kind === "file"' in mapper

    content = _function_body("livePageContentHtml")
    assert 'match.app === "rich_content"' in content
    assert 'match.app === "files"' in content

    for kind in ('"page"', '"post"', '"topic"', '"rich_content"', '"file"'):
        assert f"it.kind === {kind}" in JS, f"{kind} nodes have no drawer content"


def test_a_captured_file_offers_its_bytes_and_a_missing_one_says_so():
    """A document has no body to render, so its bytes are its content --
    the same vocabulary the reader's file list uses, rather than a second one
    invented for the ingest drawer."""
    content = _function_body("livePageContentHtml")
    assert "blobUrl(file.asset.blob_hash)" in content
    assert 'download="' in content
    assert "were not captured" in content, "a missing file must say why, not go blank"


# --- #20: inline content parity for post/topic (not just page) --------------


def test_find_real_entity_by_live_node_covers_all_three_apps():
    body = _function_body("findRealEntityByLiveNode")
    assert '"blog"' in body and "model.blogs" in body and "blog.posts" in body
    assert '"forum"' in body and "model.forums" in body and "forum.topics" in body
    assert "findRealPageByLiveNode(model, node)" in body  # wiki case delegates, unchanged


def test_live_page_content_html_renders_a_blog_posts_body_and_comments():
    body = _function_body("livePageContentHtml")
    assert 'match.app === "blog"' in body
    assert "sandboxedBodyMarkup(rendered)" in body
    assert "resolveBodyImages(post.content_html, post.assets)" in body
    assert "commentThreadHtml(post.comments" in body


def test_live_page_content_html_renders_a_forum_topics_body_and_replies():
    body = _function_body("livePageContentHtml")
    assert 'match.app === "forum"' in body
    assert "resolveBodyImages(topic.content_html, topic.assets)" in body
    assert "renderReplyThread(topic)" in body


def test_live_page_content_html_wiki_page_path_is_unchanged():
    # The pre-existing wiki-page rendering (attachments + comments) keeps
    # working exactly as before -- the new post/topic branches are additive.
    body = _function_body("livePageContentHtml")
    # The drawer resolves the wiki body's images then sandboxes it (split via a
    # `rendered` local), unchanged.
    assert "resolveBodyImages(page.content_html" in body
    assert "sandboxedBodyMarkup(rendered)" in body
    assert "renderAttachmentsReal(page)" in body
    assert "renderCommentsReal(page)" in body


def test_render_live_drawer_shows_content_pane_for_page_post_and_topic():
    body = _function_body("renderLiveDrawer")
    assert 'it.kind === "page" || it.kind === "post" || it.kind === "topic"' in body
    assert "d-live-content" in body
    assert "loadLiveDrawerContent(it)" in body


def test_render_live_drawer_shows_the_open_reader_button_for_page_post_and_topic():
    body = _function_body("renderLiveDrawer")
    assert '$("d-reader").style.display = hasContent ? "" : "none"' in body


def test_group_and_feed_nodes_keep_the_event_stream_hint_not_content_nodes():
    body = _function_body("renderLiveDrawer")
    assert "if (!hasContent)" in body
    assert "this view shows exactly what the live event stream carries" in body.lower()


# --- Open reader routes to the right entity, not always the first page ------


def test_open_reader_at_allows_post_and_topic_nodes_through():
    body = _function_body("openReaderAt")
    assert '"page" || it.kind === "post" || it.kind === "topic"' in body
    # Carries `{ reveal: true }`: a clicked node is something in mind, so
    # the reader unfolds the nav to show where it sits.
    assert "enterReaderReal(it, { reveal: true })" in body


def test_enter_reader_real_opens_a_post_or_topic_deep_link_via_the_real_reader_functions():
    body = _function_body("enterReaderReal")
    assert "findRealEntityByLiveNode(model, targetNode)" in body
    assert 'deep.app === "blog"' in body
    assert "openReaderPostReal(deep.blogId, deep.postId)" in body
    assert 'deep.app === "forum"' in body
    assert "openReaderTopicReal(deep.forumId, deep.topicId)" in body


# --- #19: never render the literal string "null" for a live node's url -----


def test_the_url_provenance_row_is_only_rendered_when_truthy():
    body = _function_body("renderLiveDrawer")
    assert "(it.url ? pr(\"url\", it.url) : '')" in body
    # The old unguarded call -- which rendered the literal string "null"
    # for every live node, since `it.url` is always null on one -- is gone.
    assert 'pr("url", it.url) + (it.discoveredFrom' not in body


# --- #21: the ingest topbar states what's being captured, not a stale path --


def test_the_hardcoded_fake_run_path_is_gone():
    assert "archive/run-2026-07-20" not in HTML
    assert "j.doe" not in HTML
    assert 'id="route-path"' in HTML


def test_the_route_path_is_set_from_the_actual_run_before_and_after_start():
    assert "function describeRunTarget(" in JS
    assert "function setRoutePath(" in JS
    start_body = _function_body("startLive")
    assert "setRoutePath(" in start_body
    assert "describeRunTarget(lastStartBody)" in start_body


def test_run_started_labels_a_demo_run_as_demo_not_its_internal_fake_url():
    body = _function_body("liveRunStarted")
    assert "lastStartBody && lastStartBody.demo" in body
    assert '"Demo (fake data)"' in body
    assert "setRoutePath(" in body


# --- hygiene / no external hosts --------------------------------------------


def test_served_console_references_no_external_hosts():
    assert find_disallowed_hosts(HTML) == set()
    assert find_disallowed_hosts(JS) == set()
