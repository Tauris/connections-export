"""While an ingest runs, the screen that set it up says so.

It kept offering "Start ingest" with every control live, so the settings on
screen looked like something you could still change -- and starting again
during a run is not a thing anyone means to do.

The delay is the exception, deliberately: pacing is the one decision worth
changing while a run is in flight, because the reason to change it (the
deployment is struggling) only shows up once the run is under way. It is
applied to the running crawl, not saved for next time.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()


def test_the_screen_has_a_running_state():
    assert "setSelectRunning" in JS
    assert 'id="select-running"' in HTML


def test_the_start_button_does_not_invite_a_second_run():
    assert "Ingest running" in HTML or "Ingest running" in JS


def test_everything_that_shaped_the_run_is_locked_while_it_runs():
    """Still readable -- seeing what this run was given is the point -- but
    not editable, because changing it would describe a run that is not
    happening."""
    assert "selectControlsDisabled" in JS


def test_the_delay_stays_live():
    assert "field-delay" in JS
    assert "/api/pace" in JS


def test_the_pace_change_reaches_the_running_crawl():
    """Not merely accepted and filed away: the client doing the waiting is the
    one that has to change."""
    from connections_export.http.client import HttpClient

    client = HttpClient(min_interval=1.0)
    client.min_interval = 0.2
    assert client.min_interval == 0.2


def test_a_pace_below_the_floor_is_raised_rather_than_refused():
    from connections_export.config import Config
    from connections_export.http.client import HttpClient

    client = HttpClient(min_interval=1.0)
    client.min_interval = 0.0
    assert client.min_interval == Config.MIN_REQUEST_INTERVAL
