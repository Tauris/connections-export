"""The reader's pure, DOM-free logic (tree order, comment threading,
link routing, inline-image rewriting, the sandboxed body render,
search) -- extracted from `console.js`, the served console's own
script, between its
`READER-PURE-BEGIN`/`READER-PURE-END` markers and driven
for real with the system `node` binary (not a new project dependency:
`node` is a system tool invoked via `subprocess`, the same way this
project already treats "no real sockets" as an offline-testing
constraint on *Python* deps, not on what tooling verifies generated
JS). This is deliberately real execution, not string matching -- see
`test_reader_static.py` for the string-level backstop that holds even
without `node` available.

**The sandbox in particular**: `sandboxedBodyMarkup` is the load-bearing
safety property. The test below feeds it a
body containing a real `<script>` tag and asserts, on the actual
returned markup string, that the iframe carries `sandbox`, carries no
`allow-scripts` anywhere, and that the script text ends up inside the
`srcdoc` attribute value -- i.e. inside the sandboxed document, never
anywhere an unsandboxed context would parse and run it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CONSOLE_JS = (
    Path(__file__).resolve().parents[2] / "connections_export" / "gui" / "static" / "console.js"
)

NODE = shutil.which("node") or shutil.which("nodejs")


def _extract_pure_block() -> str:
    text = CONSOLE_JS.read_text(encoding="utf-8")
    match = re.search(
        r"READER-PURE-BEGIN =================\n(.*)// ================= READER-PURE-END",
        text,
        re.S,
    )
    assert match, "READER-PURE-BEGIN/END markers not found in console.js"
    return match.group(1)


PURE_JS = _extract_pure_block()

pytestmark = pytest.mark.skipif(NODE is None, reason="no `node` binary available on PATH")


def _run_node(driver: str) -> object:
    """Run `PURE_JS` + `driver` (which must `console.log(JSON.stringify(...))`
    its result) under Node, and return the parsed JSON result."""
    script = PURE_JS + "\n" + driver
    result = subprocess.run(
        [NODE, "--input-type=commonjs", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


# --- blob hash validation / URL building -----------------------------


def test_valid_blob_hash_accepted():
    out = _run_node('console.log(JSON.stringify(isValidBlobHash("a".repeat(64))));')
    assert out is True


def test_non_hex_blob_hash_rejected():
    out = _run_node('console.log(JSON.stringify(isValidBlobHash("not-hex")));')
    assert out is False


def test_traversal_like_blob_hash_rejected():
    out = _run_node('console.log(JSON.stringify(isValidBlobHash("../../etc/passwd")));')
    assert out is False


def test_blob_url_strips_sha256_prefix():
    out = _run_node('console.log(JSON.stringify(blobUrl("sha256:" + "b".repeat(64))));')
    assert out == "/api/blob/" + "b" * 64


def test_blob_url_accepts_bare_hex():
    out = _run_node('console.log(JSON.stringify(blobUrl("c".repeat(64))));')
    assert out == "/api/blob/" + "c" * 64


def test_blob_url_none_for_invalid_hash():
    out = _run_node('console.log(JSON.stringify(blobUrl("../secret") || null));')
    assert out is None


# --- pre-ordered hierarchy --------------------------------------------


def test_preorder_follows_root_page_ids_and_child_ids_verbatim():
    # Deliberately NOT alphabetical order, to prove the tree is taken
    # from the model, never re-sorted (the design, "the tree uses the
    # model's pre-ordered hierarchy").
    wiki = {
        "root_page_ids": ["zeta", "alpha"],
        "pages": {
            "zeta": {"title": "Zeta", "child_ids": ["zeta-b", "zeta-a"]},
            "zeta-b": {"title": "Zeta B", "child_ids": []},
            "zeta-a": {"title": "Zeta A", "child_ids": []},
            "alpha": {"title": "Alpha", "child_ids": []},
        },
    }
    out = _run_node(
        "const wiki = " + json.dumps(wiki) + ";"
        "console.log(JSON.stringify(preorderWikiPages(wiki).map(x => [x.id, x.depth])));"
    )
    assert out == [["zeta", 0], ["zeta-b", 1], ["zeta-a", 1], ["alpha", 0]]


def test_preorder_skips_a_dangling_child_id_without_crashing():
    wiki = {"root_page_ids": ["p1"], "pages": {"p1": {"title": "P1", "child_ids": ["ghost"]}}}
    out = _run_node(
        "const wiki = " + json.dumps(wiki) + ";"
        "console.log(JSON.stringify(preorderWikiPages(wiki).map(x => x.id)));"
    )
    assert out == ["p1"]


# --- forum reply tree (id-keyed) --------------------------------------


def test_preorder_forum_replies_walks_the_id_keyed_tree_depth_first():
    # A nested thread: r1 -> r1a -> r1a-i, plus a second top-level r2.
    # Deliberately non-alphabetical id order to prove the walk follows the
    # model's own id lists, never a re-sort.
    topic = {
        "reply_ids": ["r2", "r1"],
        "replies": {
            "r1": {"child_ids": ["r1a"]},
            "r1a": {"child_ids": ["r1a-i"]},
            "r1a-i": {"child_ids": []},
            "r2": {"child_ids": []},
        },
    }
    out = _run_node(
        "const topic = " + json.dumps(topic) + ";"
        "console.log(JSON.stringify(preorderForumReplies(topic).map(x => [x.id, x.depth])));"
    )
    assert out == [["r2", 0], ["r1", 0], ["r1a", 1], ["r1a-i", 2]]


def test_preorder_forum_replies_skips_a_dangling_child_id():
    topic = {"reply_ids": ["r1"], "replies": {"r1": {"child_ids": ["ghost"]}}}
    out = _run_node(
        "const topic = " + json.dumps(topic) + ";"
        "console.log(JSON.stringify(preorderForumReplies(topic).map(x => x.id)));"
    )
    assert out == ["r1"]


# --- comment threading --------------------------------------------------


def test_comments_are_threaded_by_parent_comment_id_in_original_order():
    comments = [
        {"id": "c1", "parent_comment_id": None},
        {"id": "c2", "parent_comment_id": "c1"},
        {"id": "c3", "parent_comment_id": None},
        {"id": "c4", "parent_comment_id": "c1"},
    ]
    out = _run_node(
        "const comments = " + json.dumps(comments) + ";"
        "console.log(JSON.stringify(threadComments(comments).map(x => [x.comment.id, x.depth])));"
    )
    # c1's replies (c2, c4) immediately follow it, in original order;
    # c3 (top-level) comes after the whole c1 thread.
    assert out == [["c1", 0], ["c2", 1], ["c4", 1], ["c3", 0]]


def test_comment_with_unknown_parent_is_treated_as_top_level():
    comments = [{"id": "c1", "parent_comment_id": "does-not-exist"}]
    out = _run_node(
        "const comments = " + json.dumps(comments) + ";"
        "console.log(JSON.stringify(threadComments(comments).map(x => [x.comment.id, x.depth])));"
    )
    assert out == [["c1", 0]]


# --- link routing ---------------------------------------------------------


def test_in_export_link_routes_to_in_app_nav():
    link = {"scope": "in_export", "target_page_id": "p9", "original_href": "/wiki/x"}
    out = _run_node("console.log(JSON.stringify(classifyLink(" + json.dumps(link) + ")));")
    assert out == {"kind": "in_export", "targetPageId": "p9", "href": "/wiki/x"}


def test_hcl_deployment_link_is_marked_as_original_system():
    link = {
        "scope": "hcl_deployment",
        "resolved_url": "https://connections.example.corp/x",
        "original_href": "x",
    }
    out = _run_node("console.log(JSON.stringify(classifyLink(" + json.dumps(link) + ")));")
    assert out["kind"] == "hcl_deployment"
    assert out["label"] == "original system"
    assert out["href"] == "https://connections.example.corp/x"


def test_external_link_is_plain():
    link = {"scope": "external", "resolved_url": "https://example.com/x", "original_href": "x"}
    out = _run_node("console.log(JSON.stringify(classifyLink(" + json.dumps(link) + ")));")
    assert out["kind"] == "external"


# --- inline image resolution ----------------------------------------------


def test_resolve_body_images_rewrites_a_present_asset_to_its_blob_url():
    body = '<p><img src="img/x.png" alt="x"></p>'
    assets = [
        {
            "original_href": "img/x.png",
            "resolved_url": "https://fake/img/x.png",
            "blob_hash": "sha256:" + "d" * 64,
            "present": True,
            "scope": "same",
        }
    ]
    out = _run_node(
        "console.log(JSON.stringify(resolveBodyImages("
        + json.dumps(body)
        + ", "
        + json.dumps(assets)
        + ")));"
    )
    assert out == '<p><img src="/api/blob/' + "d" * 64 + '" alt="x"></p>'


def test_resolve_body_images_leaves_unresolved_reference_untouched():
    body = '<p><img src="img/missing.png"></p>'
    assets = [
        {
            "original_href": "img/missing.png",
            "resolved_url": "https://fake/img/missing.png",
            "blob_hash": None,
            "present": False,
            "scope": "same",
        }
    ]
    out = _run_node(
        "console.log(JSON.stringify(resolveBodyImages("
        + json.dumps(body)
        + ", "
        + json.dumps(assets)
        + ")));"
    )
    assert out == body


# --- THE security-sensitive render: sandboxed body markup -----------------


def test_sandboxed_body_markup_carries_sandbox_without_allow_scripts():
    out = _run_node('console.log(JSON.stringify(sandboxedBodyMarkup("<p>hi</p>")));')
    assert re.search(r"<iframe\b[^>]*\bsandbox\b", out)
    assert "allow-scripts" not in out
    assert "srcdoc=" in out


def test_a_script_in_the_body_ends_up_inside_the_sandboxed_srcdoc_not_outside_it():
    malicious = "<style>.x{color:red}</style><script>window.__pwned = true;</script><p>hi</p>"
    out = _run_node(
        "console.log(JSON.stringify(sandboxedBodyMarkup(" + json.dumps(malicious) + ")));"
    )
    # No allow-scripts anywhere in the output -- the iframe's script
    # execution stays permanently off regardless of body content.
    assert "allow-scripts" not in out
    assert re.search(r"<iframe\b[^>]*\bsandbox\b", out)
    # The markup is a *single* self-closing-less iframe with one
    # srcdoc attribute -- the script text is not spliced in anywhere
    # else (e.g. as a second, unsandboxed element).
    assert out.count("<iframe") == 1
    assert out.count("srcdoc=") == 1
    # The script (HTML-attribute-escaped, since it's inside the
    # srcdoc="..." attribute) is present -- it was carried into the
    # sandboxed document, not stripped -- but only within that one
    # attribute value.
    assert "window.__pwned" in out
    srcdoc_match = re.search(r'srcdoc="(.*)"></iframe>', out, re.S)
    assert srcdoc_match, "could not locate the srcdoc attribute value"
    assert "window.__pwned" in srcdoc_match.group(1)
    assert ".x{color:red}" in srcdoc_match.group(1)  # author CSS retained verbatim


def test_sandboxed_body_markup_never_uses_a_remote_src_for_the_body():
    out = _run_node('console.log(JSON.stringify(sandboxedBodyMarkup("<p>hi</p>")));')
    assert not re.search(r"<iframe\b[^>]*\bsrc=", out)


# --- search -----------------------------------------------------------


def _sample_model():
    return {
        "wikis": [
            {
                "id": "w1",
                "title": "Wiki One",
                "root_page_ids": ["p1"],
                "pages": {
                    "p1": {
                        "title": "Onboarding Guide",
                        "content_html": "<p>Welcome to the <b>release</b> checklist.</p>",
                        "comments": [{"id": "c1", "content_html": "<p>great retro notes</p>"}],
                    }
                },
            }
        ]
    }


def test_search_matches_title():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model())
        + ', "onboarding").map(r => r.pageId)));'
    )
    assert out == ["p1"]


def test_search_matches_stripped_body_text():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model())
        + ', "release").map(r => [r.pageId, r.loc])));'
    )
    assert out == [["p1", ["body"]]]


def test_search_matches_comment_text():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model())
        + ', "retro").map(r => [r.pageId, r.loc])));'
    )
    assert out == [["p1", ["comments"]]]


def test_search_short_query_returns_no_results():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel(" + json.dumps(_sample_model()) + ', "o")));'
    )
    assert out == []


# --- search across blogs + forums -----------


def _sample_model_all_apps():
    return {
        "wikis": [
            {
                "id": "w1",
                "title": "Wiki One",
                "root_page_ids": ["p1"],
                "pages": {"p1": {"title": "Onboarding", "content_html": "<p>welcome</p>"}},
            }
        ],
        "blogs": [
            {
                "id": "b1",
                "title": "Team Blog",
                "post_ids": ["post1"],
                "posts": {
                    "post1": {
                        "title": "Deployment recap",
                        "content_html": "<p>the rollout went smoothly</p>",
                        "comments": [{"id": "bc1", "content_html": "<p>nice writeup</p>"}],
                    }
                },
            }
        ],
        "forums": [
            {
                "id": "f1",
                "title": "Support",
                "topic_ids": ["topic1"],
                "topics": {
                    "topic1": {
                        "title": "Export question",
                        "content_html": "<p>how do I export a wiki</p>",
                        "reply_ids": ["r1"],
                        "replies": {
                            "r1": {"content_html": "<p>use the reader</p>", "child_ids": []}
                        },
                    }
                },
            }
        ],
    }


def test_search_matches_a_blog_post_body():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model_all_apps())
        + ', "rollout").map(r => [r.kind, r.postId, r.loc])));'
    )
    assert out == [["post", "post1", ["body"]]]


def test_search_matches_a_forum_topic_title():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model_all_apps())
        + ', "export question").map(r => [r.kind, r.topicId, r.loc])));'
    )
    assert out == [["topic", "topic1", ["title"]]]


def test_search_matches_a_forum_reply_body_reported_as_comments():
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model_all_apps())
        + ', "reader").map(r => [r.kind, r.topicId, r.loc])));'
    )
    assert out == [["topic", "topic1", ["comments"]]]


def test_wiki_search_result_shape_is_unchanged_by_the_new_apps():
    # A wiki hit still carries a `page` kind + wikiId/pageId, so existing
    # callers/tests keep working alongside the blog/forum results.
    out = _run_node(
        "console.log(JSON.stringify(searchReaderModel("
        + json.dumps(_sample_model_all_apps())
        + ', "onboarding").map(r => [r.kind, r.wikiId, r.pageId])));'
    )
    assert out == [["page", "w1", "p1"]]
