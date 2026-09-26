"""The run state machine: the only place a run's status changes.

    queued ──► claimed ──► running ──► succeeded
       │          │           ├──────► failed
       │          │           ├──────► timed_out
       │          │           └──────► needs_input
       └──────────┴──────────────────► cancelled

Beyond the spec's diagram, claimed may also fail directly: the sandbox can fail to start,
or the reaper can find a claimed run whose heartbeat stopped before it ever ran.

`apply_transition` validates and mutates the run in memory and returns the event payload;
callers (the worker, the reaper, the cancel endpoint) persist it and perform side effects
(the `run_events` row, the GitHub comment, the board event) through `record_transition`.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from nextix.db.models import Run


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
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.TIMED_OUT,
        RunStatus.NEEDS_INPUT,
        RunStatus.CANCELLED,
    }
)

TRANSITIONS: dict[str, frozenset[str]] = {
    RunStatus.QUEUED: frozenset({RunStatus.CLAIMED, RunStatus.CANCELLED}),
    RunStatus.CLAIMED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.NEEDS_INPUT,
            RunStatus.CANCELLED,
        }
    ),
}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"run cannot go from {current} to {target}")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True)
class Transition:
    previous: str
    status: str
    exit_reason: str | None

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {"from": self.previous, "status": self.status}
        if self.exit_reason:
            data["exit_reason"] = self.exit_reason
        return data


def apply_transition(
    run: Run, target: str, *, now: datetime, exit_reason: str | None = None
) -> Transition:
    """Validate and apply a status change to `run` in memory. Raises InvalidTransition."""
    current = run.status
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    run.status = target
    if target == RunStatus.CLAIMED:
        run.started_at = now
        run.last_heartbeat = now
    if target in TERMINAL_STATUSES:
        run.finished_at = now
        run.callback_secret = None  # the sandbox may no longer report anything
        if exit_reason:
            run.exit_reason = exit_reason
    return Transition(previous=current, status=target, exit_reason=exit_reason)
