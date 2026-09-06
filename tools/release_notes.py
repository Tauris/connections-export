"""Print one version's section of `CHANGELOG.md`.

The GitHub Release used `--generate-notes`, which lists the commits between
two tags. The public repository is cut fresh with a single commit per
release, so there are no commits between tags to list and the generated notes
say nothing. Written notes are the only ones that can say anything here, and
this is what hands them to the release.

Kept deliberately small and dependency-free: `.github/workflows/binaries.yml`
runs it, so it has to work from the exported tree with nothing installed.

    uv run python tools/release_notes.py 0.1.3
    uv run python tools/release_notes.py            # the version in pyproject.toml
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


#: `## <version>` optionally followed by anything (a date, usually). The
#: version is matched exactly rather than as a prefix, so asking for 0.1.3
#: cannot return 0.1.30's section.
def _heading(version: str) -> re.Pattern[str]:
    return re.compile(rf"^##\s+{re.escape(version)}(?:\s|$)")


def section(text: str, version: str) -> str:
    """The body of `## <version>`, without its heading.

    Raises `LookupError` rather than returning "" for a version the changelog
    does not mention: an empty release body looks like a release with nothing
    in it, and that is exactly the mistake this is here to prevent.
    """
    heading = _heading(version)
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise LookupError(f"CHANGELOG.md has no section for {version}")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end]).strip()


def main(argv: list[str]) -> int:
    # An EMPTY argument means absent, not "the version named ''". A workflow
    # passes `"$GITHUB_REF_NAME"`, and an unset variable expands to an empty
    # string rather than to no argument at all -- which would otherwise be
    # looked up as a version and reported as the half-sentence
    # "CHANGELOG.md has no section for " with nothing after it.
    version = argv[0].strip() if argv else ""
    if not version:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = pyproject["project"]["version"]
    # A tag is `v0.1.3` and a heading is `0.1.3`; accept either spelling so a
    # workflow can pass `$GITHUB_REF_NAME` straight through.
    version = version.removeprefix("v")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        print(section(changelog, version))
    except LookupError as error:
        print(f"connections-export release-notes: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
