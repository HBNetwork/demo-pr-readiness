import pytest

from pr_fixture.readiness import (
    PullRequestFacts,
    needs_elevated_review,
    ready_for_release,
    required_approvals,
)


def test_release_readiness_requires_a_non_draft_reviewed_green_change() -> None:
    facts = PullRequestFacts(draft=False, approvals=1, checks_green=True, mergeable=True)
    assert ready_for_release(facts)


def test_release_readiness_rejects_a_draft() -> None:
    facts = PullRequestFacts(draft=True, approvals=1, checks_green=True, mergeable=True)
    assert not ready_for_release(facts)


def test_high_risk_requires_two_approvals() -> None:
    assert required_approvals("high") == 2


def test_unknown_risk_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported risk"):
        required_approvals("unknown")


def test_security_source_requires_elevated_review() -> None:
    assert needs_elevated_review(["src/security/policy.py"])
    assert not needs_elevated_review(["src/reporting.py"])
