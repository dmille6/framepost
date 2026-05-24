"""performers + post_performers — lightweight performer tagging

A performer is a person you photograph repeatedly (regular burlesque/circus/concert
acts). The system stores just enough to auto-insert their @-mention and a hashtag
in captions when they're tagged on a post — display_name + a single instagram_handle.
We deliberately don't model per-platform handles (the IG handle is universally
recognizable enough across IG/Bluesky/Pixelfed/Threads).

instagram_handle is stored WITHOUT the leading @ for normalization. Caption builders
prepend @ at insertion time.

post_performers.position preserves insertion order so the @-line shows performers
in the order the user added them (not alphabetically, not by ID).

Revision ID: 0013_performers
Revises: 0012_reels
Create Date: 2026-05-24 16:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_performers"
down_revision: Union[str, None] = "0012_reels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "performers",
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
        sa.UniqueConstraint("display_name", name="uq_performer_display_name"),
    )
    # Case-insensitive lookup index for type-ahead matching.
    op.create_index("idx_performer_name_lower", "performers", [sa.text("lower(display_name)")])

    op.create_table(
        "post_performers",
        sa.Column("post_id", sa.String, primary_key=True),
        sa.Column("performer_id", sa.String, primary_key=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["performer_id"], ["performers.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_post_performers_performer", "post_performers", ["performer_id"])


def downgrade() -> None:
    op.drop_index("idx_post_performers_performer", table_name="post_performers")
    op.drop_table("post_performers")
    op.drop_index("idx_performer_name_lower", table_name="performers")
    op.drop_table("performers")
