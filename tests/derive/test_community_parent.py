"""A subcommunity names its parent in its own entry; derive records it.

Unknown stays None: an archive captured before the entry was archived, or a
top-level community, derives exactly as before.
"""

from __future__ import annotations

import pytest

from connections_export.adapters.communities import parent_community
from connections_export.derive.apps.communities import _link_parents
from connections_export.derive.model import DerivedCommunity

BASE = "https://fake"
REL = "http://www.ibm.com/xmlns/prod/sn/parentcommunity"


def _entry(links: str) -> bytes:
    return (
        f'<entry xmlns="http://www.w3.org/2005/Atom"><id>x</id><title>T</title>{links}</entry>'
    ).encode()


def _child_entry(parent: str = "p-1") -> bytes:
    return _entry(
        f'<link rel="{REL}" title="Parent Co" '
        f'href="{BASE}/communities/service/atom/community/instance?communityUuid={parent}"/>'
    )


def test_the_parent_link_names_the_parent():
    ref = parent_community(_child_entry())
    assert ref is not None
    assert (ref.uuid, ref.title) == ("p-1", "Parent Co")


@pytest.mark.parametrize("data", [None, b"", b"<html>login</html>", _entry("")])
def test_no_parent_is_none(data):
    assert parent_community(data) is None


class _Doc:
    def __init__(self, content: bytes) -> None:
        self.content = content


class _Index:
    def __init__(self, docs: dict[str, bytes]) -> None:
        self.docs = docs

    def get(self, url: str):
        return _Doc(self.docs[url]) if url in self.docs else None


def _url(uuid: str) -> str:
    return f"{BASE}/communities/service/atom/community/instance?communityUuid={uuid}"


def test_derive_sets_parent_id_and_prefers_the_parents_own_title():
    parent = DerivedCommunity(id="p-1", title="Real Parent Title")
    child = DerivedCommunity(id="c-1", title="Child")
    _link_parents(
        _Index({_url("c-1"): _child_entry(), _url("p-1"): _entry("")}),
        [parent, child],
        base_url=BASE,
    )
    assert parent.parent_id is None
    assert (child.parent_id, child.parent_title) == ("p-1", "Real Parent Title")


def test_a_parent_outside_the_export_keeps_the_links_title():
    child = DerivedCommunity(id="c-1")
    _link_parents(_Index({_url("c-1"): _child_entry()}), [child], base_url=BASE)
    assert (child.parent_id, child.parent_title) == ("p-1", "Parent Co")


def test_an_archive_without_the_entry_yields_none():
    child = DerivedCommunity(id="c-1")
    _link_parents(_Index({}), [child], base_url=BASE)
    assert child.parent_id is None and child.parent_title is None
