"""The same Bluesky handle for venues

Venues are mentioned in captions on exactly the same path as performers
(performers.mention_block / _add_entity), so they produce exactly the same dead
'@instagram_handle' on Bluesky. Fixing one without the other would leave a hole
that looks fixed.

Revision ID: 0029_venue_bluesky_handle
Revises: 0028_performer_bluesky_handle
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_venue_bluesky_handle"
down_revision: Union[str, None] = "0028_performer_bluesky_handle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("venues", sa.Column("bluesky_handle", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("venues", "bluesky_handle")
