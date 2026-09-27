"""tickets.pending_comment: PR feedback given as a comment, waiting for its turn

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # {"author", "state", "html_url", "body", ...}: a trusted comment on the PR that
    # arrived while a run was active (reviews use pending_review_id).
    op.add_column("tickets", sa.Column("pending_comment", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "pending_comment")
