import json

from connections_export.cli import open_main


def test_open_serves_the_given_archive(tmp_path):
    archive = tmp_path / "export-host-20260101-000000-aaaa"
    archive.mkdir(parents=True)

    # Create minimal valid interchange.json so the archive is recognized as a package
    interchange_data = {
        "schema_version": 1,
        "base_url": None,
        "source_version": None,
        "run_id": None,
        "hcl_hosts": [],
        "wikis": [],
        "blogs": [],
        "forums": [],
    }
    (archive / "interchange.json").write_text(json.dumps(interchange_data), encoding="utf-8")

    captured = {}

    def fake_run(app, host, port, **_kw):
        captured["app"] = app
        captured["host"] = host

    rc = open_main([str(archive), "--host", "127.0.0.1", "--port", "9123"], run=fake_run)
    assert rc == 0
    assert captured["app"] is not None
    assert captured["host"] == "127.0.0.1"


def test_open_missing_path_errors(tmp_path):
    rc = open_main([str(tmp_path / "nope")], run=lambda *a, **k: None)
    assert rc == 2


def test_open_on_empty_archive_reports_cleanly_instead_of_traceback(tmp_path):
    # make_app derives eagerly, so an empty or partial archive (nothing
    # imported yet) raises DeriveError from inside open_main. That has to
    # reach the user as a clean message and a non-zero exit code, not as a
    # raw traceback.
    empty_dir = tmp_path / "export-empty"
    empty_dir.mkdir()

    rc = open_main([str(empty_dir)], run=lambda *a, **k: None)
    assert rc != 0


def test_open_serves_a_raw_archive_without_interchange(tmp_path):
    from connections_export.gui.demo import run_demo

    archive_dir = tmp_path / "export-demo"
    archive_dir.mkdir()
    run_demo(lambda _event: None, seed=0, archive_dir=archive_dir, delay=0)
    assert not (archive_dir / "interchange.json").exists()

    captured = {}

    def fake_run(app, host, port, **_kw):
        captured["app"] = app

    rc = open_main([str(archive_dir)], run=fake_run)
    assert rc == 0
    assert captured["app"] is not None
