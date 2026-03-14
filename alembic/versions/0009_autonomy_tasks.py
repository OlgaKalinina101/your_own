"""create autonomy_tasks table

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autonomy_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column(
            "trigger_type",
            sa.Enum("TIME", "MANUAL", name="triggertype"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "DONE", "CANCELLED", "FAILED", name="taskstatus"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_autonomy_tasks_account_id", "autonomy_tasks", ["account_id"])
    op.create_index("ix_autonomy_tasks_status", "autonomy_tasks", ["status"])
    op.create_index("ix_autonomy_tasks_scheduled_at", "autonomy_tasks", ["scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_autonomy_tasks_scheduled_at", table_name="autonomy_tasks")
    op.drop_index("ix_autonomy_tasks_status", table_name="autonomy_tasks")
    op.drop_index("ix_autonomy_tasks_account_id", table_name="autonomy_tasks")
    op.drop_table("autonomy_tasks")
    op.execute("DROP TYPE IF EXISTS triggertype")
    op.execute("DROP TYPE IF EXISTS taskstatus")
