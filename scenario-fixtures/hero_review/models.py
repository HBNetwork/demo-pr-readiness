"""Value objects used by the release gate."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Risk(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True)
class ReleaseRequest:
    risk: Risk
    created_at: datetime
    approvals: Mapping[str, bool]
