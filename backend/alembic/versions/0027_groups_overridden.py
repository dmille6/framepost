"""Remember that the photographer chose the groups for a post

0026 seeds group submissions from defaults when a post has none. That makes
"never touched" and "deliberately emptied" the same state, so clearing every
group off a post would silently refill it with ten defaults at publish -- the
exact over-submission the throttle work exists to prevent.

This flag records the act of choosing, not the choice. Set whenever a selection
is saved, empty or not, it lets the seeder tell an untouched post from one the
photographer already decided about.

Revision ID: 0027_groups_overridden
Revises: 0026_group_match_tags
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_groups_overridden"
down_revision: Union[str, None] = "0026_group_match_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("groups_overridden", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("posts", "groups_overridden")
