"""The shipped README must not link to anything the export drops.

The public README is exported from a private repo whose research trees
(`the internal specifications`, most of `docs/`) are `export-ignore`d. A link into one of those
resolves perfectly here and is dead on GitHub and PyPI -- and nothing catches
it: `twine check` validates that the description *renders*, not that its links
point anywhere. That is how the previous README would have shipped, with nine
links into research that does not survive the export.

So the rule is stronger than "the file exists": every relative link target must
be a file `git archive` actually ships.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests._hostcheck import REPO_ROOT, _exportable_files

# Markdown inline links, minus the ones that are not repo paths:
# http(s)://… external
#   #anchor same-document
# ../../… resolved by the forge relative to the repo, not the tree
# (e.g. `../../releases` -> the GitHub Releases page)
_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _repo_relative_links(markdown: str) -> list[str]:
    out = []
    for link in _LINK_RE.findall(markdown):
        if link.startswith(("http://", "https://", "mailto:", "#", "../../")):
            continue
        out.append(link.split("#")[0])
    return out


def _exported_paths() -> set[str]:
    """Repo-relative paths `git archive` ships.

    Degrades where there is no git context to ask, which happens two ways when
    running from an extracted export: inside an enclosing repo `git ls-files`
    reports nothing, because that directory is untracked there; outside one it
    fails outright. An empty answer would read as "every path is
    export-ignored", failing the check for exactly the files that did survive.
    In both cases the tree on disk IS the export, so use it.
    """
    try:
        tracked = {p.relative_to(REPO_ROOT).as_posix() for p in _exportable_files()}
    except subprocess.CalledProcessError:
        tracked = set()  # not inside a repo at all
    if tracked:
        return tracked
    return {
        p.relative_to(REPO_ROOT).as_posix()
        for p in REPO_ROOT.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


def test_readme_links_all_survive_the_export():
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git not available")

    exported = _exported_paths()
    links = _repo_relative_links((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    assert links, "no repo-relative links found -- the parser is probably broken"

    dropped = {
        link: "missing entirely" if not (REPO_ROOT / link).exists() else "export-ignored"
        for link in links
        if link not in exported
    }

    assert dropped == {}, f"README links that do not survive `git archive`: {dropped}"


def test_the_check_would_catch_a_link_into_the_research():
    """Proves the guard bites: a path that exists in this repo and is
    export-ignored is exactly the kind of link the README must not carry."""
    exported = _exported_paths()

    private = "docs/" + "internal-notes.md"
    assert _repo_relative_links(f"see [notes]({private})") == [private]
    assert private not in exported
