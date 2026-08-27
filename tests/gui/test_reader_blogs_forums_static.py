"""Stage-2 phase 6: static/structural backstop
for the reader's Blogs + Forums wiring over the *served* `console.html`,
alongside the wiki backstop in `test_reader_static.py`. Holds
unconditionally, with no dependency on a `node` binary --
`test_reader_pure_js.py` covers the pure blog/forum logic (reply-tree
walk, cross-app search) with real execution when `node` is present.

The load-bearing property carried over from the wiki reader: a post's
or topic's body (`content_html`) is only ever rendered through the
sandboxed iframe (`sandboxedBodyMarkup(resolveBodyImages(...))`) or read
as plain text for the search index (`stripTags(...)`) -- never assigned
into a live-markup sink in the trusted top-level document.
"""

from __future__ import annotations

import re

from tests._hostcheck import find_disallowed_hosts
from tests.gui._served_assets import served_console_html, served_console_js

# The console's script lives in console.js, not in console.html; the
# functions/behavior asserted below now live there, while pure DOM/markup
# assertions stay on the served HTML.
HTML = served_console_html()
JS = served_console_js()


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


# --- the blog + forum reader functions/markers are present ------------------


def test_blog_post_reader_function_exists():
    assert "function openReaderPostReal(" in JS


def test_forum_topic_reader_function_exists():
    assert "function openReaderTopicReal(" in JS


def test_forum_reply_tree_walker_exists_and_uses_the_id_keyed_shape():
    match = re.search(r"function preorderForumReplies\(topic\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate preorderForumReplies's body"
    body = match.group(1)
    # The id-keyed tree: ordered top-level `reply_ids`, each resolved in
    # `replies`, nested via each reply's own `child_ids` -- and never sorted.
    assert "reply_ids" in body
    assert "replies" in body
    assert "child_ids" in body
    assert ".sort(" not in body


def test_reply_thread_renderer_exists():
    assert "function renderReplyThread(" in JS


def test_forum_attachments_are_rendered_in_the_reader():
    assert "reply.attachments" in JS
    assert "renderAttachmentsReal(topic)" in JS
    assert "renderDownloadAssetsReal(reply.assets)" in JS
    assert "inline-downloads" in JS
    assert "'</div>' + renderDownloadAssetsReal(reply.assets)" in JS
    assert "filename not provided by feed" in JS


def test_forum_topic_tags_are_rendered():
    assert "topic.tags" in JS


def test_reader_navigation_has_semantic_component_headers():
    assert 'document.createElement("button")' in JS
    assert 'section.className = "r-component-section"' in JS
    assert "aria-expanded" in JS
    assert "r-component-content" in JS
    # Sections open from remembered state, not unconditionally: they now come
    # up collapsed (see test_reader_nav_sections_start_collapsed). The
    # remembered key carries the community when an archive holds several --
    # two communities both have a section called "WIKIS", and without the
    # prefix they would open and close together.
    assert "const initiallyOpen = readerComponentOpen.has(openKey)" in JS
    assert "readerComponentOpen" in JS
    assert "readerComponentOpen.delete(openKey)" in JS
    assert "function updateReaderNavSelection(key)" in JS
    assert "data-reader-key" in JS
    assert "event.stopPropagation()" in JS


def test_nav_lists_blogs_and_forums_groups():
    """The app sections moved into `renderAppSections`, which renders one
    SCOPE -- the whole model, or a single community's containers when the
    archive holds several. It reads `scope.blogs` rather than
    `REAL_MODEL.blogs` for that reason; what it renders is unchanged."""
    match = re.search(r"function renderAppSections\(scope, keyPrefix\)\s*\{(.*?)\n    \}", JS, re.S)
    assert match, "could not locate renderAppSections' body"
    body = match.group(1)
    assert "scope.blogs" in body
    assert "scope.forums" in body
    assert "post_ids" in body
    assert "topic_ids" in body


def test_reader_builds_complete_navigation_on_initial_model_load():
    match = re.search(r"function enterReaderReal\([^)]*\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate enterReaderReal's body"
    assert "buildReaderNavReal();" in match.group(1)


def test_search_covers_blogs_and_forums():
    match = re.search(r"function searchReaderModel\(model, query\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate searchReaderModel's body"
    body = match.group(1)
    assert "model.blogs" in body
    assert "model.forums" in body


def test_live_stream_handles_blog_and_forum_derived_events():
    assert "blog_post_derived" in JS
    assert "forum_topic_derived" in JS
    assert "function liveBlogPostDerived(" in JS
    assert "function liveForumTopicDerived(" in JS


# --- the load-bearing safety property, extended to post/topic bodies --------


def test_no_allow_scripts_anywhere_in_the_served_console_code():
    """`allow-scripts` must never appear on the sandboxed body iframe, so
    untrusted captured HTML can never execute a `<script>`. The frame carries
    `allow-same-origin` instead (parent-side auto-height measurement), which is
    safe only because scripts are disabled — the two must never coexist."""
    assert "allow-scripts" not in _strip_js_comments(HTML)
    assert "allow-scripts" not in _strip_js_comments(JS)


def test_post_body_is_only_ever_rendered_through_the_sandboxed_iframe():
    """A blog post's raw `content_html` is read only to feed
    `resolveBodyImages(...)` (which lands in the sandboxed iframe) or
    `stripTags(...)` (plain-text search index) -- never straight into
    `.innerHTML` or any other live-markup sink in the trusted document."""
    assert "post.content_html" in JS
    for line in JS.splitlines():
        if "post.content_html" not in line:
            continue
        assert (
            "readerBody(post.content_html" in line
            or "resolveBodyImages(post.content_html" in line
            or "stripTags(post.content_html)" in line
        ), line
        assert ".innerHTML" not in line


def test_topic_body_is_only_ever_rendered_through_the_sandboxed_iframe():
    """Same guarantee for a forum topic's `content_html`."""
    assert "topic.content_html" in JS
    for line in JS.splitlines():
        if "topic.content_html" not in line:
            continue
        assert (
            "readerBody(topic.content_html" in line
            or "resolveBodyImages(topic.content_html" in line
            or "stripTags(topic.content_html)" in line
        ), line
        assert ".innerHTML" not in line


# --- hygiene / no external hosts --------------------------------------------


def test_served_console_references_no_external_hosts():
    assert find_disallowed_hosts(HTML) == set()
    assert find_disallowed_hosts(JS) == set()


# --- reader nav: collapsed by default, collapsible per container ------------


def test_reader_nav_sections_start_collapsed():
    """`initiallyOpen` was hardcoded `true`, so every section came up expanded
    and a large archive filled the nav with every page, post, and topic at
    once. Closed is the useful default; the path to whatever is open is
    revealed explicitly."""
    assert "const initiallyOpen = true;" not in JS
    assert "readerComponentOpen.has(" in JS, (
        "open state is tracked but never consulted, so it cannot survive a rebuild"
    )


def test_reader_nav_gives_each_forum_its_own_chevron():
    """Individual forums were plain `.wk` headers with no way to collapse
    them, so opening FORUMS dumped every topic of every forum into the nav."""
    assert "r-container-section" in JS
    assert "r-container-content" in JS


def test_reader_nav_remembers_which_containers_are_open():
    """The nav is rebuilt on every open, so without a remembered key each
    rebuild would silently re-close whatever the reader had expanded."""
    assert "readerContainerOpen" in JS


def test_reader_reveals_the_path_when_entered_with_something_in_mind():
    """Arriving from a run, a live node, or a chosen archive should show where
    that content sits; only a generic sidebar visit stays fully collapsed."""
    assert "function revealReaderPath(" in JS
    assert "{ reveal: true }" in JS
    assert "{ reveal: false }" in JS


def test_reader_open_buttons_do_not_pass_the_click_event_as_options():
    """`addEventListener("click", enterReader)` would hand the Event straight
    in as `opts`, making `opts.reveal` undefined."""
    assert '$("open-reader").addEventListener("click", enterReader)' not in JS
    assert "() => enterReader({ reveal: true })" in JS
