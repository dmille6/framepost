"""trending_tags — Flickr-derived popular tags per seed

Revision ID: 0003_trending_tags
Revises: 0002_disk_samples
Create Date: 2026-05-03 11:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_trending_tags"
down_revision: Union[str, None] = "0002_disk_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trending_tags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String, nullable=False),       # "related" | "popular_photos"
        sa.Column("seed_tag", sa.String, nullable=False),
        sa.Column("tag", sa.String, nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "last_synced_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("idx_trending_seed", "trending_tags", ["seed_tag"])
    op.create_index("idx_trending_tag", "trending_tags", ["tag"])


def downgrade() -> None:
    op.drop_index("idx_trending_tag", table_name="trending_tags")
    op.drop_index("idx_trending_seed", table_name="trending_tags")
    op.drop_table("trending_tags")
