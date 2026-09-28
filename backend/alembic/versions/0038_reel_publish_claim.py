"""Claim a reel for the duration of a publish attempt

The trial kind must not change while a publish is under way, and a read-check-write in
the PATCH route could not guarantee that: it could read before the worker saved its
container checkpoint and commit after the publish. The worker now takes a claim with a
conditional UPDATE and the route changes the kind with one; SQLite serialises writers,
so exactly one wins. NULL = unclaimed, which every existing reel is.

Revision ID: 0038_reel_publish_claim
Revises: 0037_reel_frame_ig_follows
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_reel_publish_claim"
down_revision: Union[str, None] = "0037_reel_frame_ig_follows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.add_column(sa.Column("publish_claimed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.drop_column("publish_claimed_at")
