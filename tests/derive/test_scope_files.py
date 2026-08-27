"""Scoping an export must not smuggle the file library along.

`_empty` clears wikis, blogs and forums, and every scoping path builds on it.
A file library is a fourth container, so a scoper that does not know about it
leaves it in place -- and "export just this one forum" quietly ships every
document in the community.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedFile,
    DerivedFileLibrary,
    DerivedForum,
    Interchange,
)
from connections_export.derive.scope import scope_interchange, select_interchange


def _model() -> Interchange:
    return Interchange(
        forums=[DerivedForum(id="F", title="Support")],
        file_libraries=[
            DerivedFileLibrary(
                id="L",
                title="Community Files",
                file_ids=["a"],
                files={"a": DerivedFile(id="a", name="Salaries.xlsx")},
            )
        ],
    )


def test_scoping_to_a_forum_drops_the_file_library():
    scoped = select_interchange(_model(), [("forum", "F")])

    assert [f.id for f in scoped.forums] == ["F"]
    assert scoped.file_libraries == []


def test_scoping_to_a_single_container_drops_the_file_library():
    scoped = scope_interchange(_model(), kind="forum", target_id="F")

    assert scoped.file_libraries == []


def test_a_file_library_can_be_selected_on_its_own():
    scoped = select_interchange(_model(), [("files", "L")])

    assert [lib.id for lib in scoped.file_libraries] == ["L"]
    assert scoped.forums == []


def test_a_library_can_be_selected_alongside_other_containers():
    scoped = select_interchange(_model(), [("forum", "F"), ("files", "L")])

    assert [f.id for f in scoped.forums] == ["F"]
    assert [lib.id for lib in scoped.file_libraries] == ["L"]


def test_an_unmatched_library_selection_yields_nothing():
    """Failing OPEN here would export the whole archive because the one thing
    asked for no longer exists."""
    scoped = select_interchange(_model(), [("files", "nonexistent")])

    assert scoped.file_libraries == []
    assert scoped.forums == []


# --- Rich Content -----------------------------------------------------------
#
# Same failure, same fix, one app later: a container the scoper does not know
# about is a container that rides along with every narrowed export.


def _with_highlights() -> Interchange:
    from connections_export.derive.model import DerivedRichContent, DerivedRichContentPage

    return Interchange(
        forums=[DerivedForum(id="F", title="Support")],
        rich_content=[
            DerivedRichContent(
                id="C",
                title="Highlights",
                page_ids=["p1"],
                pages={"p1": DerivedRichContentPage(id="p1", resource_id="p1", title="Welcome")},
            )
        ],
    )


def test_scoping_to_a_forum_drops_the_rich_content():
    scoped = select_interchange(_with_highlights(), [("forum", "F")])

    assert [f.id for f in scoped.forums] == ["F"]
    assert scoped.rich_content == []


def test_rich_content_can_be_selected_on_its_own():
    scoped = select_interchange(_with_highlights(), [("rich_content", "C")])

    assert [rc.id for rc in scoped.rich_content] == ["C"]
    assert scoped.forums == []


def test_scoping_to_a_single_container_drops_the_rich_content():
    scoped = scope_interchange(_with_highlights(), kind="forum", target_id="F")

    assert scoped.rich_content == []
