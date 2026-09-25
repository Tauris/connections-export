"""Every text read names its encoding.

`Path.read_text` with no `encoding=` decodes in the platform's locale encoding:
UTF-8 on Linux and macOS, a legacy code page on Windows. Files this tool
writes are UTF-8, so a bare read passes everywhere except Windows, where
any non-ASCII character ("·", "×", an author's name) comes back garbled.
Twice a Windows-only CI failure was exactly this; the rule is cheaper
than the next one.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARE_READ = re.compile(r"\.read_text\(\s*\)")


def test_no_text_is_read_without_an_encoding():
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for folder in ("connections_export", "tests", "tools")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE_READ.search(line)
    ]
    assert not offenders, "read_text() without encoding=: " + ", ".join(offenders)
