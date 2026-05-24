"""posts.reddit_posted_at + reddit_subreddits config seed

Adds the manual cross-platform-tracking flag for Reddit (parallel to posted_to_instagram_at)
and seeds reddit_subreddits with a sensible default for the photographer's typical posting.

Revision ID: 0008_reddit_posted
Revises: 0007_target_platforms
Create Date: 2026-05-03 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_reddit_posted"
down_revision: Union[str, None] = "0007_target_platforms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_SUBREDDITS = "Burlesque itookapicture NewOrleans circus"


def upgrade() -> None:
    op.add_column("posts", sa.Column("reddit_posted_at", sa.DateTime, nullable=True))
    # Seed reddit_subreddits if not already set
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO app_config (key, value) "
            f"VALUES ('reddit_subreddits', '{DEFAULT_SUBREDDITS}')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM app_config WHERE key = 'reddit_subreddits'"))
    op.drop_column("posts", "reddit_posted_at")
