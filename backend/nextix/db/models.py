"""ORM models mirroring the schema in the spec (§5).

GitHub is the source of truth for issues, PRs, and labels; these tables only
mirror that state plus what GitHub doesn't have (runs, events, artifacts).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextix.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("owner", "name", name="uq_repos_owner_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    default_branch: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="repo")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("repo_id", "issue_number", name="uq_tickets_repo_id_issue_number"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"), nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    issue_state: Mapped[str | None] = mapped_column(Text)  # open | closed
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_state: Mapped[str | None] = mapped_column(Text)  # open | closed | merged
    created_via: Mapped[str | None] = mapped_column(Text)  # cli | web | mcp | github
    triage_question: Mapped[str | None] = mapped_column(Text)
    pr_head_sha: Mapped[str | None] = mapped_column(Text)
    checks_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checks_error: Mapped[str | None] = mapped_column(Text)  # "forbidden" without Checks: read
    pending_review_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    repo: Mapped[Repo] = relationship(back_populates="tickets")
    runs: Mapped[list["Run"]] = relationship(back_populates="ticket", order_by="Run.attempt")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_ticket_id_attempt", "ticket_id", text("attempt DESC")),
        Index("ix_runs_status_last_heartbeat", "status", "last_heartbeat"),
        Index(
            "uq_runs_one_active_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'claimed', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str | None] = mapped_column(Text)  # initial | retry | review_feedback
    status: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Per-run HMAC key for sandbox callbacks; cleared when the run finishes. Never logged.
    callback_secret: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str | None] = mapped_column(Text)
    task_title: Mapped[str | None] = mapped_column(Text)
    task_body: Mapped[str | None] = mapped_column(Text)
    last_batch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tests: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review_errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    review_id: Mapped[int | None] = mapped_column(BigInteger)
    review_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)

    ticket: Mapped[Ticket] = relationship(back_populates="runs")
    events: Mapped[list["RunEvent"]] = relationship(back_populates="run", order_by="RunEvent.id")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="run")


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # state | log | tool_use | message | error
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    run: Mapped[Run] = relationship(back_populates="events")


class CheckRun(Base):
    """A CI check run on a commit, mirrored from GitHub (webhooks and API reads)."""

    __tablename__ = "check_runs"
    __table_args__ = (Index("ix_check_runs_repo_head", "repo_id", "head_sha"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"), nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    head_branch: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)
    html_url: Mapped[str | None] = mapped_column(Text)
    details_url: Mapped[str | None] = mapped_column(Text)
    app_name: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CommitStatus(Base):
    """A commit status (the pre-checks CI API), mirrored from GitHub on demand."""

    __tablename__ = "commit_statuses"

    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"), primary_key=True)
    sha: Mapped[str] = mapped_column(Text, primary_key=True)
    context: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)  # pending|success|failure|error
    target_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    """One row per processed GitHub delivery (X-GitHub-Delivery), for dedupe."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    run: Mapped[Run] = relationship(back_populates="artifacts")
