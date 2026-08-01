"""Executable contract for a prepared scenario; dormant on the clean baseline."""

import json
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CONTROL = json.loads((ROOT / ".pr-lab/scenario.json").read_bytes())
SCENARIO = CONTROL["scenario"]
TARGETS = {
    "agent-repair": "agent_repair.py",
    "clean-green": "clean_green.py",
    "conversational-change": "conversational_change.py",
    "first-attempt-flake": "first_attempt_flake.py",
    "persistent-ci-regression": "persistent_ci_regression.py",
    "seeded-review-finding": "seeded_review_finding.py",
}
TARGET = ROOT / "scenario-fixtures" / TARGETS[SCENARIO]


def fixture_module():
    if not TARGET.exists():
        pytest.skip("no prepared scenario fixture")
    module = types.ModuleType("prepared_scenario")
    exec(compile(TARGET.read_bytes(), TARGET, "exec"), module.__dict__)
    return module


@pytest.mark.skipif(SCENARIO == "agent-repair", reason="agent repair has its named contract")
def test_prepared_scenario_fixture_contract() -> None:
    module = fixture_module()
    if SCENARIO == "clean-green":
        assert module.readiness_label() == "clean-green"
    elif SCENARIO == "conversational-change":
        assert module.release_summary(2) == "release has 2 approval(s)"
    elif SCENARIO == "seeded-review-finding":
        assert module.ready_for_release(False, 1, True, True)
    else:
        assert module.fixture_is_healthy()


@pytest.mark.skipif(SCENARIO != "agent-repair", reason="not the agent-repair scenario")
def test_agent_repair_high_risk_threshold() -> None:
    assert fixture_module().approval_threshold("high") == 2
