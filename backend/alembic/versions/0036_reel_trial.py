"""Let a reel go out as an Instagram Trial Reel

Median Instagram reach on this account is ~9% of followers: the constraint is
distribution, not content. A Trial Reel is shown to non-followers first, and can
graduate to followers later -- the one native lever Meta built for reaching people who
don't already follow. Meta added `trial_params` to the Content Publishing API on
2025-12-03 for both Facebook Login and Instagram Login.

Nullable with no default: every existing reel is, and stays, an ordinary reel. NULL is
"not a trial"; a value is Meta's graduation_strategy (SS_PERFORMANCE | MANUAL).

Revision ID: 0036_reel_trial
Revises: 0035_group_audience_size
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_reel_trial"
down_revision: Union[str, None] = "0035_group_audience_size"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.add_column(sa.Column("trial_graduation", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.drop_column("trial_graduation")
