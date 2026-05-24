"""post_likes — per-user fave/like records across platforms

Parallel to post_comments. Each row is "user X liked your post Y on platform Z" with
deduplication on (platform, remote_id) — typically the like's stable identifier from the
platform (Bluesky: like URI, Pixelfed: account_id+status_id composite, Flickr: NSID).

Used by the Activity feed to surface "Valmarie liked your post" alongside comment text.

Revision ID: 0011_post_likes
Revises: 0010_comments_engagement
Create Date: 2026-05-07 11:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_post_likes"
down_revision: Union[str, None] = "0010_comments_engagement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_likes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("platform", sa.String, nullable=False),
        sa.Column("remote_id", sa.Text, nullable=False),
        sa.Column("actor_handle", sa.Text),
        sa.Column("actor_display_name", sa.Text),
        sa.Column("actor_url", sa.Text),
        sa.Column("liked_at", sa.DateTime),
        sa.Column(
            "fetched_at", sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("seen_at", sa.DateTime),
        sa.UniqueConstraint("platform", "remote_id", name="uq_like_platform_remote"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_likes_post", "post_likes", ["post_id"])
    op.create_index("idx_likes_platform_liked", "post_likes", ["platform", "liked_at"])
    op.create_index("idx_likes_seen_at", "post_likes", ["seen_at"])


def downgrade() -> None:
    op.drop_index("idx_likes_seen_at", table_name="post_likes")
    op.drop_index("idx_likes_platform_liked", table_name="post_likes")
    op.drop_index("idx_likes_post", table_name="post_likes")
    op.drop_table("post_likes")
