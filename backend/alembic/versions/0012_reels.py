"""reels + reel_photos — silent MP4 Reels built from up to 10 stills

A Reel is a first-class entity (re-editable, re-downloadable, listed in history). It pulls
N posts (this app stores photos as Posts), composes them into a 1080×1920 silent MP4 via
ffmpeg, and stores the result on disk. The cover_post_id supplies both the IG grid thumbnail
and the caption text — decoupled from play order so the strongest opener-photo doesn't need
to be the strongest grid-thumb.

reel_photos.crop_start_json / crop_end_json hold {x, y, width, height} in source-image
pixel coordinates (the user's chosen 9:16 viewport). crop_end_json NULL = simple mode
(static crop + gentle auto-zoom). Both set = director mode (animated pan between viewports).

Revision ID: 0012_reels
Revises: 0011_post_likes
Create Date: 2026-05-23 20:45:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_reels"
down_revision: Union[str, None] = "0011_post_likes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reels",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("cover_post_id", sa.String, nullable=False),
        sa.Column("total_duration_seconds", sa.Float, nullable=False, server_default="60.0"),
        sa.Column("caption", sa.Text),
        sa.Column("mp4_path", sa.Text),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text),
        sa.Column(
            "created_at", sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at", sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["cover_post_id"], ["posts.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_reels_created_at", "reels", ["created_at"])
    op.create_index("idx_reels_status", "reels", ["status"])

    op.create_table(
        "reel_photos",
        sa.Column("reel_id", sa.String, primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("crop_start_json", sa.Text),
        sa.Column("crop_end_json", sa.Text),
        sa.ForeignKeyConstraint(["reel_id"], ["reels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_reel_photos_post", "reel_photos", ["post_id"])


def downgrade() -> None:
    op.drop_index("idx_reel_photos_post", table_name="reel_photos")
    op.drop_table("reel_photos")
    op.drop_index("idx_reels_status", table_name="reels")
    op.drop_index("idx_reels_created_at", table_name="reels")
    op.drop_table("reels")
