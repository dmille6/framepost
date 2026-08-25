"""IG insight metrics on engagement_snapshots + daily account_stats

Instagram exposes far more than likes/comments: reach, views, saves, shares,
profile visits, and follows attributable to a single post, plus account-level daily
reach/profile-views/engaged-accounts and the follower count.

Storage choice: NULLABLE columns on engagement_snapshots rather than a side table or
a JSON blob. The existing four counters (views/likes/comments/reposts) are already a
shared-vocabulary-with-gaps model, and the new metrics are the same shape — integers
sampled per post per platform over time. NULL means "this platform never reports it",
which is exactly the distinction analytics needs (a Flickr post has no `saves`; an
Instagram post with 0 saves genuinely has zero). A JSON blob would make the SQL
aggregation the analytics page depends on far messier for no gain at this scale.

account_stats is a separate table because its grain is different: one row per platform
per day, not per post.

Revision ID: 0016_platform_metrics
Revises: 0015_ig_crop
Create Date: 2026-08-25 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_platform_metrics"
down_revision: Union[str, None] = "0015_ig_crop"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_METRICS = ("reach", "saves", "shares", "profile_visits", "follows")


def upgrade() -> None:
    with op.batch_alter_table("engagement_snapshots") as b:
        for col in NEW_METRICS:
            b.add_column(sa.Column(col, sa.Integer, nullable=True))

    op.create_table(
        "account_stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("platform", sa.String, nullable=False),
        sa.Column("stat_date", sa.Date, nullable=False),
        sa.Column("followers", sa.Integer, nullable=True),
        sa.Column("follows", sa.Integer, nullable=True),
        sa.Column("media_count", sa.Integer, nullable=True),
        sa.Column("reach", sa.Integer, nullable=True),
        sa.Column("profile_views", sa.Integer, nullable=True),
        sa.Column("accounts_engaged", sa.Integer, nullable=True),
        sa.Column("website_clicks", sa.Integer, nullable=True),
        sa.Column(
            "sampled_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        # One row per platform per day — re-running the sync updates in place.
        sa.UniqueConstraint("platform", "stat_date", name="uq_account_stats_platform_date"),
    )
    op.create_index("idx_account_stats_platform_date", "account_stats", ["platform", "stat_date"])

    # Analytics groups by (post, platform) and takes the latest sample — this is the
    # index that keeps that cheap as the series grows.
    op.create_index(
        "idx_engagement_post_platform_time",
        "engagement_snapshots",
        ["post_id", "platform", "sampled_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_engagement_post_platform_time", table_name="engagement_snapshots")
    op.drop_index("idx_account_stats_platform_date", table_name="account_stats")
    op.drop_table("account_stats")
    with op.batch_alter_table("engagement_snapshots") as b:
        for col in reversed(NEW_METRICS):
            b.drop_column(col)
