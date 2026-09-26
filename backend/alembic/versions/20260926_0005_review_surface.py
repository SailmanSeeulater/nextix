"""review surface: CI check runs, PR head sha, test results on runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_runs",
        # GitHub's own check run id: webhooks and API reads upsert the same row.
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("head_branch", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("details_url", sa.Text(), nullable=True),
        sa.Column("app_name", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_check_runs_repo_head", "check_runs", ["repo_id", "head_sha"])
    # Commit statuses: the older CI signal (Vercel, CircleCI, ...), one row per context.
    op.create_table(
        "commit_statuses",
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repos.id"), primary_key=True),
        sa.Column("sha", sa.Text(), primary_key=True),
        sa.Column("context", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The commit an open PR currently points at: CI status is shown for this sha.
    op.add_column("tickets", sa.Column("pr_head_sha", sa.Text(), nullable=True))
    # When check runs were last read from GitHub, and why that failed if it did
    # ("forbidden" when the App lacks the Checks permission).
    op.add_column(
        "tickets", sa.Column("checks_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tickets", sa.Column("checks_error", sa.Text(), nullable=True))
    # {"command", "exit_code", "passed", "duration_s"} from the runner, or null.
    op.add_column("runs", sa.Column("tests", postgresql.JSONB(), nullable=True))
    # Review steps that went wrong (setup, app start, a screenshot route, a rejected
    # artifact): [{"step", "label", "message"}], shown on the ticket page.
    op.add_column("runs", sa.Column("review_errors", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "review_errors")
    op.drop_column("runs", "tests")
    op.drop_column("tickets", "checks_error")
    op.drop_column("tickets", "checks_synced_at")
    op.drop_column("tickets", "pr_head_sha")
    op.drop_table("commit_statuses")
    op.drop_index("ix_check_runs_repo_head", table_name="check_runs")
    op.drop_table("check_runs")
