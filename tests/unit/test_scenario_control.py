import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from tools.scenario_control import (  # noqa: E402
    ControlError,
    evaluate,
    inspect,
    load_registry,
    prepare,
    safe_path,
    validate_control,
)

SCENARIOS = {
    "agent-repair",
    "clean-green",
    "conversational-change",
    "first-attempt-flake",
    "persistent-ci-regression",
    "seeded-review-finding",
}


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    shutil.rmtree(root / "scenario-fixtures")
    manifest = json.loads((root / ".pr-lab/scenarios/clean-green/scenario.json").read_text())
    control = {
        "schema_version": 1,
        **{key: manifest[key] for key in ("scenario", "behavior", "review_lenses")},
    }
    (root / ".pr-lab/scenario.json").write_text(json.dumps(control, indent=2) + "\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "scenario@example.invalid")
    git(root, "config", "user.name", "Scenario Test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    return root


def test_all_committed_manifests_validate_through_public_registry() -> None:
    assert set(load_registry(ROOT)) == SCENARIOS


@pytest.mark.parametrize(
    "path",
    ["/absolute", "a/../b", "a/./b", "a//b", "a\\b", "-option", ".GIT/x", "x/.GITMODULES"],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ControlError):
        safe_path(path)


def test_behaviors_are_attempt_deterministic() -> None:
    registry = load_registry(ROOT)
    assert evaluate(registry["clean-green"], 1)[0]
    assert not evaluate(registry["first-attempt-flake"], 1)[0]
    assert evaluate(registry["first-attempt-flake"], 2)[0]
    assert not evaluate(registry["persistent-ci-regression"], 1)[0]
    assert not evaluate(registry["persistent-ci-regression"], 99)[0]
    with pytest.raises(ControlError):
        evaluate(registry["clean-green"], 0)


def test_legacy_control_admits_exact_persistent_failure() -> None:
    control = {
        "schema_version": 1,
        "scenario": "persistent-ci-regression",
        "behavior": {"kind": "persistent_failure", "fingerprint": "stable"},
        "review_lenses": ["correctness", "test-quality", "risk"],
    }
    assert validate_control(control) == control
    with pytest.raises(ControlError):
        validate_control({**control, "behavior": {"kind": "persistent_failure"}})


def test_prepare_is_exact_and_idempotent_and_inspect_is_repeatable(repository: Path) -> None:
    first = prepare("clean-green", repository)
    assert first["changed"] is True
    target = repository / first["admitted_changed_paths"][1]
    assert target.read_bytes() == (
        repository / ".pr-lab/scenarios/clean-green/payload.py"
    ).read_bytes()
    status_before = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repository, check=True, capture_output=True
    ).stdout
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True
    ).stdout
    before_bytes = json.dumps(inspect("clean-green", repository), sort_keys=True).encode()
    after_bytes = json.dumps(inspect("clean-green", repository), sort_keys=True).encode()
    assert before_bytes == after_bytes
    before = json.loads(before_bytes)
    assert before["state"] == "prepared"
    assert before["valid"] is True
    assert subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repository, check=True, capture_output=True
    ).stdout == status_before
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True
    ).stdout == head_before
    assert prepare("clean-green", repository)["changed"] is False
    git(repository, "add", ".pr-lab/scenario.json", "scenario-fixtures/clean_green.py")
    git(repository, "commit", "-qm", "prepared scenario")
    assert prepare("clean-green", repository)["changed"] is False
    assert inspect("clean-green", repository)["state"] == "prepared"


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_prepared_scenario_executes_its_fixture_contract(
    repository: Path, scenario: str
) -> None:
    result = prepare(scenario, repository)
    control = json.loads((repository / ".pr-lab/scenario.json").read_text())
    assert control["scenario"] == scenario
    target = repository / result["admitted_changed_paths"][1]
    assert target.is_file()

    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_scenario_fixture_contract.py",
        "-q",
    ]
    first = subprocess.run(command, cwd=repository, check=False, capture_output=True, text=True)
    if scenario != "agent-repair":
        assert first.returncode == 0, first.stdout + first.stderr
        return

    assert first.returncode == 1
    assert "test_agent_repair_high_risk_threshold" in first.stdout
    before_paths = set(result["observed_changed_paths"])
    target.write_text(target.read_text().replace("return 1", "return 2"))
    after_paths = {
        line[3:]
        for line in subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    assert after_paths == before_paths
    repaired = subprocess.run(command, cwd=repository, check=False, capture_output=True, text=True)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr


def test_prepare_refuses_symlinked_target_parent_without_outside_write(
    repository: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / "scenario-fixtures").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ControlError, match="symlink"):
        prepare("clean-green", repository)
    assert list(outside.iterdir()) == []


def test_prepare_refuses_dirty_preexisting_and_wrong_basis(repository: Path) -> None:
    (repository / "unadmitted.txt").write_text("dirty")
    with pytest.raises(ControlError, match="dirty"):
        prepare("clean-green", repository)
    (repository / "unadmitted.txt").unlink()

    target = repository / "scenario-fixtures/clean_green.py"
    target.parent.mkdir()
    target.write_text("preexisting")
    git(repository, "add", "scenario-fixtures/clean_green.py")
    git(repository, "commit", "-qm", "pre-existing target")
    with pytest.raises(ControlError, match="already exists"):
        prepare("clean-green", repository)
    target.unlink()
    git(repository, "add", "scenario-fixtures/clean_green.py")
    git(repository, "commit", "-qm", "remove pre-existing target")

    (repository / "src/pr_fixture/readiness.py").write_text("wrong basis")
    with pytest.raises(ControlError, match="source basis mismatch"):
        prepare("clean-green", repository)


def test_prepare_refuses_staged_and_mixed_prepared_states(repository: Path) -> None:
    prepare("agent-repair", repository)
    git(repository, "add", ".pr-lab/scenario.json")
    with pytest.raises(ControlError, match="dirty"):
        prepare("agent-repair", repository)
    git(repository, "reset", "-q")
    (repository / "scenario-fixtures/agent_repair.py").write_text("wrong\n")
    with pytest.raises(ControlError, match="dirty"):
        prepare("agent-repair", repository)


def test_evaluate_cli_writes_exact_bounded_evidence_shape(repository: Path) -> None:
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    prepare("clean-green", repository)
    git(repository, "add", ".pr-lab/scenario.json", "scenario-fixtures/clean_green.py")
    git(repository, "commit", "-qm", "prepare clean-green")
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    evidence = repository / ".pr-lab/evidence/scenario-control.json"
    result = subprocess.run(
        [
            sys.executable,
            "tools/scenario_control.py",
            "evaluate",
            "--attempt",
            "1",
            "--evidence",
            ".pr-lab/evidence/scenario-control.json",
        ],
        cwd=repository,
        env={
            **os.environ,
            "GITHUB_REPOSITORY": "HBNetwork/demo-pr-readiness",
            "PR_NUMBER": "17",
            "BASE_SHA": base_sha,
            "HEAD_SHA": head_sha,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
        },
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0
    value = json.loads(evidence.read_text())
    assert set(value) == {
        "schema_version",
        "scenario",
        "manifest_path",
        "manifest_sha256",
        "repository",
        "pull_request",
        "base_sha",
        "head_sha",
        "observed_head",
        "run_id",
        "run_attempt",
        "behavior",
        "expected_result",
        "observed_result",
        "admitted_changed_paths",
        "observed_changed_paths",
        "payload_sha256",
        "result_sha256",
    }
    assert value["expected_result"] == value["observed_result"] == "pass"
    assert value["observed_changed_paths"] == ["scenario-fixtures/clean_green.py"]
    assert len(evidence.read_bytes()) < 4096


def test_registry_rejects_invalid_schema_and_scenario_target_aliasing(repository: Path) -> None:
    manifest_path = repository / ".pr-lab/scenarios/agent-repair/scenario.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ControlError, match="schema_version"):
        load_registry(repository)

    manifest = json.loads((ROOT / ".pr-lab/scenarios/agent-repair/scenario.json").read_text())
    target = "scenario-fixtures/clean_green.py"
    manifest["fixture"]["target"] = target
    manifest["fixture"]["admitted_changed_paths"] = [".pr-lab/scenario.json", target]
    manifest["expectation"]["target"] = target
    manifest["expectation"]["repair_paths"] = [target]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ControlError, match="duplicate admitted target"):
        load_registry(repository)


def test_evidence_refuses_escape_and_symlink_parent(repository: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    evidence_dir = repository / ".pr-lab/evidence"
    evidence_dir.symlink_to(outside, target_is_directory=True)
    script = repository / "tools/scenario_control.py"
    for path in ("../../outside.json", ".pr-lab/evidence/result.json"):
        result = subprocess.run(
            [sys.executable, script, "evaluate", "clean-green", "--evidence", path],
            cwd=repository,
            check=False,
            capture_output=True,
        )
        assert result.returncode == 2
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "value"),
    [("HEAD_SHA", "bad"), ("BASE_SHA", "A" * 40), ("PR_NUMBER", "one"), ("GITHUB_RUN_ID", "0")],
)
def test_evaluate_rejects_malformed_github_context(name: str, value: str) -> None:
    result = subprocess.run(
        ["python", "tools/scenario_control.py", "evaluate", "clean-green"],
        cwd=ROOT,
        env={**os.environ, name: value},
        check=False,
        capture_output=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["valid"] is False


def test_evaluate_rejects_head_context_that_does_not_match_observed_head() -> None:
    result = subprocess.run(
        ["python", "tools/scenario_control.py", "evaluate", "clean-green"],
        cwd=ROOT,
        env={**os.environ, "HEAD_SHA": "0" * 40},
        check=False,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "does not equal observed_head" in json.loads(result.stdout)["error"]


def test_explicit_manifest_must_be_canonical() -> None:
    approved = subprocess.run(
        [
            "python", "tools/scenario_control.py", "inspect", "--manifest",
            ".pr-lab/scenarios/clean-green/scenario.json", "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert approved.returncode == 0
    assert json.loads(approved.stdout)["valid"] is True
    rejected = subprocess.run(
        ["python", "tools/scenario_control.py", "validate", "--manifest", "README.md"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert rejected.returncode == 2


def test_attempt_defaults_from_environment_for_legacy_and_subcommand() -> None:
    env = {**os.environ, "GITHUB_RUN_ATTEMPT": "2"}
    legacy = subprocess.run(
        [sys.executable, "tools/scenario_control.py"], cwd=ROOT, env=env, capture_output=True
    )
    command = subprocess.run(
        [sys.executable, "tools/scenario_control.py", "evaluate", "first-attempt-flake"],
        cwd=ROOT,
        env=env,
        capture_output=True,
    )
    assert legacy.returncode == command.returncode == 0
    assert json.loads(command.stdout)["observed_result"] == "pass"
    invalid = subprocess.run(
        [sys.executable, "tools/scenario_control.py", "evaluate", "clean-green"],
        cwd=ROOT,
        env={**os.environ, "GITHUB_RUN_ATTEMPT": "invalid"},
        capture_output=True,
    )
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["valid"] is False
