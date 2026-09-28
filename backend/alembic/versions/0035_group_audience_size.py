"""Record each Flickr group's member and pool counts

Flickr views on this account follow group fan-out (a photo in no groups drew ~21
views; one in 15+ drew ~150), so the size of each group is the most useful routing
signal the roster lacks. The daily throttle sync already calls flickr.groups.getInfo
for every group and the response carries both counts, so capturing them costs no
extra API calls.

Nullable with no default: NULL means "never synced / Flickr omitted it", which the
UI shows as unknown rather than as an empty group.

Revision ID: 0035_group_audience_size
Revises: 0034_ig_container_checkpoint
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_group_audience_size"
down_revision: Union[str, None] = "0034_ig_container_checkpoint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("groups") as b:
        b.add_column(sa.Column("member_count", sa.Integer(), nullable=True))
        b.add_column(sa.Column("pool_count", sa.Integer(), nullable=True))
        b.add_column(sa.Column("stats_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("groups") as b:
        b.drop_column("stats_synced_at")
        b.drop_column("pool_count")
        b.drop_column("member_count")
