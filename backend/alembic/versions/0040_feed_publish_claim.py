"""Own feed post and platform delivery attempts across worker jobs.

Native ALTERs only: rebuilding posts on downgrade would cascade-delete its children.

Revision ID: 0040_feed_publish_claim
Revises: 0039_reel_claim_token
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_feed_publish_claim"
down_revision = "0039_reel_claim_token"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("posts", "post_platforms"):
        with op.batch_alter_table(table, recreate="never") as b:
            b.add_column(sa.Column("publish_claimed_at", sa.DateTime(), nullable=True))
            b.add_column(sa.Column("publish_claim_token", sa.String(), nullable=True))


def downgrade():
    for table in ("post_platforms", "posts"):
        with op.batch_alter_table(table, recreate="never") as b:
            b.drop_column("publish_claim_token")
            b.drop_column("publish_claimed_at")
