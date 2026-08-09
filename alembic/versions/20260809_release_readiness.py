"""release readiness state

Revision ID: 20260809_release_readiness
Revises:
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_release_readiness"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job") as batch:
        batch.add_column(sa.Column("manual_selection", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("scheduledtask") as batch:
        batch.add_column(sa.Column("last_skipped_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scheduledtask") as batch:
        batch.drop_column("last_skipped_at")
    with op.batch_alter_table("job") as batch:
        batch.drop_column("manual_selection")
