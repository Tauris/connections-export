""" "Sign in as yourself" means SSPI on Windows and Kerberos elsewhere.

The default auth mode is `sspi`. On macOS and Linux there is no SSPI, and
the default failed at once with "SspiAuth requires requests-negotiate-sspi";
the same single sign-on there is Kerberos. `probe sign-in` reports which one
an installation uses, whether its libraries load, and -- off Windows --
whether a Kerberos ticket is present.
"""

from __future__ import annotations

import pytest

import connections_export.cli as cli
from connections_export.config import Config
from connections_export.http.auth import KerberosAuth, SspiAuth


def _strategy(monkeypatch, platform, mode="sspi"):
    monkeypatch.setattr(cli.sys, "platform", platform)
    config = Config(base_url="https://connections.example.com", auth_mode=mode)
    return cli._resolve_auth_strategy(config, {})


def test_sspi_on_windows_is_sspi(monkeypatch):
    assert isinstance(_strategy(monkeypatch, "win32"), SspiAuth)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_sspi_elsewhere_is_kerberos(monkeypatch, platform):
    assert isinstance(_strategy(monkeypatch, platform), KerberosAuth)


def test_kerberos_stays_kerberos_everywhere(monkeypatch):
    assert isinstance(_strategy(monkeypatch, "win32", "kerberos"), KerberosAuth)


def test_probe_sign_in_reports_the_libraries_and_the_ticket(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_sign_in_modules_load", lambda names: (True, ""))
    monkeypatch.setattr(cli, "_kerberos_ticket", lambda: "someone@EXAMPLE.COM")

    assert cli.probe_main(["sign-in"]) == 0
    out = capsys.readouterr().out
    assert "Kerberos" in out
    assert "someone@EXAMPLE.COM" in out


def test_probe_sign_in_says_what_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(
        cli, "_sign_in_modules_load", lambda names: (False, "No module named 'gssapi'")
    )

    assert cli.probe_main(["sign-in"]) == 2
    out = capsys.readouterr()
    assert "gssapi" in out.out + out.err


def test_probe_sign_in_without_a_ticket_says_how_to_get_one(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli, "_sign_in_modules_load", lambda names: (True, ""))
    monkeypatch.setattr(cli, "_kerberos_ticket", lambda: None)

    assert cli.probe_main(["sign-in"]) == 0
    assert "kinit" in capsys.readouterr().out


def test_inside_an_executable_it_does_not_send_you_to_pip(monkeypatch, capsys):
    """An executable carries its sign-in; when it does not load, installing a
    package is no fix -- the message said so anyway, beside a glibc error."""
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        cli, "_sign_in_modules_load", lambda names: (False, "gssapi: GLIBC_2.38 not found")
    )

    assert cli.probe_main(["sign-in"]) == 2
    out = capsys.readouterr()
    text = out.out + out.err
    assert "GLIBC_2.38" in text
    assert "pip install" not in text
    assert "executable" in text
