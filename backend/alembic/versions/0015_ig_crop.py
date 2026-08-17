"""posts.ig_fit/ig_crop_offset + post_platforms.staging_remote_id — IG auto-transform

Instagram's API rejects feed images taller than its portrait floor (documented 4:5;
possibly 3:4 since 2025 — probed at runtime, recorded in app_config). Rather than
failing those posts permanently, the worker now renders a crop/pad variant and stages
it as a hidden machine-tagged Flickr photo for Meta's URL ingest.

- posts.ig_fit: crop | pad | pad_blur (null = crop). Per-post override in the editor.
- posts.ig_crop_offset: 0..1 crop-window position along the cropped axis, set by the
  editor's nudge slider (null = face-anchored auto via the Reels Haar cascade).
- post_platforms.staging_remote_id: "flickr_photo_id|ratio_key" of the in-flight
  staging variant; written before the publish attempt so retries reuse the upload and
  the daily orphan sweep can distinguish live from abandoned. Cleared on success.

Revision ID: 0015_ig_crop
Revises: 0014_venues_show_city_alt
Create Date: 2026-08-17 15:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_ig_crop"
down_revision: Union[str, None] = "0014_venues_show_city_alt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("ig_fit", sa.String, nullable=True))
        batch_op.add_column(sa.Column("ig_crop_offset", sa.Float, nullable=True))
    with op.batch_alter_table("post_platforms") as batch_op:
        batch_op.add_column(sa.Column("staging_remote_id", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("post_platforms") as batch_op:
        batch_op.drop_column("staging_remote_id")
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("ig_crop_offset")
        batch_op.drop_column("ig_fit")
