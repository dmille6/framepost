"""venues table + posts.venue_id/show/city/alt_text — structured context for captions + AI

The lightweight performer model already showed how attaching IG handles to entities
unlocks audience-growth via auto-mentions on platforms that resolve them (IG). Venues
work the same way — NOLA venues like Hi-Ho Lounge, AllWays Lounge, Music Box Village,
etc. have IG accounts that repost performance photos tagged from their nights. A photo
has at most one venue (you're not in two places at once), so venue_id is a nullable FK
on the post — not a junction table.

show and city are simpler — short text fields that type-ahead from distinct values
already in the table. Show names ("Slow Burn Burlesque", "Devils Night", "Tease the
World") rarely have their own IG accounts; city ("New Orleans, LA") is almost always
constant for a given user but flexible for travel shoots.

alt_text is per-photo, AI-generated using the structured context above. Sent to
Bluesky/Pixelfed/Pinterest on auto-post; surfaced in the IG copy-paste tab for the
user to paste into Instagram's "Write alt text" field manually.

Revision ID: 0014_venues_show_city_alt
Revises: 0013_performers
Create Date: 2026-05-24 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_venues_show_city_alt"
down_revision: Union[str, None] = "0013_performers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "venues",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("instagram_handle", sa.Text),
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
        sa.UniqueConstraint("display_name", name="uq_venue_display_name"),
    )
    op.create_index("idx_venue_name_lower", "venues", [sa.text("lower(display_name)")])

    # SQLite ALTER TABLE limitations: ADD COLUMN is fine for nullable columns, FK constraints
    # added via ADD COLUMN ARE accepted in newer SQLite but the constraint isn't enforced
    # unless PRAGMA foreign_keys=ON (we don't set this globally in the app). The reference
    # is informational; cascade is handled at the application level when venues are deleted.
    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("venue_id", sa.String, nullable=True))
        batch_op.add_column(sa.Column("show", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("city", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("alt_text", sa.Text, nullable=True))
        batch_op.create_foreign_key(
            "fk_posts_venue", "venues", ["venue_id"], ["id"], ondelete="SET NULL"
        )

    op.create_index("idx_posts_venue", "posts", ["venue_id"])
    op.create_index("idx_posts_city_lower", "posts", [sa.text("lower(city)")])
    op.create_index("idx_posts_show_lower", "posts", [sa.text("lower(show)")])


def downgrade() -> None:
    op.drop_index("idx_posts_show_lower", table_name="posts")
    op.drop_index("idx_posts_city_lower", table_name="posts")
    op.drop_index("idx_posts_venue", table_name="posts")
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_constraint("fk_posts_venue", type_="foreignkey")
        batch_op.drop_column("alt_text")
        batch_op.drop_column("city")
        batch_op.drop_column("show")
        batch_op.drop_column("venue_id")
    op.drop_index("idx_venue_name_lower", table_name="venues")
    op.drop_table("venues")
