"""A logged request says which deployment it went to when that is a
surprise.

Log lines were built from the path alone, so a request to the wrong
deployment rendered identically to one to the right deployment. That is
the shape of an error nobody can see: hundreds of lines scrolling past,
every one of them plausible, and no way to tell that they were answered
by a machine nobody meant to ask.

Not on every line. The host is the same for a whole run, and repeating
it several hundred times would hide the one that differs among the ones
that do not -- which is the same failure with more ink. It appears when
it is not the deployment the run is against, and that is exactly when
someone needs to see it.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js


def _short_label_body() -> str:
    """The whole of `shortLabel`, not a fixed number of characters of it:
    a slice that stops early fails when the function grows a comment."""
    js = served_console_js()
    start = js.index("function shortLabel")
    return js[start : js.index("\n  }", start)]


def test_the_log_label_can_show_a_host():
    label = _short_label_body()

    assert "host" in label, (
        "log lines are built from the path alone, so a request answered by "
        "the wrong deployment is indistinguishable from a correct one"
    )


def test_a_host_that_matches_the_run_is_not_repeated_on_every_line():
    """Ink spent on what is already known buries what is not."""
    label = _short_label_body()

    assert "expectedHost" in label or "deploymentHost" in label, (
        "the label does not compare against the deployment being read, so it "
        "either shows the host always or never"
    )


def test_the_deployment_being_read_is_derivable():
    """The comparison needs something to compare against."""
    js = served_console_js()

    assert "function deploymentHost" in js


def test_an_identity_lookup_that_is_refused_is_not_hidden():
    """A deployment that answered and declined is not the same as having
    nothing to say. Hiding the chip on failure made a refusal -- wrong
    credentials, an unreachable host, a certificate that would not verify --
    look exactly like a console that had not asked."""
    js = served_console_js()
    chip = js[js.index('text.textContent = "Demo data"') :][:1200]

    assert "Not signed in" in chip, (
        "the identity chip hides itself when the lookup is refused, so the "
        "reason the deployment gave is discarded"
    )
    assert "j.detail" in chip, "the reason the deployment gave is not shown"
