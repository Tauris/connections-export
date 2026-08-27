"""Working with archives you already have, and nothing else.

Not everyone who opens this tool can reach a Connections deployment. Someone
handed an archive to read, or keeping one for reference long after they stopped
capturing, has no use for the capture screens -- and a screen you can never
successfully use is not neutral, it is clutter that makes the tool look broken.

`archive_only` hides the surfaces that need a live system. It is a display
choice and nothing else: no data changes, and turning it off brings everything
back exactly as it was.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from connections_export.gui import make_app
from connections_export.gui import support as gui_support


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)
    return TestClient(make_app(demo=True, demo_delay=0), base_url="http://127.0.0.1")


def _save(client, **changes) -> None:
    """Save settings the way the console does.

    In demo mode the GET answers `auth_mode: null` -- there is no real
    deployment to sign in to -- and the PUT requires a real mode. The console
    never hits that, because it posts the value of its own auth field; a naive
    GET-then-PUT round trip does.
    """
    settings = client.get("/api/settings").json()
    settings["auth_mode"] = settings.get("auth_mode") or "sspi"
    settings.update(changes)
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200, response.text


def test_the_capture_screens_are_shown_by_default(client):
    """Nobody who installs this to capture something should have to find a
    setting first."""
    assert client.get("/api/settings").json()["archive_only"] is False


def test_the_setting_survives_a_round_trip(client):
    _save(client, archive_only=True)

    assert client.get("/api/settings").json()["archive_only"] is True


def test_turning_it_off_restores_everything(client):
    """A display choice must be exactly reversible. Anything that did not come
    back would make this a decision rather than a preference."""
    _save(client, archive_only=True)
    _save(client, archive_only=False)

    assert client.get("/api/settings").json()["archive_only"] is False


def test_it_changes_no_archive_and_captures_nothing(client, tmp_path):
    """It hides screens. It must not touch what is on disk -- an archive is
    the thing this tool exists to protect."""
    from connections_export.gui.demo import run_demo

    run_demo(lambda _e: None, delay=0, archive_dir=tmp_path / "export-demo", wave=0)
    before = sorted(p.name for p in (tmp_path / "export-demo").iterdir())

    _save(client, archive_only=True)

    assert sorted(p.name for p in (tmp_path / "export-demo").iterdir()) == before


def test_archives_stay_readable_when_capture_is_hidden(client, tmp_path):
    """The whole point: what remains has to keep working."""
    from connections_export.gui.demo import run_demo

    run_demo(lambda _e: None, delay=0, archive_dir=tmp_path / "export-demo", wave=0)
    _save(client, archive_only=True)

    listing = client.get("/api/archives")

    assert listing.status_code == 200
    assert listing.json()["archives"]


# --- what the console does with it ------------------------------------------


def _js() -> str:
    from tests.gui._served_assets import served_console_js

    return served_console_js()


def test_the_capture_screens_are_the_ones_hidden():
    view = _js()

    assert 'LIVE_ONLY_SECTIONS = ["select", "ingest"]' in view


def test_the_demo_is_deliberately_not_hidden():
    """It runs entirely in-process, so it still works with no deployment at
    all -- and it is the one place left that shows how an archive comes to
    exist."""
    view = _js()
    # The function that does the hiding: whatever it touches is what
    # disappears. The reasoning lives just above it.
    hiding = view[view.index("function applyArchiveOnly") :]
    hiding = hiding[: hiding.index("\n  }")]

    assert "run-demo" not in hiding
    assert "deliberately NOT here" in view


def test_live_pdf_and_extend_go_too():
    """Both need the original system to answer. Leaving them behind would
    hide the screens and keep the buttons that fail."""
    view = _js()

    assert "start-live-pdf" in view and "live-pdf-preview" in view
    assert "data-extend-action" in view


def test_hiding_the_screen_you_are_standing_on_moves_you():
    """Otherwise the console shows a section its own navigation no longer
    offers, which looks like a bug in the navigation."""
    assert 'showSection("archives")' in _js()


def test_a_save_that_does_not_mention_it_leaves_it_alone(client):
    """The setting lives in one card; the Save buttons live in others. A body
    that omits it must not silently clear it -- the same rule `base_url`
    already follows in this handler, and for the same reason: saving one
    card's fields cannot reach across the page and reset another's.
    """
    _save(client, archive_only=True)

    settings = client.get("/api/settings").json()
    settings["auth_mode"] = settings.get("auth_mode") or "sspi"
    settings.pop("archive_only")
    assert client.put("/api/settings", json=settings).status_code == 200

    assert client.get("/api/settings").json()["archive_only"] is True


def test_the_setting_has_its_explanation_where_it_is_chosen():
    from tests.gui._served_assets import served_console_html

    html = served_console_html()

    assert 'id="settings-archive-only"' in html
    assert "only work with archives I already have" in html
    assert "nothing on disk changes" in html
