"""Remember Instagram carousel children across retries.

Revision ID: 0024_carousel_children
Revises: 0023_carousels
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_carousel_children"
down_revision: Union[str, None] = "0023_carousels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "post_platforms", sa.Column("carousel_children", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("post_platforms", "carousel_children")
