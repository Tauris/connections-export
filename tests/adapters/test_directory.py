"""Profiles/people-directory adapter: resolve email -> stable snx:userid
(the directory GUID). The load-bearing subtlety (docs/reference/
hcl-profiles-api.md): on a Profiles feed the person is the `atom:contributor`,
NOT the `atom:author` (which is the "HCL Connections - Profiles" generator)."""

from connections_export.adapters.directory import (
    parse_current_user,
    parse_profile_identity,
    profile_by_email_url,
    profile_by_userid_url,
    profile_service_url,
)

# A profile feed shaped like the real one: the generator is the <author>, the
# PERSON is the <contributor> carrying the stable <snx:userid>.
_PROFILE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <title type="text">profile with email loretta@example.com</title>
  <author><name>HCL Connections - Profiles</name></author>
  <entry>
    <title type="text">Mahon, Loretta (Engineering)</title>
    <contributor>
      <name>Mahon, Loretta (Engineering)</name>
      <email>loretta@example.com</email>
      <snx:userid>57017ac0-0101-102e-8b1e-f78755f7e0ed</snx:userid>
    </contributor>
  </entry>
</feed>"""


def test_email_url_builder():
    url = profile_by_email_url(base_url="https://fake", email="a b@example.com")
    assert url.startswith("https://fake/profiles/atom/profile.do?email=")
    assert url.endswith("&format=full")
    assert "a%20b" in url and "%40" in url  # space and @ are percent-encoded


def test_userid_url_builder():
    url = profile_by_userid_url(base_url="https://fake", userid="57017ac0-0101")
    assert url.endswith("profile.do?userid=57017ac0-0101&format=full")


def test_current_user_service_document():
    data = b"""<service xmlns="http://www.w3.org/2007/app" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:snx="http://www.ibm.com/xmlns/prod/sn"><workspace><collection><atom:title>Me</atom:title><snx:userid>uid-1</snx:userid></collection></workspace></service>"""
    assert (
        profile_service_url(base_url="https://fake")
        == "https://fake/profiles/atom/profileService.do"
    )
    user = parse_current_user(data)
    assert user and user.userid == "uid-1" and user.name == "Me"


def test_resolves_userid_from_contributor_not_author():
    user = parse_profile_identity(_PROFILE_FEED)
    assert user is not None
    # The directory GUID, taken from <contributor> -- NOT the generator <author>.
    assert user.userid == "57017ac0-0101-102e-8b1e-f78755f7e0ed"
    assert user.name == "Mahon, Loretta (Engineering)"
    assert user.email == "loretta@example.com"


def test_empty_feed_resolves_to_none():
    empty = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>0 results</title></feed>'
    assert parse_profile_identity(empty) is None


def test_feed_without_contributor_userid_is_none():
    # A deployment that hides the field: an entry, but no contributor userid.
    no_uid = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><title>Someone</title><contributor><name>Someone</name></contributor></entry>"
        b"</feed>"
    )
    assert parse_profile_identity(no_uid) is None
