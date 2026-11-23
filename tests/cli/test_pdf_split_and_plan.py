"""`connections-export pdf --plan` and `--split` on the command line.

The same proposal the console shows -- estimates, and with --split the files
-- without rendering; and --split writing a folder of linked PDFs."""

from __future__ import annotations

from connections_export import cli


def test_plan_describes_the_files_and_renders_nothing(capsys):
    def render(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("--plan must not render")

    code = cli.pdf_main(["--demo", "--plan", "--split", "community"], render=render)
    out = capsys.readouterr().out
    assert code == 0
    assert "The whole export: about" in out and "3 PDFs:" in out
    assert "1 - Platform Engineering.pdf" in out
    assert "2 - Platform Engineering — Tooling.pdf" in out


def test_split_writes_a_folder_of_pdfs(tmp_path, monkeypatch, capsys):
    from connections_export.pdf import sets

    seen = {}

    def fake_set(model, blob_bytes, files, out_dir, progress=None, **options):
        seen.update(options)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for plan in files:
            path = out_dir / plan.name
            path.write_bytes(b"%PDF-1.4")
            paths.append(path)
        return paths

    monkeypatch.setattr(sets, "render_pdf_set", fake_set)
    monkeypatch.setattr(cli, "_default_pdf_render", lambda _fidelity: lambda *a, **k: b"")
    out = tmp_path / "set"
    code = cli.pdf_main(
        [
            "--demo",
            "--split",
            "app",
            "--attach-files",
            "--no-attach-inline-images",
            "--output",
            str(out),
        ],
    )
    assert code == 0
    names = sorted(p.name for p in out.glob("*.pdf"))
    assert len(names) >= 5 and names[0].startswith("1 - ")
    assert seen.get("attach_attachments") is True
    assert seen.get("attach_inline_images") is False
    assert f"to {out}" in capsys.readouterr().out
