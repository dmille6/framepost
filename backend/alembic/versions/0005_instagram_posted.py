"""posts.posted_to_instagram_at — manual cross-platform tracking flag

Revision ID: 0005_instagram_posted
Revises: 0004_flickr_engagement
Create Date: 2026-05-03 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_instagram_posted"
down_revision: Union[str, None] = "0004_flickr_engagement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("posted_to_instagram_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "posted_to_instagram_at")
