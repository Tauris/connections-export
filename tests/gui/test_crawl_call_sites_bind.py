"""Every crawl call the console makes must actually be callable.

A parameter renamed in the crawler is a `TypeError` at the call site, raised
inside a background thread, on a branch that only a search-driven ingest
reaches. Nothing in the suite reached that branch, so a rename passed a green
run and would have failed the first time somebody used it -- the same shape as
every real defect this project has had: not hard, invisible.

This binds each call site's keywords against the real signature. It is a
compile-time check the language does not give us, done once over the source
rather than by exercising every branch.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

from connections_export.crawler.crawl import (
    crawl,
    crawl_blogs,
    crawl_forums,
)
from connections_export.crawler.dispatch import run_selection

#: The names the console imports these under, and what they really are.
TARGETS = {
    "run_crawl": crawl,
    "run_crawl_blogs": crawl_blogs,
    "run_crawl_forums": crawl_forums,
    "run_selection": run_selection,
}

SOURCES = [
    pathlib.Path("connections_export/gui/routes/run.py"),
    pathlib.Path("connections_export/cli.py"),
]


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in TARGETS:
            yield name, node


def test_every_console_and_cli_crawl_call_binds():
    checked = 0
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for name, node in _calls(tree):
            signature = inspect.signature(TARGETS[name])
            accepted = set(signature.parameters)
            takes_kwargs = any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
            )
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue  # **spread: nothing static to check
                assert takes_kwargs or keyword.arg in accepted, (
                    f"{source}:{node.lineno} calls {name}({keyword.arg}=...), "
                    f"which it does not accept"
                )
            checked += 1
    # A test that found no call sites would pass forever while proving nothing.
    assert checked >= 4, f"only found {checked} call sites to check"
