"""posts.target_platforms — per-post platform targeting

Stores a JSON list of platform names (e.g. ["flickr", "bluesky"]) that this post should fire
to. NULL means "use defaults" (every connected platform with default_target=1). When
non-NULL, the scheduler honors the list literally — letting users opt out of one or more
platforms for a specific photo without affecting their global default.

Revision ID: 0007_target_platforms
Revises: 0006_multi_platform
Create Date: 2026-05-03 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_target_platforms"
down_revision: Union[str, None] = "0006_multi_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("target_platforms", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "target_platforms")
