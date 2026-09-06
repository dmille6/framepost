"""ig crop rect replaces the single-axis offset

Revises: 0018_channel_health
Create Date: 2026-09-06 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "0019_ig_crop_rect"
down_revision: Union[str, None] = "0018_channel_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RATIOS = {"3:4": 3 / 4, "4:5": 4 / 5}


def upgrade() -> None:
    with op.batch_alter_table("posts") as b:
        # Normalized crop window in 0..1 SOURCE coordinates. A single offset float can
        # only slide a maximum-area window along the long axis; a rect can also express
        # a tighter crop, which is the whole point of the crop studio.
        for col in ("ig_crop_x", "ig_crop_y", "ig_crop_w", "ig_crop_h"):
            b.add_column(sa.Column(col, sa.Float, nullable=True))
        # The target ratio this rect was authored against. The runtime probe can move
        # the floor between 4:5 and 3:4, and without this a stored rect would silently
        # mean something different afterwards.
        b.add_column(sa.Column("ig_crop_ratio", sa.String, nullable=True))

    # Backfill: derive the equivalent maximum-area rect for every post that already has
    # a hand-set offset. `ig_crop_offset` is deliberately LEFT IN PLACE rather than
    # dropped — it costs nothing, it is the fallback when no rect is set, and dropping a
    # column on a live single-user database buys nothing back.
    conn = op.get_bind()
    floor_key = (conn.execute(
        text("SELECT value FROM app_config WHERE key = 'ig_min_ratio_support'")
    ).scalar() or "4:5")
    target = RATIOS.get(floor_key, RATIOS["4:5"])

    rows = conn.execute(text(
        "SELECT id, width, height, ig_crop_offset FROM posts "
        "WHERE ig_crop_offset IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL"
    )).fetchall()

    for pid, w, h, offset in rows:
        if not w or not h:
            continue
        ratio = w / h
        off = min(1.0, max(0.0, float(offset)))
        if ratio < target:            # too tall — window slides vertically
            fw, fh = 1.0, ratio / target
            x, y = 0.0, off * (1.0 - fh)
        elif ratio > target:          # too wide — window slides horizontally
            fw, fh = target / ratio, 1.0
            x, y = off * (1.0 - fw), 0.0
        else:                         # already the target ratio — whole frame
            fw = fh = 1.0
            x = y = 0.0
        conn.execute(
            text("UPDATE posts SET ig_crop_x=:x, ig_crop_y=:y, ig_crop_w=:w, "
                 "ig_crop_h=:h, ig_crop_ratio=:r WHERE id=:id"),
            {"x": x, "y": y, "w": fw, "h": fh, "r": floor_key, "id": pid},
        )


def downgrade() -> None:
    with op.batch_alter_table("posts") as b:
        b.drop_column("ig_crop_ratio")
        for col in ("ig_crop_h", "ig_crop_w", "ig_crop_y", "ig_crop_x"):
            b.drop_column(col)
