"""Files adapter: a community's file library, and its folders.

Endpoints are against a live deployment (see
the published API reference). They are NOT the ones HCL documents:
the published `/files/basic/api/library/{libraryId}/feed` needs a library id,
and the documented route from a community to its library does not exist -- the
generic `libraries/feed` is global, reporting hundreds of thousands of
libraries. The community-scoped endpoints take the **community UUID** directly.

The entry FIELD NAMES are from two captures; the literal XML shape
carrying them is not documented, because the captures recorded which fields exist
rather than the bytes. Every field therefore degrades to None rather than
raising, so a shape that differs slightly from this guess produces a thinner
export and not a crash.
"""

from __future__ import annotations

from urllib.parse import quote

from connections_export.adapters import atom
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.model import CommunityFile, CommunityFolder

#: Attribute names carried on the entries.
_SNX = "http://www.ibm.com/xmlns/prod/sn"

#: `libraryType` of a community's own library, as opposed to a personal one.
COMMUNITY_LIBRARY_TYPE = "communityFiles"


def community_library_url(*, base_url: str, community_uuid: str) -> str:
    """A community's files. -- takes the COMMUNITY uuid, not a
    library id, which is the opposite of what the documentation implies."""
    return f"{base_url.rstrip('/')}/files/basic/api/communitylibrary/{community_uuid}/feed"


def community_collection_url(*, base_url: str, community_uuid: str) -> str:
    """A community's folders..

    Carries entries the library feed does not, personal files among them --
    filter against the library, never merge.
    """
    return f"{base_url.rstrip('/')}/files/basic/api/communitycollection/{community_uuid}/feed"


def community_introspection_url(*, base_url: str, community_uuid: str) -> str:
    """The community's Files service document.."""
    return f"{base_url.rstrip('/')}/files/basic/api/community/{community_uuid}/introspection"


def media_request_url(href: str | None) -> str | None:
    """The download URL in the form it will actually be requested in.

    File names contain spaces, colons and question marks. An HTTP client
    percent-encodes those before sending, so the URL the crawler ARCHIVES is
    not the raw href the feed carried -- and derive, looking the raw one up,
    found nothing for six files out of eight. Worse, a `?` in a name is read as
    the start of a query string, so the tail of the name silently becomes a
    query parameter.

    Encoding the final path segment here makes both sides use one unambiguous
    string, and makes them agree by construction rather than by coincidence.
    """
    if not href:
        return href
    head, sep, name = href.rpartition("/")
    if not sep:
        return href
    return f"{head}/{quote(name, safe='')}"


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _first_text(entry, *paths: str) -> str | None:
    for path in paths:
        text = atom.find_text(entry, path)
        if text:
            return text
    return None


def parse_library_feed(data: bytes | str) -> list[CommunityFile]:
    """A community library feed -> `CommunityFile`s, in feed order.

    Required: `<id>`. Everything else degrades, deliberately -- the XML shape
    is inferred, and a thin export beats a crash on a field we guessed wrong.
    """
    root = atom.parse_xml(data, expected_root="feed")
    files: list[CommunityFile] = []
    for entry in atom.entries(root):
        raw_id = atom.find_text(entry, "atom:id")
        if not raw_id:
            raise AdapterError("file entry has no <id>")
        files.append(
            CommunityFile(
                id=atom.entry_uuid(raw_id) or raw_id,
                # `label` is the file name as stored; `title` is the display
                # name. They are usually the same and occasionally are not.
                name=_first_text(entry, "td:label", "atom:title"),
                title=atom.find_text(entry, "atom:title"),
                library_id=_first_text(entry, "td:libraryId"),
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                published=atom.find_text(entry, "atom:published"),
                # On the live community feed, atom:updated is the feed/read
                # timestamp and changes on every listing. Use the file's
                # content modification timestamp for update cache decisions.
                updated=_first_text(entry, "td:modified", "atom:updated"),
                size=_int_or_none(_first_text(entry, "td:totalMediaSize")),
                content_type=_first_text(entry, "td:objectTypeName"),
                version_label=_first_text(entry, "td:versionLabel"),
                filed_in_folder=(_first_text(entry, "td:isFiledInFolder") or "").lower() == "true",
                tags=atom.bare_category_terms(entry),
                download_url=media_request_url(atom.link_href(entry, "enclosure")),
                alternate_url=atom.link_href(entry, "alternate"),
                replies_url=atom.link_href(entry, "replies"),
                provenance=raw_id,
            )
        )
    return files


def parse_collection_feed(data: bytes | str) -> list[CommunityFolder]:
    """A community collection feed -> `CommunityFolder`s.

    The literal shape is a reconstruction: what the feed contains -- file
    entries and folder associations -- but not how folder membership is
    expressed, so this reads the conservative interpretation: entries that name
    a folder become folders, and the rest are ignored.
    """
    root = atom.parse_xml(data, expected_root="feed")
    folders: dict[str, CommunityFolder] = {}
    for entry in atom.entries(root):
        raw_id = atom.find_text(entry, "atom:id")
        if not raw_id:
            continue
        entry_id = atom.entry_uuid(raw_id) or raw_id
        folder_name = _first_text(entry, "td:folderName", "td:collectionName")
        if folder_name is None:
            continue
        folder = folders.setdefault(
            entry_id, CommunityFolder(id=entry_id, name=folder_name, file_ids=[])
        )
        for child in atom.entries(entry):
            child_id = atom.find_text(child, "atom:id")
            if child_id:
                folder.file_ids.append(atom.entry_uuid(child_id) or child_id)
    return list(folders.values())


def folders_limited_to(
    folders: list[CommunityFolder], files: list[CommunityFile]
) -> list[CommunityFolder]:
    """Folder memberships, restricted to files the library actually contains.

    The collection feed carries entries the community library does not --
    personal files among them. A capture found 22 collection entries against 19
    library files, the three extra being someone's own documents. Merging the
    two feeds would pull private files into a community export; the library is
    the authority for what belongs, and folders only say how it is arranged.

    Folders left with no members are dropped: an empty folder here means every
    file it named was somebody else's.
    """
    known = {f.id for f in files}
    out: list[CommunityFolder] = []
    for folder in folders:
        kept = [file_id for file_id in folder.file_ids if file_id in known]
        if kept:
            out.append(CommunityFolder(id=folder.id, name=folder.name, file_ids=kept))
    return out
