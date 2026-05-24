"""multi-platform posting — Bluesky, Pixelfed, future Mastodon

Adds two pieces:
1. platform_credentials gains `instance_url` (Pixelfed/Mastodon need one) + `extra_json`
   (per-platform metadata: refreshable session JWTs, OAuth client_id/secret, etc).
2. post_platforms — per-post per-platform fanout state. Lets a single post succeed on
   Bluesky, fail on Pixelfed, and stay independent of the Flickr columns on Post.

Revision ID: 0006_multi_platform
Revises: 0005_instagram_posted
Create Date: 2026-05-03 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_multi_platform"
down_revision: Union[str, None] = "0005_instagram_posted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("platform_credentials", sa.Column("instance_url", sa.Text, nullable=True))
    op.add_column("platform_credentials", sa.Column("extra_json", sa.Text, nullable=True))
    op.add_column(
        "platform_credentials",
        sa.Column("default_target", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "platform_credentials",
        sa.Column("last_success_at", sa.DateTime, nullable=True),
    )
    op.add_column("platform_credentials", sa.Column("last_error", sa.Text, nullable=True))

    op.create_table(
        "post_platforms",
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("platform_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("remote_id", sa.Text, nullable=True),
        sa.Column("remote_url", sa.Text, nullable=True),
        sa.Column("posted_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime, nullable=True),
        sa.PrimaryKeyConstraint("post_id", "platform_id"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["platform_id"], ["platform_credentials.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_post_platforms_post", "post_platforms", ["post_id"])
    op.create_index("idx_post_platforms_status", "post_platforms", ["status"])


def downgrade() -> None:
    op.drop_index("idx_post_platforms_status", table_name="post_platforms")
    op.drop_index("idx_post_platforms_post", table_name="post_platforms")
    op.drop_table("post_platforms")
    op.drop_column("platform_credentials", "last_error")
    op.drop_column("platform_credentials", "last_success_at")
    op.drop_column("platform_credentials", "default_target")
    op.drop_column("platform_credentials", "extra_json")
    op.drop_column("platform_credentials", "instance_url")
