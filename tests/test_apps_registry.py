"""The five applications, named once.

The app list was restated in at least eleven places, and two of them had
already fallen out of step: the CLI rejected a component the console emitted,
and the package writer copied blobs for three apps while the manifest counted
five. This test is what makes the registry load-bearing rather than
decorative -- every consumer is checked against it, not against a copy.
"""

from __future__ import annotations

import inspect

from connections_export import apps


def test_every_app_declares_a_complete_spec():
    assert {app.kind for app in apps.APPS} == {
        "wiki",
        "forum",
        "blog",
        "ideation_blog",
        "files",
        "rich_content",
    }
    for app in apps.APPS:
        assert app.interchange_field, f"{app.kind} has no interchange field"
        assert app.adapter_version, f"{app.kind} has no adapter version"
        assert app.cap_kwarg, f"{app.kind} has no cap kwarg"
        assert app.id_arity in ("list", "each"), f"{app.kind} has a bad id arity"
        assert app.profile is not None, f"{app.kind} has no transport profile"


def test_the_cli_component_vocabulary_is_the_registry():
    from connections_export.cli import COMPONENT_KINDS

    assert tuple(COMPONENT_KINDS) == apps.COMPONENT_KINDS


def test_every_interchange_field_is_a_real_field_on_the_model():
    from connections_export.derive.model import Interchange

    fields = set(Interchange.model_fields)
    for field in apps.INTERCHANGE_FIELDS:
        assert field in fields, f"{field} is not a field on Interchange"


def test_every_cap_and_id_kwarg_is_a_real_parameter_of_its_crawl():
    """Five names for one value is bad enough; a name that does not exist is a
    TypeError at run time, in the one branch nobody exercises."""
    from connections_export.crawler.dispatch import crawl_for

    for app in apps.APPS:
        signature = inspect.signature(crawl_for(app.kind))
        assert app.cap_kwarg in signature.parameters, (
            f"{app.kind}: {app.cap_kwarg} is not a parameter of its crawl"
        )
        assert app.id_kwarg in signature.parameters, (
            f"{app.kind}: {app.id_kwarg} is not a parameter of its crawl"
        )


def test_accepts_since_matches_what_the_crawl_actually_takes():
    """Passing `since` to a crawl that has no such parameter is a TypeError in
    exactly the branch a unit test never reaches -- and NOT passing it to one
    that does is a silent full re-crawl."""
    from connections_export.crawler.dispatch import crawl_for

    for app in apps.APPS:
        takes_since = "since" in inspect.signature(crawl_for(app.kind)).parameters
        assert app.accepts_since == takes_since, (
            f"{app.kind}: accepts_since={app.accepts_since} but its crawl "
            f"{'takes' if takes_since else 'does not take'} `since`"
        )


def test_no_crawl_sends_a_cutoff_to_a_feed_that_cannot_take_one():
    """The dangerous direction, asserted one-way on purpose.

    `AppProfile.since_encoding` says what the FEED accepts;
    `AppSpec.accepts_since` says what the CRAWL implements. Claiming
    `accepts_since` for a feed whose profile says `unsupported` would send a
    cutoff to a feed that ignores it -- and a feed that ignores a date filter
    returns everything, which the crawler would then treat as "everything
    changed", or worse, a deployment could return nothing and the run would
    report "no changes" for content that had in fact moved.

    The reverse gap is legal and currently real: Files declares
    `since_encoding="rfc3339"`, so the deployment WOULD accept a cutoff, but
    `crawl_files` takes no `since` and re-reads the whole library on every
    update. That is a missed optimisation rather than a correctness bug, so it
    is recorded here rather than asserted away -- see the note on
    `apps.FILES_APP`.
    """
    for app in apps.APPS:
        if app.profile.since_encoding == "unsupported":
            assert not app.accepts_since, (
                f"{app.kind}: claims to accept a cutoff, but its feed cannot "
                f"encode one (since_encoding='unsupported')"
            )


def test_the_known_cutoff_gaps_are_the_ones_we_think_they_are():
    """A ratchet, not a rule. If someone teaches a crawl to send a cutoff,
    this fails and they delete their app from the list -- which is the moment
    to notice the update got cheaper. If someone adds a SIXTH app with the
    same gap, this fails too, and that is the moment to ask why."""
    gaps = {
        app.kind
        for app in apps.APPS
        if app.profile.since_encoding != "unsupported" and not app.accepts_since
    }

    assert gaps == {"files"}, (
        f"cutoff gaps changed: {sorted(gaps)}. A feed that accepts a date "
        f"filter whose crawl never sends one re-reads everything on update."
    )
