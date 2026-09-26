"""runs: queued_at, callback_secret, summary, question

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Per-run HMAC key for sandbox callbacks; cleared when the run finishes.
    op.add_column("runs", sa.Column("callback_secret", sa.Text(), nullable=True))
    # The agent's own summary of what it did (goes into the PR body).
    op.add_column("runs", sa.Column("summary", sa.Text(), nullable=True))
    # The question the agent asked when it ended in needs_input.
    op.add_column("runs", sa.Column("question", sa.Text(), nullable=True))
    # One active run per ticket, enforced by the database rather than by care.
    op.create_index(
        "uq_runs_one_active_per_ticket",
        "runs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'claimed', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_runs_one_active_per_ticket", table_name="runs")
    op.drop_column("runs", "question")
    op.drop_column("runs", "summary")
    op.drop_column("runs", "callback_secret")
    op.drop_column("runs", "queued_at")
