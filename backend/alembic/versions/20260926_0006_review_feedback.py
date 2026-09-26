"""feedback loop: the review a run answers, and a review waiting for its turn

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The GitHub pull request review a review_feedback run addresses, and what the ticket
    # page shows about it: {"author", "state", "html_url", "comments"}.
    op.add_column("runs", sa.Column("review_id", sa.BigInteger(), nullable=True))
    op.add_column("runs", sa.Column("review_meta", postgresql.JSONB(), nullable=True))
    # A trusted review that arrived while a run was active; queued when that run ends.
    op.add_column("tickets", sa.Column("pending_review_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "pending_review_id")
    op.drop_column("runs", "review_meta")
    op.drop_column("runs", "review_id")
