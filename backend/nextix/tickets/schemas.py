"""API shapes for tickets on the board."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    created_via: str | None = None
    updated_at: datetime
    # The latest run's tests failed (they never block the PR, but the board says so).
    tests_failing: bool = False


class CreateTicketRequest(BaseModel):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", description="owner/name")
    prompt: str = Field(min_length=3, max_length=20_000)
    labels: list[str] = Field(default_factory=list, max_length=20)
    triage: bool = True
    created_via: Literal["cli", "web", "mcp"] = "cli"

    @field_validator("prompt")
    @classmethod
    def _strip_prompt(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("prompt is too short")
        return value

    @field_validator("labels")
    @classmethod
    def _clean_labels(cls, value: list[str]) -> list[str]:
        labels = [v.strip() for v in value if v.strip()]
        if any(len(label) > 50 for label in labels):
            raise ValueError("labels must be 50 characters or fewer")
        return labels


class CreateTicketResponse(BaseModel):
    ticket: TicketCard
    issue_url: str
    board_url: str
    needs_input: bool
    clarifying_question: str | None
