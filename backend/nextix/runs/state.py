"""Run statuses. Transition rules are added in Phase 3."""

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    NEEDS_INPUT = "needs_input"
    CANCELLED = "cancelled"


ACTIVE_STATUSES: frozenset[str] = frozenset(
    {RunStatus.QUEUED, RunStatus.CLAIMED, RunStatus.RUNNING}
)
FAILED_STATUSES: frozenset[str] = frozenset(
    {RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED}
)
