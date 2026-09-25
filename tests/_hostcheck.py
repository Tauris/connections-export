"""Host checking, shared by the tests that need it.

The allow-list holds loopback, RFC 2606 placeholders, XML namespace URIs and
the public project pages named in the SBOM -- nothing about any deployment, so
this module is safe to publish and is what the console-asset tests import.

The scans that use it to sweep the whole tree, and the guards for terms that
must never ship, are export-ignored: they decide what may leave this
repository, which makes them the wrong thing to send out of it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCOPE_DIRS = ["connections_export", "tests"]
SCOPE_FILES = ["pyproject.toml", "connections-export.example.toml"]

# --- allow-list -------------------------------------------------------

_ALLOWED_EXACT = {
    "localhost",
    "127.0.0.1",
    "fake",  # tests/fakeserver, tests/adapters: placeholder host, no TLD
    "host",  # tests/archive: placeholder host, no TLD
}

_ALLOWED_SUFFIXES = {
    "example.com",
    "example.org",
    "example.net",
    "ibm.com",  # public HCL/IBM 8.0 sample fixture (navigation_entry_verbatim.xml)
    "w3.org",  # Atom namespace (http://www.w3.org/2005/Atom)
    "purl.org",  # Atom Thread Extension namespace
    "a9.com",  # OpenSearch namespace
    "github.com",  # project home / issues in pyproject [project.urls]
    # Where this tool is published. Written into every package and archive so
    # whoever is handed one has an address rather than a search term
    # (connections_export/sbom.py).
    "pypi.org",
    # The vendored JavaScript's own project pages, named in the SBOM so a
    # reader can go and check the component (connections_export/sbom.py).
    "mozilla.github.io",
    "pagedjs.org",
    "apache.org",  # the Apache-2.0 text's canonical URL, in the same file
    # The developer-export targets. The manual (served inside the console)
    # introduces Obsidian, Jekyll and Hugo and links to their homepages so a
    # reader can learn what they are. Public informational pages for the tools
    # this exports INTO -- not a deployment, and never contacted by the tool.
    "obsidian.md",
    "jekyllrb.com",
    "gohugo.io",
}


def is_allowed_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host in _ALLOWED_EXACT:
        return True
    if "example" in host.split("."):  # any *.example.* placeholder, e.g. example.corp
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_SUFFIXES)


# --- extraction ---------------------------------------------------------

_URL_HOST_RE = re.compile(r"https?://([^\s\"'/:<>{}]+)")
_CURATED_TLDS = ("com", "org", "net", "corp")
_BARE_HOST_RE = re.compile(
    r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:" + "|".join(_CURATED_TLDS) + r")\b",
    re.IGNORECASE,
)


def extract_hosts(text: str) -> set[str]:
    """Extract host-like strings from `text`: anything following
    `http(s)://` (any TLD — the scheme prefix alone is enough context
    to rule out code-identifier noise), plus bare dotted hostnames
    ending in a curated, plausible TLD (com/org/net/corp), which is
    narrow enough to skip ordinary Python attribute chains and
    packaging identifiers (`self.password`, `hatchling.build`)."""
    hosts = set()
    for match in _URL_HOST_RE.finditer(text):
        host = match.group(1).split("/")[0].split(":")[0].split("@")[-1]
        # Skip elision markers like "..." (e.g. a doc sample's
        # "http://.../wikis/..." with the host itself redacted) —
        # punctuation-only, never an actual hostname.
        if host and any(char.isalnum() for char in host):
            hosts.add(host)
    for match in _BARE_HOST_RE.finditer(text):
        hosts.add(match.group(0))
    return hosts


def find_disallowed_hosts(text: str) -> set[str]:
    """The checker function: return the subset of hosts extracted from
    `text` that are NOT in the allow-list."""
    return {host for host in extract_hosts(text) if not is_allowed_host(host)}


# --- scanning the tree -------------------------------------------------

# `vendor/` holds third-party libraries we ship verbatim (e.g.
# `pdf/vendor/paged.polyfill.js`, the paged.js polyfill used only inside the
# headless Chromium during PDF rendering -- never served to a user's browser).
# Their source comments reference spec/example hosts we neither authored nor
# ever fetch, so they're out of scope for the "our content stays offline" host
# guard. Their licences ship alongside them.
#: `vendor` holds third-party JavaScript; `_licenses` holds the generated SBOM
#: and one verbatim licence text per component; `overrides` holds licence texts
#: researched from a project's own source. None of the three is authored here
#: -- they are copied or generated from other people's published files, which
#: legitimately cite dozens of hosts, exactly as `docs/` does. A real
#: deployment host cannot reach them: nothing in this repository writes their
#: contents.
_SKIP_DIR_NAMES = {"__pycache__", "vendor", "_licenses", "overrides"}


def _iter_scope_files() -> list[Path]:
    files: list[Path] = []
    for rel_dir in SCOPE_DIRS:
        root = REPO_ROOT / rel_dir
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    for rel_file in SCOPE_FILES:
        path = REPO_ROOT / rel_file
        if path.is_file():
            files.append(path)
    return files


def scan_repo_for_disallowed_hosts() -> dict[Path, set[str]]:
    """Scan the in-scope files and return {path: {disallowed hosts}}
    for every file that has at least one — empty dict means clean."""
    violations: dict[Path, set[str]] = {}
    for path in _iter_scope_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (UnicodeDecodeError, OSError):
            continue
        bad = find_disallowed_hosts(text)
        if bad:
            violations[path] = bad
    return violations


def _exportable_files() -> list[Path]:
    """Every tracked file `git archive` would ship — i.e. tracked paths NOT
    marked `export-ignore` in.gitattributes — so the scan mirrors exactly
    what lands in the public repo/distribution. In the already-cleaned repo
    (research removed, no export-ignore entries) this is simply every file."""
    import subprocess

    tracked = [
        p
        for p in subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
        if p
    ]
    fields = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-attr", "-z", "--stdin", "export-ignore"],
        input="\0".join(tracked) + "\0",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    out: list[Path] = []
    # `-z` output is a flat NUL stream of (path, attr, value) triples.
    for i in range(0, len(fields) - 2, 3):
        path, _attr, value = fields[i], fields[i + 1], fields[i + 2]
        # Skip the empty path. With nothing tracked -- which is what
        # `git ls-files` reports from an extracted export, since that directory
        # is untracked inside whatever repo encloses it -- the stdin payload is
        # a lone NUL, and `check-attr` answers for the empty path. Without this,
        # `REPO_ROOT / ""` is REPO_ROOT, and the caller gets one bogus
        # "exportable file" that is actually a directory.
        if path and value != "set":
            out.append(REPO_ROOT / path)
    return out
