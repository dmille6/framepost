"""Mark posts that were paid-boosted on Instagram.

A boost buys reach the organic analytics were never meant to measure: account-level
follower and reach numbers jump while the per-post insights do not, and any comparison
that includes a boosted post (time of day, performer, carousel vs single) is comparing
money, not photographs. NULL = not boosted; a timestamp records when it was flagged.

Revision ID: 0041_ig_boosted
Revises: 0040_feed_publish_claim
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_ig_boosted"
down_revision = "0040_feed_publish_claim"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("posts", recreate="never") as b:
        b.add_column(sa.Column("ig_boosted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("posts", recreate="never") as b:
        b.drop_column("ig_boosted_at")
