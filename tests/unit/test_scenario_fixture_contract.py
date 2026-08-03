"""Executable contract for a prepared scenario; dormant on the clean baseline."""

import json
import sys
import types
from datetime import UTC, datetime
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
    "hero-review": "hero_review",
    "persistent-ci-regression": "persistent_ci_regression.py",
    "seeded-review-finding": "seeded_review_finding.py",
}
TARGET = ROOT / "scenario-fixtures" / TARGETS[SCENARIO]


def fixture_module():
    if not TARGET.exists():
        pytest.skip("no prepared scenario fixture")
    if SCENARIO == "hero-review":
        sys.path.insert(0, str(TARGET.parent))
        try:
            __import__("hero_review")
            return sys.modules["hero_review"]
        finally:
            sys.path.pop(0)
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
    elif SCENARIO == "hero-review":
        issued_at = datetime(2026, 1, 1, tzinfo=UTC)
        request = module.ReleaseRequest(
            risk=module.Risk.NORMAL,
            created_at=issued_at,
            approvals={"reviewer": True},
        )
        assert module.lease_deadline(issued_at, 0) == issued_at
        assert module.can_release(request)
        assert module.store_key("hbnetwork/demo", 42) == module.lookup_key("hbnetwork/demo", 42)
    else:
        assert module.fixture_is_healthy()


@pytest.mark.skipif(SCENARIO != "agent-repair", reason="not the agent-repair scenario")
def test_agent_repair_high_risk_threshold() -> None:
    assert fixture_module().approval_threshold("high") == 2
