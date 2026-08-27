"""The console echoes the CLI command for the current selection.

Assembling those switches by hand is tedious and easy to get wrong, which is
the point of showing them -- but an echoed command that does not run, or that
runs something other than what Start ingest would do, is worse than showing
nothing at all. Two things therefore have to hold, and neither is obvious from
reading either side alone:

  * every flag the JavaScript emits must be one the CLI actually accepts
  * the command must be built from the same object the run is started with

The second is why `startBody` exists as a function rather than an inline
literal: both callers take it, so they cannot describe different runs.
"""

from __future__ import annotations

import re

from connections_export.cli import COMPONENT_KINDS, _add_crawl_args, _build_parser
from tests.gui._served_assets import served_console_html, served_console_js

JS = served_console_js()
HTML = served_console_html()


def _cli_echo_source() -> str:
    match = re.search(r"function cliCommandFor\(body\)\s*\{(.*?)\n    \}", JS, re.S)
    assert match, "could not locate cliCommandFor() in the served console"
    return match.group(1)


def _crawl_parser():
    parser = _build_parser("connections-export crawl")
    _add_crawl_args(parser)
    return parser


def test_every_flag_the_console_emits_is_one_the_cli_accepts():
    emitted = set(re.findall(r'"(--[a-z-]+)"', _cli_echo_source()))
    assert emitted, "the echo emits no flags -- the parser above is probably wrong"

    accepted = {option for action in _crawl_parser()._actions for option in action.option_strings}

    assert emitted <= accepted, f"console emits flags the CLI rejects: {sorted(emitted - accepted)}"


def test_the_echoed_command_parses_and_selects_what_was_chosen():
    """A representative community selection, as the console would render it,
    parsed by the CLI's own parser -- not a reimplementation of it."""
    argv = [
        "https://connections.example.corp/communities/service/html/communityview?communityUuid=c-1",
        "--component",
        "wiki:eng-handbook",
        "--component",
        "forum:f-1",
        "--component",
        "blog:b-1",
        "--author",
        "Jane Doe",
        "--max-entries",
        "10",
        "--target-label",
        "Engineering",
    ]

    args = _crawl_parser().parse_args(argv)

    assert args.components == ["wiki:eng-handbook", "forum:f-1", "blog:b-1"]
    assert args.filter_author == "Jane Doe"
    assert args.max_entries == 10
    assert args.target_label == "Engineering"
    assert args.url == [argv[0]]


def test_component_kinds_agree_between_console_and_cli():
    """The console writes `kind:id`; the CLI splits on the same vocabulary. A
    kind on one side and not the other produces a command that looks right and
    is rejected.

    Asserted at the SOURCE of `component.kind`, not by grepping the JS. The
    console builds its checkbox values dynamically from what
    `/api/community-components` returns (`console.js`: `component.kind + ':' +
    component.id`), so the kinds are never literals in the JS at all -- which
    is why the previous version of this test, a regex enumerating
    `(wiki|forum|blog|ideation_blog|files)`, could not see that the console was
    emitting `rich_content:` and the CLI rejecting it. It was matching
    unrelated literals and hardcoding the answer it was meant to discover.
    """
    from connections_export.crawler import community

    emitted = community.EMITTED_COMPONENT_KINDS

    assert emitted, "discovery declares no component kinds"
    unknown = set(emitted) - set(COMPONENT_KINDS)
    assert not unknown, f"discovery emits kinds the CLI rejects: {sorted(unknown)}"


def test_every_cli_kind_survives_being_split():
    """A kind the parser accepts and no crawl handles is a silent no-op."""
    from connections_export.cli import _split_components

    for kind in COMPONENT_KINDS:
        grouped = _split_components([f"{kind}:some-id"])
        assert grouped[kind] == ["some-id"], f"{kind} does not survive splitting"


def test_every_cli_kind_reaches_a_real_crawl():
    """Both halves have to hold: the vocabulary and the dispatch. The CLI
    accepted `files` and had a branch for it, and accepted nothing at all for
    `rich_content`."""
    from connections_export.crawler.dispatch import crawl_for

    for kind in COMPONENT_KINDS:
        assert callable(crawl_for(kind)), f"{kind} has no crawl"


def test_the_echo_is_built_from_the_same_body_the_run_posts():
    """Not a style point: if the echo read the form fields itself, it would
    drift from what Start ingest sends the moment either side changed."""
    assert "cliCommandFor(startBody())" in JS


def test_the_echo_is_present_and_copyable_in_the_markup():
    assert 'id="cli-echo"' in HTML
    assert 'id="cli-command"' in HTML
    assert 'id="cli-copy"' in HTML


def test_the_manual_documents_pacing_and_pdf_styling():
    """Both are things a user has to be told about rather than discover: a
    delay they should raise for a big run, and a styling surface whose whole
    point is that you do not have to guess at our class names."""
    assert 'id="man-pacing"' in HTML
    assert "3 seconds overnight" in HTML
    assert 'id="man-style"' in HTML
    assert "style --dump" in HTML
    assert "[pdf_style]" in HTML


def test_the_manual_says_reader_css_comes_last():
    """The one non-obvious rule: a captured page's own styles outrank ours, so
    a reader's stylesheet is appended after them. Without knowing that, a rule
    that has no effect looks like a bug."""
    assert "after" in HTML and "captured pages' own CSS" in HTML
