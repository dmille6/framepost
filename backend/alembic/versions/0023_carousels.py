"""Carousels: group posts so they publish as one Instagram post

A carousel is a grouping over posts, not a new kind of post, because every frame still
needs its own Flickr upload — Flickr has no carousel, and the archive there should keep
one photo per photo. So members stay ordinary posts and one of them (position 0) owns
the Instagram post on the group's behalf.

Modelling it this way means the scheduler, the retry policy, the failure taxonomy and
the engagement sync all keep working untouched: it is still exactly one post_platforms
row doing the publishing.

Revision ID: 0023_carousels
Revises: 0022_ig_focal_point
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_carousels"
down_revision: Union[str, None] = "0022_ig_focal_point"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("carousel_id", sa.String(), nullable=True))
    op.add_column("posts", sa.Column("carousel_position", sa.Integer(), nullable=True))
    op.create_index("ix_posts_carousel_id", "posts", ["carousel_id"])


def downgrade() -> None:
    op.drop_index("ix_posts_carousel_id", table_name="posts")
    op.drop_column("posts", "carousel_position")
    op.drop_column("posts", "carousel_id")
