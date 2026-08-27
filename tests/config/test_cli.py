"""How every command resolves its configuration.

`_main` is the shared step underneath the commands that talk to a
deployment: parse the common flags, build a `Config` via `load_config`,
print it with secrets redacted. It is tested directly rather than through
whichever command happens to be thinnest -- a test that reaches this
through a command is really testing two things, and keeps a command alive
for its own sake.

The commands with bodies of their own (`crawl_main` runs a crawl,
`serve_main` launches a server; both return an exit code rather than a
`Config`) have their own contract tests in `tests/crawler/test_cli.py`
and `tests/gui/test_cli_serve.py`.
"""

from connections_export.cli import _main


def resolve(argv, *, env=None):
    """`_main` as a command calls it."""
    return _main("connections-export crawl", argv, env)


ALL_MAINS = [resolve]


def test_each_entry_point_builds_a_config_from_flags():
    for main in ALL_MAINS:
        config = main(
            [
                "--base-url",
                "https://connections.example.corp",
                "--auth-mode",
                "basic",
                "--auth-root",
                "form",
                "--source-version",
                "7.0",
                "--output-dir",
                "out",
            ],
            env={},
        )

        assert config.base_url == "https://connections.example.corp"
        assert config.auth_mode == "basic"
        assert config.auth_root == "form"
        assert config.source_version == "7.0"
        assert str(config.output_dir) == "out"


def test_no_flags_yields_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no./connections-export.toml here

    config = resolve([], env={})

    assert config.base_url is None
    assert config.auth_mode == "sspi"


def test_config_flag_points_at_an_explicit_file(tmp_path):
    config_file = tmp_path / "custom.toml"
    config_file.write_text('base_url = "https://connections.example.corp"\n')

    config = resolve(["--config", str(config_file)], env={})

    assert config.base_url == "https://connections.example.corp"


def test_resolved_config_is_printed_with_secret_redacted(capsys, monkeypatch):
    monkeypatch.setenv("CONNECTIONS_EXPORT_TOKEN", "super-secret-value")

    resolve(
        ["--base-url", "https://connections.example.corp", "--auth-mode", "paste_token"],
        env={"CONNECTIONS_EXPORT_TOKEN": "super-secret-value"},
    )

    out = capsys.readouterr().out

    assert "connections.example.corp" in out
    assert "super-secret-value" not in out
    assert "***" in out


def test_printed_output_shows_not_set_when_no_token(capsys):
    resolve(["--base-url", "https://connections.example.corp"], env={})

    out = capsys.readouterr().out

    assert "not set" in out.lower()
