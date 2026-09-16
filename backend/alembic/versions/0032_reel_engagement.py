"""Engagement snapshots can belong to a reel, not just a post

Reels publish through `reels.remote_id`, not through `post_platforms`, and the
engagement sync iterates post_platforms rows. So the first reel published through
the API accumulated nothing: 7.5 hours live, zero snapshots. Every reel would have
done the same, silently, which matters more now that reels are the default for a
group of photographs. Engagement not collected on the day is not recoverable later.

`reel_id` rather than a nullable `post_id`: a snapshot still belongs to a post --
the reel's cover -- so joins and the NOT NULL constraint stay intact. What the new
column adds is *which* Instagram media the numbers came from, because a cover photo
can have both its own post and a reel it appears in, and averaging those together
would be wrong in a way nothing would flag.

Existing rows get NULL, meaning "the post's own engagement", which is what they are.

Revision ID: 0032_reel_engagement
Revises: 0031_reel_publishing
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_reel_engagement"
down_revision: Union[str, None] = "0031_reel_publishing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("engagement_snapshots") as b:
        b.add_column(sa.Column("reel_id", sa.String(), nullable=True))
    op.create_index(
        "ix_engagement_snapshots_reel_id", "engagement_snapshots", ["reel_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_engagement_snapshots_reel_id", table_name="engagement_snapshots")
    with op.batch_alter_table("engagement_snapshots") as b:
        b.drop_column("reel_id")
