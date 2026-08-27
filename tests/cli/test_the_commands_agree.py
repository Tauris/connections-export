"""What the tool dispatches, what it advertises, and what the manual
documents are one list.

They were three. The dispatch table carried two stubs that resolve a
config and return it -- nothing a user can do anything with -- and the
help offered them by name; `compare-author` was dispatched but never
advertised; and the manual, written last, documented the nine commands
that actually do something. Three lists drift silently, because nothing
reads two of them at once.
"""

from __future__ import annotations

import re

from connections_export import cli
from connections_export.gui.manual import MANUAL_DOC_PATH


def _advertised() -> set[str]:
    """The command names in `connections-export --help`."""
    body = cli._MAIN_USAGE.split("commands:\n", 1)[1]
    return {
        m.group(1)
        for line in body.splitlines()
        if (m := re.match(r"\s{2}([a-z][a-z-]*)\s{2,}\S", line))
    }


def _documented() -> set[str]:
    """The command names in the manual's command table."""
    text = MANUAL_DOC_PATH.read_text(encoding="utf-8")
    table = text.split("## The command line", 1)[1].split("\n###", 1)[0]
    return set(re.findall(r"^\| `([a-z][a-z-]*)` \|", table, re.M))


def test_every_command_it_dispatches_is_one_it_advertises():
    assert set(cli._SUBCOMMANDS) == _advertised()


def test_every_command_it_advertises_is_one_the_manual_explains():
    """The manual is where a command is explained; the help is where it is
    found. A command in one and not the other is either undiscoverable or
    unexplained."""
    assert _advertised() == _documented()


def test_no_command_is_a_stub():
    """Every name on the list does something. A command that resolves a
    config and returns it is scaffolding, and scaffolding in the public
    help is a promise the tool does not keep."""
    assert "stub" not in cli._MAIN_USAGE.lower()
