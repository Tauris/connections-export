"""The sidebar chip says demo only while something demo is in view.

Nobody signs in to this console. It works on a URL from a real deployment or
on the demo's synthetic one, and a deployment may authenticate the requests it
makes -- but "not signed in" describes a state that does not exist, and saying
it permanently taught people to ignore the one place that reports what is
being worked on.

The chip carries a fact or it is not there:

  a resolved user -> "Signed in as <name>" (a deployment told us who we are)
  demo in view -> "Demo data"
  anything else -> hidden

"Demo in view" is about the WORK, never about how the server was started:
`serve --demo` hosts the console while a dropped real URL runs a real import
against a real system, and that session is not a demo session.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui.app import make_app
from tests.gui._served_assets import served_console_js

HOST = {"Host": "127.0.0.1:8765"}
JS = served_console_js()


@pytest.fixture
def client():
    return TestClient(make_app(demo=True))


def test_the_chip_never_says_not_signed_in():
    """It described a state that cannot occur, in the one piece of chrome that
    is always on screen."""
    # The two strings the chip used to display. Asserted as the literals a
    # renderer would contain, not as a substring of the whole file -- the
    # comment explaining why they went would otherwise fail this.
    assert '"Demo data — not signed in"' not in JS
    assert '"Not signed in to a deployment"' not in JS


def test_demo_in_view_is_decided_by_the_work_not_the_server_flag():
    """`serve --demo` means the SERVER has no deployment configured. A real URL
    dropped into that console runs a real import, and the chip must not call
    it demo."""
    assert "demoInView" in JS
    assert 'j.status === "demo_mode"' not in JS


def test_a_resolved_user_is_still_reported():
    assert "Signed in as" in JS


def test_the_chip_hides_when_there_is_nothing_to_say():
    assert "wrap.hidden = true" in JS


def test_the_open_archive_reports_whether_it_is_demo_data(client):
    """The server knows an archive is demo data from its own name, the same
    rule the archives list uses -- not from how the process was started."""
    payload = client.get("/api/current-archive", headers=HOST).json()
    assert "is_demo_data" in payload


def test_an_archive_of_real_data_is_not_demo_data_even_on_a_demo_server(client, monkeypatch):
    from connections_export.gui import archives

    assert archives.is_demo_archive("DEMO-FAKE-DATA-20260823-101010-x")
    assert archives.is_demo_archive("export-demo.connections.example-20260823-101010")
    assert not archives.is_demo_archive("export-intranet-20260823-101010-platform")


def test_the_demo_rule_has_one_definition():
    """The archives list and the chip must agree about what demo data is;
    two spellings of that would drift."""
    import inspect

    from connections_export.gui import archives

    source = inspect.getsource(archives)
    assert source.count('startswith("DEMO-FAKE-DATA")') == 1


def test_a_console_with_nothing_in_view_says_nothing():
    """`lastStartBody` is initialised to `{demo: true}` before any run, so
    reading it directly made a freshly opened console announce demo data while
    showing none. The chip must wait for a run to have actually started."""
    assert "runHasStarted" in JS
    assert "if (runHasStarted && lastStartBody && lastStartBody.demo) return true;" in JS


def test_a_real_archive_does_not_use_the_demo_identity_without_a_base_url():
    start = JS.index("async function loadShellIdentity()")
    body = JS[start : JS.index("\n  async function resolveCurrentUser", start)]
    assert "if (!base && !demoInView())" in body
    assert "incorrectly return the demo principal" in body


def test_identifying_a_url_refreshes_the_chip():
    """Identifying a demo URL IS the demo viewing state. Without this the chip
    only caught up at the next run or reload."""
    start = JS.index("function applyIdentifyResult")
    body = JS[start : start + 1200]
    assert "loadShellIdentity()" in body
