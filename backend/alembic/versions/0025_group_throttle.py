"""Per-group submission throttle period

`daily_limit` has existed since 0001 but nothing ever read it -- the worker
submitted every due row on every pass. That produced the `error 5: Photo limit
reached` failures already in post_groups, which classify() treats as permanent,
so an over-eager worker burns a submission slot for good instead of waiting.

Flickr groups don't all throttle per day: the pools worth joining publish limits
like "1 per week" or "30 per month". A single integer can't express those, so
this adds the period alongside the count. Existing rows keep their meaning --
'day' is the default, which is what daily_limit always implied.

Revision ID: 0025_group_throttle
Revises: 0024_carousel_children
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_group_throttle"
down_revision: Union[str, None] = "0024_carousel_children"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("limit_period", sa.String(), nullable=False, server_default="day"),
    )
    # The worker counts submissions inside a rolling window keyed on this column.
    op.create_index(
        "ix_post_groups_group_submitted",
        "post_groups",
        ["group_id", "submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_post_groups_group_submitted", table_name="post_groups")
    op.drop_column("groups", "limit_period")
