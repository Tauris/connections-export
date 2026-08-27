""" "Extend & update" has to update when the deployment is real.

The console's run bar says "Extend & update" and writes into the archive it
was pointed at. But writing into an archive is not updating it: under the
default `resume` policy every URL already captured is answered from disk, so
an update of a component the archive already holds re-reads nothing, finds
nothing, and reports success.

That is the same failure the last four bugs on this branch had -- a value
computed correctly and then not used -- so this asserts on the config the run
actually receives, not on the plan that was worked out for it.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.crawler.session import resolve_update_plan


def _plan(into: Path | None, **kwargs):
    from connections_export.config import Config

    return resolve_update_plan(Config(base_url="https://fake", into=into, **kwargs))


def test_a_run_into_an_archive_is_an_update(tmp_path):
    """Not `resume`. Under resume the archive answers every request it already
    holds, so the run would complete having asked the deployment nothing."""
    archive = tmp_path / "export-atlas"
    archive.mkdir()

    assert _plan(archive).fetch == "update"


def test_a_fresh_run_is_unaffected(tmp_path):
    assert _plan(None, output_dir=tmp_path).fetch == "resume"


def test_the_console_hands_the_run_its_update_mode(tmp_path):
    """The whole bug: `into` reached the run DIRECTORY and never reached the
    fetch mode, so the console wrote into the right archive in the wrong
    mode."""
    import inspect

    from connections_export.gui.routes import run as run_module

    source = inspect.getsource(run_module)
    started = source[source.index("def _run_real") :]
    started = started[: started.index("\n    def ")] if "\n    def " in started else started

    assert "fetch=" in started, "_run_real builds a Config that cannot express an update"

    # Counted, not searched. `"since" in started` passed while HALF the
    # function dropped the cutoff: the community branch had it, and the
    # single-app chain beside it did not. Every crawl `_run_real` dispatches
    # that CAN take a cutoff has to be handed one, so the check is one per
    # dispatch site rather than one per function.
    import re

    dispatches = re.findall(
        r"(run_selection|run_crawl_blogs|run_crawl_forums)\((.*?)\n            \)",
        started,
        re.S,
    )
    assert dispatches, "found no crawl dispatch in _run_real to check"
    missing = [name for name, args in dispatches if "since=" not in args]
    assert not missing, (
        f"these dispatches take a cutoff and were not given one: {missing}. "
        f"An 'Extend & update' through them re-reads everything and reports success."
    )


def test_the_wiki_path_is_not_given_a_cutoff():
    """The other direction, and it matters: a wiki feed cannot be asked "what
    changed since" (`profiles.WIKIS.since_encoding` is "unsupported"), so
    `crawl` has no such parameter and passing one is a TypeError in exactly
    the branch a unit test never reaches."""
    import inspect

    from connections_export.gui.routes import run as run_module

    source = inspect.getsource(run_module)
    started = source[source.index("def _run_real") :]
    wiki_call = started[started.index("run_crawl(") :]
    wiki_call = wiki_call[: wiki_call.index("\n            )")]

    assert "since=" not in wiki_call, "wikis were handed a cutoff their feeds cannot take"


def test_the_start_endpoint_passes_the_archive_through(tmp_path):
    """`into` has to travel from the request to the run, or the console's
    Extend & update button is decoration."""
    import inspect

    from connections_export.gui.routes import run as run_module

    source = inspect.getsource(run_module)
    call = source[source.index("_run_real(", source.index("def start")) :]
    call = call[: call.index("\n                    )")]

    assert "into=" in call or "fetch=" in call
