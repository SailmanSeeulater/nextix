"""runs: task snapshot and callback batch counter; tickets: triage question

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The issue title and body as they were when a trusted person started the run. The
    # agent works from this, not from the live issue, which its author may edit later.
    op.add_column("runs", sa.Column("task_title", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("task_body", sa.Text(), nullable=True))
    # Highest callback batch number accepted, so a retried batch isn't stored twice.
    op.add_column(
        "runs",
        sa.Column("last_batch", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    # Triage's clarifying question when a ticket was filed as not yet actionable.
    op.add_column("tickets", sa.Column("triage_question", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "triage_question")
    op.drop_column("runs", "last_batch")
    op.drop_column("runs", "task_body")
    op.drop_column("runs", "task_title")
