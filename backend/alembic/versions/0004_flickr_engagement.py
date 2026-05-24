"""flickr_engagement — daily snapshots of views/faves/comments per posted photo

Revision ID: 0004_flickr_engagement
Revises: 0003_trending_tags
Create Date: 2026-05-03 12:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_flickr_engagement"
down_revision: Union[str, None] = "0003_trending_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flickr_engagement",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("flickr_photo_id", sa.String, nullable=False),
        sa.Column(
            "sampled_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("faves", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_engagement_post", "flickr_engagement", ["post_id"])
    op.create_index("idx_engagement_sampled_at", "flickr_engagement", ["sampled_at"])


def downgrade() -> None:
    op.drop_index("idx_engagement_sampled_at", table_name="flickr_engagement")
    op.drop_index("idx_engagement_post", table_name="flickr_engagement")
    op.drop_table("flickr_engagement")
