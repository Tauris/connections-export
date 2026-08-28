"""Every link in the README works from both places it is read.

`pyproject.toml` names this file as the project description, so it is the
GitHub landing page AND the PyPI project page. Those resolve relative
links differently, and only one of them resolves them usefully: on PyPI
`docs/manual.md` becomes `pypi.org/project/connections-export/docs/manual.md`
and `../../releases` becomes `pypi.org/releases`, which is a real page
belonging to somebody else.

Neither half of the toolchain would object. `twine check` validates that
the description *renders*, not that its links point anywhere, and asking
whether a relative target survives the export is a question about GitHub
that is silent about the other page.

So the rule is: no relative links at all. A link is absolute, or it is an
anchor within the page. And an absolute link into this project's own
repository still has to name a file that `git archive` ships, which is
what the original rule was protecting -- a link into research that does
not survive the export resolves perfectly here and is dead everywhere
else.
"""

from __future__ import annotations

import re
import subprocess
import tomllib

import pytest

from tests._hostcheck import REPO_ROOT, _exportable_files

README = REPO_ROOT / "README.md"

_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _links(markdown: str) -> list[str]:
    return [link.strip() for link in _LINK_RE.findall(markdown)]


def _repo_base() -> str:
    """This project's own repository, from the packaging metadata.

    Read rather than repeated: the URL a link must match is the one the
    package publishes as its home.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["urls"]["Homepage"].rstrip("/")


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


def test_the_readme_is_the_package_description():
    """The premise of everything below. If it stopped being the description,
    relative links would be fine again and this rule would be needless."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["readme"] == "README.md"


def test_no_link_is_relative():
    """A relative link resolves against whichever page is rendering it, and
    one of the two pages is not this repository."""
    relative = [
        link
        for link in _links(README.read_text(encoding="utf-8"))
        if not link.startswith(("http://", "https://", "mailto:", "#"))
    ]

    assert relative == [], (
        "relative links break on the PyPI project page, which renders this "
        f"same file as the project description: {relative}"
    )


def test_links_into_this_repository_name_files_that_ship():
    """The original rule, kept. A link into research that does not survive
    the export resolves here and is dead everywhere else."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git not available")

    base = _repo_base()
    blob = re.compile(re.escape(base) + r"/blob/[^/]+/(.+)$")
    targets = []
    for link in _links(README.read_text(encoding="utf-8")):
        match = blob.match(link.split("#")[0])
        if match:
            targets.append(match.group(1))

    assert targets, "no links into this repository's own files -- parser broken?"

    exported = _exported_paths()
    dropped = {
        target: "missing entirely" if not (REPO_ROOT / target).exists() else "export-ignored"
        for target in targets
        if target not in exported
    }

    assert dropped == {}, f"README links that do not survive `git archive`: {dropped}"


def test_the_check_would_catch_a_link_into_the_research():
    """Proves the guard bites, on both halves of the rule."""
    private = "docs/" + "internal-notes.md"

    # Relative: caught for being relative at all.
    assert _links(f"see [notes]({private})") == [private]

    # Absolute, but naming something the export drops.
    assert private not in _exported_paths()
