"""Every module imports on the oldest Python this project supports.

Python 3.14 evaluates annotations lazily (PEP 649); 3.12 evaluates them at
class-definition time. So a return type naming something imported only under
`TYPE_CHECKING` works on 3.14 and raises `NameError` at import on 3.12 --
invisible to anyone whose venv is new, fatal for a user on `requires-python =
">=3.12"`. That happened once, to `archive/store.py`, and every test run at the
time was green.

Importing everything is the guard, because the failure is an import failure. A
lint rule cannot see it: the code is correct under one interpreter.

A module the rest of the suite already imports would blow up on its own -- and
`store.py` did, taking `conftest.py` with it. What this adds is the modules
nothing else pulls in, where the same mistake would sit unnoticed until a user
on 3.12 ran the command that needed it.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import connections_export

#: Modules that legitimately refuse to import without an optional dependency.
#: Nothing here yet -- if that changes, name the module and the dependency
#: rather than widening the exception.
OPTIONAL: frozenset[str] = frozenset()


def _module_names() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.walk_packages(
            connections_export.__path__, prefix="connections_export."
        )
        if module.name not in OPTIONAL
    )


@pytest.mark.parametrize("name", _module_names())
def test_the_module_imports(name):
    importlib.import_module(name)


def test_the_walk_actually_found_the_package():
    """A guard that enumerates nothing passes vacuously."""
    names = _module_names()
    assert len(names) > 50
    assert "connections_export.archive.store" in names
