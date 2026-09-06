"""`probe` answers with instructions, never a traceback.

Two ways it did not.

A missing `--author` was checked after the client was built, so on a
machine where authentication fails the user saw an SSPI stack trace for a
flag they had forgotten. Argument checking costs nothing and belongs
before anything is attempted.

And an authentication failure escaped uncaught. Every other command turns
`AuthError` into something to act on -- "an optional package not
installed, no domain to authenticate against, credentials unset" are
ordinary things to get wrong, and this one is run by whoever has the
deployment rather than by whoever wrote it.
"""

from __future__ import annotations

from connections_export.cli import probe_main

BASE = "https://connections.example.corp"


def test_a_missing_author_is_named_before_anything_is_attempted(capsys):
    code = probe_main(["search-reach", "--base-url", BASE])

    assert code == 2
    err = capsys.readouterr().err
    assert "--author" in err
    assert "Traceback" not in err


def test_a_missing_community_is_named_for_the_files_question(capsys):
    code = probe_main(["files-since", "--base-url", BASE])

    assert code == 2
    assert "--community" in capsys.readouterr().err


def test_an_authentication_failure_reads_as_instructions(capsys, monkeypatch):
    """Not a stack trace. This runs on someone else's machine."""
    import connections_export.cli as cli_module
    from connections_export.http import AuthError

    def refuse(config, env):
        raise AuthError("SspiAuth requires the 'requests-negotiate-sspi' package.")

    monkeypatch.setattr(cli_module, "_build_default_client", refuse)

    code = probe_main(["search-reach", "--author", "someone", "--base-url", BASE])

    assert code == 3
    err = capsys.readouterr().err
    assert "requests-negotiate-sspi" in err
    assert "Traceback" not in err
