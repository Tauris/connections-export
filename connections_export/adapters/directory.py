"""HCL Connections Profiles API — the people directory. Resolves a **stable
handle (email / userid)** to the stable directory GUID (`snx:userid`) that
content feeds credit as `atom:author/snx:userid`, so the author filter can
match on the uuid rather than the volatile display name ("Surname, Firstname
(Department)").

Reference + provenance: the published API reference. (Named `directory`,
not `profiles`, because `adapters/profiles.py` already holds the unrelated
per-app transport `AppProfile` objects.)

THE GOTCHA (load-bearing): on a Profiles feed the person is the
**`atom:contributor`**, not the `atom:author` (the feed's `<author>` is the
generator, "HCL Connections - Profiles"). So we read `contributor` userids,
never `author_userid` — see the doc's PARSER-CONSISTENCY NOTE.
"""

from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel

from connections_export.adapters import atom


class ResolvedUser(BaseModel):
    """A person resolved from a profile lookup. `userid` is the stable
    directory GUID (`snx:userid`) — the join key to content authorship."""

    userid: str
    name: str | None = None
    email: str | None = None


def profile_by_email_url(*, base_url: str, email: str) -> str:
    """`GET /profiles/atom/profile.do?email=<email>&format=full`.
    Returns an Atom feed whose first entry's
    `<contributor>` carries the person's `<snx:userid>`."""
    return f"{base_url}/profiles/atom/profile.do?email={quote(email, safe='')}&format=full"


def profile_by_userid_url(*, base_url: str, userid: str) -> str:
    """The reverse lookup: `profile.do?userid=<uuid>&format=full` -- confirm a
    uuid and get the display name back."""
    return f"{base_url}/profiles/atom/profile.do?userid={quote(userid, safe='')}&format=full"


def profile_service_url(*, base_url: str) -> str:
    """The authenticated Profiles service document for the current user."""
    return f"{base_url}/profiles/atom/profileService.do"


def parse_profile_identity(data: bytes | str) -> ResolvedUser | None:
    """The stable identity from a Profiles feed: the first entry's
    `<contributor><snx:userid>` (the directory GUID) + its `<name>`. `None` if
    the feed has no entry or no contributor userid (an empty/'0 results' feed,
    or a deployment that hides the field). Tolerant: never raises on a
    well-formed-but-empty feed."""
    root = atom.parse_xml(data, expected_root="feed")
    for entry in atom.entries(root):
        # NOT author_userid -- on Profiles the person is the contributor.
        userids = atom.contributor_userids(entry)
        if userids and userids[0]:
            names = atom.contributor_names(entry)
            name = names[0] if names else atom.find_text(entry, "atom:title")
            # The <email> lives inside the <contributor> element.
            contributor = entry.find("atom:contributor", namespaces=atom.NS)
            email = atom.find_text(contributor, "atom:email") if contributor is not None else None
            return ResolvedUser(userid=userids[0], name=(name or None), email=(email or None))
    return None


def parse_current_user(data: bytes | str) -> ResolvedUser | None:
    """Read the current user's identity from an authenticated service doc."""
    root = atom.parse_xml(data)
    for collection in root.iter():
        if not isinstance(collection.tag, str) or collection.tag.rsplit("}", 1)[-1] != "collection":
            continue
        userid = next(
            (
                (child.text or "").strip()
                for child in collection
                if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == "userid"
            ),
            "",
        )
        if userid:
            name = next(
                (
                    (child.text or "").strip()
                    for child in collection
                    if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == "title"
                ),
                "",
            )
            return ResolvedUser(userid=userid, name=name or None)
    return None
