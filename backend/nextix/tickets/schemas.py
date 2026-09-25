"""API shapes for tickets on the board."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Column(StrEnum):
    TODO = "todo"
    DOING = "doing"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    IN_REVIEW = "in_review"
    DONE = "done"


class RunSummary(BaseModel):
    id: uuid.UUID
    attempt: int
    status: str
    agent_id: str | None
    started_at: datetime | None
    last_heartbeat: datetime | None
    cost_usd: float


class TicketCard(BaseModel):
    id: uuid.UUID
    repo: str  # owner/name
    issue_number: int
    title: str
    labels: list[str]
    issue_state: str | None
    issue_url: str
    pr_number: int | None
    pr_state: str | None
    pr_url: str | None
    column: Column
    latest_run: RunSummary | None
    updated_at: datetime
