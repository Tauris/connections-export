"""Static/structural backstop for the reader's security- and
correctness-sensitive shape, over the *served* `console.html`
(Testing (offline)). Holds
unconditionally, with no dependency on a `node` binary being available
-- `test_reader_pure_js.py` covers the same properties with real
execution when `node` is present; this file is the always-on floor.
"""

from __future__ import annotations

import re

from tests._hostcheck import find_disallowed_hosts
from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

# The console's script lives in console.js, not in console.html; the
# functions/behavior asserted below now live there, while pure DOM/markup
# assertions stay on the served HTML.
HTML = served_console_html()
JS = served_console_js()
CSS = served_console_css()


# --- the load-bearing safety property (hard rule 2) ------------------------


def test_sandboxed_body_markup_function_exists():
    assert "function sandboxedBodyMarkup(" in JS


def _strip_js_comments(text: str) -> str:
    """Drop `/*... */` and `//...` comments so a substring search
    only sees actual code/markup, not prose that happens to mention the
    token it's describing (this file's own doc comments explain *why*
    `allow-scripts` is never added, which would otherwise self-trigger
    a naive whole-file search)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_no_allow_scripts_anywhere_in_the_served_console_code():
    """`allow-scripts` must never appear on the sandboxed body iframe, so
    untrusted captured HTML can never execute a `<script>`. The frame carries
    `allow-same-origin` instead — that lets the *parent* read its scrollHeight
    to auto-size it, and is safe precisely because `allow-scripts` is absent.
    The dangerous combination (`allow-scripts` + `allow-same-origin`, which lets
    sandboxed scripts reach the parent) therefore never arises."""
    stripped_html = _strip_js_comments(HTML)
    stripped_js = _strip_js_comments(JS)
    assert "allow-scripts" not in stripped_html, (
        "allow-scripts must never appear — it would execute untrusted captured HTML"
    )
    assert "allow-scripts" not in stripped_js, (
        "allow-scripts must never appear — it would execute untrusted captured HTML"
    )


def test_sandboxed_body_markup_never_enables_scripts():
    match = re.search(r"function sandboxedBodyMarkup\(bodyHtml\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate sandboxedBodyMarkup's body"
    body = _strip_js_comments(match.group(1))
    assert "sandbox" in body
    assert "srcdoc" in body
    # `allow-scripts` would execute untrusted captured HTML, so it must never
    # appear. `allow-same-origin` is permitted (it enables parent-side auto-height
    # measurement) only because scripts are disabled — the two must never coexist.
    assert "allow-scripts" not in body, "allow-scripts must not appear in the sandbox attribute"


def test_body_html_is_only_ever_rendered_through_the_sandboxed_iframe():
    """A page's raw `body_html` is read in two places: fed to
    `resolveBodyImages(...)` (which lands in the sandboxed iframe) and
    to `stripTags(...)` (for the *search index* -- plain text, never
    rendered/executed). It is never assigned straight into `.innerHTML`
    or any other sink that would parse it as live markup in the
    trusted top-level document."""
    assert "page.content_html" in JS
    for line in JS.splitlines():
        if "page.content_html" not in line:
            continue
        # `readerBody(...)` is the reader's safe wrapper (it internally calls
        # sandboxedBodyMarkup(resolveBodyImages(...)) -- asserted below); the
        # drawer feeds resolveBodyImages directly; stripTags is the plain-text
        # search index. None assign raw body_html into a live-markup sink.
        assert (
            "readerBody(page.content_html" in line
            or "resolveBodyImages(page.content_html" in line
            or "stripTags(page.content_html" in line
        ), line
        assert ".innerHTML" not in line


def test_reader_body_wrapper_only_routes_through_the_sandboxed_iframe():
    """`readerBody` is the reader's body render path (page/post/topic); it must
    only ever route raw body HTML through the sandboxed iframe -- i.e. wrap
    `sandboxedBodyMarkup(resolveBodyImages(...))` and nothing that would parse
    it as live markup in the trusted document."""
    import re

    match = re.search(r"function readerBody\([^)]*\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate readerBody's body"
    body = match.group(1)
    assert "sandboxedBodyMarkup(resolveBodyImages(" in body
    assert ".innerHTML" not in body


# --- pre-ordered hierarchy (hard rule 4) -----------------------------------


def test_preorder_function_exists_and_never_sorts():
    match = re.search(r"function preorderWikiPages\(wiki\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match, "could not locate preorderWikiPages's body"
    assert ".sort(" not in match.group(1)
    assert "root_page_ids" in match.group(1)
    assert "child_ids" in match.group(1)


# --- comment threading / link routing --------------------------------------


def test_thread_comments_uses_parent_comment_id():
    match = re.search(r"function threadComments\(comments\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match
    assert "parent_comment_id" in match.group(1)


def test_reply_body_does_not_inherit_main_article_spacing():
    assert ".cmt-item .ctext .inline-body" in CSS
    assert "font-size: inherit; line-height: inherit" in CSS
    assert "white-space: normal" in CSS
    assert ".cmt-item .ctext .inline-body pre" in CSS
    assert ".cmt-item .ctext .inline-body img" in CSS
    # Same 8px, now spelled as the spacing token the stylesheet snapped to.
    assert ".cmt-item .ctext .inline-body > p { margin: 0 0 var(--sp-2); }" in CSS


def test_reader_exposes_original_page_links_for_live_comparison():
    assert "function originalPageLink(url)" in JS
    assert 'target="_blank"' in JS
    assert "originalPageLink(page.alternate_url)" in JS
    assert "originalPageLink(post.alternate_url)" in JS
    assert "originalPageLink(topic.alternate_url)" in JS


def test_classify_link_routes_all_three_scopes():
    match = re.search(r"function classifyLink\(link\)\s*\{(.*?)\n  \}", JS, re.S)
    assert match
    body = match.group(1)
    assert '"in_export"' in body
    assert '"hcl_deployment"' in body
    assert '"external"' in body


# --- blob endpoint wiring ----------------------------------------------


def test_blob_hash_regex_matches_the_backend_shape():
    assert "/^[0-9a-f]{64}$/" in JS


def test_blob_url_builder_targets_the_api_blob_endpoint():
    assert '"/api/blob/"' in JS


# --- /api/model wiring -------------------------------------------------


def test_fetches_api_model():
    assert 'fetch("/api/model")' in JS


def test_handles_pending_503_status():
    assert "503" in JS


# --- hygiene / no external hosts -------------------------------------------


def test_served_console_references_no_external_hosts():
    assert find_disallowed_hosts(HTML) == set()
    assert find_disallowed_hosts(JS) == set()


def test_the_reader_reads_newest_first_and_can_be_flipped():
    """Blog posts and forum topics are read newest first by default.

    An archive is opened to see what a community was last saying, and feed
    order cannot be relied on either way: the demo's fake server feeds oldest
    first, a real Atom feed usually feeds newest first. So the view sorts, and
    the derived model keeps whatever order the source gave -- that order is
    provenance, not presentation.
    """
    assert "readerOrdered(blog.post_ids" in JS
    assert "readerOrdered(forum.topic_ids" in JS
    # Default newest, remembered across sessions, flippable from the nav.
    assert '"hcl-export-reader-order"' in JS
    assert "r-order-toggle" in JS
    assert "let readerNewestFirst = true" in JS
    assert ".r-order-btn" in CSS


def test_replies_and_comments_are_not_reordered():
    """A conversation reads forward. Only the container listings flip; a
    reply must never arrive before the message it answers."""
    assert "readerOrdered(topic.reply_ids" not in JS
    assert "readerOrdered(post.comments" not in JS


def test_an_undated_entry_keeps_its_place_rather_than_being_guessed_at():
    body = re.search(r"function readerOrdered\(.*?\n  \}", JS, re.S)
    assert body, "readerOrdered not found"
    assert "isNaN" in body.group(0), "entries with no date must be handled explicitly"
    assert "dated.concat(undated)" in body.group(0)


def test_a_category_holding_one_container_opens_it_too():
    """Two expanders that always had the same answer are one decision.

    Opening "WIKIS" and then the single wiki inside it is a fork with one
    road. A category holding exactly one container now opens it in the same
    click; a category holding several does not, because there the choice is
    real.
    """
    assert "function addReaderContainer(parent, key, label, selected, sole)" in JS
    assert "content.expandHooks = []" in JS
    assert "content.expandHooks.forEach((fn) => fn())" in JS
    assert "if (sole && !parent.hidden) readerContainerOpen.add(key)" in JS

    # Counted within the rendered SCOPE, not across the whole model. With an
    # archive of several communities that is the sharper question: a parent
    # and a child holding one forum each are two forums in the model and one
    # in each community's section, and it is the section the user clicks.
    for collection in ("wikis", "blogs", "forums", "rich_content"):
        assert f"(scope.{collection} || []).length === 1" in JS, (
            f"{collection} does not tell its container whether it is the only one"
        )


def test_a_sole_file_library_opens_with_its_category():
    """Files have no second expander, and the principle still applies.

    A library is a listing rather than a set of documents to open one at a
    time, so it is a link, not a container. With exactly one of them the
    click is a foregone conclusion -- and the nav reads "FILES" above a link
    reading "Files". Opening the category opens the listing, unless it is
    already the one being read.
    """
    assert "const soleLibrary = (scope.file_libraries || []).length === 1" in JS
    assert "if (realLibraryId === library.id) return;" in JS
    assert "openReaderFilesReal(library.id);" in JS


def test_the_count_is_the_model_as_it_stands_not_a_fixed_decision():
    """ "Only one" is re-read on every nav rebuild, including the live
    refresh that runs while a crawl streams: a category with one wiki links
    it, and stops the moment a second wiki arrives."""
    refresh = re.search(r"REAL_MODEL = model;(.*?)scheduleLiveRefresh\(\);", JS, re.S)
    assert refresh, "the live model refresh was not found"
    assert "buildReaderNavReal()" in refresh.group(1), (
        "the nav is not rebuilt as the model grows, so 'only one' would go stale"
    )
