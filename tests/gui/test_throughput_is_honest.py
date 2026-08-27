"""The throughput figure says what it measures, and can say it.

with the delay set to 0.2s the reading was
"always 60 or 70, never in between". Both halves of that are the meter, not
the crawl.

  never in between it counted fetches in a 6-second window and multiplied
                     by 10, so the only readings it could ever produce were
                     multiples of 10

  60 or 70 roughly one request per second. The delay was doing its
                     job -- measured: `min_interval=0.2` spaces requests by
                     0.20s -- and the rest of that second was the deployment's
                     own response time, which nothing on screen reported. So
                     "my delay is being ignored" and "the server is slow" were
                     indistinguishable.

And it was labelled "pages / min" while counting requests, which for a wiki is
several per page.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()


def test_the_rate_is_not_quantised_to_multiples_of_ten():
    assert "fetchTimes.length * 10" not in JS
    assert "THROUGHPUT_WINDOW_MS" in JS


def test_it_says_requests_rather_than_pages():
    """A wiki page costs several requests, so "pages / min" overstated by a
    factor nobody could see."""
    assert "pages / min" not in HTML
    assert "requests / min" in HTML


def test_the_deployment_s_own_response_time_is_shown():
    """The number that tells you where the second went."""
    assert 'id="k-latency"' in HTML
    assert "liveFetching" in JS
    assert "fetchStarted" in JS


def test_the_client_honours_the_configured_delay():
    """The other half of the report, asserted rather than assumed: what the
    console sends reaches the client that does the waiting."""
    from connections_export.cli import _build_default_client
    from connections_export.config import Config

    client = _build_default_client(
        Config(base_url="https://fake", auth_mode="basic", min_interval=0.2), env={}
    )
    assert client.min_interval == 0.2


def test_a_delay_below_the_floor_is_raised_not_dropped():
    from connections_export.cli import _build_default_client
    from connections_export.config import Config

    client = _build_default_client(
        Config(base_url="https://fake", auth_mode="basic", min_interval=0.0), env={}
    )
    assert client.min_interval == Config.MIN_REQUEST_INTERVAL
