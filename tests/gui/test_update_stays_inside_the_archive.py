"""An update touches what the archive holds, and nothing else.

"Extend & update" started ingesting wiki
content from completely different wikis.

`wiki_labels` was set only from a URL the user had identified, and extending
an archive identifies no URL -- you pick the archive from a list. So it was
`None`, and `crawl` enumerated every wiki on the deployment. The archive
holds the answer and nothing asked it.

Two sources, in order: what the console selected per component, and failing
that the archive's own summary -- because "nothing chosen" must not degrade
to "everything on the deployment".
"""

from __future__ import annotations

import json
import pathlib

from connections_export.gui.routes.run import archive_scope


def _archive_with(tmp_path: pathlib.Path, groups: list[dict]) -> pathlib.Path:
    root = tmp_path / "archive"
    root.mkdir()
    (root / "archive-summary.json").write_text(json.dumps({"groups": groups}), encoding="utf-8")
    return root


def test_the_scope_comes_from_the_archive_when_nothing_was_selected(tmp_path):
    root = _archive_with(
        tmp_path,
        [
            {"kind": "wiki", "id": "eng-handbook", "title": "Engineering Handbook"},
            {"kind": "blog", "id": "team-blog", "title": "Team Blog"},
        ],
    )

    assert archive_scope(root) == {"wiki": ["eng-handbook"], "blog": ["team-blog"]}


def test_an_explicit_selection_wins(tmp_path):
    """Extending adds components the archive does not hold yet, so the
    selection has to be able to name more than the archive does."""
    root = _archive_with(tmp_path, [{"kind": "wiki", "id": "eng-handbook"}])

    scope = archive_scope(
        root, [{"kind": "wiki", "id": "eng-handbook"}, {"kind": "forum", "id": "uuid-1"}]
    )

    assert scope == {"wiki": ["eng-handbook"], "forum": ["uuid-1"]}


def test_an_archive_that_says_nothing_scopes_to_nothing(tmp_path):
    """Not "everything". An empty scope is a run that captures nothing, which
    is visible and harmless; an unscoped one walks the deployment."""
    root = _archive_with(tmp_path, [])

    assert archive_scope(root) == {}


def test_groups_without_an_id_are_skipped_not_guessed(tmp_path):
    root = _archive_with(tmp_path, [{"kind": "wiki"}, {"kind": "blog", "id": "b1"}])

    assert archive_scope(root) == {"blog": ["b1"]}


def test_an_archives_components_say_what_kind_they_are():
    """They were told apart by icon alone, which asks someone to learn six
    pictograms to answer "is that a blog or a forum"."""
    import re

    from tests.gui._served_assets import served_console_css, served_console_js

    js = served_console_js()
    assert "kindLabel(row.kind)" in js, "the ledger row does not name the kind"
    assert ".lr-kind" in served_console_css()
    labels = re.search(r"const KIND_LABEL = \{.*?\};", js, re.S)
    assert labels, "no kind vocabulary"
    for kind in ("wiki", "blog", "forum", "files", "rich_content"):
        assert f"{kind}:" in labels.group(0), kind


def test_archive_update_dispatches_from_archive_scope_not_stale_app():
    import inspect

    from connections_export.gui.routes import run as run_module

    source = inspect.getsource(run_module)
    update = source[source.index("def _run_real") :]
    archive_branch = update[update.index("if into is not None:") :]
    archive_branch = archive_branch[: archive_branch.index('elif hcl_app == "community":')]

    assert "run_selection(" in archive_branch
    assert "scope_components or {}" in archive_branch
