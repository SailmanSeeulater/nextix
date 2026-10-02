"""ticket_creations: Idempotency-Key records for POST /api/tickets

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A client that times out while triage runs retries with the same key and gets the
    # first attempt's response, not a second GitHub issue.
    op.create_table(
        "ticket_creations",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_creations_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_ticket_creations")),
    )


def downgrade() -> None:
    op.drop_table("ticket_creations")
