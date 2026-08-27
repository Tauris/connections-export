"""A link to a captured document is upgraded like a link to a captured page.

Same reasoning as every other upgrade here: a link cannot be resolved while
its target is still being assembled, so it is resolved afterwards over the
finished model. A file linked from a wiki page in one community and captured
as part of another is exactly the case -- neither container could have known
about the other at assembly time.
"""

from __future__ import annotations

from connections_export.derive.crosslink import crosslink
from connections_export.derive.model import (
    DerivedFile,
    DerivedFileLibrary,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
)


def _interchange(href: str) -> Interchange:
    page = DerivedPage(
        id="page-1",
        label="onboarding",
        title="Onboarding",
        links=[
            LinkRef(original_href=href, resolved_url="https://fake" + href, scope="hcl_deployment")
        ],
    )
    library = DerivedFileLibrary(
        id="lib-1",
        community_uuid="c-1",
        file_ids=["DOC-1"],
        files={"DOC-1": DerivedFile(id="DOC-1", name="spec.pdf")},
    )
    return Interchange(
        wikis=[
            DerivedWiki(
                id="w",
                label="w",
                title="W",
                root_page_ids=["page-1"],
                pages={"page-1": page},
            )
        ],
        file_libraries=[library],
    )


def test_a_link_to_a_captured_document_is_upgraded():
    interchange = _interchange("/files/app/file/DOC-1")

    crosslink(interchange)

    link = interchange.wikis[0].pages["page-1"].links[0]
    assert link.scope == "in_export"
    assert link.target_file_id == "DOC-1"


def test_a_link_to_a_document_nobody_captured_is_left_alone():
    interchange = _interchange("/files/app/file/DOC-MISSING")

    crosslink(interchange)

    link = interchange.wikis[0].pages["page-1"].links[0]
    assert link.scope == "hcl_deployment"
    assert link.target_file_id is None
