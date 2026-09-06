"""include_exif flag on posts

Opt in, per post, to appending the camera/lens/exposure line to the description. Off by
default: the shot info is noise on most posts and Flickr renders EXIF natively anyway.

Revision ID: 0020_include_exif
Revises: 0019_ig_crop_rect
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_include_exif"
down_revision: Union[str, None] = "0019_ig_crop_rect"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("include_exif", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("posts", "include_exif")
