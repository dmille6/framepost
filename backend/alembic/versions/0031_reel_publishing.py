"""Reels become schedulable, so the workflow stops ending at a download

The reel builder shipped in May producing an MP4 and a copyable caption. The last
two steps were the photographer's: move the file to a phone, upload it by hand.
That is the one manual step left in an otherwise automated pipeline, and it is why
exactly one reel has ever been built -- a workflow nobody runs is a workflow that
does not exist.

These columns give a reel the same lifecycle a post already has: a time to fire,
a record of where it landed, and somewhere to put the reason when it doesn't.
`publish_attempts` exists because Meta transcodes a reel server-side and a slow
transcode is a retry, not a failure -- without a count, a reel that times out once
would be retried forever.

Revision ID: 0031_reel_publishing
Revises: 0030_opinionated_defaults
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_reel_publishing"
down_revision: Union[str, None] = "0030_opinionated_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.add_column(sa.Column("scheduled_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("posted_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("remote_id", sa.Text(), nullable=True))
        b.add_column(sa.Column("remote_url", sa.Text(), nullable=True))
        b.add_column(sa.Column("publish_error", sa.Text(), nullable=True))
        b.add_column(sa.Column(
            "publish_attempts", sa.Integer(), nullable=False, server_default="0"
        ))
        # The staged R2 object, held so it can be deleted once Meta has ingested it.
        # Without this the bucket accumulates one MP4 per reel, forever.
        b.add_column(sa.Column("staged_key", sa.Text(), nullable=True))

    op.create_index("ix_reels_scheduled_at", "reels", ["scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_reels_scheduled_at", table_name="reels")
    with op.batch_alter_table("reels") as b:
        for col in ("staged_key", "publish_attempts", "publish_error",
                    "remote_url", "remote_id", "posted_at", "scheduled_at"):
            b.drop_column(col)
