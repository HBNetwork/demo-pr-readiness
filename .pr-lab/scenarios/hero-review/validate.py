"""Validate the bounded semantic shape of the hero-review payload."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

EXPECTED_NAMES = ("__init__.py", "models.py", "gate.py", "cache.py")
SEEDED_SUGGESTION_ANCHOR = "    return issued_at + timedelta(minutes=ttl_seconds)"
REPAIRED_SUGGESTION_ANCHOR = "    return issued_at + timedelta(seconds=ttl_seconds)"
SEEDED_POLICY_ANCHOR = "    return len(request.approvals) >= required"
REPAIRED_POLICY_ANCHOR = "    return sum(request.approvals.values()) >= required"
STORE_ANCHOR = '    return f"{repository.casefold()}#{request_number}"'
SEEDED_LOOKUP_ANCHOR = '    return f"{repository}#{request_number}"'
REPAIRED_LOOKUP_ANCHOR = STORE_ANCHOR
MODEL_ANCHORS = (
    "class Risk(StrEnum):",
    '    NORMAL = "normal"',
    '    HIGH = "high"',
    "    risk: Risk",
    "    approvals: Mapping[str, bool]",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def unique_line(source: str, anchor: str, label: str) -> int:
    lines = source.splitlines()
    matches = [number for number, line in enumerate(lines, 1) if line == anchor]
    if len(matches) != 1:
        fail(f"{label} must have one unique anchor")
    return matches[0]


def require_return(tree: ast.Module, function_name: str) -> ast.Return:
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    ]
    if len(functions) != 1:
        fail(f"{function_name} must be one top-level function")
    returns = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Return)]
    if len(returns) != 1:
        fail(f"{function_name} must own one return")
    return returns[0]


def main(arguments: list[str]) -> None:
    allow_repaired = arguments[:1] == ["--allow-repaired"]
    if allow_repaired:
        arguments = arguments[1:]
    if len(arguments) != len(EXPECTED_NAMES):
        fail("validator requires the complete ordered hero payload")
    paths = [Path(value) for value in arguments]
    if tuple(path.name for path in paths) != EXPECTED_NAMES or len(set(paths)) != len(paths):
        fail("validator inputs must be unique and ordered")

    sources: dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            fail("validator inputs must be regular files")
        source = path.read_text()
        compile(source, path, "exec")
        sources[path.name] = source

    gate = sources["gate.py"]
    tree = ast.parse(gate)
    suggestion_return = require_return(tree, "lease_deadline")
    policy_return = require_return(tree, "can_release")
    suggestion_line = gate.splitlines()[suggestion_return.lineno - 1]
    policy_line = gate.splitlines()[policy_return.lineno - 1]
    if suggestion_return.end_lineno != suggestion_return.lineno:
        fail("suggestion anchor must be exactly one line")

    models = sources["models.py"]
    for anchor in MODEL_ANCHORS:
        unique_line(models, anchor, "model contract")

    cache = sources["cache.py"]
    cache_tree = ast.parse(cache)
    store_return = require_return(cache_tree, "store_key")
    lookup_return = require_return(cache_tree, "lookup_key")
    store_anchor = cache.splitlines()[store_return.lineno - 1]
    lookup_anchor = cache.splitlines()[lookup_return.lineno - 1]
    if abs(store_return.lineno - lookup_return.lineno) <= 1:
        fail("invariant anchors must be distinct and non-adjacent")

    seeded = (
        suggestion_line == SEEDED_SUGGESTION_ANCHOR
        and policy_line == SEEDED_POLICY_ANCHOR
        and store_anchor == STORE_ANCHOR
        and lookup_anchor == SEEDED_LOOKUP_ANCHOR
    )
    repaired = (
        suggestion_line == REPAIRED_SUGGESTION_ANCHOR
        and policy_line == REPAIRED_POLICY_ANCHOR
        and store_anchor == STORE_ANCHOR
        and lookup_anchor == REPAIRED_LOOKUP_ANCHOR
    )
    if not seeded and (not allow_repaired or not repaired):
        fail("hero payload must be exactly seeded or, when allowed, fully repaired")


if __name__ == "__main__":
    main(sys.argv[1:])
