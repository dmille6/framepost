"""Tag-gated group routing

A 25-group roster can't be driven by hand: picking from a 25-item checklist on
every post is the kind of friction that gets abandoned, and 517 of 518 posts
carry a stage/performance tag, so for most groups there is no decision to make.

default_enabled covers those -- but it has been dead weight since 0001, stored
and never read. This wires it up, and adds match_tags for the minority of groups
that fit only part of the catalogue: a concert pool should see the ~14% tagged
concert/livemusic, not 500 burlesque frames, which is how a moderator removes
you from a group.

Null match_tags means "no tag condition", so an existing default-on group keeps
applying to everything.

Revision ID: 0026_group_match_tags
Revises: 0025_group_throttle
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_group_match_tags"
down_revision: Union[str, None] = "0025_group_throttle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("groups", sa.Column("match_tags", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("groups", "match_tags")
