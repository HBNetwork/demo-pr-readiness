"""Tiny domain used by the PR-readiness laboratory."""

from .readiness import (
    PullRequestFacts,
    needs_elevated_review,
    ready_for_release,
    required_approvals,
)

__all__ = [
    "PullRequestFacts",
    "needs_elevated_review",
    "ready_for_release",
    "required_approvals",
]
