"""post_comments + engagement_snapshots — unified comment + engagement tracking

Adds two tables that cover Bluesky and Pixelfed activity (and going forward, Flickr
comments). Existing flickr_engagement table stays as-is for backward compat with the
analytics queries that already use it.

post_comments: one row per comment seen, deduped by (platform, remote_id). The first time
we see a comment it gets fetched_at = now() and seen_at = NULL. Marking-as-read updates
seen_at so the unread count can reset.

engagement_snapshots: parallel to flickr_engagement but generic. We start populating it for
Bluesky/Pixelfed (Flickr keeps using its own table). Eventually they could unify.

Revision ID: 0010_comments_engagement
Revises: 0009_title_templates
Create Date: 2026-05-05 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_comments_engagement"
down_revision: Union[str, None] = "0009_title_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_comments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("platform", sa.String, nullable=False),  # 'flickr' | 'bluesky' | 'pixelfed'
        sa.Column("remote_id", sa.Text, nullable=False),    # platform-specific comment ID
        sa.Column("author_handle", sa.Text),                # @handle / NSID / acct
        sa.Column("author_display_name", sa.Text),
        sa.Column("author_url", sa.Text),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("posted_at", sa.DateTime),                # when the comment was posted on the platform
        sa.Column(
            "fetched_at", sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("seen_at", sa.DateTime),                  # NULL = unread
        sa.UniqueConstraint("platform", "remote_id", name="uq_comment_platform_remote"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_comments_post", "post_comments", ["post_id"])
    op.create_index("idx_comments_platform_posted", "post_comments", ["platform", "posted_at"])
    op.create_index("idx_comments_seen_at", "post_comments", ["seen_at"])

    op.create_table(
        "engagement_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("platform", sa.String, nullable=False),
        sa.Column(
            "sampled_at", sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reposts", sa.Integer, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_engagement_snap_post", "engagement_snapshots", ["post_id"])
    op.create_index("idx_engagement_snap_platform_sampled", "engagement_snapshots", ["platform", "sampled_at"])


def downgrade() -> None:
    op.drop_index("idx_engagement_snap_platform_sampled", table_name="engagement_snapshots")
    op.drop_index("idx_engagement_snap_post", table_name="engagement_snapshots")
    op.drop_table("engagement_snapshots")
    op.drop_index("idx_comments_seen_at", table_name="post_comments")
    op.drop_index("idx_comments_platform_posted", table_name="post_comments")
    op.drop_index("idx_comments_post", table_name="post_comments")
    op.drop_table("post_comments")
