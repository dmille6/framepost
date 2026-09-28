"""Make a reel's publish claim owned, not just timestamped

A claim that is only a timestamp can be taken over as "stale" while its worker is still
alive (the transcode poll budget excludes HTTP time, so a slow Meta stretches an attempt
well past it), and the first worker's release then cleared the second worker's claim.
A random token per claim fixes both: release and every claim-guarded write require
WHERE token = mine. publish_claimed_at becomes "last renewed at", refreshed while the
attempt waits on Meta.

Downgrade drops the column with a native ALTER TABLE DROP COLUMN (recreate="never";
SQLite >= 3.35). Recreating `reels` with foreign keys on would cascade-delete every
reel_photos row.

Revision ID: 0039_reel_claim_token
Revises: 0038_reel_publish_claim
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_reel_claim_token"
down_revision: Union[str, None] = "0038_reel_publish_claim"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reels", recreate="never") as b:
        b.add_column(sa.Column("publish_claim_token", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reels", recreate="never") as b:
        b.drop_column("publish_claim_token")
