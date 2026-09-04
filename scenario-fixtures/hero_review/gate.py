"""Release-window and approval decisions."""

from datetime import datetime, timedelta

from .models import ReleaseRequest, Risk


def lease_deadline(issued_at: datetime, ttl_seconds: int) -> datetime:
    """Return when a short-lived release lease expires."""
    return issued_at + timedelta(seconds=ttl_seconds)


def can_release(request: ReleaseRequest) -> bool:
    """Return whether a release request has enough review."""
    required = 2 if request.risk is Risk.HIGH else 1
    return sum(request.approvals.values()) >= required
