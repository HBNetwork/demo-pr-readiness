"""Closed, validation-only lifecycle recipe control implemented with stdlib only."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_DIRECTORY = ".pr-lab/lifecycle"
REGISTRY_PATH = f"{LIFECYCLE_DIRECTORY}/registry.json"
MAX_DOCUMENT_BYTES = 32_768
MAX_OUTPUT_BYTES = 8_192
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

RECIPE_IDS = (
    "collaboration-gate",
    "draft-to-ready",
    "stale-base",
    "true-conflict",
)
RECIPE_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "purpose",
        "content_scenario",
        "initial_provider_state",
        "operator_actions",
        "expected_transitions",
        "completion_conditions",
        "cleanup_expectations",
    }
)
REGISTRY_FIELDS = frozenset({"schema_version", "recipes"})
REGISTRY_ENTRY_FIELDS = frozenset({"id", "path"})
CONTENT_SCENARIO_FIELDS = frozenset({"id", "manifest"})
ACTION_FIELDS = frozenset({"id", "actor", "operation"})
TRANSITION_FIELDS = frozenset({"after_action", "field", "from", "to"})
CONDITION_FIELDS = frozenset({"field", "equals"})
CLEANUP_FIELDS = frozenset({"id", "actor", "operation", "field", "from", "to"})
STATE_FIELDS = (
    "pull_request",
    "head_branch",
    "readiness",
    "base_relation",
    "mergeability",
    "collaboration",
    "review",
)
STATE_VALUES = {
    "pull_request": frozenset({"open", "closed"}),
    "head_branch": frozenset({"present", "deleted"}),
    "readiness": frozenset({"draft", "ready"}),
    "base_relation": frozenset({"current", "stale"}),
    "mergeability": frozenset({"mergeable", "conflicting"}),
    "collaboration": frozenset({"restricted", "maintainer-edits"}),
    "review": frozenset({"pending", "approved"}),
}
ACTORS = frozenset({"author", "operator", "reviewer"})
OPERATION_TRANSITIONS = {
    "mark-ready": ("readiness", "draft", "ready"),
    "advance-base": ("base_relation", "current", "stale"),
    "update-branch": ("base_relation", "stale", "current"),
    "introduce-conflicting-base-change": ("mergeability", "mergeable", "conflicting"),
    "resolve-conflict": ("mergeability", "conflicting", "mergeable"),
    "enable-maintainer-edits": ("collaboration", "restricted", "maintainer-edits"),
    "submit-approval": ("review", "pending", "approved"),
}
CLEANUP_TRANSITIONS = {
    "close-pull-request": ("pull_request", "open", "closed"),
    "delete-head-branch": ("head_branch", "present", "deleted"),
}
RECIPE_OPERATIONS = {
    "collaboration-gate": ("enable-maintainer-edits", "submit-approval"),
    "draft-to-ready": ("mark-ready",),
    "stale-base": ("advance-base", "update-branch"),
    "true-conflict": ("introduce-conflicting-base-change", "resolve-conflict"),
}
BASE_INITIAL_STATE = {
    "pull_request": "open",
    "head_branch": "present",
    "readiness": "ready",
    "base_relation": "current",
    "mergeability": "mergeable",
    "collaboration": "maintainer-edits",
    "review": "pending",
}
RECIPE_INITIAL_STATES = {
    "collaboration-gate": {**BASE_INITIAL_STATE, "collaboration": "restricted"},
    "draft-to-ready": {**BASE_INITIAL_STATE, "readiness": "draft"},
    "stale-base": BASE_INITIAL_STATE,
    "true-conflict": BASE_INITIAL_STATE,
}
RECIPE_CONTENT_SCENARIOS = dict.fromkeys(RECIPE_IDS, "clean-green")
RECIPE_COMPLETION_FIELDS = {
    "collaboration-gate": ("collaboration", "review"),
    "draft-to-ready": ("readiness",),
    "stale-base": ("base_relation",),
    "true-conflict": ("mergeability",),
}
EXPECTED_ACTORS = {
    "advance-base": "operator",
    "enable-maintainer-edits": "author",
    "introduce-conflicting-base-change": "operator",
    "mark-ready": "author",
    "resolve-conflict": "author",
    "submit-approval": "reviewer",
    "update-branch": "author",
}


class LifecycleError(ValueError):
    """A lifecycle document or invocation is outside the admitted closed contract."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} must be an object")
    return value


def _closed(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    result = _object(value, label)
    if set(result) != fields:
        raise LifecycleError(f"{label} fields must be exactly: {', '.join(sorted(fields))}")
    return result


def _array(value: Any, label: str, *, minimum: int = 1, maximum: int = 8) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise LifecycleError(f"{label} must contain between {minimum} and {maximum} items")
    return value


def _text(value: Any, label: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LifecycleError(f"{label} must be a non-empty string of at most {maximum} characters")
    return value


def safe_path(value: Any, label: str = "path") -> str:
    """Validate a normalized repository-relative POSIX path."""
    path = _text(value, label, maximum=256)
    if "\\" in path or path.startswith(("/", "-")):
        raise LifecycleError(f"{label} is not a safe relative path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} or part.startswith("-") for part in parts):
        raise LifecycleError(f"{label} is not normalized")
    if any(part.casefold() in {".git", ".gitmodules"} for part in parts):
        raise LifecycleError(f"{label} names protected git metadata")
    if PurePosixPath(path).as_posix() != path:
        raise LifecycleError(f"{label} is not normalized")
    return path


def _confined(root: Path, name: str, label: str) -> Path:
    relative = safe_path(name, label)
    resolved_root = root.resolve()
    current = resolved_root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise LifecycleError(f"{label} traverses a symlink")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise LifecycleError(f"cannot resolve {label}: {error}") from error
    if not resolved.is_relative_to(resolved_root):
        raise LifecycleError(f"{label} escapes repository root")
    return current


def _read_json(path: Path, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LifecycleError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        content = path.read_bytes()
        if len(content) > MAX_DOCUMENT_BYTES:
            raise LifecycleError(f"{label} exceeds {MAX_DOCUMENT_BYTES} bytes")
        return _object(json.loads(content, object_pairs_hook=reject_duplicates), label)
    except LifecycleError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise LifecycleError(f"cannot read {label}: {error}") from error


def _canonical_recipe_path(recipe_id: str) -> str:
    return f"{LIFECYCLE_DIRECTORY}/{recipe_id}.json"


def _validate_state(value: Any) -> dict[str, str]:
    state = _closed(value, frozenset(STATE_FIELDS), "initial_provider_state")
    for field in STATE_FIELDS:
        if not isinstance(state[field], str) or state[field] not in STATE_VALUES[field]:
            raise LifecycleError(f"initial_provider_state.{field} has an invalid value")
    if state["pull_request"] != "open" or state["head_branch"] != "present":
        raise LifecycleError("initial provider state requires an open PR and present head branch")
    return state


def _validate_content_scenario(value: Any, root: Path) -> None:
    scenario = _closed(value, CONTENT_SCENARIO_FIELDS, "content_scenario")
    scenario_id = _text(scenario["id"], "content_scenario.id")
    if not ID_PATTERN.fullmatch(scenario_id):
        raise LifecycleError("content_scenario.id is not canonical")
    expected = f".pr-lab/scenarios/{scenario_id}/scenario.json"
    if scenario["manifest"] != expected:
        raise LifecycleError("content scenario manifest path is not canonical")
    manifest = _read_json(
        _confined(root, expected, "content scenario manifest"), "content manifest"
    )
    if manifest.get("schema_version") != 2 or manifest.get("scenario") != scenario_id:
        raise LifecycleError("content scenario manifest identity does not match")
    expectation = manifest.get("expectation")
    if not isinstance(expectation, dict) or expectation.get("kind") != "content":
        raise LifecycleError("lifecycle recipes require a deterministic content scenario")


def validate_recipe(value: Any, recipe_id: str, root: Path = ROOT) -> dict[str, Any]:
    """Validate one closed recipe and its complete state-machine contract."""
    recipe = _closed(value, RECIPE_FIELDS, "recipe")
    if type(recipe["schema_version"]) is not int or recipe["schema_version"] != 1:
        raise LifecycleError("recipe schema_version must be integer 1")
    if recipe["id"] != recipe_id or recipe_id not in RECIPE_IDS:
        raise LifecycleError("recipe id must match its admitted canonical path")
    _text(recipe["purpose"], "purpose", maximum=200)
    _validate_content_scenario(recipe["content_scenario"], root)
    state = dict(_validate_state(recipe["initial_provider_state"]))
    if recipe["content_scenario"]["id"] != RECIPE_CONTENT_SCENARIOS[recipe_id]:
        raise LifecycleError("recipe content scenario does not match its canonical binding")
    if state != RECIPE_INITIAL_STATES[recipe_id]:
        raise LifecycleError("initial provider state does not match the canonical recipe")

    actions = _array(recipe["operator_actions"], "operator_actions")
    transitions = _array(recipe["expected_transitions"], "expected_transitions")
    if len(actions) != len(transitions):
        raise LifecycleError("each operator action requires exactly one expected transition")
    expected_operations = RECIPE_OPERATIONS[recipe_id]
    if (
        tuple(action.get("operation") for action in actions if isinstance(action, dict))
        != expected_operations
    ):
        raise LifecycleError("recipe operator actions do not match its admitted lifecycle")
    action_ids: set[str] = set()
    for index, (raw_action, raw_transition) in enumerate(zip(actions, transitions)):
        action = _closed(raw_action, ACTION_FIELDS, f"operator_actions[{index}]")
        transition = _closed(raw_transition, TRANSITION_FIELDS, f"expected_transitions[{index}]")
        action_id = _text(action["id"], f"operator_actions[{index}].id")
        operation = _text(action["operation"], f"operator_actions[{index}].operation")
        if action_id != operation or action_id in action_ids:
            raise LifecycleError("operator action ids must be unique and equal their operations")
        action_ids.add(action_id)
        actor = _text(action["actor"], f"operator_actions[{index}].actor")
        if actor not in ACTORS or actor != EXPECTED_ACTORS[operation]:
            raise LifecycleError(f"actor is not admitted for operation: {operation}")
        expected_transition = OPERATION_TRANSITIONS[operation]
        after_action = _text(
            transition["after_action"], f"expected_transitions[{index}].after_action"
        )
        observed_transition = tuple(
            _text(transition[key], f"expected_transitions[{index}].{key}")
            for key in ("field", "from", "to")
        )
        if after_action != action_id or observed_transition != expected_transition:
            raise LifecycleError(f"transition is not admitted for operation: {operation}")
        field, old, new = expected_transition
        if state[field] != old:
            raise LifecycleError(f"transition source does not match current state: {action_id}")
        state[field] = new

    conditions = _array(recipe["completion_conditions"], "completion_conditions")
    fields: list[str] = []
    for index, raw_condition in enumerate(conditions):
        condition = _closed(raw_condition, CONDITION_FIELDS, f"completion_conditions[{index}]")
        field = _text(condition["field"], f"completion_conditions[{index}].field")
        expected_value = _text(condition["equals"], f"completion_conditions[{index}].equals")
        if field not in STATE_VALUES or expected_value not in STATE_VALUES[field]:
            raise LifecycleError("completion condition uses an invalid field or value")
        if state[field] != expected_value:
            raise LifecycleError("completion condition does not match the observable final state")
        fields.append(field)
    if tuple(fields) != RECIPE_COMPLETION_FIELDS[recipe_id]:
        raise LifecycleError("completion conditions are incomplete or out of canonical order")

    cleanup = _array(recipe["cleanup_expectations"], "cleanup_expectations")
    if tuple(item.get("operation") for item in cleanup if isinstance(item, dict)) != tuple(
        CLEANUP_TRANSITIONS
    ):
        raise LifecycleError("cleanup expectations must close the PR then delete its head branch")
    for index, raw_cleanup in enumerate(cleanup):
        item = _closed(raw_cleanup, CLEANUP_FIELDS, f"cleanup_expectations[{index}]")
        operation = _text(item["operation"], f"cleanup_expectations[{index}].operation")
        expected = CLEANUP_TRANSITIONS[operation]
        cleanup_id = _text(item["id"], f"cleanup_expectations[{index}].id")
        actor = _text(item["actor"], f"cleanup_expectations[{index}].actor")
        observed = tuple(
            _text(item[key], f"cleanup_expectations[{index}].{key}")
            for key in ("field", "from", "to")
        )
        if cleanup_id != operation or actor != "operator" or observed != expected:
            raise LifecycleError(f"cleanup expectation is not admitted: {operation}")
        field, old, new = expected
        if state[field] != old:
            raise LifecycleError(f"cleanup source does not match completion state: {operation}")
        state[field] = new
    return recipe


def load_registry(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Load the exact lifecycle registry and reject aliases, omissions, and extra entries."""
    directory = _confined(root, LIFECYCLE_DIRECTORY, "lifecycle directory")
    try:
        children = sorted(directory.iterdir())
    except OSError as error:
        raise LifecycleError(f"cannot read lifecycle directory: {error}") from error
    if any(child.is_symlink() for child in children):
        raise LifecycleError("lifecycle registry entries must not be symlinks")
    expected_names = {"registry.json", *(f"{recipe_id}.json" for recipe_id in RECIPE_IDS)}
    if {child.name for child in children} != expected_names or any(
        not child.is_file() for child in children
    ):
        raise LifecycleError(
            "lifecycle directory must contain only the complete canonical registry"
        )

    registry = _closed(
        _read_json(_confined(root, REGISTRY_PATH, "registry path"), "registry"),
        REGISTRY_FIELDS,
        "registry",
    )
    if type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
        raise LifecycleError("registry schema_version must be integer 1")
    entries = _array(registry["recipes"], "registry recipes", minimum=4, maximum=4)
    recipes: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(entries):
        entry = _closed(raw_entry, REGISTRY_ENTRY_FIELDS, f"registry recipes[{index}]")
        recipe_id = _text(entry["id"], f"registry recipes[{index}].id")
        if recipe_id in recipes:
            raise LifecycleError(f"registry contains duplicate recipe id: {recipe_id}")
        if entry["path"] != _canonical_recipe_path(recipe_id):
            raise LifecycleError("registry recipe path is not canonical")
        recipe_path = _confined(root, entry["path"], "recipe path")
        recipes[recipe_id] = validate_recipe(_read_json(recipe_path, "recipe"), recipe_id, root)
    if tuple(recipes) != RECIPE_IDS:
        raise LifecycleError("registry must contain every admitted recipe in canonical order")
    return recipes


def _select_recipe(
    recipe_id: str | None, recipe_path: Path | None, root: Path
) -> tuple[str, dict[str, Any]]:
    recipes = load_registry(root)
    if recipe_path is not None:
        raw_path = safe_path(recipe_path.as_posix(), "recipe path")
        match = re.fullmatch(r"\.pr-lab/lifecycle/([^/]+)\.json", raw_path)
        if match is None or raw_path != _canonical_recipe_path(match.group(1)):
            raise LifecycleError("recipe path must be canonical .pr-lab/lifecycle/<id>.json")
        selected = match.group(1)
        if recipe_id is not None and recipe_id != selected:
            raise LifecycleError("recipe id and path disagree")
    elif recipe_id is not None:
        selected = recipe_id
    else:
        raise LifecycleError("recipe id or path is required")
    if selected not in recipes:
        raise LifecycleError("unknown recipe id")
    return selected, recipes[selected]


def _summary(recipe_id: str, recipe: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": recipe_id,
        "path": _canonical_recipe_path(recipe_id),
        "content_scenario": recipe["content_scenario"]["id"],
        "operator_actions": [item["id"] for item in recipe["operator_actions"]],
        "observable_transitions": len(recipe["expected_transitions"]),
        "completion_conditions": len(recipe["completion_conditions"]),
        "cleanup_expectations": len(recipe["cleanup_expectations"]),
    }


def _emit(value: dict[str, Any]) -> None:
    content = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(content) > MAX_OUTPUT_BYTES:
        raise LifecycleError("JSON output exceeds the bounded output contract")
    sys.stdout.buffer.write(content)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("recipe", nargs="?")
    validate.add_argument("--recipe", dest="recipe_path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.recipe is not None or args.recipe_path is not None:
            recipe_id, recipe = _select_recipe(args.recipe, args.recipe_path, ROOT)
            summaries = [_summary(recipe_id, recipe)]
        else:
            summaries = [
                _summary(recipe_id, recipe) for recipe_id, recipe in load_registry(ROOT).items()
            ]
        _emit({"schema_version": 1, "valid": True, "recipes": summaries})
        return 0
    except (LifecycleError, OSError) as error:
        _emit({"schema_version": 1, "valid": False, "error": str(error)[:256]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
