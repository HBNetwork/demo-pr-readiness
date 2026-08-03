"""Validate the bounded semantic shape of the hero-review payload."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

EXPECTED_NAMES = ("__init__.py", "models.py", "gate.py", "cache.py")
SUGGESTION_ANCHOR = "    return issued_at + timedelta(minutes=ttl_seconds)"
POLICY_ANCHOR = "    return len(request.approvals) >= required"
STORE_ANCHOR = '    return f"{repository.casefold()}#{request_number}"'
LOOKUP_ANCHOR = '    return f"{repository}#{request_number}"'
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


def require_return(tree: ast.Module, function_name: str, line: int) -> ast.Return:
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    ]
    if len(functions) != 1:
        fail(f"{function_name} must be one top-level function")
    returns = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Return)]
    if len(returns) != 1 or returns[0].lineno != line:
        fail(f"{function_name} must own its anchored return")
    return returns[0]


def main(arguments: list[str]) -> None:
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
    suggestion_line = unique_line(gate, SUGGESTION_ANCHOR, "suggestion")
    policy_line = unique_line(gate, POLICY_ANCHOR, "policy")
    tree = ast.parse(gate)
    suggestion_return = require_return(tree, "lease_deadline", suggestion_line)
    require_return(tree, "can_release", policy_line)
    if suggestion_return.end_lineno != suggestion_line:
        fail("suggestion anchor must be exactly one line")

    models = sources["models.py"]
    for anchor in MODEL_ANCHORS:
        unique_line(models, anchor, "model contract")

    cache = sources["cache.py"]
    store_line = unique_line(cache, STORE_ANCHOR, "store invariant")
    lookup_line = unique_line(cache, LOOKUP_ANCHOR, "lookup invariant")
    if store_line == lookup_line or abs(store_line - lookup_line) <= 1:
        fail("invariant anchors must be distinct and non-adjacent")
    cache_tree = ast.parse(cache)
    require_return(cache_tree, "store_key", store_line)
    require_return(cache_tree, "lookup_key", lookup_line)


if __name__ == "__main__":
    main(sys.argv[1:])
