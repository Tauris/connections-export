"""The per-app `AppProfile` capability objects carry the
right divergent transport data -- ps_max, since_encoding, sort_by
vocabulary, id_infix (Per-app capability objects). This is the change's own encoding of "the
transport generalizes
badly": a single shared constant would silently clamp Forums' larger
page size to Blogs' smaller one, or mis-encode one app's `since` as the
other's."""

import dataclasses

import pytest

from connections_export.adapters.profiles import BLOGS, FORUMS, AppProfile


def test_blogs_profile():
    assert BLOGS.name == "blogs"
    assert BLOGS.ps_max == 50
    assert BLOGS.since_encoding == "rfc3339"
    assert BLOGS.sort_by == {"commented", "modified", "popularity", "recommended", "title"}
    assert BLOGS.id_infix == "blogs:entry-"


def test_forums_profile():
    assert FORUMS.name == "forums"
    assert FORUMS.ps_max == 100
    assert FORUMS.since_encoding == "epoch_ms"
    assert FORUMS.sort_by == {"created", "modified"}
    assert FORUMS.id_infix == "forum:"


def test_profiles_differ():
    """Spec.md "Blogs and Forums differ" scenario, verbatim: distinct
    since-encoding and page-size ceiling."""
    assert BLOGS.since_encoding != FORUMS.since_encoding
    assert BLOGS.ps_max != FORUMS.ps_max


def test_app_profile_is_frozen():
    profile = AppProfile(
        name="test", ps_max=1, since_encoding="rfc3339", sort_by=frozenset({"x"}), id_infix="x:"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "changed"  # type: ignore[misc]
