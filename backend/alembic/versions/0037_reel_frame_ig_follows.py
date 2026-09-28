"""Let a reel's draft frames keep or drop Instagram to match the reel's kind

A reel built from drafts takes Instagram off each frame, because the reel carries those
photographs there. A Trial Reel is shown to non-followers first, so doing that for a
trial would leave followers with none of the photos unless the trial graduated. The
photographer's rule: trial reels are extra reach, and the frames still go to Instagram
as normal feed posts.

Which frames that applies to has to be remembered, because the reel's kind can be
switched after it is built: only frames that were drafts aimed at Instagram when the
reel was made. Existing rows default to false, so nothing already built is retargeted.

Revision ID: 0037_reel_frame_ig_follows
Revises: 0036_reel_trial
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_reel_frame_ig_follows"
down_revision: Union[str, None] = "0036_reel_trial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reel_photos") as b:
        b.add_column(sa.Column("ig_follows_reel", sa.Boolean(), nullable=False,
                               server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("reel_photos") as b:
        b.drop_column("ig_follows_reel")
