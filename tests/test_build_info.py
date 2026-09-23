"""The version line the console shows, and how it tells builds apart.

A real release and a throwaway test build share the pyproject version, so the
version alone cannot distinguish them. `build_info` uses a build stamp (commit +
git ref, written by the binaries workflow) to decide the channel and label.
"""

from __future__ import annotations

from connections_export.build_info import build_info


def test_an_editable_checkout_with_no_stamp_is_dev():
    info = build_info({}, editable=True)
    assert info["channel"] == "dev"
    assert info["commit"] == ""
    assert info["label"].endswith("· dev")
    assert info["label"].startswith("v")


def test_an_installed_wheel_with_no_stamp_is_a_release_not_dev():
    # A `pip install` of a release carries no build stamp (only the executables
    # do) and is NOT editable. It must read as a clean release, never "· dev".
    info = build_info({}, editable=False)
    assert info["channel"] == "release"
    assert info["label"] == "v" + info["version"]
    assert "dev" not in info["label"]


def test_a_branch_build_is_a_test_build_and_shows_its_commit():
    info = build_info({"commit": "0b83bb36fc24", "ref": "refs/heads/fix/blog-comments-repair"})
    assert info["channel"] == "test"
    assert info["commit"] == "0b83bb3"  # short
    assert "test build" in info["label"]
    assert "0b83bb3" in info["label"]


def test_a_v_tag_build_is_an_official_release_with_no_commit_clutter():
    info = build_info({"commit": "0b83bb36fc24", "ref": "refs/tags/v0.1.7"})
    assert info["channel"] == "release"
    assert info["label"] == "v" + info["version"]
    assert "test build" not in info["label"]
    assert info["commit"] not in info["label"]


def test_a_stamp_with_a_commit_but_a_non_v_tag_is_still_a_test_build():
    # A tag that is not a version release (e.g. the plain prerelease tag the
    # test-build workflow can be dispatched from) must not read as official.
    info = build_info({"commit": "abcdef0", "ref": "refs/tags/test-blog-comment-repair"})
    assert info["channel"] == "test"
    assert "test build" in info["label"]


def test_the_cli_version_flag_prints_the_build_label(capsys):
    from connections_export.build_info import build_info
    from connections_export.cli import main

    assert main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == build_info()["label"]
    assert out.startswith("v")
    assert "0.0.0" not in out  # a source checkout still knows its version
