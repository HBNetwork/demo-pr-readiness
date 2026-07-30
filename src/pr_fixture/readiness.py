"""Small, deliberately reviewable PR-readiness domain."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequestFacts:
    draft: bool
    approvals: int
    checks_green: bool
    mergeable: bool


def ready_for_release(facts: PullRequestFacts) -> bool:
    """Return whether the observed review and CI posture permits release."""
    return not facts.draft and facts.approvals > 0 and facts.checks_green


def required_approvals(risk: str) -> int:
    """Return the review threshold for a supported risk level."""
    if risk == "high":
        return 2
    if risk == "normal":
        return 1
    raise ValueError(f"unsupported risk level: {risk}")


def needs_elevated_review(paths: list[str]) -> bool:
    """Return whether changed paths touch elevated-risk source."""
    return any(path.startswith("src/security/") for path in paths)
