"""disk_samples — periodic photo-volume usage history

Revision ID: 0002_disk_samples
Revises: 0001_initial
Create Date: 2026-05-03 04:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_disk_samples"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "disk_samples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "sampled_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("total_bytes", sa.Integer, nullable=False),
        sa.Column("used_bytes", sa.Integer, nullable=False),
        sa.Column("free_bytes", sa.Integer, nullable=False),
    )
    op.create_index("idx_disk_samples_sampled_at", "disk_samples", ["sampled_at"])


def downgrade() -> None:
    op.drop_index("idx_disk_samples_sampled_at", table_name="disk_samples")
    op.drop_table("disk_samples")
