"""Validate and execute the committed scenario control using only stdlib."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

CONTROL_FIELDS = frozenset({"schema_version", "scenario", "behavior", "review_lenses"})
BEHAVIOR_FIELDS = frozenset({"kind", "fingerprint"})
LENSES = ("correctness", "test-quality", "risk")


class ControlError(ValueError):
    """The active control is not an admitted closed-schema value."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlError(f"{label} must be an object")
    return value


def validate_control(value: Any) -> dict[str, Any]:
    control = _object(value, "control")
    if set(control) != CONTROL_FIELDS:
        message = (
            "control fields must be exactly: behavior, review_lenses, scenario, schema_version"
        )
        raise ControlError(message)
    if control["schema_version"] != 1:
        raise ControlError("schema_version must be 1")
    if not isinstance(control["scenario"], str) or not control["scenario"]:
        raise ControlError("scenario must be a non-empty string")
    if control["review_lenses"] != list(LENSES):
        raise ControlError("review_lenses must be the three independent pinned lenses")

    behavior = _object(control["behavior"], "behavior")
    kind = behavior.get("kind")
    expected = {"kind"} if kind == "pass" else BEHAVIOR_FIELDS
    if kind not in {"pass", "first_attempt_flake"} or set(behavior) != expected:
        message = "behavior must be pass, or first_attempt_flake with exactly one fingerprint"
        raise ControlError(message)
    if kind == "first_attempt_flake":
        fingerprint = behavior["fingerprint"]
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ControlError("flake fingerprint must be a non-empty string")
    return control


def load_control(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ControlError(f"cannot read control: {error}") from error
    return validate_control(value)


def evaluate(control: dict[str, Any], attempt: int) -> tuple[bool, str]:
    validate_control(control)
    if attempt < 1:
        raise ControlError("attempt must be a positive integer")
    behavior = control["behavior"]
    if behavior["kind"] == "first_attempt_flake" and attempt == 1:
        return False, behavior["fingerprint"]
    return True, "scenario-control:pass"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, default=Path(".pr-lab/scenario.json"))
    default_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--attempt", type=int, default=default_attempt)
    args = parser.parse_args(argv)
    try:
        passed, conclusion = evaluate(load_control(args.control), args.attempt)
    except ControlError as error:
        print(f"scenario-control:invalid:{error}")
        return 2
    print(conclusion)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
