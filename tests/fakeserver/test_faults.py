"""Fault injection is deterministic under a fixed seed.

Forced status, malformed XML, truncated body, wrong Content-Type, and
timeout are all exercised through the ASGI app in-process. Probabilistic
faults are checked for reproducibility across two independently
constructed `Faults` instances sharing a seed.
"""

import httpx
import pytest

from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize

from .conftest import get


def _wikiset():
    return synthesize(SynthSeed(seed=51, wiki_count=1, depth=1, pages_per_level=2))


def test_forced_500_on_one_path_others_unaffected():
    wikiset = _wikiset()
    faulted_path = "/wikis/basic/api/wikis/feed"
    faults = Faults(status_for={faulted_path: 500})
    app = make_app(wikiset, faults=faults)

    r = get(app, faulted_path)
    assert r.status_code == 500

    other_path = f"/wikis/basic/api/wiki/{wikiset.wikis[0].label}/feed"
    r2 = get(app, other_path)
    assert r2.status_code == 200


def test_forced_401():
    wikiset = _wikiset()
    path = "/wikis/basic/api/wikis/feed"
    faults = Faults(status_for={path: 401})
    app = make_app(wikiset, faults=faults)
    r = get(app, path)
    assert r.status_code == 401


def test_truncated_body():
    wikiset = _wikiset()
    path = f"/wikis/basic/api/wiki/{wikiset.wikis[0].label}/feed"
    plain_app = make_app(wikiset, faults=Faults())
    full = get(plain_app, path)

    faulted_app = make_app(wikiset, faults=Faults(truncate_paths={path}))
    truncated = get(faulted_app, path)

    assert truncated.status_code == 200
    assert len(truncated.content) < len(full.content)


def test_wrong_content_type():
    wikiset = _wikiset()
    path = f"/wikis/basic/api/wiki/{wikiset.wikis[0].label}/feed"
    app = make_app(wikiset, faults=Faults(wrong_content_type={path: "application/json"}))
    r = get(app, path)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"


def test_malformed_xml():
    wikiset = _wikiset()
    path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset, faults=Faults(malformed_paths={path}))
    r = get(app, path)
    assert r.status_code == 200
    from xml.etree import ElementTree as ET

    with pytest.raises(ET.ParseError):
        ET.fromstring(r.content)


def test_timeout_path_surfaces_as_httpx_timeout():
    wikiset = _wikiset()
    path = "/wikis/basic/api/wikis/feed"
    app = make_app(wikiset, faults=Faults(timeout_paths={path}))

    import asyncio

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://fake") as client:
            return await client.get(path)

    with pytest.raises(httpx.TimeoutException):
        asyncio.run(_do())


def test_probabilistic_faults_reproducible_under_fixed_seed():
    wikiset = _wikiset()
    path = "/wikis/basic/api/wikis/feed"

    def run_sequence(seed: int) -> list[int]:
        faults = Faults(fail_probability=0.5, seed=seed)
        app = make_app(wikiset, faults=faults)
        return [get(app, path).status_code for _ in range(20)]

    first = run_sequence(99)
    second = run_sequence(99)
    assert first == second
    # With p=0.5 over 20 draws, expect a mix (not all-200 / all-500) --
    # guards against an implementation that ignores fail_probability.
    assert 200 in first
    assert 500 in first


def test_probabilistic_faults_differ_across_seeds_generally():
    wikiset = _wikiset()
    path = "/wikis/basic/api/wikis/feed"

    def run_sequence(seed: int) -> list[int]:
        faults = Faults(fail_probability=0.5, seed=seed)
        app = make_app(wikiset, faults=faults)
        return [get(app, path).status_code for _ in range(20)]

    a = run_sequence(1)
    b = run_sequence(2)
    assert a != b
