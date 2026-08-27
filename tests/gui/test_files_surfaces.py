"""Files where the console shows what a run captured.

The pipeline can capture a community's files and the PDF can list them, but
until the picker offers Files and the archive list counts them, a user has no
way to ask for them or to see that they arrived.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedFile,
    DerivedFileLibrary,
    Interchange,
)
from connections_export.gui.app import _demo_community_components
from connections_export.gui.archives import model_summary


def test_the_demo_community_offers_files_in_the_picker():
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    answer = _demo_community_components(DEMO_COMMUNITY_UUID)

    assert answer is not None
    files = [c for c in answer["components"] if c["kind"] == "files"]
    assert len(files) == 1, "the demo community has exactly one file library"
    assert files[0]["count"], "a component with no count tells the user nothing"


def test_component_counts_are_numbers_across_every_kind():
    """The picker sums counts to fill in "all N items" and drops anything that
    is not a finite number. A count carried as a string is silently missing
    from that total rather than visibly wrong."""
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    answer = _demo_community_components(DEMO_COMMUNITY_UUID)

    for component in answer["components"]:
        assert isinstance(component["count"], int), component


def test_the_archive_summary_counts_a_file_library():
    """`model_summary` is what the archive list reads -- it never touches the
    full model again -- so a library missing here is a library the list cannot
    show."""
    library = DerivedFileLibrary(
        id="L",
        title="Community Files",
        file_ids=["a", "b"],
        files={"a": DerivedFile(id="a", name="a.txt"), "b": DerivedFile(id="b", name="b.txt")},
    )

    summary = model_summary(Interchange(file_libraries=[library]))

    groups = [g for g in summary["groups"] if g["kind"] == "files"]
    assert len(groups) == 1
    assert groups[0]["title"] == "Community Files"
    assert groups[0]["count"] == 2
    # Addressed by the community uuid, so a later visit can ask the deployment
    # what this library holds now.
    assert groups[0]["id"]


def test_an_untitled_library_still_names_itself():
    library = DerivedFileLibrary(id="L-1", title=None)

    summary = model_summary(Interchange(file_libraries=[library]))

    assert summary["groups"][0]["title"]


# --- Rich Content -----------------------------------------------------------


def test_the_demo_community_offers_rich_content_in_the_picker():
    """The crawler, derive, reader and PDF all handled Rich Content while the
    picker never offered it -- fully supported and unreachable."""
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    answer = _demo_community_components(DEMO_COMMUNITY_UUID)

    found = [c for c in answer["components"] if c["kind"] == "rich_content"]
    assert len(found) == 1
    assert found[0]["id"] == DEMO_COMMUNITY_UUID
    assert found[0]["count"] > 0


def test_the_picker_is_told_how_many_areas_exist_not_only_how_many_have_content():
    """`placed` is what lets the checkbox say "2 with content, 1 area never
    written in" instead of just "2"."""
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID

    answer = _demo_community_components(DEMO_COMMUNITY_UUID)

    found = next(c for c in answer["components"] if c["kind"] == "rich_content")
    assert found["placed"] > found["count"]


def test_the_archive_summary_counts_rich_content():
    from connections_export.derive.model import DerivedRichContent, DerivedRichContentPage

    highlights = DerivedRichContent(
        id="C",
        title="Highlights",
        page_ids=["p1"],
        pages={"p1": DerivedRichContentPage(id="p1", resource_id="p1", title="Welcome")},
    )

    summary = model_summary(Interchange(rich_content=[highlights]))

    groups = [g for g in summary["groups"] if g["kind"] == "rich_content"]
    assert len(groups) == 1
    assert groups[0]["title"] == "Highlights"
    assert groups[0]["count"] == 1
    assert groups[0]["id"]
