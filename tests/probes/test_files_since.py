"""The Files `since=` probe: does it filter on modification or creation?

The parameter is documented but unconfirmed, so the crawler takes the
conservative side (it sends no cutoff): the wrong reading loses edits to old
documents silently.

What is tested here is the part that can be: the choice of subjects and the
truth table. The two GETs need a deployment, which is the whole point.
"""

from __future__ import annotations

import pytest

from connections_export.adapters.model import CommunityFile
from connections_export.probes import (
    interpret_files_since,
    plan_files_since_probe,
)


def _file(ident: str, published: str, updated: str) -> CommunityFile:
    return CommunityFile(id=ident, name=f"{ident}.pdf", published=published, updated=updated)


#: An edited-old document and one untouched long before it.
LIBRARY = [
    CommunityFile(
        id="old-and-quiet",
        name="quiet.pdf",
        published="2024-01-01T00:00:00Z",
        updated="2024-01-02T00:00:00Z",
    ),
    CommunityFile(
        id="old-but-edited",
        name="edited.pdf",
        published="2024-02-01T00:00:00Z",
        updated="2026-08-01T00:00:00Z",
    ),
]


def test_the_plan_picks_a_document_edited_long_after_it_was_written():
    plan = plan_files_since_probe(LIBRARY)

    assert plan is not None
    assert plan.candidate_id == "old-but-edited"
    assert plan.control_id == "old-and-quiet"


def test_the_cutoff_falls_between_the_candidates_two_dates():
    """Between, so the candidate is old by creation and new by modification --
    which is the only way one request separates the two readings."""
    from connections_export.adapters.since import parse_cutoff

    plan = plan_files_since_probe(LIBRARY)
    cutoff = parse_cutoff(plan.cutoff)

    assert parse_cutoff("2024-02-01T00:00:00Z") < cutoff < parse_cutoff("2026-08-01T00:00:00Z")
    # ...and after the control's last change, or the control proves nothing.
    assert parse_cutoff("2024-01-02T00:00:00Z") < cutoff


def test_a_library_nobody_has_ever_edited_cannot_answer_the_question():
    """Said plainly rather than guessed at: every document was uploaded and
    left alone, so no cutoff exists that the two readings disagree about."""
    untouched = [
        _file("a", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        _file("b", "2024-03-01T00:00:00Z", "2024-03-01T00:00:00Z"),
    ]

    assert plan_files_since_probe(untouched) is None


def test_one_document_alone_cannot_answer_it_either():
    """No control means no way to tell "filters on modification" from
    "ignored the parameter" -- both return the candidate."""
    assert (
        plan_files_since_probe([_file("only", "2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")])
        is None
    )


@pytest.mark.parametrize(
    ("returned", "behaviour", "safe"),
    [
        (["old-but-edited"], "modification", True),
        ([], "creation", False),
        (["old-but-edited", "old-and-quiet"], "ignored", False),
        (["old-and-quiet"], "incoherent", False),
    ],
)
def test_the_truth_table(returned, behaviour, safe):
    plan = plan_files_since_probe(LIBRARY)

    verdict = interpret_files_since(plan, returned)

    assert verdict.behaviour == behaviour
    assert verdict.safe_to_send is safe


def test_only_the_modification_reading_is_ever_called_safe():
    """The point of the probe. Three of the four outcomes leave the crawler
    exactly as it is."""
    plan = plan_files_since_probe(LIBRARY)
    outcomes = [
        interpret_files_since(plan, returned)
        for returned in ([], ["old-and-quiet"], ["old-but-edited", "old-and-quiet"])
    ]

    assert not any(v.safe_to_send for v in outcomes)
    # Each says which reading it saw and what follows from it, rather than
    # leaving the reader to work out what an outcome means.
    assert {v.behaviour for v in outcomes} == {"creation", "ignored", "incoherent"}
    assert all(len(v.detail) > 60 for v in outcomes)


def test_the_probe_runs_end_to_end_against_a_server(tmp_path):
    """The two GETs and the parsing, wired together. The fake returns the
    whole library whatever `since` says -- which is itself one of the four
    answers, and the one this proves the probe can reach."""
    import httpx

    from connections_export.fakeserver.app import make_app
    from connections_export.fakeserver.model import DEMO_COMMUNITY_UUID
    from connections_export.fakeserver.synth import (
        SynthSeed,
        synthesize,
        synthesize_files,
    )
    from connections_export.gui.bridge import SyncASGIBridge
    from connections_export.http.client import HttpClient
    from connections_export.probes import probe_files_since

    seed = SynthSeed(seed=0, human_names=True)
    app = make_app(
        synthesize(seed), fileset=synthesize_files(seed, community_uuid=DEMO_COMMUNITY_UUID)
    )
    client = HttpClient(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request), sleep=lambda _s: None
    )

    verdict = probe_files_since(
        client=client, base_url="https://fake", community_uuid=DEMO_COMMUNITY_UUID
    )

    # Whatever the fake does, the probe must reach a stated verdict rather
    # than an exception or a shrug.
    assert verdict.behaviour in {
        "modification",
        "creation",
        "ignored",
        "incoherent",
        "inconclusive",
    }
    assert verdict.detail
    # ...and it must never call an unproven filter safe.
    assert verdict.safe_to_send is (verdict.behaviour == "modification")


def test_a_community_with_no_library_says_so_rather_than_failing():
    import httpx

    from connections_export.fakeserver.app import make_app
    from connections_export.fakeserver.synth import SynthSeed, synthesize
    from connections_export.gui.bridge import SyncASGIBridge
    from connections_export.http.client import HttpClient
    from connections_export.probes import probe_files_since

    app = make_app(synthesize(SynthSeed(seed=0)))
    client = HttpClient(
        transport=httpx.MockTransport(SyncASGIBridge(app).handle_request), sleep=lambda _s: None
    )

    verdict = probe_files_since(
        client=client, base_url="https://fake", community_uuid="no-such-community"
    )

    assert verdict.behaviour == "inconclusive"
    assert not verdict.safe_to_send
