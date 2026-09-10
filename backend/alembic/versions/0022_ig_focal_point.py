"""Photographer-set focal point for the Instagram crop

Face detection answers "where is the face", which is usually but not always the
same question as "what is this photograph about". A hand on a fire fan, a
performer mid-drop with their back turned, a two-person duo act where Haar picks
the wrong face — in all of those the auto crop anchors on the wrong thing and the
only recourse was to hand-crop every frame.

These two columns let the photographer move the anchor instead. Null keeps the
existing behaviour exactly: detect a face, fall back to centre.

Revision ID: 0022_ig_focal_point
Revises: 0021_default_include_exif
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_ig_focal_point"
down_revision: Union[str, None] = "0021_default_include_exif"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("ig_focal_x", sa.Float(), nullable=True))
    op.add_column("posts", sa.Column("ig_focal_y", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "ig_focal_y")
    op.drop_column("posts", "ig_focal_x")
