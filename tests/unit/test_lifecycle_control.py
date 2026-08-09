import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from tools.lifecycle_control import (  # noqa: E402
    RECIPE_IDS,
    LifecycleError,
    load_registry,
    safe_path,
    validate_recipe,
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    return root


def read_recipe(root: Path, recipe_id: str) -> dict[str, object]:
    return json.loads((root / f".pr-lab/lifecycle/{recipe_id}.json").read_bytes())


def write_recipe(root: Path, recipe_id: str, recipe: dict[str, object]) -> None:
    (root / f".pr-lab/lifecycle/{recipe_id}.json").write_text(json.dumps(recipe) + "\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_registry_is_exact_complete_and_validation_only() -> None:
    registry = load_registry(ROOT)

    assert tuple(registry) == RECIPE_IDS
    assert {recipe["content_scenario"]["id"] for recipe in registry.values()} == {"clean-green"}
    assert not hasattr(sys.modules["tools.lifecycle_control"], "execute")
    assert not hasattr(sys.modules["tools.lifecycle_control"], "prepare")


@pytest.mark.parametrize(
    "recipe_id",
    ["draft-to-ready", "stale-base", "true-conflict", "collaboration-gate"],
)
def test_each_recipe_reaches_completion_then_declares_full_cleanup(recipe_id: str) -> None:
    recipe = load_registry(ROOT)[recipe_id]

    assert len(recipe["operator_actions"]) <= len(recipe["expected_transitions"])
    assert [item["operation"] for item in recipe["cleanup_expectations"]] == [
        "close-pull-request",
        "delete-head-branch",
    ]


def test_cli_validates_all_or_one_and_emits_canonical_bounded_json() -> None:
    commands = (
        [sys.executable, "tools/lifecycle_control.py", "validate"],
        [sys.executable, "tools/lifecycle_control.py", "validate", "draft-to-ready"],
        [
            sys.executable,
            "tools/lifecycle_control.py",
            "validate",
            "--recipe",
            ".pr-lab/lifecycle/draft-to-ready.json",
        ],
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        parsed = json.loads(result.stdout)
        assert parsed["valid"] is True
        assert len(result.stdout) <= 8192
        assert (
            result.stdout
            == (json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
    assert (
        len(
            json.loads(subprocess.run(commands[0], cwd=ROOT, capture_output=True).stdout)["recipes"]
        )
        == 4
    )
    assert (
        len(
            json.loads(subprocess.run(commands[1], cwd=ROOT, capture_output=True).stdout)["recipes"]
        )
        == 1
    )


@pytest.mark.parametrize(
    "path",
    ["/absolute", "../escape", "a/../b", "a/./b", "a//b", "a\\b", "-option", ".GIT/x"],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(LifecycleError):
        safe_path(path)


def test_duplicate_json_keys_are_rejected(repository: Path) -> None:
    path = repository / ".pr-lab/lifecycle/draft-to-ready.json"
    content = path.read_text().replace(
        '"schema_version": 1,', '"schema_version": 1,\n  "schema_version": 1,', 1
    )
    path.write_text(content)

    with pytest.raises(LifecycleError, match="duplicate key: schema_version"):
        load_registry(repository)


def test_recipe_and_registry_require_exact_fields(repository: Path) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    recipe["execution_command"] = "gh pr ready"
    write_recipe(repository, "draft-to-ready", recipe)

    with pytest.raises(LifecycleError, match="fields must be exactly"):
        load_registry(repository)


def test_registry_rejects_missing_duplicate_and_noncanonical_entries(repository: Path) -> None:
    registry_path = repository / ".pr-lab/lifecycle/registry.json"
    registry = json.loads(registry_path.read_bytes())
    registry["recipes"][1] = dict(registry["recipes"][0])
    registry_path.write_text(json.dumps(registry) + "\n")

    with pytest.raises(LifecycleError, match="duplicate recipe id"):
        load_registry(repository)


def test_registry_rejects_unregistered_files_and_symlinks(repository: Path) -> None:
    lifecycle = repository / ".pr-lab/lifecycle"
    (lifecycle / "extra.json").write_text("{}\n")
    with pytest.raises(LifecycleError, match="only the complete canonical registry"):
        load_registry(repository)
    (lifecycle / "extra.json").unlink()
    (lifecycle / "alias.json").symlink_to("draft-to-ready.json")
    with pytest.raises(LifecycleError, match="must not be symlinks"):
        load_registry(repository)


def test_content_manifest_symlinks_are_rejected(repository: Path) -> None:
    manifest = repository / ".pr-lab/scenarios/clean-green/scenario.json"
    original = manifest.read_bytes()
    manifest.unlink()
    target = repository / "scenario-copy.json"
    target.write_bytes(original)
    manifest.symlink_to(target)

    with pytest.raises(LifecycleError, match="traverses a symlink"):
        load_registry(repository)


@pytest.mark.parametrize(
    ("recipe_id", "operation", "replacement"),
    [
        ("draft-to-ready", "mark-ready", "submit-approval"),
        ("stale-base", "update-branch", "mark-ready"),
        ("true-conflict", "resolve-conflict", "update-branch"),
        ("collaboration-gate", "submit-approval", "mark-ready"),
    ],
)
def test_unadmitted_recipe_operations_are_rejected(
    repository: Path, recipe_id: str, operation: str, replacement: str
) -> None:
    recipe = read_recipe(repository, recipe_id)
    action = next(item for item in recipe["operator_actions"] if item["operation"] == operation)
    action["operation"] = replacement
    write_recipe(repository, recipe_id, recipe)

    with pytest.raises(LifecycleError, match="admitted lifecycle"):
        load_registry(repository)


def test_transition_must_follow_current_state(repository: Path) -> None:
    recipe = read_recipe(repository, "stale-base")
    recipe["initial_provider_state"]["base_relation"] = "stale"

    with pytest.raises(LifecycleError, match="initial provider state"):
        validate_recipe(recipe, "stale-base", repository)


def test_completion_and_cleanup_must_match_observable_state(repository: Path) -> None:
    recipe = read_recipe(repository, "collaboration-gate")
    recipe["completion_conditions"][1]["equals"] = "pending"
    with pytest.raises(LifecycleError, match="observable final state"):
        validate_recipe(recipe, "collaboration-gate", repository)

    recipe = read_recipe(repository, "collaboration-gate")
    recipe["cleanup_expectations"][0]["to"] = "open"
    with pytest.raises(LifecycleError, match="cleanup expectation is not admitted"):
        validate_recipe(recipe, "collaboration-gate", repository)


def test_non_content_scenario_binding_is_rejected(repository: Path) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    recipe["content_scenario"] = {
        "id": "agent-repair",
        "manifest": ".pr-lab/scenarios/agent-repair/scenario.json",
        "manifest_sha256": file_sha256(
            repository / ".pr-lab/scenarios/agent-repair/scenario.json"
        ),
    }

    with pytest.raises(LifecycleError, match="deterministic content scenario"):
        validate_recipe(recipe, "draft-to-ready", repository)


def test_alternate_content_scenario_binding_is_rejected(repository: Path) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    recipe["content_scenario"] = {
        "id": "first-attempt-flake",
        "manifest": ".pr-lab/scenarios/first-attempt-flake/scenario.json",
        "manifest_sha256": file_sha256(
            repository / ".pr-lab/scenarios/first-attempt-flake/scenario.json"
        ),
    }

    with pytest.raises(LifecycleError, match="canonical binding"):
        validate_recipe(recipe, "draft-to-ready", repository)


def test_unrelated_initial_state_is_rejected(repository: Path) -> None:
    recipe = read_recipe(repository, "collaboration-gate")
    recipe["initial_provider_state"]["readiness"] = "draft"

    with pytest.raises(LifecycleError, match="initial provider state"):
        validate_recipe(recipe, "collaboration-gate", repository)


def test_content_manifest_bytes_are_pinned_and_fully_validated(repository: Path) -> None:
    manifest_path = repository / ".pr-lab/scenarios/clean-green/scenario.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["review_lenses"] = ["risk", "test-quality", "correctness"]
    manifest_path.write_text(json.dumps(manifest) + "\n")

    with pytest.raises(LifecycleError, match="manifest_sha256"):
        load_registry(repository)

    recipe = read_recipe(repository, "draft-to-ready")
    recipe["content_scenario"]["manifest_sha256"] = file_sha256(manifest_path)
    with pytest.raises(LifecycleError, match="content scenario manifest is invalid"):
        validate_recipe(recipe, "draft-to-ready", repository)


def test_content_manifest_digest_must_be_lowercase_sha256(repository: Path) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    recipe["content_scenario"]["manifest_sha256"] = "BAD"
    with pytest.raises(LifecycleError, match="lowercase sha256"):
        validate_recipe(recipe, "draft-to-ready", repository)


def test_true_conflict_requires_coupled_transitions_and_completion(repository: Path) -> None:
    recipe = read_recipe(repository, "true-conflict")
    assert [
        (item["after_action"], item["field"], item["from"], item["to"])
        for item in recipe["expected_transitions"]
    ] == [
        ("introduce-conflicting-base-change", "base_relation", "current", "stale"),
        ("introduce-conflicting-base-change", "mergeability", "mergeable", "conflicting"),
        ("resolve-conflict", "base_relation", "stale", "current"),
        ("resolve-conflict", "mergeability", "conflicting", "mergeable"),
    ]
    assert recipe["completion_conditions"] == [
        {"field": "base_relation", "equals": "current"},
        {"field": "mergeability", "equals": "mergeable"},
    ]

    invalid = json.loads(json.dumps(recipe))
    del invalid["expected_transitions"][0]
    with pytest.raises(LifecycleError, match="complete expected transitions"):
        validate_recipe(invalid, "true-conflict", repository)

    invalid = json.loads(json.dumps(recipe))
    invalid["expected_transitions"][0:2] = reversed(invalid["expected_transitions"][0:2])
    with pytest.raises(LifecycleError, match="not admitted"):
        validate_recipe(invalid, "true-conflict", repository)

    invalid = json.loads(json.dumps(recipe))
    del invalid["completion_conditions"][0]
    with pytest.raises(LifecycleError, match="incomplete or out of canonical order"):
        validate_recipe(invalid, "true-conflict", repository)


@pytest.mark.parametrize(
    ("location", "value"),
    [
        (("operator_actions", 0, "actor"), []),
        (("completion_conditions", 0, "field"), {}),
        (("completion_conditions", 0, "equals"), []),
    ],
)
def test_non_string_enum_values_are_rejected(
    repository: Path, location: tuple[str, int, str], value: object
) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    collection, index, field = location
    recipe[collection][index][field] = value
    write_recipe(repository, "draft-to-ready", recipe)

    with pytest.raises(LifecycleError, match="must be a non-empty string"):
        load_registry(repository)


def test_cli_converts_malformed_types_to_canonical_bounded_json(repository: Path) -> None:
    recipe = read_recipe(repository, "draft-to-ready")
    recipe["operator_actions"][0]["actor"] = []
    write_recipe(repository, "draft-to-ready", recipe)

    result = subprocess.run(
        [sys.executable, "tools/lifecycle_control.py", "validate"],
        cwd=repository,
        check=False,
        capture_output=True,
    )

    parsed = json.loads(result.stdout)
    assert result.returncode == 2
    assert parsed["valid"] is False
    assert result.stderr == b""
    assert len(result.stdout) <= 8192
    assert (
        result.stdout == (json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )


def test_cli_rejects_noncanonical_recipe_path_with_bounded_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "tools/lifecycle_control.py",
            "validate",
            "--recipe",
            ".pr-lab/lifecycle/../lifecycle/draft-to-ready.json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["valid"] is False
    assert len(result.stdout) <= 8192
